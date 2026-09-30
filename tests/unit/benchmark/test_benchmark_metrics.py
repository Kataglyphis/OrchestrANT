"""Unit tests for the harness's answer matching (LB1): a value inside a <think> block never counts."""

from orchestrant.benchmark.correctness import _answer_matches


class TestAnswerMatching:
    def test_plain_match(self):
        assert _answer_matches("248171", ["248171"])

    def test_case_insensitive(self):
        assert _answer_matches("Canberra", ["canberra"])

    def test_ignores_thousands_separator(self):
        assert _answer_matches("The answer is 248,171.", ["248171"])

    def test_strips_markdown_emphasis(self):
        assert _answer_matches("**289**", ["289"])

    def test_thinking_block_is_stripped(self):
        # The model reasons its way to a WRONG value, then answers correctly.
        content = "<think>maybe it is 5? no...</think>\n3"
        assert _answer_matches(content, ["3"])

    def test_wrong_answer_after_correct_thinking_fails(self):
        # The right number only inside the thinking, with a wrong answer, must not count.
        content = "<think>it should be 3 r's</think>\nThe answer is 2."
        assert not _answer_matches(content, ["3"])

    def test_word_boundary_prevents_substring_hit(self):
        assert not _answer_matches("The answer is 13", ["3"])
        assert not _answer_matches("0.31", ["3"])

    def test_decimal_expected_value_is_exact(self):
        assert _answer_matches("9.9", ["9.9"])
        assert not _answer_matches("9.11", ["9.9"])

    def test_multiple_accepted_forms(self):
        assert _answer_matches("five", ["5", "five"])

    def test_garbage_output_scores_wrong(self):
        # Fluent nonsense from broken i-quant kernels, which every speed metric rates as a good run.
        assert not _answer_matches("\n\n\n....\n\n", ["3"])
        assert not _answer_matches(
            " majorityathersyrelicht reconciliation", ["canberra"]
        )

    def test_empty_content(self):
        assert not _answer_matches("", ["3"])

    def test_trailing_period_does_not_break_match(self):
        # A sentence-ending period must not make a correct answer wrong.
        assert _answer_matches("The answer is 248,171.", ["248171"])
        assert _answer_matches("It is 9.9.", ["9.9"])

    def test_decimal_continuation_still_blocks(self):
        # ...but ".<digit>" must still block: 3 != 3.5
        assert not _answer_matches("The answer is 3.5", ["3"])

    def test_unclosed_think_block_is_not_correct(self):
        # Out of budget mid-thought: the value appears but was never answered; counting it would hide truncation.
        content = "<think>Let me compute 847*293 = 248171, but wait, let me"
        assert not _answer_matches(content, ["248171"])

    def test_closed_think_block_still_works(self):
        content = "<think>847*293 = 248171</think>248171"
        assert _answer_matches(content, ["248171"])
