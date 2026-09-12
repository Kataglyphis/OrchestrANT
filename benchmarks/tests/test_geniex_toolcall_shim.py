"""Tests for the Qwen tool-call shim.

The fixtures are REAL server output, captured from GenieX on 2026-09-04, not
invented examples: the whole reason the shim exists is that the actual template
differs from what the API contract implies, so a made-up fixture would test the
wrong thing.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import geniex_toolcall_shim as shim

# Verbatim from empero-ai/Qwen3.8-9B-Distill-GGUF:Q4_K_M, asked to fix a failing
# test. GenieX returned this as `content` with tool_calls empty.
REAL_9B = (
    "I need to first run the test suite to see what's failing, then examine "
    "the source code to find the bug.\n</think>\n\n"
    "<tool_call>\n"
    "<function=bash>\n"
    "<parameter=command>\n"
    "cd /tmp/tmp.Dr27GdIafy && python -m pytest test_calc.py -v 2>&1\n"
    "</parameter>\n"
    "</function>\n"
    "</tool_call>"
)

# Verbatim from the 2B on the same prompt: prose and markdown fences, no
# template at all.
REAL_2B = "The user wants me to run the test suite.\n</think>\n\n```bash\nls -la\n```\n"


class TestRealServerOutput:
    def test_the_9b_call_is_recovered(self):
        text, calls = shim.parse_tool_calls(REAL_9B)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "bash"
        args = json.loads(calls[0]["function"]["arguments"])
        assert args["command"] == (
            "cd /tmp/tmp.Dr27GdIafy && python -m pytest test_calc.py -v 2>&1"
        )

    def test_the_template_does_not_leak_into_the_text(self):
        text, _ = shim.parse_tool_calls(REAL_9B)
        for marker in ("<tool_call>", "<function=", "<parameter=", "</think>"):
            assert marker not in text, f"{marker} leaked into the visible answer"

    def test_the_2b_has_no_call_to_recover(self):
        # Markdown fences are NOT a tool call. Inventing one from them would be
        # the shim guessing, which is how an agent runs a command the model
        # never asked for.
        text, calls = shim.parse_tool_calls(REAL_2B)
        assert calls == []
        assert "ls -la" in text, "the answer itself must survive"
        assert "</think>" not in text


class TestParsing:
    def test_plain_text_is_untouched_apart_from_thinking(self):
        text, calls = shim.parse_tool_calls("Just an answer.")
        assert calls == [] and text == "Just an answer."

    def test_several_calls_stay_separate(self):
        raw = (
            "<tool_call><function=read><parameter=path>a.py</parameter>"
            "</function></tool_call>"
            "<tool_call><function=read><parameter=path>b.py</parameter>"
            "</function></tool_call>"
        )
        _, calls = shim.parse_tool_calls(raw)
        assert [json.loads(c["function"]["arguments"])["path"] for c in calls] == [
            "a.py",
            "b.py",
        ]
        assert len({c["id"] for c in calls}) == 2, "ids must be distinct"

    def test_several_parameters_are_all_kept(self):
        raw = (
            "<tool_call><function=edit>"
            "<parameter=path>x.py</parameter>"
            "<parameter=old>a</parameter>"
            "<parameter=new>b</parameter>"
            "</function></tool_call>"
        )
        _, calls = shim.parse_tool_calls(raw)
        assert json.loads(calls[0]["function"]["arguments"]) == {
            "path": "x.py",
            "old": "a",
            "new": "b",
        }

    def test_inner_indentation_survives(self):
        # Only the newline the template adds is stripped. Python code whose
        # indentation is trimmed is broken code.
        raw = (
            "<tool_call><function=write><parameter=content>\n"
            "def f():\n    return 1\n"
            "</parameter></function></tool_call>"
        )
        _, calls = shim.parse_tool_calls(raw)
        assert (
            json.loads(calls[0]["function"]["arguments"])["content"]
            == "def f():\n    return 1"
        )

    def test_arguments_are_a_json_string_not_an_object(self):
        # The OpenAI contract says arguments is a STRING; an object here makes
        # clients throw.
        _, calls = shim.parse_tool_calls(REAL_9B)
        assert isinstance(calls[0]["function"]["arguments"], str)

    def test_a_truncated_call_does_not_leak_markup(self):
        raw = "<tool_call>\n<function=bash>\n<parameter=command>\nls\n"
        text, calls = shim.parse_tool_calls(raw)
        # No closing tags: nothing can be parsed, and the half-written template
        # must not be shown as if it were an answer.
        assert calls == []
        assert "<tool_call>" not in text


class TestResponseConversion:
    def _response(self, content):
        return {
            "id": "c",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
        }

    def test_finish_reason_becomes_tool_calls(self):
        # An agent loop keys off this. Left as "stop" the turn ends and the call
        # is never executed -- exactly the failure this shim exists to fix.
        out = shim.convert_response(self._response(REAL_9B))
        assert out["choices"][0]["finish_reason"] == "tool_calls"

    def test_a_server_that_already_parsed_is_left_alone(self):
        payload = self._response("")
        payload["choices"][0]["message"]["tool_calls"] = [{"id": "x"}]
        out = shim.convert_response(payload)
        assert out["choices"][0]["message"]["tool_calls"] == [{"id": "x"}]
        assert out["choices"][0]["finish_reason"] == "stop"

    def test_a_plain_answer_keeps_finish_reason_stop(self):
        out = shim.convert_response(self._response("Hello."))
        assert out["choices"][0]["finish_reason"] == "stop"
        assert out["choices"][0]["message"]["content"] == "Hello."

    def test_empty_choices_do_not_crash(self):
        assert shim.convert_response({"choices": []})["choices"] == []


class TestStreamRendering:
    def test_the_stream_carries_the_tool_call_and_terminates(self):
        payload = shim.convert_response(
            {
                "id": "c",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": REAL_9B},
                    }
                ],
            }
        )
        sse = shim.as_sse(payload)
        chunks = [
            json.loads(l[5:].strip())
            for l in sse.splitlines()
            if l.startswith("data:") and "[DONE]" not in l
        ]
        assert sse.endswith("data: [DONE]\n\n")
        tool_deltas = [c for c in chunks if c["choices"][0]["delta"].get("tool_calls")]
        assert len(tool_deltas) == 1
        tc = tool_deltas[0]["choices"][0]["delta"]["tool_calls"][0]
        assert tc["index"] == 0, "streamed tool calls must carry an index"
        assert tc["function"]["name"] == "bash"
        assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


class TestThinking:
    def test_paired_block_is_removed(self):
        assert shim.strip_thinking("<think>secret</think>answer") == "answer"

    def test_unpaired_closing_tag_is_removed(self):
        # Observed on every Qwen3.8 distill response we captured.
        assert (
            shim.strip_thinking("reasoning\n</think>\nanswer") == "reasoning\n\nanswer"
        )

    def test_a_pair_and_a_stray_closer_in_one_message(self):
        # Pairs go first; whatever closing tag survives has no opener left and
        # is stray markup however it got there.
        assert shim.strip_thinking("<think>a</think>b</think>c") == "bc"

    def test_an_unclosed_opening_tag_is_left_alone(self):
        # Still open: everything after it is reasoning we cannot delimit, and
        # deleting to end of message would throw the answer away with it.
        assert shim.strip_thinking("answer <think>a") == "answer <think>a"
