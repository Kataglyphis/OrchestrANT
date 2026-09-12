"""GPU integration in the benchmark runner: hardware capture and sampling.

The GPU fields must appear when a vendor backend answers and stay absent,
never zero, when none does -- a benchmark run against a remote endpoint has
no local GPU, and a fake 0% would read as "GPU idle" instead of "unknown".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from orchestrant.benchmark import openai_api
from orchestrant.monitoring.gpu_types import GPUSnapshot


if TYPE_CHECKING:
    from typing import Self


class FakeGpuProbe:
    """Context-manager probe with the fields collect_gpu_info() consumes."""

    available = True
    vendor = "amd"
    backend = "adl"
    gpu_name = "AMD Radeon RX 9070 XT"
    memory_type = "GDDR6"
    memory_bandwidth_mbps = 644608

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def read(self) -> GPUSnapshot:
        return GPUSnapshot(
            utilization=55.0,
            memory_used_bytes=4 * 1024**3,
            memory_total_bytes=16 * 1024**3,
            temperature_celsius=60.0,
            power_watts=210.0,
            vendor="amd",
        )

    def shutdown(self) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args) -> bool:
        return False


class NoGpuProbe(FakeGpuProbe):
    """Probe that answers "no GPU here"."""

    available = False


class TestCollectGpuInfo:
    def test_records_vendor_name_vram_and_bandwidth(self, monkeypatch) -> None:
        monkeypatch.setattr("orchestrant.monitoring.gpu.GPUProbe", FakeGpuProbe)

        info = openai_api.collect_gpu_info()

        assert info["vendor"] == "amd"
        assert info["name"] == "AMD Radeon RX 9070 XT"
        assert info["backend"] == "adl"
        assert info["memory_type"] == "GDDR6"
        assert info["memory_bandwidth_gbps"] == 644.6
        assert info["memory_total_mb"] == 16384
        assert info["memory_used_mb"] == 4096

    def test_no_gpu_is_an_empty_dict_not_a_zero_row(self, monkeypatch) -> None:
        monkeypatch.setattr("orchestrant.monitoring.gpu.GPUProbe", NoGpuProbe)

        assert openai_api.collect_gpu_info() == {}


class FakeSamplingProbe:
    """Probe whose read() carries the sampler fields."""

    def read(self) -> GPUSnapshot:
        return GPUSnapshot(
            utilization=75.0,
            memory_used_bytes=3 * 1024**3,
            memory_total_bytes=16 * 1024**3,
            power_watts=180.0,
        )


class TestSampleGpuResources:
    def test_reports_utilization_memory_and_power(self, monkeypatch) -> None:
        monkeypatch.setattr(openai_api, "gpu_probe", FakeSamplingProbe)

        assert openai_api.sample_gpu_resources() == {
            "gpu_utilization_percent": 75.0,
            "gpu_memory_used_gb": 3.0,
            "gpu_power_watts": 180.0,
        }

    def test_no_probe_returns_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(openai_api, "gpu_probe", lambda: None)

        assert openai_api.sample_gpu_resources() == {}

    def test_a_broken_read_does_not_raise(self, monkeypatch) -> None:
        class BoomProbe:
            def read(self) -> GPUSnapshot:
                msg = "driver gone"
                raise RuntimeError(msg)

        monkeypatch.setattr(openai_api, "gpu_probe", BoomProbe)

        assert openai_api.sample_gpu_resources() == {}


class TestSampleResourcesMerge:
    def test_gpu_fields_ride_along_with_cpu_and_ram(self, monkeypatch) -> None:
        monkeypatch.setattr(
            openai_api,
            "sample_gpu_resources",
            lambda: {"gpu_utilization_percent": 10.0},
        )
        monkeypatch.setattr(openai_api, "sample_resources_glances", lambda: None)
        monkeypatch.setattr(
            openai_api,
            "sample_resources_psutil",
            lambda: {
                "cpu_percent": 5.0,
                "ram_percent": 20.0,
                "ram_used_gb": 1.0,
                "ram_total_gb": 8.0,
            },
        )

        result = openai_api.sample_resources()

        assert result["cpu_percent"] == 5.0
        assert result["gpu_utilization_percent"] == 10.0

    def test_a_flaky_glances_falls_back_to_psutil(self, monkeypatch) -> None:
        def broken_glances():
            msg = "No module named 'requests'"
            raise ModuleNotFoundError(msg)

        monkeypatch.setattr(openai_api, "sample_gpu_resources", dict)
        monkeypatch.setattr(openai_api, "sample_resources_glances", broken_glances)
        monkeypatch.setattr(
            openai_api,
            "sample_resources_psutil",
            lambda: {
                "cpu_percent": 7.0,
                "ram_percent": 0.0,
                "ram_used_gb": 0.0,
                "ram_total_gb": 0.0,
            },
        )

        assert openai_api.sample_resources()["cpu_percent"] == 7.0


class TestAvgOptional:
    def test_averages_when_both_samples_carry_the_metric(self) -> None:
        assert openai_api._avg_optional(  # noqa: SLF001
            {"a": 1.0}, {"a": 3.0}, "a"
        ) == pytest.approx(2.0)

    def test_absent_metric_is_none_not_zero(self) -> None:
        assert openai_api._avg_optional({}, {}, "a") is None  # noqa: SLF001
