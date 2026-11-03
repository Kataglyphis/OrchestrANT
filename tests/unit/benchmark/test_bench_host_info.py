"""Host identity survives what a host lacks; narrowed handlers let anything unexpected surface."""

from __future__ import annotations

import builtins
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from orchestrant.benchmark import host_info


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


class FakePsutilError(Exception):
    """Stands in for psutil.Error, so these tests hold where psutil is not installed."""


class FakeProcess:
    """A process whose CPU read either answers or raises."""

    def __init__(self, pid: int, cpu: float | Exception, name: str = "worker") -> None:
        self.pid = pid
        self.info = {"name": name}
        self._cpu = cpu

    def cpu_percent(self, _interval: None) -> float:
        """Return the scripted figure or raise the scripted error."""
        if isinstance(self._cpu, Exception):
            raise self._cpu
        return self._cpu


def _fake_psutil(iterate: Iterator[FakeProcess] | Exception) -> SimpleNamespace:
    """A psutil module whose process_iter yields from `iterate` or raises it."""

    def process_iter(_attrs: list[str]) -> Iterator[FakeProcess]:
        if isinstance(iterate, Exception):
            raise iterate
        return iterate

    return SimpleNamespace(Error=FakePsutilError, process_iter=process_iter)


def _fail_proc_reads(
    monkeypatch: pytest.MonkeyPatch, error: Exception, only: str | None = None
) -> None:
    """Make open() of `only`, else of every /proc path, raise `error`; other /proc paths are missing."""
    real_open = cast("Callable[..., object]", builtins.open)

    def fake_open(path: object, *args: object, **kwargs: object) -> object:
        if only is None or path == only:
            if str(path).startswith("/proc/"):
                raise error
        elif str(path).startswith("/proc/"):
            raise FileNotFoundError(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)


def _raise_bug() -> str:
    """Stand in for a call that fails in a way no handler should tolerate."""
    raise RuntimeError("bug")


def _break_psutil_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `import psutil` raise something other than ImportError."""
    real_import = cast("Callable[..., object]", builtins.__import__)

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "psutil":
            raise RuntimeError("bug")
        return real_import(name, *args, **kwargs)

    # setitem first: an imported psutil must return afterwards, and delitem refuses an absent key.
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.delitem(sys.modules, "psutil")
    monkeypatch.setattr(builtins, "__import__", fake_import)


def _no_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the GPU probe out of a test about the host."""
    monkeypatch.setattr(host_info, "collect_gpu_info", dict)


class TestMissingProc:
    """A host without /proc still yields a report that names its gaps."""

    def test_missing_proc_files_leave_a_usable_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError from every /proc read keeps the platform identity and names the gaps."""
        _fail_proc_reads(monkeypatch, FileNotFoundError("/proc"))
        _no_gpu(monkeypatch)
        monkeypatch.setitem(sys.modules, "psutil", None)

        info = host_info.collect_hardware_info()

        assert info["os"]
        assert info["in_container"] is None
        assert "cpu_model" not in info
        assert info["incomplete"] == ["cpu_total_threads", "ram_total_gb"]

    def test_malformed_meminfo_is_a_gap(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A MemTotal line without a figure raises IndexError inside the probe, not out of it."""
        truncated = tmp_path / "meminfo"
        truncated.write_text("MemTotal:\n")
        real_open = cast("Callable[..., object]", builtins.open)

        def fake_open(path: object, *args: object, **kwargs: object) -> object:
            if path == "/proc/meminfo":
                return real_open(truncated, *args, **kwargs)
            if str(path).startswith("/proc/"):
                raise FileNotFoundError(path)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", fake_open)
        _no_gpu(monkeypatch)
        monkeypatch.setitem(sys.modules, "psutil", None)

        info = host_info.collect_hardware_info()

        assert "ram_total_kb" not in info
        assert "ram_total_gb" in info["incomplete"]

    @pytest.mark.parametrize(
        "path", ["/proc/cpuinfo", "/proc/meminfo", "/proc/1/cgroup"]
    )
    def test_an_unexpected_error_surfaces(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """A programming error is not a missing file: it must not read as an absent figure."""
        _fail_proc_reads(monkeypatch, RuntimeError("bug"), only=path)
        _no_gpu(monkeypatch)
        # psutil reads /proc through the same open(), which would raise for the probe under test.
        monkeypatch.setitem(sys.modules, "psutil", None)

        with pytest.raises(RuntimeError, match="bug"):
            host_info.collect_hardware_info()


class TestPlatformAndPsutil:
    """The fallbacks tolerate their own failures and nothing else."""

    def test_a_failing_platform_call_is_a_gap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError from the platform module leaves the identity listed as incomplete."""
        _fail_proc_reads(monkeypatch, FileNotFoundError("/proc"))
        _no_gpu(monkeypatch)
        monkeypatch.setitem(sys.modules, "psutil", None)

        def no_system() -> str:
            raise OSError("uname")

        monkeypatch.setattr(host_info.platform, "system", no_system)

        assert "os" in host_info.collect_hardware_info()["incomplete"]

    def test_an_unexpected_error_in_os_info_surfaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`version` is read only by the first probe, so nothing later can raise in its place."""
        monkeypatch.setattr(host_info.platform, "version", _raise_bug)

        with pytest.raises(RuntimeError, match="bug"):
            host_info.collect_hardware_info()

    def test_an_unexpected_error_in_the_fallback_surfaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The first probe tolerates its OSError, so the second call's bug reaches the fallback."""
        errors = iter([OSError("uname"), RuntimeError("bug")])

        def machine() -> str:
            raise next(errors)

        monkeypatch.setattr(host_info.platform, "machine", machine)

        with pytest.raises(RuntimeError, match="bug"):
            host_info.collect_hardware_info()

    def test_a_broken_psutil_import_surfaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Absent is ImportError; a package that fails otherwise on import is a real fault."""
        _no_gpu(monkeypatch)
        _break_psutil_import(monkeypatch)

        with pytest.raises(RuntimeError, match="bug"):
            host_info.collect_hardware_info()

    def _psutil_whose_cpu_count_raises(self, error: Exception) -> SimpleNamespace:
        def cpu_count(**_settings: bool) -> int:
            raise error

        return SimpleNamespace(Error=FakePsutilError, cpu_count=cpu_count)

    @pytest.mark.parametrize("error", [FakePsutilError("psutil"), OSError("proc")])
    def test_a_failing_psutil_is_a_gap(
        self, monkeypatch: pytest.MonkeyPatch, error: Exception
    ) -> None:
        """Errors of psutil itself and OSError leave the counts listed as incomplete."""
        _fail_proc_reads(monkeypatch, FileNotFoundError("/proc"))
        _no_gpu(monkeypatch)
        fake = self._psutil_whose_cpu_count_raises(error)
        monkeypatch.setitem(sys.modules, "psutil", fake)

        assert "cpu_total_threads" in host_info.collect_hardware_info()["incomplete"]

    def test_an_unexpected_psutil_error_surfaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A programming error inside the fallback is not a missing figure."""
        _fail_proc_reads(monkeypatch, FileNotFoundError("/proc"))
        _no_gpu(monkeypatch)
        fake = self._psutil_whose_cpu_count_raises(RuntimeError("bug"))
        monkeypatch.setitem(sys.modules, "psutil", fake)

        with pytest.raises(RuntimeError, match="bug"):
            host_info.collect_hardware_info()


class TestTopCpuProcesses:
    """The per-request ranking stays a list whatever the host offers."""

    def test_without_psutil_it_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failed import means no ranking, not a failed request."""
        monkeypatch.setitem(sys.modules, "psutil", None)

        assert host_info.top_cpu_processes() == []

    def test_an_unlistable_proc_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """process_iter lists /proc, so a missing tree raises OSError before any process."""
        fake = _fake_psutil(FileNotFoundError("/proc"))
        monkeypatch.setitem(sys.modules, "psutil", fake)

        assert host_info.top_cpu_processes() == []

    def test_an_unexpected_listing_error_surfaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only a missing /proc or a psutil error means there is nothing to rank."""
        fake = _fake_psutil(RuntimeError("bug"))
        monkeypatch.setitem(sys.modules, "psutil", fake)

        with pytest.raises(RuntimeError, match="bug"):
            host_info.top_cpu_processes()

    def test_a_broken_psutil_import_surfaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Absent is ImportError; a package that fails otherwise on import is a real fault."""
        _break_psutil_import(monkeypatch)

        with pytest.raises(RuntimeError, match="bug"):
            host_info.top_cpu_processes()

    def test_a_process_ending_mid_read_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One vanished process costs its own row, and the idle pid 0 never ranks."""
        running = [
            FakeProcess(0, 700.0, "System Idle Process"),
            FakeProcess(1, FakePsutilError("gone")),
            FakeProcess(2, 5.0),
            FakeProcess(3, 50.0),
        ]
        monkeypatch.setitem(sys.modules, "psutil", _fake_psutil(iter(running)))

        rows = host_info.top_cpu_processes(limit=1)

        assert rows == [{"pid": 3, "name": "worker", "cpu_percent": 50.0}]

    def test_an_unexpected_process_error_surfaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only psutil's own errors mean a process went away."""
        running = iter([FakeProcess(1, RuntimeError("bug"))])
        monkeypatch.setitem(sys.modules, "psutil", _fake_psutil(running))

        with pytest.raises(RuntimeError, match="bug"):
            host_info.top_cpu_processes()
