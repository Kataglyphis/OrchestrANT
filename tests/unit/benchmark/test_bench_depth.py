"""Tests for the decode-at-depth trace (`orchestrant-bench depth`).

The campaign's depth traces came from a scratch script that stored no
provenance and was never committed: the 2026-09-24 `*-depth-8k.json` files
cannot be tied to a runtime or a host load. These pin that the lab tool sends
the same prompt, cuts the same windows and writes the same fields, and that
its report carries what the scratch files lack. The lane is a canned stream.
"""

import hashlib
import io
import json
import os
import sys
import urllib.error

import pytest

from orchestrant.benchmark import contract, depth, provenance


REPO = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
RESULTS = os.path.join(REPO, "benchmarks", "benchmark_results")
TRACKED = os.path.join(RESULTS, "2026-09-24-roadmap", "cpu-9b-depth-8k.json")
# sha256 of the scratch script's prompt at 8000 tokens, computed from its own
# expression (depthtrace2.py) before depth.py existed.
PROMPT_8000_SHA256 = "e179e2287f9a565960a9968e38fdd6a3a465e96984516c5942078f19ff45847a"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # write_report() runs collect(), which asks every registry endpoint and
    # the lane itself; a unit test must do neither.
    monkeypatch.setattr(provenance, "busy_lanes", lambda *a, **k: [])
    monkeypatch.setattr(provenance, "_server_models", lambda *a, **k: None)
    monkeypatch.setattr(provenance, "runtime_info", lambda *a, **k: None)


class Clock:
    """Stands in for depth's `time`: the stream and sleep() move it, nothing else."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def chunk(content=None, reasoning=None, finish=None, usage=None, role=None):
    delta = {}
    if role:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    body = {"choices": [{"delta": delta, "finish_reason": finish}]}
    if usage is not None:
        body = {"choices": [], "usage": usage}
    return f"data: {json.dumps(body)}"


class Lane:
    """Stands in for post_json. The warm-up answers at once; the stream plays
    `lines`, the first after `prefill` seconds and each next `step` later.
    `fail` raises instead of answering the measured request; `cut_after`
    raises mid-stream once that many lines have arrived.
    """

    def __init__(self, clock, lines, prefill=100.0, step=0.1, **faults):
        self.clock, self.lines = clock, lines
        self.prefill, self.step = prefill, step
        self.fail, self.cut_after = faults.get("fail"), faults.get("cut_after")
        self.requests = []

    def __call__(self, url, body, entry=None, stream=False, timeout=300, deadline=None):
        self.requests.append(
            {"url": url, "body": body, "stream": stream, "timeout": timeout}
        )
        if stream and self.fail is not None:
            raise self.fail
        return _Reply(self)


class _Reply:
    def __init__(self, lane):
        self.lane = lane

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}

    def lines(self):
        lane = self.lane
        for i, line in enumerate(lane.lines):
            if lane.cut_after is not None and i == lane.cut_after:
                raise TimeoutError("timed out")
            lane.clock.now += lane.prefill if i == 0 else lane.step
            yield line


def reply(tokens, usage=None):
    """A stream of `tokens` one-token deltas after a role-only first chunk."""
    usage = usage or {"prompt_tokens": 7159, "completion_tokens": tokens}
    lines = [chunk(role="assistant", content="")]
    lines += [chunk(content=f"t{i} ") for i in range(tokens - 1)]
    lines += [chunk(content="end", finish="length"), chunk(usage=usage), "data: [DONE]"]
    return lines


class TestPrompt:
    """The tracked traces were measured on exactly this prompt; a new trace
    compares with them only while it is the same.
    """

    def test_it_is_the_scratch_scripts_prompt(self):
        scratch = (
            f"Background notes, for reference only:\n\n"
            f"{contract.filler(8000, seed=7)}\n\n---\n\n"
            "Write a detailed technical blog post about the benefits and "
            "challenges of running large language models locally."
        )
        assert depth.depth_prompt(8000) == scratch

    def test_the_8000_token_prompt_is_pinned(self):
        # Pins contract.filler too: a new vocabulary or seed would make every
        # new trace incomparable with the tracked ones without a failing test.
        digest = hashlib.sha256(depth.depth_prompt(8000).encode()).hexdigest()
        assert digest == PROMPT_8000_SHA256

    def test_no_context_is_the_bare_question(self):
        assert depth.depth_prompt(0) == depth.QUESTION


class TestWindows:
    """The scratch script's windows: from token 1 (token 0 ends the prefill),
    `size` tokens each, the tail dropped when under a quarter window.
    """

    @staticmethod
    def _even(n, first=100.0, step=0.1):
        return [first + i * step for i in range(n)]

    def test_a_1024_token_reply_reads_as_the_tracked_trace_does(self):
        rows = depth.windows(self._even(1024))
        tracked = ["1-257", "257-513", "513-769", "769-1023"]
        assert [w["tokens"] for w in rows] == tracked
        assert all(w["tok_per_s"] == pytest.approx(10.0) for w in rows)

    def test_a_short_reply_ends_where_the_ollama_trace_did(self):
        # ollama-9b-depth-8k.json: 1022 deltas, last window 769-1021.
        assert depth.windows(self._even(1022))[-1]["tokens"] == "769-1021"

    def test_the_prefill_never_enters_a_window(self):
        stamps = [600.0] + [700.0 + i * 0.2 for i in range(1, 300)]
        assert depth.windows(stamps)[0]["tok_per_s"] == pytest.approx(5.0)

    def test_a_tail_under_a_quarter_window_is_dropped(self):
        # 300 deltas: 1-257, then 257-299 is 42 tokens, under 64.
        assert [w["tokens"] for w in depth.windows(self._even(300))] == ["1-257"]
        assert [w["tokens"] for w in depth.windows(self._even(330))] == [
            "1-257",
            "257-329",
        ]

    def test_the_window_size_is_a_parameter(self):
        rows = depth.windows(self._even(300), size=128)
        assert [w["tokens"] for w in rows] == ["1-129", "129-257", "257-299"]

    def test_a_window_that_arrived_in_one_burst_has_no_rate(self):
        # Ollama delivered whole replies in one burst (defect 2); a window
        # timed at 0 s must not read as a rate, nor divide by zero.
        stamps = [100.0] * 300
        assert depth.windows(stamps) == [{"tokens": "1-257", "tok_per_s": None}]

    def test_nothing_to_window(self):
        assert depth.windows([]) == [] and depth.windows([1.0]) == []


class TestMeasure:
    """One streamed request; every delta that carries text is a token."""

    def _measure(self, monkeypatch, lane):
        monkeypatch.setattr(depth, "post_json", lane)
        monkeypatch.setattr(depth, "time", lane.clock)
        return depth.measure("http://lane:1", "m", 8000, 1024, timeout=3600)

    def test_the_request_is_the_scratch_scripts(self, monkeypatch):
        lane = Lane(Clock(), reply(4))
        self._measure(monkeypatch, lane)
        (sent,) = lane.requests
        assert sent["url"] == "http://lane:1/v1/chat/completions"
        assert sent["stream"] is True and sent["timeout"] == 3600
        body = sent["body"]
        (message,) = body["messages"]
        assert message == {"role": "user", "content": depth.depth_prompt(8000)}
        assert body["max_tokens"] == 1024
        assert body["stream_options"] == {"include_usage": True}
        # The scratch script set no sampler: the lane's default decodes.
        assert "temperature" not in body

    def test_stamps_are_seconds_from_the_request(self, monkeypatch):
        stamps, state = self._measure(monkeypatch, Lane(Clock(), reply(4)))
        # The role-only chunk carries no text and is not a token.
        assert stamps == pytest.approx([100.1, 100.2, 100.3, 100.4])
        assert state["usage"]["prompt_tokens"] == 7159
        assert state["finish_reason"] == "length" and state["error"] is None

    def test_thinking_is_output_too(self, monkeypatch):
        lines = [chunk(reasoning="hm"), chunk(content="ok"), "data: [DONE]"]
        stamps, _ = self._measure(monkeypatch, Lane(Clock(), lines))
        assert len(stamps) == 2

    def test_a_failed_request_is_recorded_not_raised(self, monkeypatch):
        body = io.BytesIO(b'{"error": "context overflow"}')
        err = urllib.error.HTTPError("http://lane:1", 400, "Bad Request", {}, body)
        stamps, state = self._measure(monkeypatch, Lane(Clock(), [], fail=err))
        assert stamps == []
        assert state["error"] == 'HTTP 400: {"error": "context overflow"}'

    def test_what_arrived_before_a_timeout_is_kept(self, monkeypatch):
        lane = Lane(Clock(), reply(10), cut_after=5)
        stamps, state = self._measure(monkeypatch, lane)
        assert len(stamps) == 4
        assert state["error"] == "TimeoutError: timed out"

    @pytest.mark.parametrize(
        "fault",
        [
            'data: {"error": {"code": 500, "message": "context size exceeded"}}',
            'error: {"code": 500, "message": "context size exceeded"}',
        ],
    )
    def test_a_server_error_inside_the_stream_is_an_error(self, monkeypatch, fault):
        # An already-200 stream can still fail: dropped, the fault read as a
        # short clean trace (error None, exit 0). bench_coding learned the
        # same -- it graded such a reply "no code found".
        lane = Lane(Clock(), [*reply(10)[:5], fault, "data: [DONE]"])
        stamps, state = self._measure(monkeypatch, lane)
        assert len(stamps) == 4
        assert "context size exceeded" in state["error"]

    def test_a_reply_with_no_generated_token_is_an_error(self, monkeypatch):
        # A lane that ignored "stream" answers one JSON body: nothing was
        # measured, and a report with no error said the opposite.
        body = json.dumps({"choices": [{"message": {"content": "ok"}}]})
        stamps, state = self._measure(monkeypatch, Lane(Clock(), [body]))
        assert stamps == []
        assert state["error"].startswith("no generated token arrived")


class TestRow:
    def test_the_scratch_scripts_fields_and_the_new_ones(self):
        stamps = [100.0 + i * 0.1 for i in range(1024)]
        state = {"usage": {"prompt_tokens": 7159}, "finish_reason": "length"}
        warmup = {"seconds": 1.2, "error": None}
        row = depth.trace_row("m", 8000, 256, stamps, {**state, "error": None}, warmup)
        assert row["model"] == "m" and row["context_tokens_requested"] == 8000
        assert row["usage"] == {"prompt_tokens": 7159}
        assert row["ttft_s"] == 100.0 and row["tokens"] == 1024
        assert len(row["windows"]) == 4
        assert row["finish_reason"] == "length" and row["error"] is None
        assert row["warmup"] == {"seconds": 1.2, "error": None}
        # The windows can be re-cut at another size from the report alone.
        assert row["delta_times_s"][:2] == [100.0, 100.1]

    def test_no_tokens_is_a_null_ttft_not_a_crash(self):
        state = {"usage": None, "finish_reason": None, "error": "boom"}
        row = depth.trace_row("m", 8000, 256, [], state, None)
        assert row["ttft_s"] is None and row["tokens"] == 0 and row["windows"] == []

    @pytest.mark.skipif(not os.path.isfile(TRACKED), reason="lab results absent")
    def test_a_tracked_trace_has_no_field_a_new_row_lacks(self):
        with open(TRACKED, encoding="utf-8") as f:
            old = json.load(f)
        state = {"usage": old["usage"], "finish_reason": "length", "error": None}
        stamps = [1.0 + i for i in range(1024)]
        row = depth.trace_row("m", 8000, 256, stamps, state, None)
        assert set(old) <= set(row)
        assert [w["tokens"] for w in row["windows"]] == [
            w["tokens"] for w in old["windows"]
        ]


class TestMain:
    """The measurement in the shared envelope, with the provenance the
    scratch files never had.
    """

    def _run(self, monkeypatch, tmp_path, lane, *flags):
        out = tmp_path / "depth.json"
        monkeypatch.setattr(depth, "post_json", lane)
        monkeypatch.setattr(depth, "time", lane.clock)
        argv = ["orchestrant-bench depth", "--base-url", "http://lane:1"]
        argv += ["--model", "m", *flags, "--output", str(out)]
        monkeypatch.setattr(sys, "argv", argv)
        code = depth.main()
        return code, json.loads(out.read_text())

    def test_writes_the_envelope_with_provenance(self, monkeypatch, tmp_path):
        code, doc = self._run(monkeypatch, tmp_path, Lane(Clock(), reply(300)))
        assert code == 0
        assert doc["benchmark"] == "bench_depth"
        (row,) = doc["reports"]
        assert row["label"] == "m" and row["tokens"] == 300
        prov = doc["provenance"]
        assert prov["tool_files"] == ["answers.py", "depth.py"]
        assert prov["tool_sha256"] and prov["source_changed_during_run"] is False
        assert prov["host_load"]["note"] == "stubbed by conftest"
        assert prov["argv"][0] == "orchestrant-bench depth"

    def test_the_config_says_what_was_asked(self, monkeypatch, tmp_path):
        _, doc = self._run(
            monkeypatch, tmp_path, Lane(Clock(), reply(8)), "--window", "128"
        )
        config = doc["config"]
        assert config["context_tokens"] == 8000 and config["max_tokens"] == 1024
        assert config["window"] == 128 and config["rest_s"] == 30
        assert config["timeout_s"] == 3600 and config["filler_seed"] == 7
        prompt = depth.depth_prompt(8000).encode()
        assert config["prompt_sha256"] == hashlib.sha256(prompt).hexdigest()
        assert config["backend_entry"]["headers"] == []

    def test_the_rest_is_the_host_load_window(self, monkeypatch, tmp_path, host_load):
        # The load that slows a CPU lane is the load just before the measured
        # request: the 30 s rest is spent reading it, not before the warm-up.
        clock = Clock()
        lane = Lane(clock, reply(8))
        self._run(monkeypatch, tmp_path, lane)
        assert host_load == [{"seconds": 30, "lane": "http://lane:1"}]
        warm, measured = lane.requests
        assert warm["stream"] is False and warm["body"]["max_tokens"] == 1
        # Not client.spacer's 120 s: a model still loading after it would be
        # counted in the measured request's TTFT.
        assert warm["timeout"] == measured["timeout"] == 3600
        assert measured["stream"] is True
        # The stubbed reading returned at once, so the rest was slept instead.
        assert clock.slept == [pytest.approx(30)]

    def test_a_failed_trace_is_written_and_exits_1(self, monkeypatch, tmp_path):
        lane = Lane(Clock(), [], fail=TimeoutError("timed out"))
        code, doc = self._run(monkeypatch, tmp_path, lane)
        assert code == 1
        assert doc["reports"][0]["error"] == "TimeoutError: timed out"

    def test_an_error_inside_the_stream_exits_1(self, monkeypatch, tmp_path):
        lines = [*reply(10)[:5], 'data: {"error": {"message": "boom"}}']
        code, doc = self._run(monkeypatch, tmp_path, Lane(Clock(), lines))
        assert code == 1
        (row,) = doc["reports"]
        assert row["tokens"] == 4 and "boom" in row["error"]

    def test_the_report_is_written_before_the_summary_prints(
        self, monkeypatch, tmp_path
    ):
        # write_report's rule: nothing that merely prints may cost a finished
        # measurement (a format string raising on a None label once did).
        def boom(row):
            raise TypeError("unsupported operand")

        monkeypatch.setattr(depth, "summary_line", boom)
        with pytest.raises(TypeError):
            self._run(monkeypatch, tmp_path, Lane(Clock(), reply(8)))
        doc = json.loads((tmp_path / "depth.json").read_text())
        assert doc["reports"][0]["tokens"] == 8

    def test_orchestrant_bench_routes_it(self):
        from orchestrant.benchmark.__main__ import COMMANDS, USAGE

        assert COMMANDS["depth"] is depth.main
        assert "depth" in USAGE
