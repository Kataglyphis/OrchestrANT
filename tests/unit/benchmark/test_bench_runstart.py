"""Tests for the run-start record: the start hash, the host load, and OPS-9.

The GenieX CPU lane decoded about 30 tok/s on a quiet machine and 14 beside
0.93 cores of other load, and until the start record no report said which
of the two it was taken under. These pin the load arithmetic, the record
each tool takes, the notes compare() writes from it, and what each tool
now puts in its fingerprint.
"""

import json
import pathlib
import sys
from datetime import datetime
from typing import NamedTuple

import pytest

from orchestrant.benchmark import client, determinism, hostload, provenance
from orchestrant.benchmark.provenance import collect, compare, tool_fingerprint


# The real functions, captured at import: the suite's conftest replaces the
# module attribute with a recorder for every test.
_REAL_LOAD_SNAPSHOT = hostload.load_snapshot


class CpuTimes(NamedTuple):
    """psutil's scputimes, reduced to the fields Window reads."""

    user: float
    system: float
    idle: float


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # write_report() runs collect(), which asks every registry endpoint and
    # the lane itself; a unit test must do neither (DNS alone took 90 s).
    monkeypatch.setattr(provenance, "busy_lanes", lambda *a, **k: [])
    monkeypatch.setattr(provenance, "_server_models", lambda *a, **k: None)
    monkeypatch.setattr(provenance, "runtime_info", lambda *a, **k: None)


class _Clock:
    """Stands in for hostload's `time`: sleep() advances monotonic() exactly."""

    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def time(self):
        return self.now


class _Psutil:
    """cpu_times() before and after the window; 8 cores unless told otherwise."""

    def __init__(self, before, after, cpus=8):
        self._samples = iter([before, after])
        self._cpus = cpus

    def cpu_times(self):
        return next(self._samples)

    def cpu_count(self):
        return self._cpus


class _Lane:
    available = True
    reason = None

    def __init__(self, *seconds):
        self._seconds = iter(seconds)

    def cpu_seconds(self):
        return next(self._seconds)


def _half_busy(monkeypatch):
    """A window in which 4 of 8 cores were busy (12 busy of 24 CPU-seconds)."""
    ps = _Psutil(CpuTimes(0.0, 0.0, 0.0), CpuTimes(10.0, 2.0, 12.0))
    monkeypatch.setattr(hostload, "_psutil", lambda: ps)
    monkeypatch.setattr(hostload, "time", _Clock())


class TestLoadSnapshot:
    def test_other_cores_is_busy_minus_the_lane(self, monkeypatch):
        _half_busy(monkeypatch)
        snap = _REAL_LOAD_SNAPSHOT(3, lane=_Lane(10.0, 13.0))
        assert snap["busy_cores"] == 4.0
        assert snap["lane_cores"] == 1.0  # 3 CPU-seconds over a 3 s window
        assert snap["other_cores"] == 3.0
        assert snap["seconds"] == 3.0 and snap["cpus"] == 8
        assert snap["note"] is None

    def test_without_a_lane_every_busy_core_is_other(self, monkeypatch):
        _half_busy(monkeypatch)
        snap = _REAL_LOAD_SNAPSHOT(3)
        assert snap["other_cores"] == snap["busy_cores"] == 4.0
        assert snap["lane_cores"] is None and "no lane named" in snap["note"]

    def test_a_lane_on_another_host_leaves_other_load_unknown(self, monkeypatch):
        # A remote lane shares no cores with this host, and from WSL2 these
        # counters are the VM's: either way they are not the lane's host.
        _half_busy(monkeypatch)
        snap = _REAL_LOAD_SNAPSHOT(3, lane="http://summy-server:11434")
        assert snap["busy_cores"] == 4.0
        assert snap["other_cores"] is None
        assert "not on this host" in snap["note"]

    def test_a_lane_that_restarted_is_unknown_not_zero(self, monkeypatch):
        _half_busy(monkeypatch)
        snap = _REAL_LOAD_SNAPSHOT(3, lane=_Lane(10.0, None))
        assert snap["lane_cores"] is None and snap["other_cores"] is None
        assert "could not be read" in snap["note"]

    def test_the_lane_never_counts_below_zero_other_load(self, monkeypatch):
        _half_busy(monkeypatch)
        snap = _REAL_LOAD_SNAPSHOT(3, lane=_Lane(0.0, 15.0))  # 5 lane cores > 4 busy
        assert snap["other_cores"] == 0.0

    def test_without_psutil_nothing_is_invented(self, monkeypatch):
        monkeypatch.setattr(hostload, "_psutil", lambda: None)
        monkeypatch.setattr(hostload, "time", _Clock())
        snap = _REAL_LOAD_SNAPSHOT(3, lane="http://127.0.0.1:18181")
        assert snap["busy_cores"] is None and snap["other_cores"] is None
        assert snap["note"] == "psutil not installed"


class TestLoadLine:
    def test_a_busy_host_is_warned_about(self):
        line = hostload.load_line(
            {"other_cores": 1.4, "lane_cores": 0.1, "seconds": 3.0}
        )
        assert "1.40 other cores" in line and "the lane itself: 0.10" in line
        assert "WARNING" in line

    def test_a_quiet_host_is_not(self):
        line = hostload.load_line({"other_cores": 0.2, "seconds": 3.0})
        assert "0.20 other cores" in line and "WARNING" not in line

    def test_an_unknown_load_says_why(self):
        line = hostload.load_line(
            {"busy_cores": 2.0, "other_cores": None, "note": "lane not visible"}
        )
        assert "unknown" in line and "lane not visible" in line
        assert "2.00 busy cores" in line


class TestRunStart:
    def test_records_the_time_the_hash_and_the_load(self, host_load, capsys):
        rec = client.run_start(("stats.py",), "http://127.0.0.1:18181")
        assert rec["tool_files"] == ["stats.py"]
        assert rec["tool_sha256"] == tool_fingerprint("stats.py")
        assert host_load == [{"seconds": 3, "lane": "http://127.0.0.1:18181"}]
        assert rec["host_load"]["other_cores"] == 0.2
        assert datetime.fromisoformat(rec["started_utc"]).tzinfo is not None
        assert "Host load:" in capsys.readouterr().out

    def test_a_failed_reading_never_costs_the_run(self, monkeypatch):
        def broken(seconds=3, lane=None):
            raise RuntimeError("counters gone")

        monkeypatch.setattr(hostload, "load_snapshot", broken)
        rec = client.run_start(("stats.py",))
        assert rec["host_load"]["other_cores"] is None
        assert "RuntimeError" in rec["host_load"]["note"]


class TestWriteReportCarriesTheStart:
    def _write(self, tmp_path, tool_files, start):
        out = tmp_path / "r.json"
        client.write_report(str(out), "b", {}, [], None, tool_files, run_start=start)
        return json.loads(out.read_text())["provenance"]

    def test_load_start_time_and_an_unchanged_source(self, tmp_path):
        start = client.run_start(("stats.py",))
        prov = self._write(tmp_path, ("stats.py",), start)
        assert prov["host_load"] == start["host_load"]
        assert prov["run_started_utc"] == start["started_utc"]
        assert prov["source_changed_during_run"] is False
        assert prov["tool_files"] == ["stats.py"]

    def test_a_mid_run_edit_is_caught(self, tmp_path):
        start = dict(client.run_start(("stats.py",)), tool_sha256="0" * 16)
        prov = self._write(tmp_path, ("stats.py",), start)
        assert prov["source_changed_during_run"] is True
        assert prov["tool_sha256_at_start"] == "0" * 16

    def test_another_file_set_is_not_read_as_an_edit(self, tmp_path):
        # Hashes of different sets always differ; comparing them would report
        # an edit that never happened.
        start = client.run_start(("stats.py",))
        prov = self._write(tmp_path, ("report.py",), start)
        assert "source_changed_during_run" not in prov
        assert "tool_sha256_at_start" in prov["incomplete"]

    def test_without_a_start_the_fields_are_explicit_nulls(self, tmp_path):
        prov = self._write(tmp_path, ("stats.py",), None)
        assert prov["host_load"] is None and prov["run_started_utc"] is None
        assert "source_changed_during_run" not in prov


def _loaded(other):
    return {"host_load": {"other_cores": other}}


class TestLoadNotes:
    def test_runs_under_different_load_are_named(self):
        notes = compare(_loaded(0.13), _loaded(0.86))
        assert len(notes) == 1 and "different load" in notes[0]
        assert "0.13 vs 0.86" in notes[0]

    def test_the_spread_of_two_quiet_runs_is_not(self):
        assert compare(_loaded(0.13), _loaded(0.42)) == []

    def test_exactly_the_threshold_apart_is_not_more_than_it(self):
        # 0.80 - 0.50 is 0.30000000000000004 in floats; 0.43 - 0.13 is 0.3.
        assert compare(_loaded(0.5), _loaded(0.8)) == []
        assert compare(_loaded(0.13), _loaded(0.43)) == []
        assert compare(_loaded(0.5), _loaded(0.81))

    def test_a_busy_host_gets_the_stronger_note(self):
        notes = compare(_loaded(0.2), _loaded(1.3))
        assert len(notes) == 1 and notes[0].startswith("HOST WAS BUSY")
        assert "the new run" in notes[0]

    def test_both_busy_is_said_even_at_equal_load(self):
        notes = compare(_loaded(1.2), _loaded(1.25))
        assert notes and "old and the new run" in notes[0]

    def test_a_difference_needs_both_sides(self):
        # Every report older than the start record: nothing to difference.
        assert compare({}, _loaded(0.9)) == []
        assert compare(_loaded(None), _loaded(0.9)) == []

    def test_a_busy_run_is_named_against_a_baseline_without_the_record(self):
        # The first comparison after this record exists is against a baseline
        # that predates it; a busy new run must not hide behind that.
        notes = compare({}, _loaded(1.5))
        assert len(notes) == 1 and notes[0].startswith("HOST WAS BUSY")
        assert "the new run" in notes[0] and "unrecorded vs 1.50" in notes[0]
        notes = compare(_loaded(2.0), {"host_load": None})
        assert notes and "the old run" in notes[0]

    def test_a_malformed_record_is_ignored_not_a_crash(self):
        # bench_compare reads whatever JSON it is handed.
        assert compare({"host_load": "error"}, _loaded(0.2)) == []
        assert compare(_loaded("n/a"), _loaded(0.2)) == []
        assert compare(_loaded(True), _loaded(0.2)) == []

    def test_load_is_not_liveness(self):
        # Same lanes live, different load: only the load note fires.
        old = {**_loaded(0.1), "live_lanes": ["geniex-npu"]}
        new = {**_loaded(0.9), "live_lanes": ["geniex-npu"]}
        notes = compare(old, new)
        assert len(notes) == 1 and "different load" in notes[0]


class TestContractDiffIsNotGated:
    """bench_compare withholds a speed or timing verdict across unlike load;
    `contract --diff` prints the same notes and keeps its exit status. Its
    answers are behaviours, not rates, and the one timing it reads (the prefix
    cache) is a repeat against a cold request inside the same run.
    """

    def _diff(self, monkeypatch, tmp_path, old_answer, new_answer):
        from orchestrant.benchmark import contract

        paths = []
        for name, answer, cores in (("old", old_answer, 0.2), ("new", new_answer, 1.6)):
            checks = [{"id": "prefix_cache", "answer": answer}]
            report = {"provenance": _loaded(cores), "reports": [{"checks": checks}]}
            (tmp_path / f"{name}.json").write_text(json.dumps(report))
            paths.append(str(tmp_path / f"{name}.json"))
        monkeypatch.setattr(sys, "argv", ["contract", "--diff", *paths])
        return contract.main()

    def test_a_busy_host_is_named_and_nothing_refused(
        self, monkeypatch, tmp_path, capsys
    ):
        assert self._diff(monkeypatch, tmp_path, "yes", "yes") == 0
        assert "! HOST WAS BUSY when the new run started" in capsys.readouterr().out

    def test_a_moved_answer_still_exits_1(self, monkeypatch, tmp_path):
        assert self._diff(monkeypatch, tmp_path, "yes", "no") == 1


class TestFingerprintScope:
    """OPS-9: provenance.py is plumbing and left every tool's tool_sha256."""

    def test_the_probe_is_re_exported_unchanged(self):
        assert provenance.determinism_probe is determinism.determinism_probe
        assert provenance.PROBE_PROMPT == determinism.PROBE_PROMPT

    def test_the_probe_module_is_fingerprintable(self):
        assert tool_fingerprint("determinism.py")

    def test_the_hash_does_not_depend_on_how_a_path_is_spelled(self, tmp_path):
        # bench_tools mixes absolute paths with names resolved beside
        # provenance.py; sorting full strings ordered "C:\..." and "/mnt/..."
        # before "determinism.py" but "e:\..." after -- identical source,
        # two hashes, and a false BENCHMARK SOURCE CHANGED between hosts.
        here = tmp_path / "determinism.py"
        here.write_bytes(pathlib.Path(determinism.__file__).read_bytes())
        last = tmp_path / "zz_last.py"
        last.write_text("z = 1\n")
        spelled_absolute = tool_fingerprint(str(last), str(here))
        assert tool_fingerprint(str(last), "determinism.py") == spelled_absolute

    def test_a_changed_file_set_is_named_beside_the_source_note(self):
        old = {"tool_sha256": "a", "tool_files": ["bench_tools.py", "provenance.py"]}
        new = {"tool_sha256": "b", "tool_files": ["bench_tools.py", "determinism.py"]}
        notes = compare(old, new)
        assert notes[0].startswith("BENCHMARK SOURCE CHANGED")
        assert "covers different files" in notes[0]

    def test_collect_names_the_files_by_basename(self):
        p = collect(tool_files=("/abs/path/bench_x.py", "stats.py"))
        assert p["tool_files"] == ["bench_x.py", "stats.py"]


class TestToolMains:
    """Each orchestrant tool takes a start record and hands it to its report."""

    def test_lanes(self, monkeypatch, tmp_path, host_load):
        from orchestrant.benchmark import lanes

        run = {"lanes": {"x": {"decode_tok_per_sec": 1.0}}, "baseline": {}}
        run.update(aggregate_tok_per_sec=1.0, wall_s=1.0)
        monkeypatch.setattr(lanes, "run_lanes", lambda *a, **k: run)
        out = tmp_path / "lanes.json"
        argv = [
            "bench_lanes.py",
            "--lanes",
            "x=http://h:1,model=m",
            "--output",
            str(out),
        ]
        monkeypatch.setattr(sys, "argv", argv)
        lanes.main()
        prov = json.loads(out.read_text())["provenance"]
        assert prov["tool_files"] == ["answers.py", "lanes.py"]
        assert prov["source_changed_during_run"] is False
        assert prov["host_load"]["note"] == "stubbed by conftest"
        assert host_load == [{"seconds": 3, "lane": "http://h:1"}]

    def test_contract(self, monkeypatch, tmp_path, host_load):
        from orchestrant.benchmark import contract

        monkeypatch.setattr(contract, "run", lambda *a, **k: [])
        out = tmp_path / "contract.json"
        argv = ["contract", "--base-url", "http://lane:1", "--model", "m"]
        monkeypatch.setattr(sys, "argv", [*argv, "--output", str(out)])
        assert contract.main() == 0
        prov = json.loads(out.read_text())["provenance"]
        assert prov["tool_files"] == ["contract.py"]
        assert prov["source_changed_during_run"] is False
        assert host_load == [{"seconds": 3, "lane": "http://lane:1"}]

    def test_contract_diff_takes_no_start(self, monkeypatch, tmp_path, host_load):
        from orchestrant.benchmark import contract

        report = tmp_path / "c.json"
        report.write_text(json.dumps({"provenance": {}, "reports": [{"checks": []}]}))
        monkeypatch.setattr(
            sys, "argv", ["contract", "--diff", str(report), str(report)]
        )
        assert contract.main() == 0
        assert host_load == []
