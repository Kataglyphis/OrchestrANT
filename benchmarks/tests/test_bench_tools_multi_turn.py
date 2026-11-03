"""Tests for the tool-calling grader's multi-turn cases, error recovery among them."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMultiTurn:
    """Does the model USE what a tool returned, which single-turn scores cannot see?"""

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


class TestErrorRecoveryHistory:
    """An identical retry fails, and the admit words must be the model's own and whole."""

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
    def test_followup_written_as_a_text_call_fails_with_or_without_the_flag(self):
        from bench_tools import grade_followup

        m = {
            "content": '{"name": "read_file", "parameters": {"path": "VERSION.txt"}} 9.4.1',
            "tool_calls": [],
        }
        # Naming the fact beside a call is not answering from the result.
        ok, detail = grade_followup(m, ["9.4.1"])
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
