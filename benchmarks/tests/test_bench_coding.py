"""Tests for the coding grader, on the replies that would silently corrupt a ranking."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_coding as bc  # noqa: E402
from bench_coding import TASKS, extract_code, looks_truncated, run_candidate  # noqa: E402

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
        # The draft is LONGER and defines the same function, so only <think> stripping saves it.
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
        # Cut off mid-thought: a complete-looking draft in there must not grade PASS.
        text = (
            "<think>\nLet me try:\n```python\ndef merge_sorted(a,b): return a+b\n```\n"
        )
        assert extract_code(text, want="merge_sorted") == ""

    def test_prefers_the_block_defining_the_required_function(self):
        # The longer demo block ALSO defines a function, so only the `want` branch picks right.
        text = (
            "```python\ndef merge_sorted(a, b):\n    return a\n```\n"
            "Example:\n```python\ndef demo():\n"
            "    print(merge_sorted([1], [2]))\n"
            "    print(merge_sorted([3], [4]))\n"
            "    print(merge_sorted([5], [6]))\n```"
        )
        assert extract_code(text, want="merge_sorted").startswith("def merge_sorted")

    def test_falls_back_to_a_block_containing_any_def(self):
        # Without a name, a defining block still beats a longer one that only calls things.
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
    """Cut off != wrong: a server limit must not read as the model's ability."""

    def test_hitting_the_generation_cap_counts_as_truncated(self):
        # The cap is the request's own output budget, not a server constant.
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
    """A closing fence is no opener, and a real cut is CUT even when its prefix compiles."""

    # A typo in a closed fence: only reading the tail as an opener could make it CUT.
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
        # Stopped mid-body, below the cap, no finish reason: CUT, though the prefix parses.
        from bench_coding import extract_code, looks_truncated

        text = (
            "```python\ndef merge_sorted(a, b):\n    out = []\n"
            "    for x in a:\n        out.append(x)\n"
        )
        code = extract_code(text, want="merge_sorted")
        compile(code, "<prefix>", "exec")  # the prefix really is valid
        assert looks_truncated(text, 60, code, None, cap=3000)


# (lang, the name the task pins, a complete answer that is wrong: 41, not 42)
NON_PYTHON_WRONG = [
    ("powershell", "Get-Answer", "function Get-Answer {\n    return 41\n}"),
    ("bash", "answer", "answer() {\n    echo 41\n}"),
    (
        "cmake",
        "answer",
        "function(answer out)\n    set(${out} 41 PARENT_SCOPE)\nendfunction()",
    ),
    ("dockerfile", None, "FROM alpine:3.20\nRUN echo 41"),
]
_LANGS = pytest.mark.parametrize(
    ("lang", "want", "code"), NON_PYTHON_WRONG, ids=[w[0] for w in NON_PYTHON_WRONG]
)


def _unclosed(lang, code):
    """A reply whose final fence never closed."""
    return f"Here it is:\n```{lang}\n{code}\n"


def _closed(lang, code):
    return f"```{lang}\n{code}\n```\n"


class TestTruncationOutsidePython:
    """Outside Python, the finish reason, token cap and fence parity decide CUT, not compile()."""

    @_LANGS
    def test_a_stop_with_an_unclosed_fence_is_graded_not_cut(self, lang, want, code):
        text = _unclosed(lang, code)
        extracted = extract_code(text, want=want, lang=lang)
        assert extracted == code
        with pytest.raises(SyntaxError):  # what made every one of them CUT
            compile(extracted, "<candidate>", "exec")
        assert not looks_truncated(text, 60, extracted, "stop", 3000, lang)

    @_LANGS
    def test_a_stop_with_a_closed_fence_is_graded(self, lang, want, code):
        assert not looks_truncated(_closed(lang, code), 60, code, "stop", 3000, lang)

    @_LANGS
    @pytest.mark.parametrize("fence", [_closed, _unclosed], ids=["closed", "open"])
    def test_a_length_finish_is_a_cut(self, lang, want, code, fence):
        assert looks_truncated(fence(lang, code), 60, code, "length", 3000, lang)

    @_LANGS
    @pytest.mark.parametrize("finish", [None, "stop"])
    def test_an_unclosed_fence_at_the_token_cap_is_a_cut(
        self, lang, want, code, finish
    ):
        text = _unclosed(lang, code)
        assert looks_truncated(text, 3000, code, finish, 3000, lang)

    @_LANGS
    def test_an_unclosed_fence_with_no_finish_reason_is_a_cut(self, lang, want, code):
        # Parity alone, as in Python: a silent stream that ends inside a fence is a cut.
        assert looks_truncated(_unclosed(lang, code), 60, code, None, 3000, lang)

    def test_python_keeps_its_syntax_probe(self):
        # With a stop, an unclosed Python block is CUT only when it does not compile.
        broken = "def merge_sorted(a, b):\n    return (a +"
        fine = "def merge_sorted(a, b):\n    return a + b"
        assert looks_truncated(
            _unclosed("python", broken), 60, broken, "stop", 3000, "python"
        )
        assert not looks_truncated(
            _unclosed("python", fine), 60, fine, "stop", 3000, "python"
        )


class TestNonPythonRowsReadFailOrCut:
    """The same rule where it lands: the verdict evaluate() writes on the row."""

    def _report(self, monkeypatch, lang, want, reply, finish, tokens):
        task = {"name": f"{lang}_answer", "kind": "spec-transcription"}
        task |= {"lang": lang, "function": want, "prompt": "Write it.", "tests": ""}
        monkeypatch.setattr(bc, "TASKS", [task])
        monkeypatch.setattr(
            bc,
            "ask",
            lambda *a, **k: (reply, 0.1, 1.0, tokens, 10, "", finish, tokens, False),
        )
        monkeypatch.setattr(bc, "run_candidate", self._wrong)
        return bc.evaluate("http://x", "m", "lbl", 3000, warmup=False)

    @staticmethod
    def _wrong(*_args, **_kwargs):
        """The runner is not the subject: the answer is wrong, and no toolchain need be here."""
        return False, "expected 42, got 41", {"passed": 0, "total": 1}

    @_LANGS
    def test_complete_but_wrong_with_a_stop_is_fail(
        self, monkeypatch, lang, want, code
    ):
        r = self._report(monkeypatch, lang, want, _unclosed(lang, code), "stop", 60)
        row = r["results"][0]
        assert row["truncated"] is False and row["passed"] is False
        assert (r["wrong"], r["truncated"]) == (1, 0)

    @_LANGS
    def test_complete_but_wrong_with_a_length_finish_is_cut(
        self, monkeypatch, lang, want, code
    ):
        r = self._report(monkeypatch, lang, want, _closed(lang, code), "length", 60)
        assert r["results"][0]["truncated"] is True
        assert (r["wrong"], r["truncated"]) == (0, 1)

    @_LANGS
    @pytest.mark.parametrize("finish", [None, "stop"])
    def test_an_unclosed_fence_at_the_token_cap_is_cut(
        self, monkeypatch, lang, want, code, finish
    ):
        # "stop" is the case only the cap decides; without it the open fence alone is a cut.
        reply = _unclosed(lang, code)
        r = self._report(monkeypatch, lang, want, reply, finish, 3000)
        assert r["results"][0]["truncated"] is True
        assert "CUT OFF at 3000 tokens" in r["results"][0]["detail"]


class TestMultiFenceAndIndentedExtraction:
    """Split fences, a signature-quoting demo block and list-indented fences still grade right."""

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
        assert ok, detail  # not NameError: _take

    def test_an_import_in_an_earlier_fence_is_kept(self):
        text = (
            "```python\nimport heapq\n```\n\n"
            "```python\ndef merge_sorted(a, b):\n    return list(heapq.merge(a, b))\n```"
        )
        ok, detail, _ = self._graded(text)
        assert ok, detail  # not NameError: heapq

    def test_a_demo_quoting_the_signature_in_its_docstring_does_not_win(self):
        # The demo is LONGER and quotes the signature; on the tree only the real block defines it.
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
        # A list-indented fence keeps its margin on every line but the first; strip() is not enough.
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
