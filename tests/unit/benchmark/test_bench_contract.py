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
        fake.messages.append(messages)
        fake.timeouts.append(timeout)
        return queue.pop(0)

    fake.calls, fake.messages, fake.timeouts = [], [], []
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


class TestOutputCap:
    """Does the server stop before a 3000-token budget, and where?"""

    def _ask(self, chat, content, finish, usage):
        queue, fake = chat
        queue.append((90.0, _reply(content, finish, usage), None))
        out = contract.check_output_cap(CTX)
        assert fake.calls[-1]["max_tokens"] == 3000
        return out

    def test_a_spent_budget_is_no_cap(self, chat):
        out = self._ask(chat, "1\n2\n3", "length", {"completion_tokens": 3000})
        assert out["answer"] == "no"
        assert out["stopped_at_tokens"] == 3000

    def test_a_length_stop_short_of_the_budget_is_a_cap_and_says_where(self, chat):
        # GenieX v0.5.0: every reply cut at 2048 whatever max_tokens asked.
        usage = {"completion_tokens": 2048, "prompt_tokens": 38}
        out = self._ask(chat, "1\n2\n3", "length", usage)
        assert out["answer"] == "yes"
        assert out["stopped_at_tokens"] == 2048
        assert "prompt_tokens=38" in out["evidence"]

    def test_a_round_number_reported_as_a_finish_is_a_cap(self, chat):
        out = self._ask(chat, "1\n2\n3", "stop", {"completion_tokens": 2048})
        assert out["answer"] == "yes"

    def test_a_reply_that_finished_on_its_own_is_inconclusive(self, chat):
        # An instruct model that declines to count to 5000 stops at 40 tokens:
        # nothing about a cap can be read from that.
        out = self._ask(chat, "That is a long list.", "stop", {"completion_tokens": 40})
        assert out["answer"] == "inconclusive"
        assert out["stopped_at_tokens"] == 40

    def test_no_completion_count_is_inconclusive(self, chat):
        out = self._ask(chat, "1\n2\n3", "length", {})
        assert out["answer"] == "inconclusive"
        assert out["stopped_at_tokens"] is None

    def test_more_than_asked_is_named(self, chat):
        out = self._ask(chat, "1\n2\n3", "stop", {"completion_tokens": 3600})
        assert out["answer"] == "no"
        assert "MORE than asked" in out["evidence"]

    def test_the_long_reply_gets_a_long_timeout(self, chat):
        queue, fake = chat
        queue.append((0.1, None, "HTTP 500: boom"))
        assert contract.check_output_cap(CTX)["answer"] == "error"
        assert fake.timeouts[-1] >= 1800


class TestResponseFormat:
    """Honoured, ignored or refused -- the prompt itself never asks for JSON."""

    def _ask(self, chat, content=None, err=None):
        queue, fake = chat
        queue.append((0.5, None if err else _reply(content), err))
        out = contract.check_response_format(CTX)
        sent = fake.calls[-1]["response_format"]
        assert sent["type"] == "json_schema"
        # Room for a thinking lane: 354 tokens went on "2 + 3" (v0.7.0 CPU).
        assert fake.calls[-1]["max_tokens"] >= 1024
        assert "JSON" not in fake.messages[-1][-1]["content"]
        return out

    def test_the_schema_object_is_honoured(self, chat):
        out = self._ask(chat, '{"colour": "red", "letters": 3}')
        assert (out["answer"], out["outcome"]) == ("yes", "honoured")

    def test_prose_is_ignored(self, chat):
        out = self._ask(chat, "Red has three letters.")
        assert (out["answer"], out["outcome"]) == ("no", "ignored")

    def test_json_of_another_shape_is_a_json_mode_without_the_schema(self, chat):
        for content in (
            '{"answer": "red"}',
            '{"colour": "red", "letters": "3"}',
            '{"colour": "green", "letters": 5}',
            '{"colour": "red", "letters": true}',
        ):
            out = self._ask(chat, content)
            assert (out["answer"], out["outcome"]) == ("no", "json_only"), content

    def test_a_fenced_object_was_steered_not_constrained(self, chat):
        out = self._ask(chat, '```json\n{"colour": "blue", "letters": 4}\n```')
        assert (out["answer"], out["outcome"]) == ("no", "fenced")

    def test_a_4xx_is_refused_and_a_5xx_is_an_error(self, chat):
        out = self._ask(chat, err="HTTP 400: response_format is not supported")
        assert (out["answer"], out["outcome"]) == ("no", "refused")
        assert self._ask(chat, err="HTTP 500: boom")["answer"] == "error"

    def test_thinking_is_stripped_and_an_unclosed_block_is_no_answer(self, chat):
        out = self._ask(chat, '<think>red, 3</think>\n{"colour": "red", "letters": 3}')
        assert out["answer"] == "yes"
        out = self._ask(chat, '<think>{"colour": "red", "letters": 3}')
        assert out["answer"] == "inconclusive"


class TestBundleSystemPrompt:
    """Read from usage: an explicit system message replaces a default."""

    @staticmethod
    def _counts(chat, system, none, twice, none_again, report="NONE"):
        queue, _ = chat
        # The throwaway first: a count it carried would be read as a hidden prompt.
        queue.append((0.1, _reply("ok", usage={"prompt_tokens": 999}), None))
        for tokens in (system, none, twice, none_again):
            queue.append((0.1, _reply("ok", usage={"prompt_tokens": tokens}), None))
        queue.append((0.5, _reply(report), None))
        return contract.check_bundle_system_prompt(CTX)

    def test_without_a_default_only_the_framing_shows(self, chat):
        # 22 tokens of user turn, a 5-token system turn frame, a 14-token text.
        out = self._counts(chat, 41, 22, 55, 22)
        assert out["answer"] == "no"
        assert out["hidden_tokens"] == -5

    def test_a_default_that_an_explicit_message_replaces(self, chat):
        # The QAIRT bundle's "You are a helpful AI assistant.": 9 tokens that a
        # request with no system message carries and one with a message does not.
        out = self._counts(chat, 41, 36, 55, 36)
        assert out["answer"] == "yes"
        assert out["hidden_tokens"] == 9

    def test_the_requests_alternate_and_never_repeat(self, chat):
        _, fake = chat
        self._counts(chat, 41, 22, 55, 22)
        # A throwaway with no system turn goes first, so the first measured
        # request follows the same kind of predecessor as the other three.
        assert [m["role"] for m in fake.messages[0]] == ["user"]
        measured = fake.messages[1:5]
        roles = [m[0]["role"] for m in measured]
        assert roles == ["system", "user", "system", "user"]
        users = [m[-1]["content"] for m in fake.messages[:5]]
        assert len(set(users)) == 5
        # One run nonce: every user message is the same length, so costs the same.
        assert len({len(u) for u in users[1:]}) == 1
        assert len(measured[2][0]["content"]) > len(measured[0][0]["content"])

    def test_a_failed_throwaway_does_not_stop_the_measurement(self, chat):
        queue, _ = chat
        queue.append((0.1, None, "HTTP 500: loading"))
        for tokens in (41, 36, 55, 36):
            queue.append((0.1, _reply("ok", usage={"prompt_tokens": tokens}), None))
        queue.append((0.5, _reply("NONE"), None))
        assert contract.check_bundle_system_prompt(CTX)["answer"] == "yes"

    def test_a_system_message_that_costs_nothing_is_inconclusive(self, chat):
        # Dropped by the server, or prompt_tokens is not the prompt's size.
        out = self._counts(chat, 30, 30, 30, 30)
        assert out["answer"] == "inconclusive"
        assert out["hidden_tokens"] is None

    def test_disagreeing_no_system_counts_are_inconclusive(self, chat):
        assert self._counts(chat, 41, 22, 55, 30)["answer"] == "inconclusive"

    def test_missing_counts_are_inconclusive(self, chat):
        assert self._counts(chat, 41, 0, 55, 22)["answer"] == "inconclusive"

    def test_the_self_report_is_recorded_and_never_votes(self, chat):
        out = self._counts(chat, 41, 22, 55, 22, "You are a helpful AI assistant.")
        assert out["answer"] == "no"
        assert out["self_report"] == "You are a helpful AI assistant."
        out = self._counts(chat, 41, 22, 55, 22, "<think>the user asks whether")
        assert out["self_report"].startswith("(no answer")

    def test_a_failed_measurement_is_an_error_naming_the_request(self, chat):
        queue, _ = chat
        queue.append((0.1, _reply("ok", usage={"prompt_tokens": 30}), None))
        queue.append((0.1, _reply("ok", usage={"prompt_tokens": 41}), None))
        queue.append((0.1, None, "HTTP 500: boom"))
        out = contract.check_bundle_system_prompt(CTX)
        assert out["answer"] == "error"
        assert out["evidence"].startswith("none:")


class TestTheNewChecksAreRegistered:
    def test_every_new_check_is_in_the_run_order(self):
        ids = [c[0] for c in contract.CHECKS]
        for check_id in (
            "output_cap",
            "response_format_json_schema",
            "bundle_system_prompt",
        ):
            assert check_id in ids
        assert len(ids) == len(set(ids))


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
