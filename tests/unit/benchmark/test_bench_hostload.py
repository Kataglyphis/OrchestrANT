"""Tests for CPU accounting over a request window.

The runner used to average a CPU sample taken before a request with one taken
after it, so a lane pinning 7.5 of 8 cores read as idle. These pin the window
arithmetic and the lane lookup that replace it.
"""

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orchestrant.benchmark.hostload import (
    LaneProcess,
    RequestBracket,
    Window,
    _port,
    summary_lines,
)


class TestPort:
    def test_loopback_urls_have_a_local_port(self):
        assert _port("http://127.0.0.1:18181") == 18181
        assert _port("http://localhost:11434") == 11434
        assert _port("http://127.0.0.1") == 80

    def test_a_remote_url_is_not_this_host(self):
        assert _port("http://summy-server:11434") is None
        assert _port("https://api.example.com/v1") is None


class TestLaneProcess:
    def test_a_remote_endpoint_is_unavailable_with_a_reason(self):
        lane = LaneProcess("http://summy-server:11434")
        assert not lane.available and "not on this host" in lane.reason
        assert lane.cpu_seconds() is None and lane.info() is None

    def test_finds_the_process_listening_on_a_local_port(self):
        class Quiet(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            lane = LaneProcess(f"http://127.0.0.1:{srv.server_port}")
            if not lane.available:
                pytest.skip(f"listener lookup not permitted here: {lane.reason}")
            assert lane.proc.pid == os.getpid()
            assert lane.info()["pid"] == os.getpid()
            assert lane.cpu_seconds() >= 0
        finally:
            srv.shutdown()


class TestRemoteLaneIsNotMetered:
    def test_this_hosts_rails_are_not_recorded_for_another_host(self):
        from orchestrant.benchmark.hostload import open_meters

        _lane, meter = open_meters("http://summy-server:1")
        try:
            assert not meter.available and meter.reason.startswith("not metered")
        finally:
            meter.stop()


class TestLaneThatDisappears:
    def test_a_gone_lane_is_unknown_not_zero(self):
        # A restarted lane read 0.0 CPU-seconds, so the whole machine's load
        # became "other" load.
        import psutil

        class Gone:
            pid = 1

            def cpu_times(self):
                raise psutil.NoSuchProcess(1)

        lane = LaneProcess("http://summy-server:1")
        lane.proc = Gone()
        assert lane.cpu_seconds() is None


class TestSummaryLines:
    def test_energy_per_token_is_a_ratio_of_sums(self):
        # Measured on the v0.6.1 NPU lane: a 0.57 s answer read 0.44 J/token
        # net (meter noise around 8 tokens) beside 0.13 for an 11 s one. A mean
        # of per-request ratios lets the short one dominate; tokens must weigh.
        rows = [
            {
                "completion_tokens": 8,
                "cpu_rail_window_s": 0.5,
                "cpu_rail_energy_j": 5.0,
                "cpu_rail_net_energy_j": 4.0,
            },
            {
                "completion_tokens": 256,
                "cpu_rail_window_s": 11.0,
                "cpu_rail_energy_j": 55.0,
                "cpu_rail_net_energy_j": 33.0,
            },
        ]
        line = next(x for x in summary_lines(rows) if "CPU-rail energy" in x)
        assert "0.227 J/token gross" in line  # 60 J over 264 tokens
        assert "0.140 net of idle" in line  # 37 J over 264 tokens
        assert "5.2 W" in line  # 60 J over 11.5 s

    def test_other_load_is_named_beside_the_lane(self):
        # Measured: 0.52 other cores left the CPU lane at 23 tok/s, 0.93 at 14,
        # and a quiet machine gave 31. The report must say which it was.
        rows = [{"other_cores": 0.52}, {"other_cores": 0.93}]
        line = next(x for x in summary_lines(rows) if "Other load" in x)
        assert "0.73 cores avg, max 0.93" in line

    def test_no_metered_rows_no_energy_line(self):
        assert not [
            x for x in summary_lines([{"completion_tokens": 5}]) if "energy" in x
        ]


class TestRequestBracket:
    class _Lane:
        available = True

        def __init__(self):
            self.seconds = iter([10.0, 11.5])

        def cpu_seconds(self):
            return next(self.seconds)

    def test_other_cores_is_system_load_minus_the_lane(self, monkeypatch):
        sample = {"cpu_percent": 0.0, "ram_used_gb": 1.0}
        bracket = RequestBracket(lambda: dict(sample), list, lane=self._Lane())
        bracket._ps_cores = 8  # noqa: SLF001 -- pin the host size
        bracket.start()
        bracket.stop()
        # Pretend the window saw 50 % of 8 cores busy while the lane used 1.5 s/s.
        bracket._load = {  # noqa: SLF001
            "cpu_busy_percent_window": 50.0,
            "lane_cpu_s": 1.5,
            "lane_cores": 1.5,
        }
        out = bracket.fields(completion_tokens=10)
        assert out["cpu_percent_method"] == "window"
        assert out["other_cores"] == 2.5  # 4 busy cores - 1.5 lane cores

    def test_no_other_cores_without_a_local_lane(self):
        sample = {"cpu_percent": 10.0, "ram_used_gb": 1.0}
        bracket = RequestBracket(lambda: dict(sample), list).start()
        out = bracket.stop().fields(completion_tokens=5)
        assert out["cpu_percent_method"] == "before/after snapshots"
        assert "other_cores" not in out


class TestWindow:
    def test_lane_cpu_seconds_are_differenced_over_the_window(self):
        class FakeLane:
            available = True

            def __init__(self):
                self.calls = 0

            def cpu_seconds(self):
                self.calls += 1
                return 10.0 if self.calls == 1 else 13.0

        w = Window(FakeLane()).start()
        time.sleep(0.05)
        out = w.stop()
        assert out["lane_cpu_s"] == 3.0
        assert out["lane_cores"] > 0

    def test_system_busy_share_is_a_percentage(self):
        w = Window().start()
        sum(i * i for i in range(200_000))  # some work inside the window
        time.sleep(0.1)  # Windows' CPU-time clock ticks at ~15.6 ms
        out = w.stop()
        assert 0.0 <= out["cpu_busy_percent_window"] <= 100.0
        assert "lane_cpu_s" not in out
