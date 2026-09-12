"""Unit tests for the AMD GPU backends (ADL and amdgpu sysfs).

No AMD hardware, no driver and no Windows are needed: the sysfs backend is
pointed at a temporary directory shaped like ``/sys/class/drm`` and the ADL
backend at a fake API object exposing the same three methods as the ctypes
binding. The real ADL path is verified on hardware separately; these tests
pin the selection logic, the sensor mapping and the fallbacks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from orchestrant.monitoring import gpu as gpu_module, gpu_amd
from orchestrant.monitoring.gpu_amd import (
    _AdlAdapter,
    _AdlBackend,
    _SysfsBackend,
)
from orchestrant.monitoring.gpu_types import GPUSnapshot


if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _make_card(
    root: Path,
    card: str,
    *,
    vendor: str = "0x1002",
    vram_total: int = 8 << 30,
    vram_used: int = 1 << 30,
    busy: int = 42,
    temp_milli: int = 45000,
    power_uw: int | None = 120_000_000,
) -> None:
    """Create one card-shaped directory tree below ``root``."""
    device = root / card / "device"
    _write(device / "vendor", vendor)
    _write(device / "gpu_busy_percent", str(busy))
    _write(device / "mem_info_vram_total", str(vram_total))
    _write(device / "mem_info_vram_used", str(vram_used))
    hwmon = device / "hwmon" / "hwmon0"
    _write(hwmon / "name", "amdgpu")
    _write(hwmon / "temp1_input", str(temp_milli))
    if power_uw is not None:
        _write(hwmon / "power1_average", str(power_uw))


@pytest.fixture(autouse=True)
def _no_amdsmi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep sysfs tests independent of whether AMD SMI is installed."""
    monkeypatch.setattr(gpu_amd, "_amdsmi_product_name", lambda _index: None)


class TestSysfsBackend:
    """Linux amdgpu backend over a fake /sys/class/drm tree."""

    def test_reads_metrics_and_orders_largest_vram_first(self, tmp_path: Path) -> None:
        """Index 0 is the dGPU on an APU+dGPU box, and every field maps."""
        _make_card(tmp_path, "card0", vram_total=2 << 30, busy=5)
        _make_card(
            tmp_path,
            "card1",
            vram_total=16 << 30,
            vram_used=3 << 30,
            busy=77,
            temp_milli=51000,
            power_uw=210_000_000,
        )

        backend = _SysfsBackend(0, root=tmp_path)

        assert backend.available is True
        assert backend.name == "AMD GPU (card1)"
        snapshot = backend.read()
        assert snapshot is not None
        assert snapshot.utilization == 77.0
        assert snapshot.memory_used_bytes == 3 << 30
        assert snapshot.memory_total_bytes == 16 << 30
        assert snapshot.temperature_celsius == 51.0
        assert snapshot.power_watts == 210.0
        assert snapshot.vendor == "amd"

    def test_second_index_is_the_smaller_card(self, tmp_path: Path) -> None:
        """An explicit index picks the next card in the VRAM order."""
        _make_card(tmp_path, "card0", vram_total=16 << 30, busy=10)
        _make_card(tmp_path, "card1", vram_total=2 << 30, busy=20)

        backend = _SysfsBackend(1, root=tmp_path)

        assert backend.name == "AMD GPU (card1)"
        assert backend.read().utilization == 20.0

    def test_non_amd_vendors_are_ignored(self, tmp_path: Path) -> None:
        """Only vendor 0x1002 cards are candidates."""
        _make_card(tmp_path, "card0", vendor="0x10de", busy=99)
        _make_card(tmp_path, "card1", vram_total=8 << 30, busy=33)

        backend = _SysfsBackend(0, root=tmp_path)

        assert backend.name == "AMD GPU (card1)"
        assert backend.read().utilization == 33.0

    def test_power1_input_is_the_fallback(self, tmp_path: Path) -> None:
        """Kernels without power1_average still report power1_input."""
        _make_card(tmp_path, "card0", power_uw=None)
        device = tmp_path / "card0" / "device" / "hwmon" / "hwmon0"
        _write(device / "power1_input", "90000000")

        snapshot = _SysfsBackend(0, root=tmp_path).read()

        assert snapshot is not None
        assert snapshot.power_watts == 90.0

    def test_missing_fields_degrade_to_zero(self, tmp_path: Path) -> None:
        """A card with only the vendor file still produces a snapshot."""
        device = tmp_path / "card0" / "device"
        _write(device / "vendor", "0x1002")

        snapshot = _SysfsBackend(0, root=tmp_path).read()

        assert snapshot is not None
        assert snapshot.utilization == 0.0
        assert snapshot.memory_total_bytes == 0

    def test_out_of_range_index_is_unavailable(self, tmp_path: Path) -> None:
        """A bad index fails like NVML's out-of-range device index."""
        _make_card(tmp_path, "card0")

        backend = _SysfsBackend(3, root=tmp_path)

        assert backend.available is False
        assert backend.read() is None

    def test_no_amd_card_at_all(self, tmp_path: Path) -> None:
        """A tree with only Intel cards yields an unavailable backend."""
        _make_card(tmp_path, "card0", vendor="0x8086")

        backend = _SysfsBackend(0, root=tmp_path)

        assert backend.available is False


class _FakeAdl:
    """Stand-in for ``_AdlApi`` with the same public surface."""

    def __init__(
        self,
        adapters: list[_AdlAdapter],
        *,
        sensors: dict[int, int] | None = None,
        used_mb: int | None = None,
    ) -> None:
        self.adapters = adapters
        self.sensors = sensors or {}
        self.used_mb = used_mb
        self.closed = False

    def list_adapters(self) -> list[_AdlAdapter]:
        return list(self.adapters)

    def read_sensors(self, _index: int) -> dict[int, int]:
        return dict(self.sensors)

    def vram_used_mb(self, _index: int) -> int | None:
        return self.used_mb

    def close(self) -> None:
        self.closed = True


def _adapter(
    index: int,
    name: str,
    *,
    memory_total_bytes: int = 8 << 30,
    memory_type: str = "GDDR6",
    bandwidth: int = 500_000,
) -> _AdlAdapter:
    return _AdlAdapter(
        index=index,
        name=name,
        pnp_id=f"PCI\\VEN_1002&DEV_{index:04X}",
        memory_total_bytes=memory_total_bytes,
        memory_type=memory_type,
        memory_bandwidth_mbps=bandwidth,
    )


class TestAdlBackend:
    """Windows ADL backend over the PMLog sensor map."""

    def test_selects_the_adapter_with_the_most_vram(self) -> None:
        """The APU is listed first by ADL but the dGPU must win index 0."""
        api = _FakeAdl(
            [
                _adapter(0, "AMD Radeon(TM) Graphics", memory_total_bytes=2 << 30),
                _adapter(5, "AMD Radeon RX 9070 XT", memory_total_bytes=16 << 30),
            ]
        )

        backend = _AdlBackend(0, api=api)

        assert backend.available is True
        assert backend.name == "AMD Radeon RX 9070 XT"
        assert backend.memory_type == "GDDR6"
        assert backend.memory_bandwidth_mbps == 500_000

    def test_maps_pmlog_sensors_to_a_snapshot(self) -> None:
        """Sensors 19/8/23 are activity, edge temperature and ASIC power."""
        api = _FakeAdl(
            [_adapter(5, "AMD Radeon RX 9070 XT", memory_total_bytes=16 << 30)],
            sensors={8: 36, 19: 4, 23: 21},
            used_mb=29,
        )

        snapshot = _AdlBackend(0, api=api).read()

        assert snapshot == GPUSnapshot(
            utilization=4.0,
            memory_used_bytes=29 * 1024**2,
            memory_total_bytes=16 << 30,
            temperature_celsius=36.0,
            power_watts=21.0,
            vendor="amd",
        )

    def test_hotspot_temp_and_board_power_are_the_fallbacks(self) -> None:
        """RDNA4 cards that omit ASIC power still report board power."""
        api = _FakeAdl(
            [_adapter(5, "AMD Radeon RX 9070 XT")],
            sensors={27: 41, 19: 90, 73: 120},
            used_mb=None,
        )

        snapshot = _AdlBackend(0, api=api).read()

        assert snapshot is not None
        assert snapshot.temperature_celsius == 41.0
        assert snapshot.power_watts == 120.0
        assert snapshot.memory_used_bytes == 0

    def test_empty_adapter_list_closes_the_context(self) -> None:
        """No AMD adapter means no context leak."""
        api = _FakeAdl([])

        backend = _AdlBackend(0, api=api)

        assert backend.available is False
        assert api.closed is True

    def test_out_of_range_index_closes_the_context(self) -> None:
        """An explicit index beyond the adapter list fails cleanly."""
        api = _FakeAdl([_adapter(0, "AMD Radeon(TM) Graphics")])

        backend = _AdlBackend(4, api=api)

        assert backend.available is False
        assert api.closed is True

    def test_shutdown_closes_the_context(self) -> None:
        """shutdown() destroys the ADL context and disables reads."""
        api = _FakeAdl([_adapter(0, "AMD Radeon RX 9070 XT")])

        backend = _AdlBackend(0, api=api)
        backend.shutdown()

        assert api.closed is True
        assert backend.available is False
        assert backend.read() is None


class _FakeAmdProbe:
    """Stand-in for ``AmdGpuProbe`` used by the GPUProbe facade tests."""

    def __init__(self, _gpu_index: int) -> None:
        self.available = True
        self.backend = "adl"
        self.name = "AMD Radeon RX 9070 XT"
        self.memory_type = "GDDR6"
        self.memory_bandwidth_mbps = 644_608
        self.handle = 5

    def read(self) -> GPUSnapshot:
        return GPUSnapshot(utilization=12.0, vendor="amd")

    def shutdown(self) -> None:
        self.available = False


class TestGpuProbeFacade:
    """GPUProbe's vendor selection, without touching NVML or the driver."""

    def test_falls_back_to_amd_when_nvml_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No NVML support must fall through to the AMD backend."""
        monkeypatch.setattr(gpu_module, "PYNVML_AVAILABLE", False)
        monkeypatch.setattr(gpu_module, "AMD_AVAILABLE", True)
        monkeypatch.setattr(gpu_module, "AmdGpuProbe", _FakeAmdProbe)

        probe = gpu_module.GPUProbe()

        assert probe.available is True
        assert probe.vendor == "amd"
        assert probe.backend == "adl"
        assert probe.gpu_name == "AMD Radeon RX 9070 XT"
        assert probe.memory_type == "GDDR6"
        assert probe.read().utilization == 12.0
        probe.shutdown()
        assert probe.available is False

    def test_vendor_nvidia_does_not_fall_back_to_amd(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit vendor never silently probes the other one."""
        monkeypatch.setattr(gpu_module, "PYNVML_AVAILABLE", False)
        monkeypatch.setattr(gpu_module, "AMD_AVAILABLE", True)
        monkeypatch.setattr(gpu_module, "AmdGpuProbe", _FakeAmdProbe)

        probe = gpu_module.GPUProbe(vendor="nvidia")

        assert probe.available is False
        assert probe.vendor == "none"

    def test_no_backend_at_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With both backends disabled the probe degrades to N/A."""
        monkeypatch.setattr(gpu_module, "PYNVML_AVAILABLE", False)
        monkeypatch.setattr(gpu_module, "AMD_AVAILABLE", False)

        probe = gpu_module.GPUProbe()

        assert probe.available is False
        assert probe.gpu_name == "N/A"
        assert probe.read() is None
