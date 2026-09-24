"""Tests for the EMI energy meter's arithmetic.

The meter publishes about once a second, so the per-request number is an
interpolation over its cumulative curve; these pin that arithmetic without a
meter. Reading the real counters is exercised only on Windows.
"""

import sys

import pytest

from orchestrant.benchmark import energy
from orchestrant.benchmark.energy import (
    EPOCH_1601_MS,
    PWH_TO_J,
    EnergyMeter,
    _interpolate,
    energy_block,
    renet,
    request_energy,
)


def _meter(samples, rails=("CPU_CLUSTER_0",)):
    """An EnergyMeter carrying canned samples, no PDH behind it."""
    m = EnergyMeter(poll_s=0.01, enabled=False)
    m.available, m.reason, m.rails = True, None, list(rails)
    m._samples = samples  # noqa: SLF001 -- the seam a unit test needs
    return m


class TestInterpolate:
    SAMPLES = [(100.0, {"r": 0}), (101.0, {"r": 1000}), (102.0, {"r": 3000})]

    def test_linear_between_published_samples(self):
        assert _interpolate(self.SAMPLES, "r", 100.5) == 500
        assert _interpolate(self.SAMPLES, "r", 101.5) == 2000

    def test_outside_the_sampled_range_is_unknown_not_extrapolated(self):
        assert _interpolate(self.SAMPLES, "r", 99.0) is None
        assert _interpolate(self.SAMPLES, "r", 102.5) is None


class TestEnergyBetween:
    def test_joules_over_a_window_that_straddles_updates(self):
        # a gigawatt-picohour per second is 3.6 W
        m = _meter(
            [
                (0.0, {"CPU_CLUSTER_0": 0}),
                (1.0, {"CPU_CLUSTER_0": 10**9}),
                (2.0, {"CPU_CLUSTER_0": 2 * 10**9}),
            ]
        )
        joules = m.energy_between(0.5, 1.5)
        assert joules == {"CPU_CLUSTER_0": pytest.approx(10**9 * PWH_TO_J, abs=1e-3)}

    def test_a_window_the_meter_has_not_covered_is_none(self):
        m = _meter([(0.0, {"CPU_CLUSTER_0": 0}), (1.0, {"CPU_CLUSTER_0": 10})])
        m.wait_until = lambda t, timeout=3.0: False  # no waiting in a unit test
        assert m.energy_between(0.5, 5.0) is None

    def test_an_unavailable_meter_reports_nothing(self):
        m = _meter([])
        m.available = False
        assert m.energy_between(0.0, 1.0) is None


class _FakeMeter:
    """Ten joules over any window, split across two rails."""

    available = True

    def energy_between(self, t0, t1):
        return {"CPU_CLUSTER_0": 6.0, "CPU_CLUSTER_1": 4.0}


class TestRequestEnergy:
    def test_gross_net_and_per_token(self):
        fields = request_energy(
            _FakeMeter(), 0.0, 2.0, completion_tokens=20, idle_w=1.5
        )
        assert fields["cpu_rail_energy_j"] == 10.0
        assert fields["cpu_rail_avg_w"] == 5.0
        assert fields["cpu_rail_j_per_token"] == 0.5
        # net of idle: ten joules minus 1.5 W over two seconds leaves seven
        assert fields["cpu_rail_net_energy_j"] == 7.0
        assert fields["cpu_rail_net_j_per_token"] == 0.35

    def test_no_meter_no_fields_rather_than_zeros(self):
        assert request_energy(None, 0.0, 1.0, 10) == {}

    def test_a_disabled_meter_adds_no_fields(self):
        assert request_energy(EnergyMeter(enabled=False), 0.0, 1.0, 10) == {}

    def test_net_energy_never_goes_negative(self):
        fields = request_energy(
            _FakeMeter(), 0.0, 2.0, completion_tokens=0, idle_w=50.0
        )
        assert fields["cpu_rail_net_energy_j"] == 0.0
        assert "cpu_rail_j_per_token" not in fields


def _stamp(unix_s):
    return {"CPU_CLUSTER_0": int(unix_s * 1000 + EPOCH_1601_MS)}


class TestSamples:
    def test_a_torn_read_is_dropped_and_the_next_poll_recovers(self):
        # Energy and stamp from different publications: a new stamp with the
        # previous second's energy put ~16 J into the wrong interval.
        m = _meter([(100.0, {"CPU_CLUSTER_0": 1000})])
        m._append({"CPU_CLUSTER_0": 1000}, _stamp(101.0))  # noqa: SLF001
        assert len(m._samples) == 1  # noqa: SLF001
        m._append({"CPU_CLUSTER_0": 2000}, _stamp(101.0))  # noqa: SLF001
        assert m._samples[-1] == (101.0, {"CPU_CLUSTER_0": 2000})  # noqa: SLF001

    def test_a_counter_reset_starts_the_curve_again(self):
        # A reset gave -1002 J and a negative J/token that the summary summed;
        # refusing every later sample instead would unmeter the rest of the run.
        m = _meter([(100.0, {"CPU_CLUSTER_0": 5000})])
        m._append({"CPU_CLUSTER_0": 10}, _stamp(101.0))  # noqa: SLF001
        assert m._samples == [(101.0, {"CPU_CLUSTER_0": 10})]  # noqa: SLF001
        assert m.resets == 1
        m._append({"CPU_CLUSTER_0": 900}, _stamp(102.0))  # noqa: SLF001
        assert len(m._samples) == 2  # noqa: SLF001


class _Baselines:
    def __init__(self, *watts):
        self.baselines = list(watts)
        self.available, self.reason, self.rails = True, None, ["CPU_CLUSTER_0"]


class TestBaselines:
    def test_a_zero_second_idle_window_is_no_baseline_not_a_crash(self):
        m = _meter([])
        assert m.idle_power(0) is None and m.baselines == []

    def test_rows_are_netted_against_the_mean_of_before_and_after(self):
        # 2026-09-24: one 5-s window read 1.26 W in one NPU run and 1.84 W in
        # the next; netting against it turned +21 % gross into "+70 % net".
        rows = [
            {
                "cpu_rail_energy_j": 20.0,
                "cpu_rail_window_s": 10.0,
                "completion_tokens": 100,
            }
        ]
        assert renet(rows, _Baselines(1.0, 1.4)) == 1.2
        assert rows[0]["cpu_rail_net_energy_j"] == 8.0
        assert rows[0]["cpu_rail_net_j_per_token"] == 0.08

    def test_one_baseline_cannot_vouch_for_itself(self):
        assert energy_block(_Baselines(1.84))["net_reliable"] is None

    def test_drift_between_the_baselines_is_reported(self):
        block = energy_block(_Baselines(1.26, 1.84))
        assert block["idle_drift_w"] == 0.58 and block["net_reliable"] is False
        assert energy_block(_Baselines(1.80, 1.84))["net_reliable"] is True


class TestAvailability:
    @pytest.mark.skipif(
        sys.platform == "win32", reason="the refusal is for non-Windows hosts"
    )
    def test_outside_windows_the_meter_says_why(self):
        m = EnergyMeter()
        assert m.available is False and "not Windows" in m.reason

    @pytest.mark.skipif(
        sys.platform != "win32", reason="Energy Meter counters are Windows-only"
    )
    def test_on_windows_it_either_reads_rails_or_says_why(self):
        m = EnergyMeter()
        try:
            assert m.available or m.reason
            if m.available:
                assert m.rails and "_Total" not in m.rails
        finally:
            m.stop()

    def test_scope_names_what_is_not_metered(self):
        assert "NPU" in energy.SCOPE
