"""Tests for the coding grader.

If the grader is wrong, every measurement it produces is worthless -- so the
cases below are the ones that would silently corrupt a ranking: code hidden in
a <think> block, a plausible-but-wrong implementation, an infinite loop, and a
reply with no code at all.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bench_coding import TASKS, extract_code, run_candidate  # noqa: E402

MERGE = next(t for t in TASKS if t["name"] == "merge_sorted")
BALANCED = next(t for t in TASKS if t["name"] == "balanced")

GOOD_MERGE = """
def merge_sorted(a: list, b: list) -> list:
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            out.append(a[i]); i += 1
        else:
            out.append(b[j]); j += 1
    out.extend(a[i:]); out.extend(b[j:])
    return out
"""


class TestExtraction:
    def test_fenced_python_block(self):
        assert "def f" in extract_code("blah\n```python\ndef f(): pass\n```\ndone")

    def test_unfenced_code_is_still_graded(self):
        # Refusing bare code would measure formatting compliance, not ability.
        assert extract_code("Here you go:\ndef f():\n    return 1").startswith("def f")

    def test_thinking_block_is_dropped(self):
        # A discarded draft inside <think> must never be graded instead of the
        # real answer -- that would score a model on code it rejected.
        # The draft is deliberately LONGER than the answer and defines the same
        # function: the earlier version of this test had a shorter draft, so
        # "prefer the longest defining block" picked the answer with the
        # stripping disabled and the test proved nothing.
        draft = "def merge_sorted(a, b):\n    # first attempt -- wrong\n    return None"
        text = (
            f"<think>\n```python\n{draft}\n```\n</think>\n"
            "```python\ndef merge_sorted(a,b): return a+b\n```"
        )
        assert (
            extract_code(text, want="merge_sorted")
            == "def merge_sorted(a,b): return a+b"
        )

    def test_unclosed_thinking_block_yields_nothing(self):
        # Cut off mid-thought: everything is reasoning, and a complete-looking
        # draft in there was being graded PASS at the generation cap.
        text = (
            "<think>\nLet me try:\n```python\ndef merge_sorted(a,b): return a+b\n```\n"
        )
        assert extract_code(text, want="merge_sorted") == ""

    def test_prefers_the_block_defining_the_required_function(self):
        # The rule this replaced ("longest block wins") was measured wrong:
        # models answer with a compact function plus a longer usage block, the
        # demo got extracted, the function was never defined, and the hidden
        # tests died with NameError — scoring a correct model as a failure.
        # The second block must ALSO define a function, or the plain
        # "any block with a def" fallback already picks the right one and the
        # `want` branch is never exercised -- which is how this test passed
        # with that branch deleted.
        text = (
            "```python\ndef merge_sorted(a, b):\n    return a\n```\n"
            "Example:\n```python\ndef demo():\n"
            "    print(merge_sorted([1], [2]))\n"
            "    print(merge_sorted([3], [4]))\n"
            "    print(merge_sorted([5], [6]))\n```"
        )
        assert extract_code(text, want="merge_sorted").startswith("def merge_sorted")

    def test_falls_back_to_a_block_containing_any_def(self):
        # Without a name to look for, a block that defines something still beats
        # a longer block that only calls things.
        text = (
            "```python\ndef f():\n    return 42\n```\n"
            "```python\nprint(1)\nprint(2)\nprint(3)\nprint(4)\nprint(5)\n```"
        )
        assert "def f" in extract_code(text)

    def test_longest_wins_only_when_no_block_defines_anything(self):
        text = "```python\nx=1\n```\n```python\ny=2\nz=3\n```"
        assert "y=2" in extract_code(text)

    def test_no_code_yields_empty(self):
        assert extract_code("I cannot help with that.") == ""


class TestGrading:
    def test_correct_implementation_passes(self):
        ok, detail, _credit = run_candidate(GOOD_MERGE, MERGE["tests"])
        assert ok, detail

    def test_plausible_but_wrong_fails(self):
        # Drops duplicates -- looks right, fails merge_sorted([1,1,2],[1,3]).
        bad = """
def merge_sorted(a: list, b: list) -> list:
    out = []
    for x in a + b:
        if x not in out:
            out.append(x)
    return out
"""
        ok, _, _credit = run_candidate(bad, MERGE["tests"])
        assert not ok

    def test_infinite_loop_is_caught_not_hung(self):
        spin = """
def merge_sorted(a: list, b: list) -> list:
    while True:
        pass
"""
        ok, detail, _credit = run_candidate(spin, MERGE["tests"], timeout=3)
        assert not ok and "timed out" in detail

    def test_empty_reply_fails_with_a_clear_reason(self):
        ok, detail, _credit = run_candidate("", MERGE["tests"])
        assert not ok and "no code" in detail

    def test_syntax_error_fails(self):
        ok, _, _credit = run_candidate(
            "def merge_sorted(a, b) return a", MERGE["tests"]
        )
        assert not ok

    def test_wrong_function_name_fails(self):
        ok, _, _credit = run_candidate("def merge(a, b): return a + b", MERGE["tests"])
        assert not ok

    def test_balanced_task_rejects_a_naive_counter(self):
        # Counting brackets without checking nesting passes "([)]" wrongly.
        naive = """
def balanced(s: str) -> bool:
    return s.count("(") == s.count(")") and s.count("[") == s.count("]") and s.count("{") == s.count("}")
"""
        ok, _, _credit = run_candidate(naive, BALANCED["tests"])
        assert not ok, "the test set must catch a counter that ignores nesting"


class TestTruncationDetection:
    """Cut off != wrong. Grading a truncated reply as incompetence is how a
    server limit gets misreported as a model's ability -- the exact mistake the
    first run of this benchmark made against Qwen3-4B."""

    def test_hitting_the_generation_cap_counts_as_truncated(self):
        # The cap is the request's own output budget now, not a server
        # constant: GenieX v0.5.0 stopped at 2048 whatever you asked for,
        # v0.6.1 honours max_tokens and has no ceiling.
        from bench_coding import generation_cap, looks_truncated

        assert looks_truncated("some text", 2048, "def f(): pass", cap=2048)
        assert generation_cap(3000) == 3000
        assert generation_cap(None) == 2048

    def test_a_server_reported_length_finish_is_a_cut_without_any_cap(self):
        from bench_coding import looks_truncated

        assert looks_truncated("some text", 10, "def f(): pass", finish="length")

    def test_short_reply_is_not_truncated(self):
        from bench_coding import looks_truncated

        assert not looks_truncated(
            "```python\ndef f(): pass\n```", 120, "def f(): pass"
        )

    def test_unclosed_fence_with_broken_code_is_truncated(self):
        from bench_coding import looks_truncated

        text = "```python\ndef f(:\n    return ("  # fence never closed
        assert looks_truncated(text, 100, "def f(:\n    return (")

    def test_valid_code_in_a_closed_fence_is_never_truncated(self):
        from bench_coding import looks_truncated

        text = "```python\ndef f():\n    return 1\n```"
        assert not looks_truncated(text, 100, "def f():\n    return 1")

    def test_a_plain_typo_in_a_closed_fence_is_wrong_not_truncated(self):
        # Closed fence + syntax error = the model wrote bad code on purpose.
        from bench_coding import looks_truncated

        text = "```python\ndef f() return 1\n```"
        assert not looks_truncated(text, 100, "def f() return 1")


class TestTruncationTails:
    """D1/D2. A regex that could not tell a CLOSING fence from an opener read
    every reply ending in "```\\n" as unclosed: a model that wrote broken code
    on purpose was excluded from the rate, the interval and the rank instead of
    counted wrong. The mirror hole graded a real cut FAIL when its prefix
    happened to compile.
    """

    # A deliberate typo: closed fence, so the only thing deciding CUT vs FAIL
    # is whether the tail is read as an opener.
    TYPO = "def merge_sorted(a, b)\n    return a\n"

    @pytest.mark.parametrize(
        "tail",
        ["```", "```\n", "```\n\nHope this helps"],
        ids=["bare", "newline", "prose"],
    )
    @pytest.mark.parametrize("finish", [None, "stop"])
    def test_a_closing_fence_is_not_an_unclosed_opener(self, tail, finish):
        from bench_coding import extract_code, looks_truncated

        text = "```python\n" + self.TYPO + tail
        code = extract_code(text, want="merge_sorted")
        assert not looks_truncated(text, 60, code, finish, cap=3000), (
            "a syntax-error reply with a CLOSED fence is wrong, not cut"
        )

    def test_a_genuinely_unclosed_final_fence_is_still_a_cut(self):
        from bench_coding import extract_code, looks_truncated

        text = "```python\ndef merge_sorted(a, b):\n    return (a +"
        assert looks_truncated(
            text, 60, extract_code(text, want="merge_sorted"), None, cap=3000
        )

    def test_the_server_reported_length_still_wins_over_a_closed_fence(self):
        from bench_coding import looks_truncated

        text = "```python\n" + self.TYPO + "```\n"
        assert looks_truncated(text, 60, self.TYPO, "length", cap=3000)

    def test_the_delta_count_cap_still_wins_over_a_closed_fence(self):
        from bench_coding import looks_truncated

        text = "```python\n" + self.TYPO + "```\n"
        assert looks_truncated(text, 3000, self.TYPO, None, cap=3000)

    def test_a_cut_landing_on_a_compiling_prefix_is_a_cut_not_a_failure(self):
        # D2: the stream stopped mid-body, below the cap, with no finish
        # reason. The prefix parses -- and used to be graded FAIL.
        from bench_coding import extract_code, looks_truncated

        text = (
            "```python\ndef merge_sorted(a, b):\n    out = []\n"
            "    for x in a:\n        out.append(x)\n"
        )
        code = extract_code(text, want="merge_sorted")
        compile(code, "<prefix>", "exec")  # the prefix really is valid
        assert looks_truncated(text, 60, code, None, cap=3000)


class TestMultiFenceAndIndentedExtraction:
    """D4/D5/D9. Three ways a CORRECT answer was graded FAIL: its imports or
    helper lived in an earlier fence, a demo block quoting the signature in a
    docstring outranked the real definition, or the fence was indented inside a
    markdown list.
    """

    def _graded(self, text):
        return run_candidate(
            extract_code(text, want="merge_sorted"),
            MERGE["tests"],
            forbidden=MERGE["forbidden"],
        )

    def test_a_helper_in_an_earlier_fence_is_kept(self):
        text = (
            "First a helper:\n```python\ndef _take(xs):\n    return xs[0], xs[1:]\n```\n"
            "Then the function:\n```python\ndef merge_sorted(a, b):\n"
            "    out = []\n    while a and b:\n"
            "        if a[0] <= b[0]:\n            x, a = _take(a)\n"
            "        else:\n            x, b = _take(b)\n"
            "        out.append(x)\n    return out + a + b\n```\n"
        )
        ok, detail, _ = self._graded(text)
        assert ok, detail  # used to die with NameError: _take

    def test_an_import_in_an_earlier_fence_is_kept(self):
        text = (
            "```python\nimport heapq\n```\n\n"
            "```python\ndef merge_sorted(a, b):\n    return list(heapq.merge(a, b))\n```"
        )
        ok, detail, _ = self._graded(text)
        assert ok, detail  # used to die with NameError: heapq

    def test_a_demo_quoting_the_signature_in_its_docstring_does_not_win(self):
        # The demo is LONGER and its docstring quotes the signature, so
        # longest-wins picked it. On the tree, only the real block defines it.
        real = (
            "def merge_sorted(a, b):\n    out, i, j = [], 0, 0\n"
            "    while i < len(a) and j < len(b):\n"
            "        if a[i] <= b[j]:\n            out.append(a[i]); i += 1\n"
            "        else:\n            out.append(b[j]); j += 1\n"
            "    return out + a[i:] + b[j:]\n"
        )
        demo = (
            "def demo():\n"
            '    """Usage of def merge_sorted(a, b) -- the signature above.\n\n'
            "    Call it as: def merge_sorted(a, b) -> list\n"
            "    Prints a few merges so you can see def merge_sorted(a, b) run.\n"
            '    """\n'
            "    print(merge_sorted([1, 3], [2]))\n"
            "    print(merge_sorted([], [4]))\n"
            "    print(merge_sorted([9], []))\n"
            "    print(merge_sorted([0, 0], [0]))\n"
        )
        assert len(demo) > len(real), (
            "the demo must be the longer block or this proves nothing"
        )
        ok, detail, _ = self._graded(
            f"```python\n{real}```\n\nExample:\n```python\n{demo}```\n"
        )
        assert ok, detail

    def test_an_indented_fence_with_two_statements_compiles(self):
        # A fence inside a markdown list keeps its margin on every line but the
        # first: strip() alone left an IndentationError that then read as CUT.
        text = (
            "1. Put this in a file:\n\n"
            "   ```python\n"
            "   import math\n\n"
            "   def merge_sorted(a, b):\n"
            "       out = []\n"
            "       i = j = 0\n"
            "       while i < len(a) and j < len(b):\n"
            "           if a[i] <= b[j]:\n"
            "               out.append(a[i]); i += 1\n"
            "           else:\n"
            "               out.append(b[j]); j += 1\n"
            "       return out + a[i:] + b[j:] + [math.inf][:0]\n"
            "   ```\n"
        )
        ok, detail, _ = self._graded(text)
        assert ok, detail
