"""A reply that failed or was cut must never be recorded as a measurement or a wrong answer."""

import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orchestrant.benchmark import correctness, openai_api
from orchestrant.benchmark.answers import read_stream, stream_chunks
from orchestrant.benchmark.correctness import CORRECTNESS_PROBES, graded_item, summarise


PROBE = CORRECTNESS_PROBES[0]
RIGHT = PROBE.accepted[0]


def _data(payload):
    return "data: " + json.dumps(payload)


def _delta(text):
    return _data({"choices": [{"delta": {"content": text}, "finish_reason": None}]})


class Reply:
    """post_json's answer: `lines` as a stream, `body` as JSON."""

    def __init__(self, lines=(), body=None):
        self._lines, self._body = lines, body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def lines(self):
        return iter(self._lines)

    def json(self):
        return self._body


def _rows(monkeypatch, reply, *, stream=True):
    def post(url, payload, **kwargs):
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(openai_api, "post_json", post)
    return list(
        openai_api.benchmark_chat(
            "m", ["hi"], stream=stream, warmup=False, base_url="http://lane:1"
        )
    )


class TestAServerErrorInsideTheStream:
    def test_an_error_chunk_raises_with_the_servers_words(self):
        lines = [_delta("The"), _data({"error": {"message": "context overflow"}})]
        with pytest.raises(RuntimeError, match="context overflow"):
            read_stream(lines)

    def test_an_error_event_line_raises(self):
        with pytest.raises(RuntimeError, match="server error in stream: slot busy"):
            list(stream_chunks(["event: error", "error: slot busy"]))

    def test_keep_alives_and_done_are_still_no_error(self):
        lines = [": keep-alive", "", _delta("ok"), "data: [DONE]", _delta("late")]
        assert read_stream(lines).content == "ok"

    def test_the_speed_row_is_an_error_row_not_a_zero_token_reply(self, monkeypatch):
        lines = [_data({"error": {"message": "model crashed", "code": 500}})]
        (row,) = _rows(monkeypatch, Reply(lines))
        assert "model crashed" in row["error"]
        assert "completion_tokens" not in row

    def test_an_http_error_row_carries_the_body_not_just_the_status(self, monkeypatch):
        body = io.BytesIO(b'{"error":{"message":"model m not found"}}')
        refused = urllib.error.HTTPError("http://lane:1", 404, "Not Found", {}, body)
        (row,) = _rows(monkeypatch, refused)
        assert row["error"].startswith("HTTP 404: ")
        assert "model m not found" in row["error"]
        assert body.closed


class TestTokensOfAReplyWithoutUsage:
    def test_a_streamed_reply_counts_its_chunks_and_says_so(self, monkeypatch):
        (row,) = _rows(monkeypatch, Reply([_delta("a"), _delta("b"), "data: [DONE]"]))
        assert (row["completion_tokens"], row["tokens_estimated"]) == (2, True)

    def test_reasoning_that_was_counted_and_not_streamed_leaves_the_rates(
        self, monkeypatch
    ):
        usage = {
            "prompt_tokens": 9,
            "completion_tokens": 420,
            "completion_tokens_details": {"reasoning_tokens": 400},
        }
        lines = [_delta("a"), _delta("b"), _data({"choices": [], "usage": usage})]
        (row,) = _rows(monkeypatch, Reply([*lines, "data: [DONE]"]))
        assert (row["completion_tokens"], row["hidden_reasoning_tokens"]) == (420, 400)
        # The wait for the first token was mostly thinking, so it measures no prefill.
        assert row["prefill_tok_per_sec"] is None
        # Two deltas in one burst: whatever the window, 419 tokens were not decoded in it.
        assert row["decode_tok_per_sec"] is None

    def test_a_whole_body_is_not_one_estimated_token(self, monkeypatch):
        body = {"choices": [{"message": {"content": "A long answer."}}]}
        (row,) = _rows(monkeypatch, Reply(body=body), stream=False)
        assert (row["completion_tokens"], row["tokens_estimated"]) == (0, False)
        assert row["tokens_per_sec"] == 0.0


class TestACutProbeIsNeverWrong:
    def test_thinking_in_its_own_field_cut_by_the_budget_is_truncated(self):
        item = graded_item(
            PROBE, "", reasoning="17 times 23 is", finish_reason="length"
        )
        assert (item["truncated"], item["correct"]) == (True, False)
        assert summarise([item])["verdict"] == "INCONCLUSIVE"

    def test_null_content_is_an_empty_answer_not_a_crash(self):
        item = graded_item(PROBE, None, reasoning="hm", finish_reason="length")
        assert item["truncated"]
        assert graded_item(PROBE, None)["correct"] is False

    def test_untagged_thinking_that_names_the_value_is_not_an_answer(self):
        # Qwen3's template opens <think> in the prompt, so a cut reply carries no tag.
        thinking = f"Let me see, 20 * 17 is 340, plus 3 * 17 gives {RIGHT}, but check"
        item = graded_item(PROBE, thinking, finish_reason="length")
        assert (item["truncated"], item["correct"]) == (True, False)

    def test_an_answer_after_the_thinking_ended_counts_though_cut(self):
        tagged = graded_item(PROBE, f"<think>hm</think>{RIGHT}", finish_reason="length")
        beside = graded_item(PROBE, RIGHT, reasoning="hm", finish_reason="length")
        assert tagged["correct"] and beside["correct"]
        assert not tagged["truncated"] and not beside["truncated"]

    def test_a_finished_reply_grades_as_before(self):
        assert graded_item(PROBE, RIGHT, finish_reason="stop")["correct"]
        wrong = graded_item(PROBE, "0", finish_reason="stop")
        assert (wrong["truncated"], wrong["correct"]) == (False, False)
        assert graded_item(PROBE, "<think>still")["truncated"]

    def test_the_runner_grades_a_reply_that_is_all_reasoning(self, monkeypatch):
        message = {"content": None, "reasoning_content": "Okay, the user asks"}
        body = {"choices": [{"message": message, "finish_reason": "length"}]}
        monkeypatch.setattr(openai_api, "post_json", lambda *a, **k: Reply(body=body))
        block = openai_api.run_correctness_probe("m", base_url="http://lane:1")
        assert block["truncated"] == len(CORRECTNESS_PROBES)
        assert block["wrong"] == 0
        assert correctness.exit_code(block) == 2


class _Refusing(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.do_POST()

    def log_message(self, *a):
        pass


def test_a_run_where_every_request_failed_writes_its_report_and_exits_one(
    monkeypatch, tmp_path
):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Refusing)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    report = tmp_path / "speed.json"
    # main() rebinds both globals; restore them for the tests after this.
    for name in ("LLM_BASE_URL", "OLLAMA_BASE_URL"):
        monkeypatch.setattr(openai_api, name, getattr(openai_api, name))
    monkeypatch.setattr(openai_api, "collect_hardware_info", dict)
    base = f"http://127.0.0.1:{server.server_port}"
    argv = ["speed", "--base-url", base, "--model", "m", "--prompts", "2"]
    monkeypatch.setattr(
        "sys.argv", [*argv, "--no-warmup", "--no-energy", "--output", str(report)]
    )
    try:
        with pytest.raises(SystemExit) as stop:
            openai_api.main()
    finally:
        server.shutdown()
        server.server_close()
    assert stop.value.code == 1
    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["config"]["prompts_completed"] == 0
    assert [row["error"][:8] for row in written["results"]] == ["HTTP 404"] * 2
