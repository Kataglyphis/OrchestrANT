"""Tests for the tool-calling grader.

Same reasoning as the coding grader: a wrong grader makes every number it
produces worthless. These cases are the ones that would silently distort a
ranking -- arguments arriving as a JSON string vs a dict, a model that answers
in prose instead of calling, and a model that calls a tool when it should not.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bench_tools import CASES, TOOLS, grade  # noqa: E402

EXPECT_READ = {"name": "read_file", "args": {"path": "README.md"}}


def expects_of(case):
    e = case["expect"]
    return e if isinstance(e, list) else [e]


def msg(name, arguments, content=None):
    """Build a message the way an OpenAI-compatible server returns one."""
    return {
        "content": content,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }


class TestHappyPath:
    def test_correct_call_with_json_string_arguments(self):
        ok, _ = grade(msg("read_file", '{"path": "README.md"}'), EXPECT_READ)
        assert ok

    def test_arguments_may_arrive_as_a_dict(self):
        # Not every server stringifies them; grading must not depend on that.
        ok, _ = grade(msg("read_file", {"path": "README.md"}), EXPECT_READ)
        assert ok

    def test_tolerates_a_leading_dot_slash(self):
        # "./README.md" is the same file; blaming the model for it would
        # measure formatting, not capability.
        ok, _ = grade(msg("read_file", '{"path": "./README.md"}'), EXPECT_READ)
        assert ok

    def test_tool_with_no_required_arguments(self):
        ok, _ = grade(msg("run_tests", "{}"), {"name": "run_tests", "args": {}})
        assert ok


class TestFailures:
    def test_wrong_tool_is_caught(self):
        ok, detail = grade(msg("list_files", '{"directory": "."}'), EXPECT_READ)
        assert not ok and "expected 'read_file'" in detail

    def test_missing_required_argument(self):
        ok, detail = grade(msg("read_file", "{}"), EXPECT_READ)
        assert not ok and "missing argument" in detail

    def test_wrong_argument_value(self):
        ok, detail = grade(msg("read_file", '{"path": "LICENSE"}'), EXPECT_READ)
        assert not ok and "expected" in detail

    def test_invalid_json_arguments(self):
        ok, detail = grade(msg("read_file", '{"path": '), EXPECT_READ)
        assert not ok and "not valid JSON" in detail

    def test_prose_instead_of_a_call(self):
        # The classic failure: the model describes the call instead of making it.
        ok, detail = grade(
            {"content": "I would call read_file with README.md", "tool_calls": []},
            EXPECT_READ,
        )
        assert not ok and "no tool call" in detail

    def test_several_calls_when_one_was_expected(self):
        m = msg("read_file", '{"path": "README.md"}')
        m["tool_calls"].append(m["tool_calls"][0])
        ok, detail = grade(m, EXPECT_READ)
        assert not ok and "2 tool calls" in detail

    def test_boolean_argument_must_match_exactly(self):
        expect = {
            "name": "search_code",
            "args": {"query": "Foo", "case_sensitive": True},
        }
        ok, _ = grade(
            msg("search_code", '{"query": "Foo", "case_sensitive": false}'), expect
        )
        assert not ok


class TestNoToolExpected:
    def test_answering_directly_is_correct(self):
        ok, _ = grade({"content": "4", "tool_calls": []}, None)
        assert ok

    def test_calling_a_tool_anyway_is_wrong(self):
        # Over-eager tool use burns a round trip and can spin an agent loop.
        ok, detail = grade(msg("run_tests", "{}"), None)
        assert not ok and "when none was needed" in detail


class TestSuiteShape:
    def test_every_expected_tool_exists_in_the_advertised_set(self):
        names = {t["function"]["name"] for t in TOOLS}
        for case in CASES:
            if case["expect"]:
                for exp in expects_of(case):
                    assert exp["name"] in names, case["name"]

    def test_the_suite_includes_a_negative_case(self):
        assert any(c["expect"] is None for c in CASES), (
            "without a 'no tool needed' case, over-eager calling goes unmeasured"
        )

    def test_case_names_are_unique(self):
        from bench_tools import MULTI_CASES

        names = [c["name"] for c in CASES] + [c["name"] for c in MULTI_CASES]
        assert len(names) == len(set(names))

    def test_the_suite_is_large_enough_to_prove_a_regression(self):
        # The number that motivated the expansion: at 8 cases a real 8/8 -> 6/8
        # degradation was NOT provable. 27 brings the smallest provable drop to
        # roughly 100% -> 75%, which is the point of having a tripwire at all.
        from orchestrant.benchmark.stats import smallest_separable_rate
        from bench_tools import MULTI_CASES

        n = len(CASES) + len(MULTI_CASES)
        assert n >= 27, f"only {n} cases — too few to prove a 25-point drop"
        assert smallest_separable_rate(n) >= 0.70

    def test_several_tools_are_near_neighbours(self):
        # Selection is only tested if some tools are genuinely confusable;
        # with all-distinct tools a model can succeed by elimination.
        names = {t["function"]["name"] for t in TOOLS}
        for pair in (
            ("write_file", "apply_patch"),
            ("git_status", "git_diff"),
            ("read_file", "list_files"),
        ):
            assert set(pair) <= names, pair

    def test_restraint_is_measured_more_than_once(self):
        # One negative case out of 27 would let a tool-happy model score well.
        assert sum(1 for c in CASES if c["expect"] is None) >= 3


class TestMultiCaseShape:
    def test_every_multi_case_has_a_known_grader(self):
        from bench_tools import MULTI_CASES

        for case in MULTI_CASES:
            assert case["kind"] in ("use_result", "recover"), case["name"]

    def test_use_result_cases_state_what_must_appear(self):
        from bench_tools import MULTI_CASES

        for case in MULTI_CASES:
            if case["kind"] == "use_result":
                assert case.get("must_contain"), case["name"]
                # The token must be in a tool result and NOT in the prompt, or
                # the case is unpassable — or passable without reading it.
                results = "\n".join(
                    m["content"] for m in case["history"] if m["role"] == "tool"
                )
                for token in case["must_contain"]:
                    assert token in results, (
                        f"{case['name']}: {token!r} not in any tool result"
                    )
                    assert token not in case["history"][0]["content"], case["name"]

    def test_recover_cases_feed_back_an_actual_error(self):
        from bench_tools import MULTI_CASES

        for case in MULTI_CASES:
            if case["kind"] == "recover":
                assert "error" in case["history"][-1]["content"].lower(), case["name"]

    def test_histories_are_well_formed(self):
        # One user turn, then (assistant call, tool result) pairs whose ids match.
        from bench_tools import MULTI_CASES

        for case in MULTI_CASES:
            roles = [m["role"] for m in case["history"]]
            assert roles[0] == "user" and len(roles) % 2 == 1, case["name"]
            assert roles[1:] == ["assistant", "tool"] * (len(roles) // 2), case["name"]
            for call_msg, result_msg in zip(
                case["history"][1::2], case["history"][2::2]
            ):
                assert result_msg["tool_call_id"] == call_msg["tool_calls"][0]["id"], (
                    case["name"]
                )

    def test_deep_history_case_has_five_tool_turns(self):
        from bench_tools import MULTI_CASES

        deep = [c for c in MULTI_CASES if c["category"] == "deep_history"]
        assert deep and all(len(c["history"]) >= 11 for c in deep)

    def test_long_result_case_is_long_and_holds_the_fact_once(self):
        # ~2k tokens of tool output, and the graded fact appears nowhere but in it.
        from bench_tools import MULTI_CASES

        long = [c for c in MULTI_CASES if c["category"] == "long_result"]
        assert long
        for case in long:
            result = case["history"][-1]["content"]
            assert 7000 <= len(result) <= 12000, len(result)
            assert result.count("FAILED") >= 1

    def test_repeated_error_case_fails_the_same_call_twice(self):
        from bench_tools import MULTI_CASES

        rep = [c for c in MULTI_CASES if c["category"] == "repeated_error"]
        assert rep
        for case in rep:
            calls = [
                m["tool_calls"][0]["function"]
                for m in case["history"]
                if m["role"] == "assistant"
            ]
            assert (
                len(calls) >= 2
                and len({(c["name"], c["arguments"]) for c in calls}) == 1
            )
            assert all(
                "error" in m["content"].lower()
                for m in case["history"]
                if m["role"] == "tool"
            )

    def test_both_kinds_are_represented(self):
        from bench_tools import MULTI_CASES

        kinds = {c["kind"] for c in MULTI_CASES}
        assert kinds == {"use_result", "recover"}


class TestMultiTurn:
    """Single-turn scores cannot see whether a model USES what a tool returned.
    A model that emits one perfect call and then ignores the result is useless
    in a loop, and that is the failure agents actually hit."""

    def test_uses_the_returned_value(self):
        from bench_tools import grade_followup

        ok, _ = grade_followup(
            {"content": "The file contains version 9.4.1-rc2."}, ["9.4.1"]
        )
        assert ok

    def test_ignoring_the_result_fails(self):
        from bench_tools import grade_followup

        ok, detail = grade_followup({"content": "I have read the file."}, ["9.4.1"])
        assert not ok and "does not mention" in detail

    def test_calling_another_tool_instead_of_answering_fails(self):
        from bench_tools import grade_followup

        ok, detail = grade_followup(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "x",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
            ["9.4.1"],
        )
        assert not ok and "another tool" in detail

    def test_empty_reply_fails(self):
        from bench_tools import grade_followup

        ok, detail = grade_followup({"content": "  "}, ["9.4.1"])
        assert not ok and "empty" in detail

    def test_match_is_case_insensitive(self):
        from bench_tools import grade_followup

        assert grade_followup({"content": "VERSION 9.4.1-RC2"}, ["9.4.1"])[0]


class TestErrorRecovery:
    """A tool failed. Admitting it or retrying is fine; inventing the contents
    of a file that could not be read is the dangerous answer."""

    def test_admitting_the_failure_passes(self):
        from bench_tools import grade_error_recovery

        for reply in (
            "The file does not exist.",
            "I could not read it.",
            "Error: no such file.",
            "Unable to open that path.",
        ):
            assert grade_error_recovery({"content": reply})[0], reply

    def test_retrying_with_another_call_passes(self):
        from bench_tools import grade_error_recovery

        ok, detail = grade_error_recovery(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "y",
                        "type": "function",
                        "function": {"name": "list_files", "arguments": "{}"},
                    }
                ],
            }
        )
        assert ok and "retried" in detail

    def test_inventing_content_fails(self):
        # The failure that matters: answering as though the read had succeeded.
        from bench_tools import grade_error_recovery

        ok, detail = grade_error_recovery(
            {"content": "The config contains the database password and two API keys."}
        )
        assert not ok and "ignored the error" in detail

    def test_empty_reply_fails(self):
        from bench_tools import grade_error_recovery

        ok, detail = grade_error_recovery({"content": ""})
        assert not ok and "empty" in detail


class TestTextJsonFallback:
    """Three models from three vendors emit the right tool name and arguments
    as prose. The fallback measures what an agent-side parser would recover —
    and must never manufacture a call the model did not actually describe."""

    def _msg(self, text):
        return {"content": text, "tool_calls": []}

    def test_recovers_a_parameters_shaped_call(self):
        m = self._msg('{"name": "read_file", "parameters": {"path": "README.md"}}')
        assert not grade(m, EXPECT_READ)[0], "off by default"
        ok, detail = grade(m, EXPECT_READ, accept_text_json=True)
        assert ok and "recovered from text" in detail

    def test_recovers_an_arguments_shaped_call(self):
        m = self._msg('{"name": "read_file", "arguments": {"path": "README.md"}}')
        assert grade(m, EXPECT_READ, accept_text_json=True)[0]

    def test_recovers_from_surrounding_prose(self):
        m = self._msg(
            'Sure! I will call:\n{"name": "read_file", '
            '"parameters": {"path": "README.md"}}\nLet me know.'
        )
        assert grade(m, EXPECT_READ, accept_text_json=True)[0]

    def test_stringified_arguments_are_parsed(self):
        m = self._msg(
            '{"name": "read_file", "arguments": "{\\"path\\": \\"README.md\\"}"}'
        )
        assert grade(m, EXPECT_READ, accept_text_json=True)[0]

    def test_a_wrong_tool_in_text_is_still_wrong(self):
        # The fallback fixes the CHANNEL, never the answer.
        m = self._msg('{"name": "list_files", "parameters": {"directory": "."}}')
        assert not grade(m, EXPECT_READ, accept_text_json=True)[0]

    def test_wrong_arguments_in_text_are_still_wrong(self):
        m = self._msg('{"name": "read_file", "parameters": {"path": "LICENSE"}}')
        assert not grade(m, EXPECT_READ, accept_text_json=True)[0]

    def test_prose_without_json_recovers_nothing(self):
        m = self._msg("I would read README.md for you.")
        assert not grade(m, EXPECT_READ, accept_text_json=True)[0]

    def test_json_without_a_name_recovers_nothing(self):
        # Must not invent a call out of an unrelated JSON object.
        m = self._msg('{"path": "README.md"}')
        assert not grade(m, EXPECT_READ, accept_text_json=True)[0]

    def test_the_no_tool_case_is_unaffected(self):
        # A model that answers "4" must still pass, and one that describes a
        # call in text must still count as calling one.
        assert grade(self._msg("4"), None, accept_text_json=True)[0]
        assert not grade(
            self._msg('{"name": "run_tests", "parameters": {}}'),
            None,
            accept_text_json=True,
        )[0]


class TestTypedArguments:
    """The schema declares a type; 1 is not True and '40' is not 40."""

    def test_integer_one_is_not_true(self):
        expect = {
            "name": "search_code",
            "args": {"query": "Foo", "case_sensitive": True},
        }
        ok, detail = grade(
            msg("search_code", '{"query": "Foo", "case_sensitive": 1}'), expect
        )
        assert not ok and "boolean" in detail

    def test_string_true_is_not_true(self):
        expect = {
            "name": "search_code",
            "args": {"query": "Foo", "case_sensitive": True},
        }
        assert not grade(
            msg("search_code", '{"query": "Foo", "case_sensitive": "true"}'), expect
        )[0]

    def test_string_digits_are_not_an_integer(self):
        expect = {
            "name": "read_file",
            "args": {"path": "CHANGELOG.md", "max_lines": 40},
        }
        assert not grade(
            msg("read_file", '{"path": "CHANGELOG.md", "max_lines": "40"}'), expect
        )[0]
        assert grade(
            msg("read_file", '{"path": "CHANGELOG.md", "max_lines": 40}'), expect
        )[0]

    def test_true_is_not_an_integer(self):
        expect = {"name": "read_file", "args": {"path": "CHANGELOG.md", "max_lines": 1}}
        assert not grade(
            msg("read_file", '{"path": "CHANGELOG.md", "max_lines": true}'), expect
        )[0]

    def test_enum_value_outside_the_set_is_named(self):
        expect = {"name": "run_tests", "args": {"verbosity": "verbose"}}
        ok, detail = grade(msg("run_tests", '{"verbosity": "loud"}'), expect)
        assert not ok and "not one of" in detail
        assert grade(msg("run_tests", '{"verbosity": "verbose"}'), expect)[0]

    def test_array_is_order_insensitive_but_typed(self):
        expect = {
            "name": "search_code",
            "args": {"query": "TODO", "include": ["*.py", "*.md"]},
        }
        assert grade(
            msg("search_code", '{"query": "TODO", "include": ["*.md", "*.py"]}'), expect
        )[0]
        assert not grade(
            msg("search_code", '{"query": "TODO", "include": "*.py,*.md"}'), expect
        )[0]
        assert not grade(
            msg("search_code", '{"query": "TODO", "include": ["*.py"]}'), expect
        )[0]

    def test_every_typed_case_uses_a_declared_type(self):
        # Every graded argument of a typed case must be declared in the schema,
        # or the type check is silently skipped.
        for case in CASES:
            if case["category"] != "typed_args":
                continue
            for exp in expects_of(case):
                props = next(
                    t["function"]["parameters"]["properties"]
                    for t in TOOLS
                    if t["function"]["name"] == exp["name"]
                )
                for key in exp["args"]:
                    assert props[key].get("type"), f"{case['name']}: {key} untyped"

    def test_tools_cover_int_bool_enum_and_array(self):
        kinds = set()
        for t in TOOLS:
            for p in t["function"]["parameters"]["properties"].values():
                kinds.add("enum" if p.get("enum") else p["type"])
        assert {"integer", "boolean", "string", "array", "enum"} <= kinds


class TestPathLeniency:
    """Exactly one './' and one trailing '/', on path-like arguments only."""

    def test_trailing_slash_on_a_directory(self):
        expect = {"name": "list_files", "args": {"directory": "src"}}
        assert grade(msg("list_files", '{"directory": "src/"}'), expect)[0]

    def test_a_trailing_dot_on_content_is_wrong(self):
        expect = {
            "name": "write_file",
            "args": {"path": "notes.txt", "content": "done"},
        }
        for content in ("done.", "done/", ".done", "./done"):
            assert not grade(
                msg(
                    "write_file", json.dumps({"path": "notes.txt", "content": content})
                ),
                expect,
            )[0], content
        assert grade(
            msg("write_file", '{"path": "notes.txt", "content": "done"}'), expect
        )[0]

    def test_a_slashed_query_is_wrong(self):
        expect = {"name": "search_code", "args": {"query": "__init__.py"}}
        assert not grade(msg("search_code", '{"query": "/__init__.py/"}'), expect)[0]
        assert not grade(msg("search_code", '{"query": "./__init__.py"}'), expect)[0]

    def test_only_one_dot_slash_is_forgiven(self):
        assert grade(msg("read_file", '{"path": "./README.md"}'), EXPECT_READ)[0]
        assert not grade(msg("read_file", '{"path": "././README.md"}'), EXPECT_READ)[0]
        assert not grade(msg("read_file", '{"path": "..README.md"}'), EXPECT_READ)[0]

    def test_patch_requires_the_diff(self):
        case = next(c for c in CASES if c["name"] == "patch_not_overwrite")
        ok, detail = grade(msg("apply_patch", '{"path": "app.py"}'), case["expect"])
        assert not ok and "diff" in detail
        ok, detail = grade(
            msg("apply_patch", '{"path": "app.py", "diff": "--- a\\n+++ b\\n"}'),
            case["expect"],
        )
        assert not ok and "does not contain" in detail
        diff = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
        assert grade(
            msg("apply_patch", json.dumps({"path": "app.py", "diff": diff})),
            case["expect"],
        )[0]


class TestParallelCalls:
    """N expected calls means exactly N calls, in any order."""

    EXPECT = [
        {"name": "read_file", "args": {"path": "README.md"}},
        {"name": "read_file", "args": {"path": "LICENSE"}},
    ]

    def _two(self, first, second):
        m = msg(*first)
        m["tool_calls"].append(msg(*second)["tool_calls"][0])
        return m

    def test_both_calls_in_either_order(self):
        a = ("read_file", '{"path": "README.md"}')
        b = ("read_file", '{"path": "LICENSE"}')
        assert grade(self._two(a, b), self.EXPECT)[0]
        assert grade(self._two(b, a), self.EXPECT)[0]

    def test_only_one_of_two_fails(self):
        ok, detail = grade(msg("read_file", '{"path": "README.md"}'), self.EXPECT)
        assert not ok and "1 tool calls, expected 2" in detail

    def test_the_same_call_twice_fails(self):
        a = ("read_file", '{"path": "README.md"}')
        ok, detail = grade(self._two(a, a), self.EXPECT)
        assert not ok and "LICENSE" in detail

    def test_three_calls_for_two_expected_fails(self):
        m = self._two(
            ("read_file", '{"path": "README.md"}'), ("read_file", '{"path": "LICENSE"}')
        )
        m["tool_calls"].append(msg("git_status", "{}")["tool_calls"][0])
        assert not grade(m, self.EXPECT)[0]

    def test_parallel_multiple_mixes_tools(self):
        expect = [
            {"name": "read_file", "args": {"path": "setup.py"}},
            {"name": "git_status", "args": {}},
        ]
        m = self._two(("git_status", "{}"), ("read_file", '{"path": "setup.py"}'))
        assert grade(m, expect)[0]
        m = self._two(("git_diff", "{}"), ("read_file", '{"path": "setup.py"}'))
        ok, detail = grade(m, expect)
        assert not ok and "git_status" in detail

    def test_suite_has_parallel_and_parallel_multiple_cases(self):
        par = [c for c in CASES if c["category"] == "parallel"]
        assert len(par) >= 3
        multiple = [c for c in par if len({e["name"] for e in c["expect"]}) > 1]
        same = [c for c in par if len({e["name"] for e in c["expect"]}) == 1]
        assert multiple and same


class TestRestraintEmptyReply:
    def test_empty_none_and_whitespace_all_fail(self):
        for content in ("", None, "   \n"):
            ok, detail = grade({"content": content, "tool_calls": []}, None)
            assert not ok and detail == "empty reply", repr(content)


class TestIrrelevanceAndVariants:
    STOP = {
        "a",
        "an",
        "the",
        "of",
        "in",
        "to",
        "it",
        "its",
        "with",
        "and",
        "or",
        "for",
        "not",
        "does",
        "do",
        "is",
        "are",
        "be",
        "by",
        "them",
        "themselves",
        "which",
        "that",
        "this",
        "me",
        "my",
        "i",
        "you",
        "your",
        "so",
        "far",
        "from",
        "on",
    }

    def _content_words(self, text):
        return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in self.STOP}

    def test_irrelevance_prompts_never_mention_tools(self):
        cases = [c for c in CASES if c["category"] == "irrelevance"]
        assert 3 <= len(cases) <= 4
        for c in cases:
            assert c["expect"] is None
            assert not re.search(r"\btools?\b|\bcall", c["prompt"], re.I), c["name"]

    def test_instructed_restraint_is_still_measured(self):
        assert sum(1 for c in CASES if c["category"] == "restraint") >= 3

    def test_selection_variants_share_under_two_content_words_with_the_description(
        self,
    ):
        by_name = {t["function"]["name"]: t["function"]["description"] for t in TOOLS}
        seen = 0
        for case in CASES:
            if case["category"] != "selection":
                continue
            assert case.get("variants"), f"{case['name']} has no paraphrase"
            desc = self._content_words(by_name[case["expect"]["name"]])
            for v in case["variants"]:
                shared = self._content_words(v) & desc
                assert len(shared) < 2, f"{case['name']}: {sorted(shared)}"
                seen += 1
        assert seen >= 7

    def test_every_case_has_a_category(self):
        from bench_tools import MULTI_CASES

        for c in CASES + MULTI_CASES:
            assert c.get("category"), c["name"]


class TestErrorRecoveryHistory:
    """An identical retry is a wasted round trip; the admit words must be the
    model's own and whole."""

    HISTORY = [
        {"role": "user", "content": "What is in config/secret.yaml?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "config/secret.yaml"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "Error: ENOENT: no such file or directory",
        },
    ]

    def _retry(self, name, arguments):
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": "r",
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        }

    def test_identical_retry_fails(self):
        from bench_tools import grade_error_recovery

        ok, detail = grade_error_recovery(
            self._retry("read_file", '{"path": "config/secret.yaml"}'), self.HISTORY
        )
        assert not ok and "identical" in detail

    def test_identical_retry_with_dot_slash_still_fails(self):
        from bench_tools import grade_error_recovery

        ok, _ = grade_error_recovery(
            self._retry("read_file", '{"path": "./config/secret.yaml"}'), self.HISTORY
        )
        assert not ok

    def test_different_call_passes(self):
        from bench_tools import grade_error_recovery

        ok, detail = grade_error_recovery(
            self._retry("list_files", '{"directory": "config"}'), self.HISTORY
        )
        assert ok and "retried" in detail

    def test_third_identical_call_in_repeated_error_case_fails(self):
        from bench_tools import MULTI_CASES, grade_error_recovery

        case = next(c for c in MULTI_CASES if c["category"] == "repeated_error")
        failed = case["history"][1]["tool_calls"][0]["function"]
        ok, _ = grade_error_recovery(
            self._retry(failed["name"], failed["arguments"]), case["history"]
        )
        assert not ok

    def test_admit_words_are_word_bounded(self):
        from bench_tools import grade_error_recovery

        invented = (
            "Here is the config:\nerror_reporting: true\n"
            "missing_value_policy: strict\ndatabase: prod"
        )
        ok, detail = grade_error_recovery({"content": invented}, self.HISTORY)
        assert not ok and "ignored the error" in detail

    def test_quoting_the_tool_output_alone_is_not_an_admission(self):
        from bench_tools import grade_error_recovery

        ok, detail = grade_error_recovery(
            {"content": "Error: ENOENT: no such file or directory"}, self.HISTORY
        )
        assert not ok and "quoted" in detail
        ok, _ = grade_error_recovery(
            {
                "content": "> Error: ENOENT: no such file or directory\nThe secrets are: a, b."
            },
            self.HISTORY,
        )
        assert not ok

    def test_own_admission_beside_a_quote_passes(self):
        from bench_tools import grade_error_recovery

        ok, _ = grade_error_recovery(
            {
                "content": "The tool said `Error: ENOENT: no such file or directory`, "
                "so the file does not exist."
            },
            self.HISTORY,
        )
        assert ok

    def test_invented_fenced_block_fails_even_with_an_admission(self):
        from bench_tools import grade_error_recovery

        reply = (
            "The read failed, but the file probably contains:\n"
            "```yaml\ndb_password: hunter2\n```"
        )
        ok, detail = grade_error_recovery({"content": reply}, self.HISTORY)
        assert not ok and "invented" in detail

    def test_history_is_optional(self):
        from bench_tools import grade_error_recovery

        assert grade_error_recovery({"content": "The file was not found."})[0]


class TestTextJsonInMultiTurn:
    def test_followup_written_as_a_text_call_fails_under_the_flag(self):
        from bench_tools import grade_followup

        m = {
            "content": '{"name": "read_file", "parameters": {"path": "VERSION.txt"}} 9.4.1',
            "tool_calls": [],
        }
        assert grade_followup(m, ["9.4.1"])[0], "off: the text is just an answer"
        ok, detail = grade_followup(m, ["9.4.1"], accept_text_json=True)
        assert not ok and "another tool" in detail

    def test_text_retry_passes_recovery_under_the_flag(self):
        from bench_tools import grade_error_recovery

        m = {
            "content": '{"name": "list_files", "parameters": {"directory": "config"}}',
            "tool_calls": [],
        }
        assert not grade_error_recovery(m, TestErrorRecoveryHistory.HISTORY)[0]
        ok, detail = grade_error_recovery(
            m, TestErrorRecoveryHistory.HISTORY, accept_text_json=True
        )
        assert ok and "retried" in detail

    def test_text_identical_retry_fails_under_the_flag(self):
        from bench_tools import grade_error_recovery

        m = {
            "content": '{"name": "read_file", "parameters": {"path": "config/secret.yaml"}}',
            "tool_calls": [],
        }
        ok, detail = grade_error_recovery(
            m, TestErrorRecoveryHistory.HISTORY, accept_text_json=True
        )
        assert not ok and "identical" in detail


class TestQwenTemplateSalvage:
    def _msg(self, text):
        return {"content": text, "tool_calls": []}

    def test_two_blocks_yield_two_calls(self):
        from bench_tools import _tool_calls_from_text

        text = (
            "<tool_call>\n<function=read_file>\n<parameter=path>\nREADME.md\n</parameter>\n"
            "</function>\n</tool_call>\n<tool_call>\n<function=read_file>\n"
            "<parameter=path>\nLICENSE\n</parameter>\n</function>\n</tool_call>"
        )
        calls = _tool_calls_from_text(self._msg(text), TOOLS)
        assert [json.loads(c["function"]["arguments"])["path"] for c in calls] == [
            "README.md",
            "LICENSE",
        ]
        # And grade() sees two calls, exactly as the native channel would.
        ok, detail = grade(self._msg(text), EXPECT_READ, accept_text_json=True)
        assert not ok and "2 tool calls" in detail
        assert grade(self._msg(text), TestParallelCalls.EXPECT, accept_text_json=True)[
            0
        ]

    def test_values_are_coerced_by_the_schema_not_by_shape(self):
        from bench_tools import _tool_calls_from_text

        text = (
            "<tool_call><function=search_code><parameter=query>123</parameter>"
            "<parameter=case_sensitive>true</parameter></function></tool_call>"
        )
        (c,) = _tool_calls_from_text(self._msg(text), TOOLS)
        assert json.loads(c["function"]["arguments"]) == {
            "query": "123",
            "case_sensitive": True,
        }

    def test_a_json_list_recovers_several_calls(self):
        text = (
            '[{"name": "read_file", "parameters": {"path": "README.md"}}, '
            '{"name": "read_file", "parameters": {"path": "LICENSE"}}]'
        )
        assert grade(self._msg(text), TestParallelCalls.EXPECT, accept_text_json=True)[
            0
        ]


class TestContextPadding:
    def test_padding_never_contains_the_answer_key(self):
        from bench_tools import CASES, MULTI_CASES, PAD_SOURCES, context_padding

        assert (
            "bench_tools.py" not in PAD_SOURCES
            and "tools_opencode.py" not in PAD_SOURCES
        )
        pad = context_padding(64000)
        assert len(pad) > 100000
        for case in CASES:
            assert case["prompt"] not in pad, case["name"]
            assert json.dumps(case["expect"], sort_keys=True) not in pad, case["name"]
        for case in MULTI_CASES:
            assert case["history"][0]["content"] not in pad, case["name"]
        assert "# ---- bench_tools.py ----" not in pad


class TestOpencodeToolSet:
    def test_ten_tools_at_a_realistic_length(self):
        import tools_opencode as oc

        names = [t["function"]["name"] for t in oc.TOOLS]
        assert len(names) == 10 and len(set(names)) == 10
        assert {"bash", "read", "edit", "write", "grep", "glob"} <= set(names)
        assert 4000 <= oc.approx_tokens() <= 7000, oc.approx_tokens()
        assert "APPROXIMATION" in oc.__doc__ and "approximation" in oc.SOURCE

    def test_required_parameters_exist(self):
        import tools_opencode as oc

        for t in oc.TOOLS:
            params = t["function"]["parameters"]
            assert set(params["required"]) <= set(params["properties"]), t["function"][
                "name"
            ]

    def test_every_translation_names_an_advertised_tool(self):
        import tools_opencode as oc

        names = {t["function"]["name"] for t in oc.TOOLS}
        translated = 0
        for case in CASES:
            exp = oc.translate_expect(case["expect"])
            if exp is oc.UNTRANSLATABLE or exp is None:
                continue
            translated += 1
            for e in exp if isinstance(exp, list) else [exp]:
                assert e["name"] in names, case["name"]
                props = next(
                    t["function"]["parameters"]["properties"]
                    for t in oc.TOOLS
                    if t["function"]["name"] == e["name"]
                )
                assert set(e["args"]) <= set(props), (case["name"], e)
        assert translated >= 15

    def test_translated_history_uses_advertised_tools(self):
        import tools_opencode as oc
        from bench_tools import MULTI_CASES, _translate_history

        names = {t["function"]["name"] for t in oc.TOOLS}
        for case in MULTI_CASES:
            for m in _translate_history(case["history"]):
                for c in m.get("tool_calls") or []:
                    assert c["function"]["name"] in names, case["name"]

    def test_translated_patch_grades_an_edit(self):
        import tools_opencode as oc

        case = next(c for c in CASES if c["name"] == "patch_not_overwrite")
        exp = oc.translate_expect(case["expect"])
        m = msg(
            "edit",
            json.dumps(
                {"filePath": "app.py", "oldString": "x = 1", "newString": "x = 2"}
            ),
        )
        assert grade(m, exp, tools=oc.TOOLS)[0]
        m = msg(
            "edit",
            json.dumps(
                {"filePath": "app.py", "oldString": "x = 1", "newString": "x = 3"}
            ),
        )
        assert not grade(m, exp, tools=oc.TOOLS)[0]
