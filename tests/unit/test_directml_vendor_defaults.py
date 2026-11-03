"""The packaged DirectML flavor must choose its own physical vendor by default."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from orchestrant.smoke import directml
from orchestrant.yolo.cli import default_gpu, parse_args


ROOT = Path(__file__).resolve().parents[2]

if TYPE_CHECKING:
    from collections.abc import Callable


def test_candidate_builder_binds_vendor_marker_to_all_formats() -> None:
    """The same vendor marker is selected and checked in finished packages."""
    builder = (ROOT / "scripts/windows/Build-DirectML.ps1").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts/windows/Test-DirectMLRuntime.ps1").read_text(
        encoding="utf-8"
    )
    assert "packaging/directml-vendor-$Vendor.txt" in builder
    assert "share/orchestrant/directml-vendor.txt" in builder
    assert "share/orchestrant/directml-vendor.txt" in verifier
    for vendor in ("amd", "nvidia"):
        assert (ROOT / f"packaging/directml-vendor-{vendor}.txt").read_text(
            encoding="utf-8"
        ).strip() == vendor


@pytest.mark.parametrize(("vendor", "index"), [("amd", 3), ("nvidia", 2)])
def test_packaged_vendor_sets_default_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, vendor: str, index: int
) -> None:
    """A GPU flavor defaults to strict DirectML on its matching adapter."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("ORCHESTRANT_DATA_DIR", str(tmp_path))
    (tmp_path / "directml-vendor.txt").write_text(vendor + "\n", encoding="utf-8")
    seen = []
    monkeypatch.setattr(
        directml,
        "physical_adapter",
        lambda value: (seen.append(value) or index, vendor),
    )
    args = parse_args([])
    assert (args.device, args.gpu, seen) == ("directml", index, [vendor])
    explicit_auto = parse_args(["--device", "auto"])
    assert (explicit_auto.device, explicit_auto.gpu, seen) == (
        "auto",
        0,
        [vendor],
    )


@pytest.mark.parametrize(("vendor", "index"), [("amd", 3), ("nvidia", 2)])
def test_diagnostic_default_is_the_monitors_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, vendor: str, index: int
) -> None:
    """The parity diagnostic asks the same owner, and only for DirectML."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("ORCHESTRANT_DATA_DIR", str(tmp_path))
    (tmp_path / "directml-vendor.txt").write_text(vendor + "\n", encoding="utf-8")
    seen = []
    monkeypatch.setattr(
        directml,
        "physical_adapter",
        lambda value: (seen.append(value) or index, vendor),
    )
    assert default_gpu("directml") == index
    assert default_gpu("cuda") == 0
    assert seen == [vendor]


def test_explicit_index_and_cpu_do_not_enumerate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """User-provided index and CPU choices remain authoritative."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("ORCHESTRANT_DATA_DIR", str(tmp_path))
    (tmp_path / "directml-vendor.txt").write_text("nvidia\n", encoding="utf-8")

    def unexpected(_vendor: str) -> tuple[int, str]:
        message = "explicit user choice must not enumerate adapters"
        raise AssertionError(message)

    monkeypatch.setattr(directml, "physical_adapter", unexpected)
    explicit_index = parse_args(["--gpu", "7"])
    assert (explicit_index.device, explicit_index.gpu) == ("directml", 7)
    explicit_auto = parse_args(["--device", "auto", "--gpu", "5"])
    assert (explicit_auto.device, explicit_auto.gpu) == ("auto", 5)
    explicit_auto_default = parse_args(["--device", "auto"])
    assert (explicit_auto_default.device, explicit_auto_default.gpu) == ("auto", 0)
    assert (
        parse_args(["--device", "cpu"]).device,
        parse_args(["--device", "cpu"]).gpu,
    ) == ("cpu", 0)


def test_unavailable_or_invalid_vendor_fails_clearly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Absent hardware and invalid package metadata fail with a CPU escape hatch."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("ORCHESTRANT_DATA_DIR", str(tmp_path))
    marker = tmp_path / "directml-vendor.txt"
    marker.write_text("nvidia\n", encoding="utf-8")

    def unavailable(_vendor: str) -> tuple[int, str]:
        message = "No matching physical nvidia DXGI adapter was found"
        raise RuntimeError(message)

    monkeypatch.setattr(directml, "physical_adapter", unavailable)
    with pytest.raises(SystemExit):
        parse_args([])
    assert "use --device cpu" in capsys.readouterr().err
    marker.write_text("intel\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        parse_args([])
    assert "Invalid DirectML package vendor marker" in capsys.readouterr().err


def test_generic_and_linux_defaults_remain_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-vendor and Linux installs retain the existing automatic default."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("ORCHESTRANT_DATA_DIR", str(tmp_path))
    assert parse_args([]).gpu == 0
    (tmp_path / "directml-vendor.txt").write_text("nvidia\n", encoding="utf-8")
    monkeypatch.setattr(sys, "platform", "linux")
    assert parse_args([]).gpu == 0


def test_multi_adapter_selection_filters_vendor_and_prefers_dedicated() -> None:
    """Software and other vendors cannot override the requested physical GPU."""
    adapters = [
        (0, "AMD integrated", 0x1002, 0, 512),
        (1, "NVIDIA software", 0x10DE, 2, 65536),
        (2, "NVIDIA discrete", 0x10DE, 0, 8192),
        (3, "AMD discrete", 0x1002, 0, 16384),
        (4, "AMD same memory", 0x1002, 0, 16384),
    ]

    def matches(vendor: str) -> list[tuple[int, str, int]]:
        return [
            (i, name, vram)
            for i, name, vid, flags, vram in adapters
            if directml.matches_adapter(vid, flags, vendor)
        ]

    assert directml.preferred_adapter(matches("nvidia")) == (2, "NVIDIA discrete")
    assert directml.preferred_adapter(matches("amd")) == (3, "AMD discrete")


def _enum_status(position: int, count: int, state: dict[str, int | None]) -> int | None:
    if position == state["failure"]:
        return -2147467259
    if position == state["null"]:
        return 0
    if position >= count:
        return -2005270526
    return None


@pytest.fixture
def fake_dxgi(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple[str, int, int, int]], list[int], dict[str, int | None]]:
    """Replace COM calls with a controlled, observable adapter list."""
    adapters = [
        ("AMD integrated", 0x1002, 0, 512),
        ("NVIDIA software", 0x10DE, 2, 65536),
        ("NVIDIA discrete", 0x10DE, 0, 8192),
        ("AMD discrete", 0x1002, 0, 16384),
    ]
    released = []
    state: dict[str, int | None] = {"failure": None, "null": None}

    class FakeDxgi:
        def CreateDXGIFactory1(self, _iid: object, target: object) -> int:  # noqa: N802 -- mirror the DXGI API
            ctypes.cast(target, ctypes.POINTER(ctypes.c_void_p)).contents.value = 99
            return 0

    def method(
        obj: ctypes.c_void_p, index: int, _restype: object, *_args: object
    ) -> Callable[..., int]:
        if obj.value == 99 and index == 12:

            def enumerate_adapter(
                _factory: object, position: int, target: object
            ) -> int:
                status = _enum_status(position, len(adapters), state)
                if status is not None:
                    return status
                ctypes.cast(target, ctypes.POINTER(ctypes.c_void_p)).contents.value = (
                    1000 + position
                )
                return 0

            return enumerate_adapter
        if index == 10:

            def describe(_adapter: object, target: object) -> int:
                name, vendor, flags, vram = adapters[obj.value - 1000]
                desc = ctypes.cast(
                    target, ctypes.POINTER(directml.AdapterDescription)
                ).contents
                desc.description, desc.vendor, desc.flags, desc.vram = (
                    name,
                    vendor,
                    flags,
                    vram,
                )
                return 0

            return describe
        if index == 2:
            return lambda _obj: released.append(obj.value) or 0
        raise AssertionError((obj.value, index))

    def load_dxgi(name: str, *, winmode: int) -> FakeDxgi:
        assert name == "dxgi.dll"
        assert winmode == 0x00000800
        return FakeDxgi()

    monkeypatch.setattr(directml.ctypes, "WinDLL", load_dxgi, raising=False)
    monkeypatch.setattr(directml, "_method", method)
    return adapters, released, state


def test_dxgi_enumeration_returns_vendor_discrete_index(
    fake_dxgi: tuple[list[tuple[str, int, int, int]], list[int], dict[str, int | None]],
) -> None:
    """The real enumeration path chooses by vendor and releases all handles."""
    _adapters, released, _state = fake_dxgi
    assert directml.physical_adapter("nvidia") == (2, "NVIDIA discrete")
    assert set(released) == {99, 1000, 1001, 1002, 1003}
    assert directml.physical_adapter("amd") == (3, "AMD discrete")


def test_dxgi_errors_do_not_accept_an_incomplete_list(
    fake_dxgi: tuple[list[tuple[str, int, int, int]], list[int], dict[str, int | None]],
) -> None:
    """An enumeration error, null entry or cap overflow cannot select a partial list."""
    adapters, released, state = fake_dxgi
    state["failure"] = 1
    with pytest.raises(OSError, match="EnumAdapters1 failed"):
        directml.physical_adapter("amd")
    state["failure"] = None
    state["null"] = 1
    with pytest.raises(RuntimeError, match="returned no adapter"):
        directml.physical_adapter("amd")
    state["null"] = None
    adapters *= 9
    with pytest.raises(RuntimeError, match="more than 32 adapters"):
        directml.physical_adapter("amd")
    assert 1032 in released
