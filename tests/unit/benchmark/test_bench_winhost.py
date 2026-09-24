"""Tests for the Windows host's load, read from WSL2 through interop (P1.5).

A WSL2 harness facing a Windows lane recorded `other_cores: null`: the VM's
counters read quiet while VS Code or Defender loaded the host. These pin the
arithmetic on the raw Windows counters, when interop is tried at all, and
that every way it can fail keeps the old null with a note naming why -- a
load reading never costs a run. No test here runs powershell.exe: each stubs
`winhost._run`, and conftest refuses it for every other test.
"""

import base64
import json
import subprocess
from typing import NamedTuple

import pytest

from orchestrant.benchmark import hostload, winhost


# The real function, captured at import: conftest replaces the module
# attribute with a recorder for every test.
_REAL_LOAD_SNAPSHOT = hostload.load_snapshot

TICKS = winhost.TICKS_PER_S
PS = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

# Windows PowerShell's stderr when started with -EncodedCommand while stderr
# is redirected: CLIXML, a progress record first, then the error text with
# its CR/LF escaped. Its first line alone, "#< CLIXML", names nothing.
_CLIXML_DENIED = (
    "#< CLIXML\r\n"
    '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
    '<Obj S="progress" RefId="0"><TN RefId="0">'
    "<T>System.Management.Automation.PSCustomObject</T><T>System.Object</T></TN>"
    '<MS><I64 N="SourceId">1</I64><PR N="Record">'
    "<AV>Module werden für erstmalige Verwendung vorbereitet.</AV><AI>0</AI>"
    "<Nil /><PI>-1</PI><PC>-1</PC><T>Completed</T><SR>-1</SR><SD> </SD></PR>"
    "</MS></Obj>"
    '<S S="Error">Get-CimInstance : Zugriff verweigert _x000D__x000A_</S>'
    '<S S="Error">In Zeile:59 Zeichen:12_x000D__x000A_</S>'
    "</Objs>"
)


def _script_output(
    cpus=8, busy=4.0, seconds=3.0, pid=4242, lane_s=1.5, lane_wall=3.0, gone=False
):
    """The script's JSON line: `busy` of `cpus` cores busy for `seconds`.

    The lane (pid None: nothing listens) used `lane_s` CPU-seconds between
    samples `lane_wall` apart; `gone` is a lane restarted in the window.
    """
    start = 10**15  # counters count from boot, far from zero
    lane = [None, None]
    at = [None, None]
    if pid is not None:
        lane = [5 * TICKS, None if gone else 5 * TICKS + round(lane_s * TICKS)]
        at = [TICKS, TICKS + round(lane_wall * TICKS)]
    doc = {
        "port": 18181,
        "pid": pid,
        "cpus": [cpus, cpus],
        "idle": [start, start + round((cpus - busy) * seconds * TICKS)],
        "ts": [start, start + round(seconds * TICKS)],
        "lane": lane,
        "lane_at": at,
    }
    return json.dumps(doc, separators=(",", ":")) + "\n"


class CpuTimes(NamedTuple):
    user: float
    system: float
    idle: float


class _Clock:
    """Stands in for hostload's `time`: sleep() advances monotonic() exactly."""

    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _VmPsutil:
    """The WSL VM's own counters: 4 of 8 cores busy over the window."""

    def __init__(self):
        self._samples = iter([CpuTimes(0.0, 0.0, 0.0), CpuTimes(10.0, 2.0, 12.0)])

    def cpu_times(self):
        return next(self._samples)

    def cpu_count(self):
        return 8


class _WindowsLane:
    """What LaneProcess sees from WSL2 for a Windows lane: nothing on the port."""

    available = False
    listens_here = False
    base_url = "http://127.0.0.1:18181"
    reason = (
        "no local process listens on port 18181 (WSL2 cannot see the Windows host's)"
    )

    def cpu_seconds(self):
        return None


class _Wsl:
    """A WSL2 harness whose powershell.exe answers with `run`; records calls."""

    def __init__(self, monkeypatch, run, clock):
        self.calls = []
        self.clock = clock

        def recorded(argv, timeout):
            self.calls.append((argv, timeout))
            return run(argv, timeout)

        monkeypatch.setattr(winhost, "in_wsl", lambda: True)
        monkeypatch.setattr(winhost, "find_powershell", lambda: PS)
        monkeypatch.setattr(winhost, "_run", recorded)
        monkeypatch.setattr(hostload, "_psutil", _VmPsutil)
        monkeypatch.setattr(hostload, "time", clock)


def _answers(stdout, code=0, stderr="", spend=3.4):
    """A powershell.exe that took `spend` seconds and printed `stdout`."""
    clock = _Clock()

    def run(argv, timeout):
        clock.sleep(spend)
        return code, stdout, stderr

    return run, clock


def _raises(exc, spend=0.0):
    clock = _Clock()

    def run(argv, timeout):
        clock.sleep(spend)
        raise exc

    return run, clock


def _snapshot(monkeypatch, run_and_clock, lane=None):
    run, clock = run_and_clock
    wsl = _Wsl(monkeypatch, run, clock)
    snap = _REAL_LOAD_SNAPSHOT(3, lane=lane or _WindowsLane())
    return snap, wsl


class TestInWsl:
    """WSL is its interop handler or Microsoft's kernel, and only on Linux."""

    def _host(self, monkeypatch, tmp_path, platform, release, handler=None):
        if handler:
            (tmp_path / handler).write_text("enabled\n")
        monkeypatch.setattr(winhost.sys, "platform", platform)
        monkeypatch.setattr(winhost, "_INTEROP_GLOB", str(tmp_path / "WSLInterop*"))
        monkeypatch.setattr(winhost, "_kernel_release", lambda: release)

    def test_microsofts_kernel_is_wsl(self, monkeypatch, tmp_path):
        self._host(monkeypatch, tmp_path, "linux", "6.18.33.2-microsoft-standard-WSL2")
        assert winhost.in_wsl()

    def test_the_interop_handler_alone_is_enough(self, monkeypatch, tmp_path):
        # Newer WSL builds register it as WSLInterop-late.
        self._host(monkeypatch, tmp_path, "linux", "6.8.0", "WSLInterop-late")
        assert winhost.in_wsl()

    def test_plain_linux_is_not(self, monkeypatch, tmp_path):
        self._host(monkeypatch, tmp_path, "linux", "6.8.0-45-generic")
        assert not winhost.in_wsl()

    def test_windows_itself_is_not(self, monkeypatch, tmp_path):
        self._host(monkeypatch, tmp_path, "win32", "microsoft", "WSLInterop")
        assert not winhost.in_wsl()

    def test_wsl1s_capitalised_kernel_is_wsl_too(self, monkeypatch, tmp_path):
        # WSL1 reports "Microsoft", WSL2 "microsoft": the match ignores case.
        self._host(monkeypatch, tmp_path, "linux", "4.4.0-19041-Microsoft")
        assert winhost.in_wsl()


class TestFindPowershell:
    """Windows PowerShell at its System32 path under /mnt/c, else on PATH."""

    def _which(self, monkeypatch, found):
        asked = []

        def which(name):
            asked.append(name)
            return found

        monkeypatch.setattr(winhost.shutil, "which", which)
        return asked

    def test_the_system32_path_comes_first(self, monkeypatch, tmp_path):
        exe = tmp_path / "powershell.exe"
        exe.write_text("")
        monkeypatch.setattr(winhost, "POWERSHELL", str(exe))
        self._which(monkeypatch, "/usr/local/bin/powershell.exe")
        assert winhost.find_powershell() == str(exe)

    def test_path_when_c_is_mounted_elsewhere(self, monkeypatch, tmp_path):
        # automount root = /c/ moves System32, and interop's PATH still has it.
        monkeypatch.setattr(winhost, "POWERSHELL", str(tmp_path / "absent.exe"))
        asked = self._which(monkeypatch, "/c/Windows/powershell.exe")
        assert winhost.find_powershell() == "/c/Windows/powershell.exe"
        assert asked == ["powershell.exe"]

    def test_none_when_neither_exists(self, monkeypatch, tmp_path):
        monkeypatch.setattr(winhost, "POWERSHELL", str(tmp_path / "absent.exe"))
        self._which(monkeypatch, None)
        assert winhost.find_powershell() is None


class TestScript:
    """One script, CIM classes only, with nothing spliced in but two integers."""

    def test_the_port_and_the_window_are_integers(self):
        text = winhost.script(18181, 3)
        assert "$port = 18181" in text and "Start-Sleep -Milliseconds 3000" in text
        with pytest.raises(ValueError):
            winhost.script("18181; Remove-Item C:\\", 3)

    def test_no_localised_counter_path(self):
        # typeperf's paths are German on this host; WMI class names are not.
        text = winhost.script(18181, 3)
        assert "Win32_PerfRawData_PerfOS_Processor" in text
        assert "Win32_Process" in text
        assert "typeperf" not in text and "Get-Counter" not in text


class TestParse:
    """Busy cores from idle ticks, the lane over its own samples' span."""

    def test_busy_cores_from_the_idle_counter(self):
        got = winhost.parse(_script_output(busy=2.0), 18181)
        assert got["load"]["cpu_busy_percent_window"] == 25.0
        assert got["wall"] == 3.0 and got["cpus"] == 8 and got["pid"] == 4242

    def test_lane_cores_over_the_span_of_its_own_samples(self):
        got = winhost.parse(_script_output(lane_s=1.5, lane_wall=3.3), 18181)
        assert got["load"]["lane_cpu_s"] == 1.5
        assert got["load"]["lane_cores"] == 0.45  # 1.5 CPU-seconds over 3.3 s
        assert got["wall"] == 3.0  # the window is the counters' span, not the lane's

    def test_a_child_gone_mid_window_reads_no_lane_load_not_negative(self):
        # Its ticks leave the second sum. Negative lane cores would book more
        # than the host's busy cores as other load; the native path clamps too.
        got = winhost.parse(_script_output(lane_s=-0.5), 18181)
        assert got["load"]["lane_cpu_s"] == 0.0 and got["load"]["lane_cores"] == 0.0

    def test_idle_beyond_the_window_reads_zero_busy_not_negative(self):
        got = winhost.parse(_script_output(busy=-0.5), 18181)
        assert got["load"]["cpu_busy_percent_window"] == 0.0

    def test_text_before_the_reading_is_ignored(self):
        got = winhost.parse("WARNUNG: irgendwas\n" + _script_output(), 18181)
        assert got["load"]["cpu_busy_percent_window"] == 50.0

    def test_nothing_listening_has_no_lane_fields(self):
        got = winhost.parse(_script_output(pid=None), 18181)
        assert got["pid"] is None and "lane_cores" not in got["load"]

    def test_lane_samples_stamped_alike_have_no_lane_fields(self):
        # Not a ZeroDivisionError: that escapes parse()'s InteropError contract.
        got = winhost.parse(_script_output(lane_wall=0.0), 18181)
        assert "lane_cores" not in got["load"]

    @pytest.mark.parametrize(
        ("stdout", "why"),
        [
            ("Die Benennung wurde nicht erkannt.\n", "no reading"),
            ('{"cpus": [8, 8]\n', "unreadable"),
            ('{"cpus": [8, 8], "idle": [1, 2]}\n', "unreadable"),
            (
                '{"cpus": [8], "idle": [1], "ts": [1], "lane": [null],'
                ' "lane_at": [null], "pid": null}\n',
                "not two samples",
            ),
            (_script_output().replace('"cpus":[8,8]', '"cpus":[8,12]'), "inconsistent"),
            (_script_output(seconds=0.0), "inconsistent"),
            # Idle running backwards would read more cores busy than exist.
            (_script_output(busy=9.0), "inconsistent"),
            # No processor instances: a ZeroDivisionError would escape the contract.
            (_script_output(cpus=0, busy=0.0), "inconsistent"),
        ],
    )
    def test_garbage_is_an_error_naming_why(self, stdout, why):
        with pytest.raises(winhost.InteropError, match=why):
            winhost.parse(stdout, 18181)


class TestMeasure:
    """One encoded Windows PowerShell call, bounded, every failure named."""

    def test_runs_windows_powershell_with_the_script_encoded(self, monkeypatch):
        seen = []
        monkeypatch.setattr(winhost, "find_powershell", lambda: PS)
        monkeypatch.setattr(
            winhost,
            "_run",
            lambda a, t: seen.append((a, t)) or (0, _script_output(), ""),
        )
        winhost.measure(18181, 3)
        [(argv, timeout)] = seen
        assert argv[0] == PS and argv[-2] == "-EncodedCommand"
        # A prompt would otherwise wait out the timeout, and a profile runs the
        # user's code and can print before the reading.
        assert "-NonInteractive" in argv and "-NoProfile" in argv
        sent = base64.b64decode(argv[-1]).decode("utf-16-le")
        assert sent == winhost.script(18181, 3)
        assert timeout == 3 + winhost.OVERHEAD_S

    @pytest.mark.parametrize(
        ("run", "why"),
        [
            (subprocess.TimeoutExpired(PS, 23), "no reading within 23 s"),
            (OSError(8, "Exec format error"), "did not start: OSError"),
            (
                (1, "", "Get-CimInstance : Zugriff verweigert\nmore\n"),
                "exited 1: Get-CimInstance : Zugriff verweigert",
            ),
        ],
    )
    def test_every_failure_is_an_interop_error(self, monkeypatch, run, why):
        def answer(argv, timeout):
            if isinstance(run, BaseException):
                raise run
            return run

        monkeypatch.setattr(winhost, "find_powershell", lambda: PS)
        monkeypatch.setattr(winhost, "_run", answer)
        with pytest.raises(winhost.InteropError, match=why):
            winhost.measure(18181, 3)

    def test_no_powershell_is_an_interop_error(self, monkeypatch):
        monkeypatch.setattr(winhost, "find_powershell", lambda: None)
        with pytest.raises(winhost.InteropError, match=r"powershell\.exe not found"):
            winhost.measure(18181, 3)

    def test_a_clixml_error_stream_is_read_as_its_text(self, monkeypatch):
        monkeypatch.setattr(winhost, "find_powershell", lambda: PS)
        monkeypatch.setattr(winhost, "_run", lambda a, t: (1, "", _CLIXML_DENIED))
        with pytest.raises(winhost.InteropError) as caught:
            winhost.measure(18181, 3)
        assert str(caught.value) == (
            "powershell.exe exited 1: Get-CimInstance : Zugriff verweigert"
        )


class TestErrorText:
    """stderr as the text PowerShell meant, whether it came as CLIXML or not."""

    def test_plain_text_is_kept(self):
        assert winhost.error_text("Zugriff verweigert\n") == "Zugriff verweigert\n"

    def test_xml_entities_and_escaped_characters_are_decoded(self):
        err = (
            '#< CLIXML\r\n<Objs Version="1.1.0.1"><S S="Error">'
            "Die Benennung &quot;Get-NetTCPConnection&quot; wurde nicht "
            'erkannt._x000D__x000A_</S><S S="Error">a_x005F_x0041_b</S></Objs>'
        )
        assert winhost.error_text(err) == (
            'Die Benennung "Get-NetTCPConnection" wurde nicht erkannt.\r\na_x0041_b'
        )

    def test_clixml_without_an_error_record_is_empty(self):
        # Progress alone: the caller falls back to stdout, then "no output".
        progress_only = _CLIXML_DENIED.partition('<S S="Error">')[0] + "</Objs>"
        assert winhost.error_text(progress_only) == ""


class TestLoadSnapshotThroughInterop:
    """From WSL2, a Windows lane's run start records the Windows host's load."""

    def test_other_cores_is_the_windows_host_net_of_the_lane(self, monkeypatch):
        reading = _script_output(busy=2.0, lane_s=1.5)
        snap, wsl = _snapshot(monkeypatch, _answers(reading))
        assert snap == {
            "busy_cores": 2.0,
            "lane_cores": 0.5,
            "other_cores": 1.5,
            "seconds": 3.0,
            "cpus": 8,
            "note": None,
            "via": "wsl-interop",
        }
        assert len(wsl.calls) == 1

    def test_one_scale_with_a_local_reading(self, monkeypatch):
        # The same window read locally (4 of 8 busy, the lane 1.0) and through
        # interop gives the same numbers: same rounding, same subtraction.
        snap, _ = _snapshot(monkeypatch, _answers(_script_output(busy=4.0, lane_s=3.0)))

        class Local:
            available, reason = True, None

            def __init__(self):
                self.seconds = iter([10.0, 13.0])

            def cpu_seconds(self):
                return next(self.seconds)

        monkeypatch.setattr(hostload, "_psutil", _VmPsutil)
        monkeypatch.setattr(hostload, "time", _Clock())
        local = _REAL_LOAD_SNAPSHOT(3, lane=Local())
        keys = ("busy_cores", "lane_cores", "other_cores", "seconds", "cpus", "note")
        assert {k: snap[k] for k in keys} == {k: local[k] for k in keys}
        assert local["via"] == "local"

    def test_a_lane_windows_does_not_see_either_is_unknown_not_zero(self, monkeypatch):
        # Counting it as 0 would book the lane's own load, wherever it runs,
        # as other load: the rule for a lane that restarted, applied here too.
        snap, _ = _snapshot(monkeypatch, _answers(_script_output(pid=None)))
        assert snap["busy_cores"] == 4.0 and snap["via"] == "wsl-interop"
        assert snap["lane_cores"] is None and snap["other_cores"] is None
        assert "port 18181 on the Windows host either" in snap["note"]

    def test_the_lane_never_counts_below_zero_other_load(self, monkeypatch):
        # Its span brackets the counters', so a lane pinning the host can read
        # above busy_cores; the rest is 0, as on the local path.
        reading = _script_output(busy=2.0, lane_s=7.5)  # 2.5 lane cores
        snap, _ = _snapshot(monkeypatch, _answers(reading))
        assert snap["lane_cores"] == 2.5 and snap["other_cores"] == 0.0

    def test_a_lane_that_restarted_in_the_window_is_unknown(self, monkeypatch):
        snap, _ = _snapshot(monkeypatch, _answers(_script_output(gone=True)))
        assert snap["lane_cores"] is None and snap["other_cores"] is None
        assert "could not be read" in snap["note"]

    @pytest.mark.parametrize(
        ("run_and_clock", "why"),
        [
            (_answers("Das System kann die Datei nicht finden.\n"), "no reading"),
            (_answers("", code=1, stderr="Zugriff verweigert\n"), "exited 1"),
            (
                _answers("", code=1, stderr=_CLIXML_DENIED),
                "exited 1: Get-CimInstance : Zugriff verweigert",
            ),
            (_raises(subprocess.TimeoutExpired(PS, 23)), "no reading within"),
            (_raises(OSError(8, "Exec format error")), "did not start"),
            (_raises(KeyError("surprise")), "KeyError"),
        ],
    )
    def test_a_failed_reading_keeps_the_old_null_and_says_why(
        self, monkeypatch, run_and_clock, why
    ):
        snap, _ = _snapshot(monkeypatch, run_and_clock)
        assert snap["other_cores"] is None and snap["via"] == "local"
        assert snap["busy_cores"] == 4.0  # the VM's own reading, as before
        assert snap["note"].startswith("the lane's host is not visible from here")
        assert "through WSL interop" in snap["note"] and why in snap["note"]

    def test_without_powershell_the_old_null_names_it(self, monkeypatch):
        run_and_clock = _answers(_script_output())
        run, clock = run_and_clock
        _Wsl(monkeypatch, run, clock)
        monkeypatch.setattr(winhost, "find_powershell", lambda: None)
        snap = _REAL_LOAD_SNAPSHOT(3, lane=_WindowsLane())
        assert snap["other_cores"] is None
        assert "powershell.exe not found" in snap["note"]

    def test_a_failure_does_not_add_a_second_window(self, monkeypatch):
        # A timeout already spent the window; the fallback must not sleep again.
        timed_out = _raises(subprocess.TimeoutExpired(PS, 23), spend=3.5)
        snap, _ = _snapshot(monkeypatch, timed_out)
        assert snap["seconds"] == 3.5

    def test_an_early_failure_still_gives_the_fallback_its_window(self, monkeypatch):
        snap, _ = _snapshot(monkeypatch, _raises(OSError(8, "Exec format error")))
        assert snap["seconds"] == 3.0


class TestInteropIsNotTried:
    """Only a WSL2 harness facing a loopback lane nothing here listens on."""

    def _calls(self, monkeypatch, lane, wsl=True):
        run, clock = _answers(_script_output())
        seen = _Wsl(monkeypatch, run, clock)
        monkeypatch.setattr(winhost, "in_wsl", lambda: wsl)
        snap = _REAL_LOAD_SNAPSHOT(3, lane=lane)
        return snap, seen.calls

    def test_not_outside_wsl(self, monkeypatch):
        snap, calls = self._calls(monkeypatch, _WindowsLane(), wsl=False)
        assert calls == [] and snap["other_cores"] is None
        before = f"the lane's host is not visible from here: {_WindowsLane.reason}"
        assert snap["note"] == before  # word for word what it recorded until now

    def test_not_for_a_listener_here_whose_pid_is_hidden(self, monkeypatch):
        # Another user's server inside WSL: the Windows port is someone else's.
        lane = _WindowsLane()
        lane.listens_here = True
        _snap, calls = self._calls(monkeypatch, lane)
        assert calls == []

    def test_not_when_the_lookup_here_failed(self, monkeypatch):
        # Unknown is not "nothing here": the lane may still be inside WSL.
        lane = _WindowsLane()
        lane.listens_here, lane.reason = None, "listener lookup failed: AccessDenied"
        snap, calls = self._calls(monkeypatch, lane)
        assert calls == [] and "through WSL interop" not in snap["note"]

    def test_not_for_a_remote_lane(self, monkeypatch):
        lane = _WindowsLane()
        lane.base_url, lane.listens_here = "http://summy-server:11434", None
        _snap, calls = self._calls(monkeypatch, lane)
        assert calls == []

    def test_not_without_a_lane(self, monkeypatch):
        snap, calls = self._calls(monkeypatch, None)
        assert calls == [] and snap["other_cores"] == 4.0

    def test_not_for_a_lane_visible_here(self, monkeypatch):
        class Local:
            available, reason = True, None

            def cpu_seconds(self):
                return 1.0

        snap, calls = self._calls(monkeypatch, Local())
        assert calls == [] and snap["via"] == "local"


class TestLoadLine:
    """The console says which host a reading describes."""

    def test_a_windows_reading_is_named(self):
        snap = {"other_cores": 0.5, "lane_cores": 0.1, "seconds": 3.0}
        line = hostload.load_line({**snap, "via": "wsl-interop"})
        assert "0.50 other cores on the Windows host over the 3 s" in line

    def test_an_unknown_windows_reading_says_where_it_looked(self):
        line = hostload.load_line(
            {"busy_cores": 2.0, "other_cores": None, "via": "wsl-interop", "note": "x"}
        )
        assert "2.00 busy cores on the Windows host; other load unknown" in line

    def test_a_local_reading_reads_as_before(self):
        line = hostload.load_line({"busy_cores": 2.0, "other_cores": None, "note": "x"})
        assert "2.00 busy cores here;" in line
