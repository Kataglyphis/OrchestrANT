"""Tests for the runtime contract probe's verdicts.

The checks talk to a server; what is pinned here is how an observed reply
becomes an answer, using canned replies instead of a lane.
"""

import sys

import pytest

from orchestrant.benchmark import contract


def _reply(content="", finish="stop", usage=None, tool_calls=None):
    msg = {"content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {
        "choices": [{"message": msg, "finish_reason": finish}],
        "usage": usage or {},
    }


@pytest.fixture
def chat(monkeypatch):
    """Replace the transport: each call pops the next canned (seconds, body, err)."""
    queue = []

    def fake(ctx, messages, timeout=600, **params):
        fake.calls.append(params)
        return queue.pop(0)

    fake.calls = []
    monkeypatch.setattr(contract, "_chat", fake)
    return queue, fake


CTX = {
    "base_url": "http://h:1",
    "model": "m",
    "entry": {},
    "prefix_tokens": 50,
    "overflow_tokens": 0,
}


class TestFiller:
    def test_is_deterministic_and_roughly_sized(self):
        a, b = contract.filler(1000), contract.filler(1000)
        assert a == b
        assert 600 < len(a.split()) < 900

    def test_a_different_seed_differs(self):
        assert contract.filler(200) != contract.filler(200, seed=99)


class TestVerdicts:
    def test_max_tokens_honoured_needs_the_count_and_the_reason(self, chat):
        queue, _ = chat
        queue.append((0.1, _reply("1 2 3", "length", {"completion_tokens": 16}), None))
        assert contract.check_max_tokens(CTX)["answer"] == "yes"
        queue.append((0.1, _reply("1 2 3", "stop", {"completion_tokens": 40}), None))
        assert contract.check_max_tokens(CTX)["answer"] == "no"

    def test_a_transport_error_is_error_not_no(self, chat):
        queue, _ = chat
        queue.append((0.1, None, "HTTP 500: boom"))
        assert contract.check_max_tokens(CTX)["answer"] == "error"

    def test_power_mode_is_understood_only_if_nonsense_is_refused(self, chat):
        queue, fake = chat
        # A server that ignores unknown fields accepts both: NOT understood.
        queue += [(0.1, _reply("ok"), None)] * 3
        assert contract.check_power_mode(CTX)["answer"] == "no"
        queue += [
            (12.7, _reply("ok"), None),
            (0.1, None, "HTTP 400: invalid power mode"),
            (12.4, _reply("ok"), None),
        ]
        out = contract.check_power_mode(CTX)
        assert out["answer"] == "yes"
        assert fake.calls[-2]["power_mode"] == "turbo"
        # The reload it costs is on the record.
        assert out["evidence"]["power_saver"] == "accepted in 12.7s"

    def test_power_mode_leaves_the_lane_as_launched(self, chat):
        # On GenieX v0.7.0 power_mode is part of the model's cache key: the
        # lane reloaded into power_saver and stayed there until some tool's
        # plain request reloaded it back, inside that tool's measurement.
        queue, fake = chat
        queue += [(0.1, _reply("ok"), None)] * 3
        contract.check_power_mode(CTX)
        assert "power_mode" not in fake.calls[-1]

    def test_temperature0_draws_are_never_back_to_back(self, chat):
        # GenieX answers an identical follow-up along a cache path that changes
        # the reply; the draws must measure the sampler, not that. The first
        # draw is spaced too: live, the previous check ended on this prompt and
        # its first draw came back as " it's a bit challenging...".
        queue, fake = chat
        queue += [(0.1, _reply("The sea."), None)] * 4
        assert contract.check_temperature0(CTX)["answer"] == "yes"
        assert [c["max_tokens"] for c in fake.calls] == [1, 48, 1, 48]
        assert fake.calls[1] == fake.calls[3]

    def test_temperature0_read_as_unset_is_named(self, chat):
        queue, fake = chat
        queue += [
            (0.1, _reply("ok"), None),
            (0.1, _reply("The sea whispers"), None),
            (0.1, _reply("ok"), None),
            (0.1, _reply("The sea is a vast"), None),
        ]
        assert contract.check_temperature0_is_greedy(CTX)["answer"] == "no"
        assert fake.calls[1]["temperature"] == 0
        assert fake.calls[3]["top_k"] == 1

    def test_identical_repeat_is_judged_only_on_a_reproducible_setting(self, chat):
        queue, _ = chat
        same, other = _reply("The sea is a vast"), _reply(" seabed</think>")
        spacer = (0.1, _reply("ok"), None)
        # Spaced greedy draws agree; back to back the second one differs.
        queue += [spacer, (0.1, same, None), spacer, (0.1, same, None)]
        queue += [spacer, (0.1, same, None), (0.1, other, None)]
        assert contract.check_identical_repeat(CTX)["answer"] == "no"
        # Spaced greedy draws already differ: the repeat path cannot be blamed.
        queue += [spacer, (0.1, same, None), spacer, (0.1, other, None)]
        assert contract.check_identical_repeat(CTX)["answer"] == "inconclusive"

    def test_thinking_in_a_separate_field_counts(self, chat):
        queue, _ = chat
        body = _reply("5")
        body["choices"][0]["message"]["reasoning_content"] = "2 plus 3..."
        queue.append((0.5, body, None))
        assert contract.check_thinks(CTX)["answer"] == "yes"

    def test_a_fast_cold_prefill_cannot_show_a_cache(self, chat):
        queue, _ = chat
        self._timings(queue, cold=0.4, repeat=0.1, extend=0.1, fork=0.4)
        assert contract.check_prefix_cache(dict(CTX))["answer"] == "inconclusive"

    def test_a_server_error_mentioning_the_context_is_not_clean(self, chat):
        # QAIRT's load failure says "Context create from binary failed".
        queue, _ = chat
        queue.append((0.1, None, "HTTP 500: Context create from binary failed"))
        assert (
            contract.check_overflow({**CTX, "overflow_tokens": 6000})["answer"] == "no"
        )

    def test_completions_stop_needs_the_sequence_to_get_there(self, monkeypatch):
        class Reply:
            def __init__(self, text):
                self.text = text

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def json(self):
                return {"choices": [{"text": self.text}]}

        for text, answer in (
            (" 5, 6,", "yes"),
            (" 5, 6, 7, 8", "no"),
            ("", "inconclusive"),
        ):
            monkeypatch.setattr(contract, "post_json", lambda *a, t=text, **k: Reply(t))
            assert contract.check_completions_stop(CTX)["answer"] == answer

    def test_overflow_is_skipped_unless_asked(self, chat):
        assert contract.check_overflow(CTX)["answer"] == "skipped"

    def test_an_overflow_error_naming_the_context_is_clean(self, chat):
        queue, _ = chat
        queue.append(
            (0.1, None, 'HTTP 400: {"error":{"code":"context_length_exceeded"}}')
        )
        assert (
            contract.check_overflow({**CTX, "overflow_tokens": 6000})["answer"] == "yes"
        )
        queue.append((0.0, _reply("", "stop", {"completion_tokens": 0}), None))
        assert (
            contract.check_overflow({**CTX, "overflow_tokens": 6000})["answer"] == "no"
        )

    @staticmethod
    def _timings(queue, cold, repeat, extend, fork):
        for seconds, tokens in (
            (cold, 2000),
            (repeat, 2000),
            (extend, 2300),
            (fork, 2300),
        ):
            queue.append((seconds, _reply("ok", usage={"prompt_tokens": tokens}), None))

    def test_the_three_cache_answers_share_one_measurement(self, chat):
        # The v0.6.1 llama.cpp lane: a repeat and a one-turn extension come from
        # the cache, a shared prefix with a different tail does not.
        queue, fake = chat
        self._timings(queue, cold=12.0, repeat=0.2, extend=1.1, fork=13.0)
        ctx = dict(CTX)
        out = contract.check_prefix_cache(ctx)
        assert out["answer"] == "yes"
        assert out["prefill_tok_per_s"] == pytest.approx(166.7, abs=0.1)
        assert contract.check_prefix_extend(ctx)["answer"] == "yes"
        assert contract.check_prefix_fork(ctx)["answer"] == "no"
        assert len(fake.calls) == 4  # measured once, read three times

    def test_no_cache_at_all(self, chat):
        queue, _ = chat
        self._timings(queue, cold=1.7, repeat=2.4, extend=2.0, fork=2.0)
        ctx = dict(CTX)
        assert contract.check_prefix_cache(ctx)["answer"] == "no"
        assert contract.check_prefix_extend(ctx)["answer"] == "no"

    def test_a_cached_repeat_must_keep_its_prompt_size(self, chat):
        # The v0.6.1 llama.cpp lane: 27 tokens, then 0 for the identical prompt.
        queue, _ = chat
        queue += [(0.2, _reply("ok", usage={"prompt_tokens": 27}), None)] * 2
        assert contract.check_cached_prompt_tokens(CTX)["answer"] == "yes"
        queue += [
            (0.2, _reply("ok", usage={"prompt_tokens": 27}), None),
            (0.1, _reply("ok", usage={"prompt_tokens": 0}), None),
        ]
        assert contract.check_cached_prompt_tokens(CTX)["answer"] == "no"

    def test_a_stop_string_that_never_came_up_is_inconclusive(self, chat):
        queue, _ = chat
        queue.append((1.0, _reply("1 2 3 4 5 6 7 8", "stop"), None))
        assert contract.check_stop(CTX)["answer"] == "no"  # leaked
        queue.append((1.0, _reply("1 2 3 4 5 6 ", "stop"), None))
        assert contract.check_stop(CTX)["answer"] == "yes"
        queue.append((9.0, _reply("<think>counting upward from one", "length"), None))
        assert contract.check_stop(CTX)["answer"] == "inconclusive"

    def test_tool_call_must_arrive_as_tool_calls(self, chat):
        queue, _ = chat
        call = [
            {"function": {"name": "read_file", "arguments": '{"path":"README.md"}'}}
        ]
        queue.append((0.5, _reply("", "tool_calls", tool_calls=call), None))
        assert contract.check_tool_calls(CTX)["answer"] == "yes"
        queue.append(
            (0.5, _reply('<tool_call>{"name": "read_file"}</tool_call>'), None)
        )
        assert contract.check_tool_calls(CTX)["answer"] == "no"


class TestDiff:
    @staticmethod
    def _report(**answers):
        return {
            "reports": [
                {"checks": [{"id": k, "answer": v} for k, v in answers.items()]}
            ]
        }

    def test_names_every_moved_answer_in_check_order(self):
        old = self._report(
            prefix_cache="no", max_tokens_honoured="no", stream_usage="no"
        )
        new = self._report(
            prefix_cache="yes", max_tokens_honoured="yes", stream_usage="no"
        )
        rows = contract.diff(old, new)
        assert [r[0] for r in rows] == [
            "max_tokens_honoured",
            "stream_usage",
            "prefix_cache",
        ]
        assert [r[3] for r in rows] == [True, False, True]

    def test_a_check_only_one_side_ran_is_a_change(self):
        rows = contract.diff(self._report(), self._report(power_mode_understood="yes"))
        assert rows == [("power_mode_understood", "-", "yes", True)]


class TestModelDetection:
    def test_the_model_is_asked_of_the_lane_being_probed(self, monkeypatch):
        # Detection used to ask the module default (localhost:11434).
        from orchestrant.benchmark import openai_api

        asked = []
        monkeypatch.setattr(
            openai_api,
            "detect_model_via_api",
            lambda base_url=None, entry=None: asked.append(base_url) or "m",
        )
        monkeypatch.setattr(contract, "run", lambda *a, **k: [])
        monkeypatch.setattr(sys, "argv", ["contract", "--base-url", "http://lane:1"])
        assert contract.main() == 0
        assert asked == ["http://lane:1"]
