"""Unit tests for the concurrency probes (LB4 + LB5).

Runs against a throwaway HTTP server in-process: no model, no GPU, no network
beyond loopback. What is worth testing here is the JUDGEMENT, not the plumbing
-- specifically that "the second request only started after the first
finished" is reported as serialised, because that verdict decides whether you
buy throughput with more clients or with more servers.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orchestrant.benchmark.lanes import parse_lane, probe_batching, stream_once


def make_server(*, serialise, tokens=5, delay=0.02):
    """A tiny SSE endpoint. If serialise=True it holds a lock for the whole
    response, so a second request cannot start until the first has finished --
    exactly the behaviour the probe must detect.
    """
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 + Connection: close means the body is delimited by EOF, so
        # the stream needs no chunk framing and no Content-Length. Declaring
        # "chunked" without actually framing the chunks breaks the client.
        protocol_version = "HTTP/1.0"

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()

            def emit():
                for _ in range(tokens):
                    time.sleep(delay)
                    # No space after "data:" on purpose -- the spec allows it
                    # and at least one real server does exactly this.
                    chunk = json.dumps({"choices": [{"delta": {"content": "x"}}]})
                    self.wfile.write(f"data:{chunk}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data:[DONE]\n\n")
                self.wfile.flush()

            try:
                if serialise:
                    with lock:
                        emit()
                else:
                    emit()
            except BrokenPipeError:
                pass  # client hung up early; nothing to do

        def log_message(self, *a):
            pass

    # ThreadingHTTPServer, otherwise the SERVER serialises regardless of the
    # handler and the "overlapping" case could never be expressed.
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


class TestStreamOnce:
    def test_parses_sse_without_space_after_data(self):
        srv, url = make_server(serialise=False, tokens=4)
        try:
            r = stream_once(url, "m", "hi", max_tokens=16)
        finally:
            srv.shutdown()
        assert r["tokens"] == 4
        assert r["ttft_s"] is not None and r["ttft_s"] > 0

    def test_unreachable_endpoint_reports_error_not_raise(self):
        # A dead lane must not kill a multi-lane run.
        r = stream_once("http://127.0.0.1:1", "m", "hi", timeout=2)
        assert "error" in r
        assert "wall_s" in r


class TestBatchingVerdict:
    def test_detects_serialised_server(self):
        srv, url = make_server(serialise=True, tokens=8, delay=0.05)
        try:
            report = probe_batching(url, "m", "hi", 16)
        finally:
            srv.shutdown()
        assert report["serialised"] is True
        assert "SERIALISED" in report["verdict"]

    def test_detects_overlapping_server(self):
        srv, url = make_server(serialise=False, tokens=8, delay=0.05)
        try:
            report = probe_batching(url, "m", "hi", 16)
        finally:
            srv.shutdown()
        assert report["serialised"] is False
        assert "OVERLAPPED" in report["verdict"]


class TestLaneSpecParsing:
    def test_parses_name_url_model(self):
        name, url, model = parse_lane("npu=http://127.0.0.1:18181,model=org/Model:Q4")
        assert (name, url, model) == ("npu", "http://127.0.0.1:18181", "org/Model:Q4")

    def test_strips_trailing_slash(self):
        _, url, _ = parse_lane("cpu=http://h:1/,model=m")
        assert url == "http://h:1"

    def test_model_name_may_contain_commas_after_the_marker(self):
        _, _, model = parse_lane("a=http://h:1,model=some,weird:name")
        assert model == "some,weird:name"

    def test_rejects_malformed_spec(self):
        with pytest.raises(Exception):
            parse_lane("this-is-not-a-lane")


class TestLaneResolution:
    """A bare backend name must work as a lane, and a typo must not be
    silently turned into something plausible.
    """

    def test_bare_backend_name_resolves_from_registry(self):
        from orchestrant.benchmark.lanes import resolve_lane

        name, url, model = resolve_lane("geniex-npu")
        assert name == "geniex-npu"
        assert url.startswith("http://")
        assert model  # the registry supplies the default model

    def test_full_spec_still_works(self):
        from orchestrant.benchmark.lanes import resolve_lane

        assert resolve_lane("x=http://h:1,model=m") == ("x", "http://h:1", "m")

    def test_unknown_name_is_rejected_with_the_known_list(self):
        import argparse

        from orchestrant.benchmark.lanes import resolve_lane

        with pytest.raises(argparse.ArgumentTypeError) as e:
            resolve_lane("not-a-backend")
        assert "not-a-backend" in str(e.value)
        assert "geniex-npu" in str(e.value) or "ollama" in str(e.value)

    def test_backend_without_a_default_model_explains_itself(self):
        # 'ollama' has no pinned model: the error must say how to supply one
        # rather than fail with a KeyError deep inside the run.
        import argparse

        from orchestrant.benchmark.lanes import resolve_lane

        with pytest.raises(argparse.ArgumentTypeError) as e:
            resolve_lane("ollama")
        assert "model=" in str(e.value)


class TestEmptyReplyHandling:
    """A request can succeed and return NOTHING — exactly what the QAIRT lane
    does past its context limit: HTTP 200, zero tokens, ttft_s None. Every
    print used to raise TypeError on it and take the whole run down.
    """

    def test_seconds_formatter_tolerates_none(self):
        from orchestrant.benchmark.lanes import _secs

        assert "n/a" in _secs(None)
        assert "1.50" in _secs(1.5)

    def test_batching_probe_is_inconclusive_rather_than_crashing(self):
        from orchestrant.benchmark.lanes import probe_batching

        srv, url = make_server(serialise=False, tokens=0)
        try:
            report = probe_batching(url, "m", "hi", 16)
        finally:
            srv.shutdown()
        assert report["verdict"] == "inconclusive"
        assert report["serialised"] is None


class TestRegistrySeam:
    """resolve_lane took no path, so its tests were wired to the shipped
    backends.json and broke on any edit to it. A test that fails for an
    unrelated change is a test people learn to ignore.
    """

    def test_resolves_against_a_supplied_registry(self, tmp_path):
        import json as _json

        from orchestrant.benchmark.lanes import resolve_lane

        reg = tmp_path / "b.json"
        reg.write_text(
            _json.dumps(
                {
                    "default": "x",
                    "backends": {"x": {"base_url": "http://h:1/", "model": "org/M"}},
                }
            )
        )
        assert resolve_lane("x", path=str(reg)) == ("x", "http://h:1", "org/M")

    def test_unknown_name_lists_the_supplied_registry_not_the_shipped_one(
        self, tmp_path
    ):
        import argparse
        import json as _json

        import pytest as _pytest

        from orchestrant.benchmark.lanes import resolve_lane

        reg = tmp_path / "b.json"
        reg.write_text(
            _json.dumps({"backends": {"only-this": {"base_url": "http://h:1"}}})
        )
        with _pytest.raises(argparse.ArgumentTypeError) as e:
            resolve_lane("nope", path=str(reg))
        assert "only-this" in str(e.value)


class TestReportEnvelope:
    """D29: the lane report was the one --output that bypassed write_report.
    The manifest indexed it as an empty 'throughput' run and bench_compare
    passed ANY two of them.
    """

    LANE_RUN = {
        "lanes": {
            "npu": {"decode_tok_per_sec": 19.25, "ttft_s": 0.4},
            "gpu": {"error": "dead"},
        },
        "baseline": {"npu": {"decode_tok_per_sec": 20.0}},
        "aggregate_tok_per_sec": 19.25,
        "wall_s": 9.0,
    }
    LANES = {"npu": ("http://h:1", "m1"), "gpu": ("http://h:2", "m2")}

    def test_one_row_per_lane_plus_the_aggregate(self):
        from orchestrant.benchmark.lanes import build_reports

        rows = build_reports(lane_run=self.LANE_RUN, lanes=self.LANES)
        assert [r["label"] for r in rows] == ["npu", "gpu", "aggregate"]
        assert (
            rows[0]["tok_per_sec"] == 19.25
            and rows[0]["alone"]["decode_tok_per_sec"] == 20.0
        )
        assert rows[1]["tok_per_sec"] is None  # a dead lane is a row, not a crash
        assert rows[2]["tok_per_sec"] == 19.25 and rows[2]["lanes"] == ["gpu", "npu"]

    def test_batching_row_carries_the_verdict(self):
        from orchestrant.benchmark.lanes import build_reports

        rows = build_reports(
            batching={
                "verdict": "OVERLAPPED (batching)",
                "serialised": False,
                "wall_s": 1.0,
                "requests": {},
            },
            batching_endpoint=("http://h:1", "m"),
        )
        assert rows[0]["label"] == "batching" and rows[0]["serialised"] is False

    def test_a_failed_batching_probe_is_still_a_row(self):
        from orchestrant.benchmark.lanes import build_reports

        rows = build_reports(batching=None, batching_endpoint=("http://h:1", "m"))
        assert rows[0]["serialised"] is None and rows[0]["verdict"] == "error"

    def test_no_rows_are_scores(self):
        from orchestrant.benchmark.lanes import build_reports
        from orchestrant.benchmark.report import is_scored

        rows = build_reports(lane_run=self.LANE_RUN, lanes=self.LANES)
        assert not any(is_scored(r) for r in rows)

    def test_main_writes_the_shared_envelope(self, tmp_path, monkeypatch):
        # End to end against the loopback server; the provenance probes that
        # would touch other hosts are stubbed so this never leaves the machine.
        from orchestrant.benchmark import (
            lanes as bench_lanes,
            provenance as bench_provenance,
        )
        from orchestrant.benchmark.report import build_manifest

        monkeypatch.setattr(bench_provenance, "busy_lanes", lambda *a, **k: [])
        monkeypatch.setattr(bench_provenance, "_server_models", lambda *a, **k: None)
        srv, url = make_server(serialise=False, tokens=4)
        out = tmp_path / "lanes.json"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "bench_lanes.py",
                "--lanes",
                f"x={url},model=m",
                "--no-baseline",
                "--output",
                str(out),
            ],
        )
        try:
            bench_lanes.main()
        finally:
            srv.shutdown()
        doc = json.loads(out.read_text())
        assert doc["benchmark"] == "bench_lanes"
        assert doc["provenance"]["base_url"] == url and doc["provenance"]["tool_sha256"]
        assert doc["config"] == {
            "prompt": bench_lanes.DEFAULT_PROMPT,
            "max_tokens": 256,
            "baseline": False,
        }
        assert [r["label"] for r in doc["reports"]] == ["x", "aggregate"]

        entry = build_manifest(str(tmp_path), "T", "m", "now")["configs"][0]
        assert entry["kind"] == "bench_lanes" and "scored" not in entry

        # The normalisation half belongs to bench_compare, which is lab code and
        # moves in a later phase; bridge to the hub while it still lives there.
        hub = os.path.join(
            os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            ),
            "third_party",
            "ANTfrastructure",
            "linux",
            "llm-stack",
        )
        if not os.path.isfile(os.path.join(hub, "bench_compare.py")):
            pytest.skip("bench_compare is lab code; the load() assertions move with it")
        sys.path.insert(0, hub)
        try:
            from bench_compare import load

            norm = load(str(out))
        finally:
            sys.path.remove(hub)
        assert norm["benchmark"] == "bench_lanes"
        assert {e["label"]: e["tok_per_sec"] for e in norm["entries"]}["x"] is not None
