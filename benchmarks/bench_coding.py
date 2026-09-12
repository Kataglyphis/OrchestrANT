#!/usr/bin/env python3
"""Rank models by whether their code actually RUNS (LB-coding).

The generic correctness probe in benchmark_openai_api.py answers "is this model
working at all". It cannot answer "is this model good at code" -- a model can
recite Canberra and still emit a broken function.

So: give each model a small set of coding tasks with an exact required
signature, extract the code it produces, execute it against hidden test cases
in a subprocess, and report how many tasks passed alongside the time it took to
get there. Everything here is measured, nothing is judged by eye.

Ranking uses time to a FINISHED answer, not tok/s: a reasoning model can be the
fastest per token and the slowest to usable code.

SAFETY: this executes model-generated code. Each candidate runs in a temporary
directory as a separate process with a hard timeout. Do not point it at an
untrusted endpoint.

Usage:
    python3 bench_coding.py --backend geniex-npu
    python3 bench_coding.py --backend geniex-cpu --model unsloth/Qwen3-4B-GGUF:Q4_0
    python3 bench_coding.py --compare candidates.json --output coding.json
"""

import argparse
import ast
import hashlib
import secrets
import selectors
import shutil
import signal
import json
import os
import resource
import statistics
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# One request path for every tool: the entry's Authorization header, its
# request_extra and the total-duration deadline live in bench_cli, not here.

# Standalone runs of these scripts (they are not a package) need the repo
# root on sys.path; the runner lives in orchestrant.benchmark.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from orchestrant.benchmark import client as bench_cli

# ── Tasks ─────────────────────────────────────────────────────────────────────
# Each task pins an exact signature so the check is mechanical, and the tests
# include the edge cases a plausible-looking wrong answer trips over.

TASKS = [
    {
        "name": "merge_sorted",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": (
            "Write a Python function with this exact signature:\n"
            "    def merge_sorted(a: list, b: list) -> list\n"
            "It merges two already-sorted lists into one sorted list. "
            "Do not use sorted() or list.sort(). "
            "Reply with the function in a single ```python code block and nothing else."
        ),
        # The prompt states a constraint; without this the tests cannot see it.
        # `return sorted(a + b)` passed every assertion until this was added.
        "forbidden": ["sorted", "list.sort", ".sort("],
        "tests": """
assert merge_sorted([], []) == []
assert merge_sorted([1], []) == [1]
assert merge_sorted([], [2]) == [2]
assert merge_sorted([1,3,5], [2,4,6]) == [1,2,3,4,5,6]
assert merge_sorted([1,1,2], [1,3]) == [1,1,1,2,3]
assert merge_sorted([-5,0], [-9,-1,7]) == [-9,-5,-1,0,7]
assert merge_sorted([1,2,3], []) == [1,2,3]
""",
        "reference": """def merge_sorted(a: list, b: list) -> list:
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            out.append(a[i]); i += 1
        else:
            out.append(b[j]); j += 1
    out.extend(a[i:]); out.extend(b[j:])
    return out
""",
        # Ignores the stated constraint -- must be rejected by `forbidden`.
        "wrong": "def merge_sorted(a: list, b: list) -> list:\n    return sorted(a + b)\n",
    },
    {
        "name": "balanced",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": (
            "Write a Python function with this exact signature:\n"
            "    def balanced(s: str) -> bool\n"
            "It returns True if the brackets (), [] and {} in s are correctly "
            "balanced and nested, ignoring all other characters. "
            "Reply with the function in a single ```python code block and nothing else."
        ),
        "tests": """
assert balanced("") is True
assert balanced("()") is True
assert balanced("([]{})") is True
assert balanced("(]") is False
assert balanced("([)]") is False
assert balanced("(") is False
assert balanced(")(") is False
assert balanced("a(b[c]{d})e") is True
""",
        "reference": """def balanced(s: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    for ch in s:
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack
""",
        # Counts brackets without checking nesting: accepts "([)]".
        "wrong": (
            "def balanced(s: str) -> bool:\n"
            "    return s.count('(') == s.count(')') and s.count('[') == s.count(']')\n"
        ),
    },
    {
        "name": "parse_version",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": (
            "Write a Python function with this exact signature:\n"
            "    def parse_version(v: str) -> tuple\n"
            "It parses a version string like '1.2.3' into a tuple of ints (1, 2, 3). "
            "Missing components default to 0, so '1.2' gives (1, 2, 0) and '2' gives (2, 0, 0). "
            "A leading lowercase 'v' is allowed and ignored. Each component must be one or "
            "more ASCII digits 0-9 and there are at most three components. Raise "
            "ValueError on anything else: an uppercase 'V', whitespace anywhere in the "
            "string, signs, underscores, a fourth component or an empty one. "
            "Reply with the function in a single ```python code block and nothing else."
        ),
        "tests": """
def _bad(s):
    try:
        parse_version(s)
    except ValueError:
        return True
    except Exception:
        return False
    return False

assert parse_version("1.2.3") == (1, 2, 3)
assert parse_version("1.2") == (1, 2, 0)
assert parse_version("2") == (2, 0, 0)
assert parse_version("v3.4.5") == (3, 4, 5)
assert parse_version("0.0.0") == (0, 0, 0)
assert parse_version("v10.20") == (10, 20, 0)
assert _bad("abc")
assert _bad("")
assert _bad("v")
assert _bad("1.2.3.4")
assert _bad("1.-2")
assert _bad("1_0.2")
assert _bad(" 1.2")
assert _bad("1.2 ")
assert _bad("V1.2")
assert _bad("1..2")
assert _bad("1.")
assert _bad("+1.2")
assert _bad("١.2")
""",
        "reference": """def parse_version(v: str) -> tuple:
    t = v[1:] if v.startswith("v") else v
    parts = t.split(".")
    if len(parts) > 3 or not all(p and all(c in "0123456789" for c in p) for p in parts):
        raise ValueError("bad version")
    nums = [int(p) for p in parts]
    return tuple(nums + [0] * (3 - len(nums)))
""",
        # Trusts int(): accepts a fourth component, negatives and underscores.
        "wrong": (
            "def parse_version(v: str) -> tuple:\n"
            "    p = [int(x) for x in v.lstrip('v').split('.')]\n"
            "    return tuple(p + [0] * (3 - len(p)))\n"
        ),
        # Filters with isdigit() and never raises on garbage.
        "wrong_variants": [
            "def parse_version(v: str) -> tuple:\n"
            "    p = [int(x) for x in v.lstrip('v').split('.') if x.isdigit()]\n"
            "    return tuple(p + [0] * (3 - len(p)))\n",
            # Strips whitespace and accepts 'V': grants what the prompt refuses.
            "def parse_version(v: str) -> tuple:\n"
            "    t = v.strip()\n"
            "    t = t[1:] if t[:1].lower() == 'v' else t\n"
            "    parts = t.split('.')\n"
            "    if len(parts) > 3 or not all(p.isdigit() for p in parts):\n"
            "        raise ValueError('bad')\n"
            "    p = [int(x) for x in parts]\n"
            "    return tuple(p + [0] * (3 - len(p)))\n",
        ],
    },
]

# ── Novel tasks ───────────────────────────────────────────────────────────────
#
# The tasks above (merging sorted lists, bracket balancing, version parsing) are
# textbook problems that appear thousands of times in any training corpus, so a
# model can ace them from RECALL without composing anything. That measures
# memorisation, not coding.
#
# These three are built from formats invented in this repository, with every
# rule stated in full in the prompt. The primitives are ordinary; the
# COMBINATION cannot have been memorised, so passing requires reading the spec
# and following it. Run both sets and compare: a model far better on the
# classic set than on this one is recalling, not reasoning.

try:
    from bench_tasks import EXTENDED_TASKS, LANGUAGE_TASKS
except ImportError:  # pragma: no cover — the suite still runs without them
    EXTENDED_TASKS = LANGUAGE_TASKS = []


def _tally(tasks, field):
    """How many tasks of each kind/lang a run covered — for the report envelope."""
    out = {}
    for t in tasks:
        out[t.get(field, "unknown")] = out.get(t.get(field, "unknown"), 0) + 1
    return dict(sorted(out.items()))


NOVEL_TASKS = [
    {
        "name": "parse_lane_spec",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": (
            "Write a Python function with this exact signature:\n"
            "    def parse_lane(spec: str) -> tuple\n"
            "It parses a benchmark lane specification of the form\n"
            "    name=URL,model=MODEL\n"
            "into the tuple (name, url, model). Rules:\n"
            "- Split on the FIRST occurrence of the literal ',model=' — the model "
            "name itself may contain commas and equals signs, and everything after "
            "that marker belongs to the model.\n"
            "- Split the part before it on the FIRST '=' into name and url.\n"
            "- Strip surrounding whitespace from all three, and strip any trailing "
            "'/' from the url.\n"
            "- Raise ValueError if the ',model=' marker is absent or if there is no "
            "'=' before it.\n"
            "Reply with the function in a single ```python code block and nothing else."
        ),
        "tests": """
assert parse_lane("npu=http://h:1,model=org/M:Q4") == ("npu", "http://h:1", "org/M:Q4")
assert parse_lane("cpu=http://h:1/,model=m") == ("cpu", "http://h:1", "m")
assert parse_lane(" a = http://h:2 ,model= weird,name=x ") == ("a", "http://h:2", "weird,name=x")
assert parse_lane("a=http://h:1,model=a,model=b") == ("a", "http://h:1", "a,model=b")
try:
    parse_lane("no-marker-here"); raise AssertionError("should have raised")
except ValueError:
    pass
try:
    parse_lane("nomodelequals,model=m"); raise AssertionError("should have raised")
except ValueError:
    pass
""",
        "reference": """def parse_lane(spec: str) -> tuple:
    marker = ",model="
    if marker not in spec:
        raise ValueError("missing ,model= marker")
    head, model = spec.split(marker, 1)
    if "=" not in head:
        raise ValueError("missing = before the marker")
    name, url = head.split("=", 1)
    return (name.strip(), url.strip().rstrip("/"), model.strip())
""",
        # Splits on the LAST marker, so a model name containing ',model=' breaks.
        "wrong": (
            "def parse_lane(spec: str) -> tuple:\n"
            "    head, model = spec.rsplit(',model=', 1)\n"
            "    name, url = head.split('=', 1)\n"
            "    return (name.strip(), url.strip().rstrip('/'), model.strip())\n"
        ),
    },
    {
        "name": "attempt_verdict",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": (
            "Write a Python function with this exact signature:\n"
            "    def verdict(passed: bool, tokens: int, cap: int, closed_fence: bool) -> str\n"
            "It classifies one benchmark attempt, returning exactly one of the "
            "strings 'PASS', 'CUT' or 'FAIL'. Apply the rules IN THIS ORDER:\n"
            "1. If passed is True, return 'PASS' — regardless of every other argument.\n"
            "2. Otherwise, if tokens >= cap, return 'CUT'.\n"
            "3. Otherwise, if closed_fence is False, return 'CUT'.\n"
            "4. Otherwise return 'FAIL'.\n"
            "Raise ValueError if cap is less than 1 or tokens is negative.\n"
            "Reply with the function in a single ```python code block and nothing else."
        ),
        "tests": """
assert verdict(True, 5000, 2048, False) == 'PASS'
assert verdict(True, 10, 2048, True) == 'PASS'
assert verdict(False, 2048, 2048, True) == 'CUT'
assert verdict(False, 3000, 2048, True) == 'CUT'
assert verdict(False, 100, 2048, False) == 'CUT'
assert verdict(False, 100, 2048, True) == 'FAIL'
assert verdict(False, 0, 1, True) == 'FAIL'
try:
    verdict(False, 10, 0, True); raise AssertionError("should have raised")
except ValueError:
    pass
try:
    verdict(False, -1, 10, True); raise AssertionError("should have raised")
except ValueError:
    pass
""",
        "reference": """def verdict(passed: bool, tokens: int, cap: int, closed_fence: bool) -> str:
    if cap < 1 or tokens < 0:
        raise ValueError("bad bounds")
    if passed:
        return "PASS"
    if tokens >= cap:
        return "CUT"
    if not closed_fence:
        return "CUT"
    return "FAIL"
""",
        # Checks the cap before `passed`, inverting rule order 1 and 2.
        "wrong": (
            "def verdict(passed, tokens, cap, closed_fence):\n"
            "    if cap < 1 or tokens < 0: raise ValueError('bad')\n"
            "    if tokens >= cap: return 'CUT'\n"
            "    if passed: return 'PASS'\n"
            "    return 'FAIL' if closed_fence else 'CUT'\n"
        ),
    },
    {
        "name": "rank_quants",
        "kind": "spec-transcription",
        "lang": "python",
        "prompt": (
            "Write a Python function with this exact signature:\n"
            "    def rank_quants(names: list) -> list\n"
            "It sorts GGUF quantisation names from most to least precise, using "
            "ONLY these rules:\n"
            "- The precision of a name is the first digit appearing in it. "
            "'Q4_K_M' is 4, 'IQ3_XXS' is 3, 'Q8_0' is 8.\n"
            "- Higher digit sorts first.\n"
            "- Among names with the SAME digit, a name starting with 'Q' sorts "
            "before one starting with 'IQ'.\n"
            "- If still tied, sort alphabetically.\n"
            "- A name containing no digit is dropped from the result entirely.\n"
            "Reply with the function in a single ```python code block and nothing else."
        ),
        "tests": """
assert rank_quants(['IQ3_XXS', 'Q8_0', 'Q4_K_M']) == ['Q8_0', 'Q4_K_M', 'IQ3_XXS']
assert rank_quants(['IQ4_XS', 'Q4_0']) == ['Q4_0', 'IQ4_XS']
assert rank_quants(['Q4_K_S', 'Q4_K_M']) == ['Q4_K_M', 'Q4_K_S']
# BF16's FIRST digit is 1, not 16 — so it ranks below Q2_K. Deliberately
# counter-intuitive: the rule as written is what counts, not what the name
# suggests. (This assertion was wrong when first written; the reference-solution
# test caught it.)
assert rank_quants(['BF16', 'nodigits', 'Q2_K']) == ['Q2_K', 'BF16']
assert rank_quants([]) == []
assert rank_quants(['nodigit']) == []
""",
        "reference": """def rank_quants(names: list) -> list:
    def digit(n):
        for ch in n:
            if ch.isdigit():
                return int(ch)
        return None
    kept = [n for n in names if digit(n) is not None]
    return sorted(kept, key=lambda n: (-digit(n), 0 if n.startswith("Q") else 1, n))
""",
        # Forgets the Q-before-IQ tiebreak.
        "wrong": (
            "def rank_quants(names: list) -> list:\n"
            "    def d(n):\n"
            "        for c in n:\n"
            "            if c.isdigit(): return int(c)\n"
            "        return None\n"
            "    return sorted([n for n in names if d(n) is not None],\n"
            "                  key=lambda n: (-d(n), n))\n"
        ),
    },
]


# Tolerate a space before, and a suffix after, the language word: "```python3",
# "``` python" and CRLF fences all matched nothing and graded a correct answer
# as a SyntaxError. The info string is otherwise ignored -- extract_code
# prefers the block that defines the required symbol, so an over-broad match
# cannot pick a bash block over the answer.
CODE_FENCE = re.compile(r"```[ \t]*(?:python3?|py3?)?[^\n]*\n(.*?)```", re.S | re.I)


def _want_from_prompt(task):
    """The required function name, taken from the signature the prompt pins."""
    m = re.search(r"def\s+(\w+)\s*\(", task.get("prompt", ""))
    return m.group(1) if m else None


# ── Long-context padding ──────────────────────────────────────────────────────
#
# The tasks above are ~40-token prompts. A coding agent never sends that: it
# sends a system prompt plus files, routinely thousands of tokens. Prefill is
# what the user waits on (a 2.5k-token prompt cost 13.1 s to first token on the
# NPU lane), and the QAIRT bundles carry a hard 4096-token context -- so the
# short-prompt ranking says nothing about the case that actually matters.
#
# Padding is real source from this repo rather than lorem ipsum: the point is a
# realistic prefill and a realistic distraction, not a token count.

PAD_SOURCES = ["bench_tools.py", "bench_agent.py", "inspect_gguf.py"]


def build_context(approx_tokens):
    """Return a context block of roughly `approx_tokens` tokens of real code.

    ~4 characters per token is a rule of thumb for source; the measured
    prompt_tokens reported by the server is what gets recorded, so the estimate
    only has to be close enough to hit the intended size band.
    """
    if not approx_tokens:
        return ""
    here = os.path.dirname(os.path.abspath(__file__))
    chunks = []
    for name in PAD_SOURCES:
        try:
            with open(os.path.join(here, name)) as f:
                chunks.append(f"# ---- {name} ----\n{f.read()}")
        except OSError:
            continue
    body = "\n\n".join(chunks)
    while len(body) < approx_tokens * 4 and chunks:
        body += "\n\n" + "\n\n".join(chunks)
    return body[: approx_tokens * 4]


# A reasoning model can spend its whole output budget inside <think> and get cut
# off mid-function -- which grades as a SyntaxError and looks like incompetence.
# It is neither: it is an unmeasured task, reported apart from a genuine wrong
# answer exactly as the correctness probe does.
#
# HOW A CUT IS RECOGNISED, in order of trust:
#   1. finish_reason == "length" from the server. Authoritative, and available
#      on GenieX v0.6+ and on Ollama.
#   2. usage.completion_tokens reaching this cap.
#   3. the streamed delta count reaching it -- a proxy, exact only where one
#      delta is one token.
#
# The cap is no longer a constant of the server: GenieX v0.5.0 stopped at 2048
# regardless of max_tokens (which it ignored outright -- max_tokens=3000 gave
# 642 tokens, max_tokens=500 gave 1249). v0.6.1 honours max_tokens exactly and
# has no hard ceiling (3000 requested, 3000 delivered, measured 2026-09-05), so
# a fixed 2048 would now report a long, legitimate answer as CUT. It therefore
# defaults to the request's own budget and only falls back to 2048 for servers
# that impose one; BENCH_GENERATION_CAP overrides both.
GENERATION_CAP = int(os.environ.get("BENCH_GENERATION_CAP", "0")) or None


def generation_cap(max_tokens):
    """The token count at which a reply counts as cut off."""
    return GENERATION_CAP or max_tokens or 2048


# How each non-Python language spells "this block defines `want`", and what a
# block of that language looks like when the task pins no name.
_DEFINES = {
    "bash": r"(?:^|\s)(?:function\s+)?{want}\s*\(\s*\)\s*\{{",
    "cmake": r"(?im)^\s*(?:function|macro)\s*\(\s*{want}\b",
    "dockerfile": r"(?im)^\s*FROM\s+\S",
}
_LANG_SHAPE = {
    "bash": r"(?m)^\s*(?:#!|\w+\s*\(\s*\)\s*\{|function\s+\w+)",
    "cmake": r"(?im)^\s*(?:function|macro|set|if|cmake_minimum_required)\s*\(",
    "dockerfile": r"(?im)^\s*FROM\s+\S",
}


def _defines(block, want, lang="python"):
    """Does this fenced block define `want` at top level?

    Decided on the tree: a demo whose docstring quotes `def merge_sorted(` is
    not a definition. Text fallback only for a block that does not parse (a
    mid-body cut still has to be picked so looks_truncated can see it).
    The other languages have no tree here, so they match their own spelling.
    """
    if lang != "python":
        pattern = _DEFINES.get(lang)
        if not pattern:
            return False
        return re.search(pattern.format(want=re.escape(want or "")), block) is not None
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return re.search(rf"\bdef\s+{re.escape(want)}\s*\(", block) is not None
    return any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == want
        for n in tree.body
    )


def _is_preamble(block):
    """Imports, constants, helpers -- a block safe to prepend to the answer.

    Anything that CALLS at top level (a demo, a print) is left out: it would
    run before the function exists and NameError the whole candidate.
    """
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return False
    for stmt in tree.body:
        if isinstance(
            stmt,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Import,
                ast.ImportFrom,
            ),
        ):
            continue
        if any(isinstance(n, ast.Call) for n in ast.walk(stmt)):
            return False
    return True


def extract_code(text, want=None, lang="python"):
    """Pull the code out of a reply.

    Prefers the fenced block that DEFINES the required function (`want`), with
    the imports/helper blocks that precede it prepended; then any block
    containing a def, and only then the longest. Falls back to the first
    `def ...` onwards, because some models answer with bare code -- refusing
    to grade that would measure formatting compliance rather than coding
    ability.
    """
    # Strip a reasoning block first: a discarded draft inside <think> must not
    # be graded instead of the real answer.
    if "</think>" in text:
        text = text.split("</think>")[-1]
    elif "<think>" in text:
        # Unclosed: the whole reply is reasoning. The answer, by this
        # benchmark's own rule, comes after </think>; a draft cut off
        # mid-thought was being graded PASS at the generation cap.
        return ""
    # dedent: a fence indented inside a markdown list keeps its margin on every
    # line but the first, and the second statement was an IndentationError.
    blocks = [textwrap.dedent(b).strip() for b in CODE_FENCE.findall(text) if b.strip()]
    if blocks:
        # Longest-block was wrong: models routinely answer with a compact
        # function plus a longer usage/test block, and picking by length
        # extracted the demo — the function was never defined and the hidden
        # tests died with NameError, scoring a correct model as a failure.
        # Prefer a block that actually defines the required symbol.
        if want:
            defining = [i for i, b in enumerate(blocks) if _defines(b, want, lang)]
            if defining:
                chosen = max(defining, key=lambda i: len(blocks[i]))
                if lang != "python":
                    # No import/helper split outside Python: one block is the
                    # answer, and prepending prose would break the script.
                    return blocks[chosen]
                # `import heapq` or a helper in its own earlier fence is part of
                # the answer; graded alone the function died with NameError.
                preamble = [
                    blocks[i]
                    for i in range(chosen)
                    if i not in defining and _is_preamble(blocks[i])
                ]
                return "\n\n".join(preamble + [blocks[chosen]])
        if lang != "python":
            # A task that pins no name (a Dockerfile) still must not be graded
            # on the model's example output block.
            shaped = [b for b in blocks if re.search(_LANG_SHAPE[lang], b)]
            return max(shaped or blocks, key=len)
        with_def = [b for b in blocks if re.search(r"^\s*def\s+\w+\s*\(", b, re.M)]
        if with_def:
            return max(with_def, key=len)
        return max(blocks, key=len)
    if lang != "python":
        # Bare code outside Python: anchor on the language's own opening shape.
        m = re.search(_LANG_SHAPE[lang], text)
        if not m:
            return ""
        tail = text[m.start() :]
        cut = re.search(r"^\s*```\s*$", tail, re.M)
        return (tail[: cut.start()] if cut else tail).strip()
    # Bare code: anchor at the first column-0 code line, not the first "def "
    # substring -- that discarded the imports and constants a function needs
    # and happily started inside prose ("a Python def for it:").
    m = re.search(
        r"^(?:from\s+\S+\s+import\b|import\s+\S|def\s+\w+\s*\(|"
        r"class\s+\w+|[A-Za-z_]\w*\s*=)",
        text,
        re.M,
    )
    if not m:
        return ""
    tail = text[m.start() :]
    # A bare fence line ends the code: if we are here with one in the text,
    # the fence was not recognised, and the closing marks are not Python.
    cut = re.search(r"^\s*```\s*$", tail, re.M)
    return (tail[: cut.start()] if cut else tail).strip()


# Openers sit at a line start (indented in a list, at most); "Wrap it in ```"
# mid-prose is not one. Closers may be glued to the last code line.
_PAIRED_FENCE = re.compile(r"^[ \t]*" + CODE_FENCE.pattern, re.S | re.I | re.M)


def _unclosed_fence(text):
    """Does the reply end inside a fenced block that never closed?

    Closed blocks are removed by pairing; an opener left over at a line start
    is unclosed. A regex that looked at the LAST ``` read the closing fence of
    every "```\\n"-terminated reply as an opener.
    """
    tail = text.split("</think>")[-1]
    rest = _PAIRED_FENCE.sub("", tail)
    return re.search(r"^[ \t]*```", rest, re.M) is not None


def looks_truncated(text, chunks, code, finish=None, cap=None):
    """Was the reply cut off by the server rather than finished by the model?

    In order of trust:
      * finish_reason "length": the server said so;
      * the generation reached the cap;
      * the reply ends inside an unclosed fenced block and the server did not
        report a finish reason -- decisive on its own, because a cut can land
        on a prefix that compiles;
      * with a finish reason, an unclosed block still counts only when the
        code does not parse (a mid-token cut), so a model that stopped on its
        own and merely forgot the closing fence is graded on its code.
    A closed fence with a plain syntax error is wrong, never cut.
    """
    if finish == "length":
        return True  # the server said so; nothing to infer
    if cap and chunks >= cap:
        return True
    if not _unclosed_fence(text):
        return False
    if finish is None:
        return True
    if code:
        try:
            compile(code, "<candidate>", "exec")
        except SyntaxError:
            return True
    return False


_FORBIDDEN_NAMES = {"sorted": ("sorted",), "list.sort": ("sort",), ".sort(": ("sort",)}
# Calls whose string argument names the thing looked up, and subscript bases
# that reach the builtins table: only there is a str constant a lookup.
_LOOKUP_CALLS = {"getattr", "__import__", "import_module", "attrgetter", "methodcaller"}
_LOOKUP_TABLES = {"__builtins__", "builtins", "vars", "globals", "locals", "__dict__"}


_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _scope_nodes(scope):
    """Every node evaluated in `scope` itself, stopping at a nested scope.

    The nested def/class node is yielded (its NAME is bound here) but its body
    is not: a parameter called `sorted` belongs to that function alone.
    """
    out = []
    for child in ast.iter_child_nodes(scope):
        out.append(child)
        if not isinstance(child, _SCOPES):
            out.extend(_scope_nodes(child))
    return out


def _bound_names(scope):
    """Names bound by THIS scope -- minus `sorted = sorted`-style captures,
    which bind the builtin under its own name and are a use of it."""
    bound, captured = set(), set()
    for node in _scope_nodes(scope):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".")[0])
        targets, values = [], []
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            values = [node.value] if node.value is not None else []
        elif isinstance(node, (ast.For, ast.comprehension)):
            targets, values = [node.target], [node.iter]
        elif isinstance(node, ast.arguments):
            targets = node.posonlyargs + node.args + node.kwonlyargs
            values = node.defaults + [d for d in node.kw_defaults if d is not None]
        names = {
            n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)
        } | {t.arg for t in targets if isinstance(t, ast.arg)}
        loads = {
            n.id
            for v in values
            for n in ast.walk(v)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        captured |= names & loads
    return bound - captured


def _is_lookup_string(node, parent):
    """Is this str constant the name handed to getattr/__import__/... or the
    key into __builtins__/vars()?"""
    if isinstance(parent, ast.Call) and node in parent.args:
        f = parent.func
        name = (
            f.id
            if isinstance(f, ast.Name)
            else f.attr
            if isinstance(f, ast.Attribute)
            else None
        )
        return name in _LOOKUP_CALLS
    if isinstance(parent, ast.Subscript) and node is parent.slice:
        return any(
            (isinstance(n, ast.Name) and n.id in _LOOKUP_TABLES)
            or (isinstance(n, ast.Attribute) and n.attr in _LOOKUP_TABLES)
            for n in ast.walk(parent.value)
        )
    return False


def _unbound_uses(tree, wanted):
    """The `wanted` Name nodes that NO enclosing scope binds.

    Scoped, not file-wide: binding the token in an unrelated helper (a
    parameter named `sorted`) used to whitelist every call in the file.
    """
    hits = set()

    def walk(node, visible, class_body):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _SCOPES):
                inner = _bound_names(child)
                # A class body's names are invisible to functions nested in it,
                # so they never travel into the next scope down.
                if isinstance(child, ast.ClassDef):
                    walk(child, visible, inner)
                else:
                    walk(child, visible | inner, frozenset())
                continue
            if (
                isinstance(child, ast.Name)
                and child.id in wanted
                and child.id not in visible
                and child.id not in class_body
            ):
                hits.add(child)
            walk(child, visible, class_body)

    walk(tree, _bound_names(tree), frozenset())
    return hits


def check_forbidden(code, forbidden):
    """Reject a solution that ignores a constraint the prompt stated.

    A benchmark that states a rule and does not enforce it measures nothing:
    `return sorted(a + b)` satisfied every assertion of the merge task while
    doing exactly what the prompt forbade.

    Checked on the syntax tree, not the text: a text scan let `s = sorted;
    s(a + b)`, `builtins.sorted(...)` and `getattr(__builtins__, "sorted")`
    through, and had to strip comments and strings by regex so that a
    docstring *mentioning* sorted() was not punished. On the tree a use is:
    a name no ENCLOSING scope binds (its own `def sort` is not the builtin,
    but an unrelated helper's parameter exempts nothing), an attribute, an
    import alias, or a string constant handed to a
    lookup (getattr/__import__/import_module, a __builtins__/vars() key) --
    not every `mode='sort'` default or dict key. Code that does not parse
    falls back to the text scan -- it will fail the tests anyway.
    """
    if not forbidden:
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return _check_forbidden_text(code, forbidden)
    wanted = set()
    unmapped = []
    for token in forbidden:
        names = _FORBIDDEN_NAMES.get(token)
        if names:
            wanted.update(names)
        else:
            unmapped.append(token)
    unbound = _unbound_uses(tree, wanted)
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in wanted and node in unbound:
            return f"used {node.id}(), which the prompt forbids"
        if isinstance(node, ast.Attribute) and node.attr in wanted:
            return f"used .{node.attr}(), which the prompt forbids"
        if isinstance(node, ast.alias) and node.name in wanted:
            return f"imported {node.name}, which the prompt forbids"
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in wanted
            and _is_lookup_string(node, parents.get(node))
        ):
            return f"named {node.value!r} to look it up, which the prompt forbids"
    # The text scan runs on the MAPPED tokens too: a module-level binding that
    # never executes (`for sorted in []`) leaves the builtin reachable anyway.
    return _check_forbidden_text(code, forbidden) or (
        _check_forbidden_text(code, unmapped) if unmapped else None
    )


def _check_forbidden_text(code, forbidden):
    """The old text scan, kept for tokens with no syntactic meaning."""
    if not forbidden:
        return None
    stripped = re.sub(r"#.*", "", code)
    stripped = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', "", stripped)
    stripped = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', "", stripped)
    for token in forbidden:
        if token == "sorted":
            if re.search(r"\bsorted\s*\(", stripped):
                return "used sorted(), which the prompt forbids"
        elif token in stripped:
            return f"used {token}, which the prompt forbids"
    return None


def _nonstd_imports(code):
    """Names imported from outside the standard library, or [] / None on error.

    Three prompts say "Use only the Python standard library." and nothing
    checked it; a solution importing a third-party package that happens to be
    installed on the host passed. Top-level module of every Import/ImportFrom,
    against sys.stdlib_module_names.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            bad.append(node.module.split(".")[0])
    return sorted({m for m in bad if m not in allowed})


_NETNS = None


def _netns_available():
    """Can the candidate be given an empty network namespace, with prlimit
    inside it? Probed once."""
    global _NETNS
    if _NETNS is None:
        try:
            _NETNS = (
                subprocess.run(
                    ["unshare", "-rn", "prlimit", f"--nproc={RLIMIT_NPROC}", "true"],
                    capture_output=True,
                    timeout=10,
                ).returncode
                == 0
            )
        except Exception:  # noqa: BLE001
            _NETNS = False
    return _NETNS


def _try_asserts(node):
    """A try whose BODY raises AssertionError or asserts is a should-raise check."""
    return any(
        isinstance(n, ast.Assert)
        or (isinstance(n, ast.Raise) and "AssertionError" in ast.dump(n))
        for stmt in node.body
        for n in ast.walk(stmt)
    )


def _assertion_harness(tests):
    """Wrap each top-level statement so one failure does not hide the rest.

    Pass/fail alone cannot distinguish "wrong algorithm" from "one edge case
    missed", and a model at 6 of 7 assertions is not the same as one at 0 of 7.
    Running the block as a whole stops at the FIRST failure, so most of the
    signal was being thrown away.

    Statements are found with the parser, not by line prefix: decorators,
    bracketed continuations at column 0, `except:` and dedented lines inside
    triple-quoted strings all fooled the prefix rule and turned a correct
    answer into a 0/0 SyntaxError.

    Only groups that ASSERT are counted: a top-level assert, and a should-raise
    check (`try: f(bad); raise AssertionError ... except ValueError: pass`)
    as ONE assertion. A setup line or helper def is wrapped so a failure there
    cannot kill the harness, but it is not an assertion and must not pad the
    denominator -- nine tasks were publishing inflated near-miss fractions. A
    setup failure is recorded separately and still denies PASS: a candidate
    that makes `out[0][0] = 99` raise must not be scored on the aliasing check
    that then passes trivially.

    Returns (program_text, number_of_counted_assertions).
    """
    tree = ast.parse(
        tests
    )  # author-controlled: a broken test string should fail loudly
    lines = tests.splitlines()
    body = []
    asserts = 0
    for node in tree.body:
        first = min(
            [node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]
        )
        seg = lines[first - 1 : node.end_lineno]
        block = "\n".join("    " + ln for ln in seg)
        counted = (
            isinstance(node, ast.Assert)
            or (isinstance(node, ast.Raise) and "AssertionError" in ast.dump(node))
            or (isinstance(node, ast.Try) and _try_asserts(node))
        )
        if counted:
            i = asserts
            asserts += 1
            body.append(f"try:\n{block}\n    _RESULTS.append(({i}, True, ''))")
            # BaseException, not Exception: a candidate that calls sys.exit()
            # inside the tested function used to end the harness with the
            # rows never printed, discarding every assertion already passed.
            body.append(
                f"except BaseException as _e:\n    _RESULTS.append(({i}, False, "
                f"type(_e).__name__ + ': ' + str(_e)[:80]))"
            )
        else:
            body.append(f"try:\n{block}")
            body.append(
                "except BaseException as _e:\n    _SETUP_FAILED.append("
                "type(_e).__name__ + ': ' + str(_e)[:80])"
            )
    program = (
        "_RESULTS = []\n_SETUP_FAILED = []\n"
        + "\n".join(body)
        + "\n_json_dumps = __import__('json').dumps\n"
        + "print(_MARKER + _json_dumps({'rows': _RESULTS, 'setup': _SETUP_FAILED}))\n"
    )
    return program, asserts


# Per-candidate ceilings: address space, file size, processes. An allocating
# candidate took WSL2 down with it; these are generous for a real solution.
RLIMIT_AS_BYTES = 1 << 30
RLIMIT_FSIZE_BYTES = 8 << 20
RLIMIT_NPROC = 64  # forks allowed ABOVE what the user already runs
_NPROC_CEILING = None
OUTPUT_LIMIT_BYTES = 1 << 20


def _nproc_ceiling():
    """RLIMIT_NPROC is per-UID, counted live and host-wide, and counts TASKS, not
    processes. A bare 64 therefore limits the DESKTOP, not the candidate: this host
    runs 102 processes but 591 tasks, so the fork fails, bash retries it forever,
    and every bash/CMake row dies at the timeout blaming "likely an infinite loop".
    Count tasks and allow RLIMIT_NPROC above them. Probed once, like _NETNS, so the
    ceiling cannot drift between launch and assertion.
    """
    global _NPROC_CEILING
    if _NPROC_CEILING is None:
        uid, mine = os.getuid(), 0
        try:
            entries = list(os.scandir("/proc"))
        except OSError:
            entries = []
        for e in entries:
            # A pid that exits mid-census raises; skip that entry. Catching it
            # around the whole loop zeroed the count and restored the broken 64.
            try:
                if not (e.name.isdigit() and e.stat().st_uid == uid):
                    continue
                mine += len(os.listdir(f"/proc/{e.name}/task"))
            except OSError:
                continue
        _NPROC_CEILING = mine + RLIMIT_NPROC
    return _NPROC_CEILING


def _candidate_rlimits():
    resource.setrlimit(resource.RLIMIT_AS, (RLIMIT_AS_BYTES, RLIMIT_AS_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (RLIMIT_FSIZE_BYTES, RLIMIT_FSIZE_BYTES))
    # NPROC set here is checked against the HOST-wide count of the user's
    # processes even inside `unshare -r`; there, prlimit sets it in the namespace.
    if not _netns_available():
        ceiling = _nproc_ceiling()
        resource.setrlimit(resource.RLIMIT_NPROC, (ceiling, ceiling))


def _communicate_bounded(proc, timeout, limit=OUTPUT_LIMIT_BYTES):
    """communicate() with a byte cap per stream.

    Returns (stdout, stderr, overflowed). A candidate printing without end
    used to be buffered whole into this process; past the cap the read stops
    and the caller kills the tree. Raises TimeoutExpired like communicate().
    """
    deadline = time.monotonic() + timeout
    bufs = {proc.stdout: bytearray(), proc.stderr: bytearray()}
    sel = selectors.DefaultSelector()
    for f in bufs:
        sel.register(f, selectors.EVENT_READ)
    try:
        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(proc.args, timeout)
            for key, _ in sel.select(remaining):
                data = os.read(key.fd, 65536)
                if not data:
                    sel.unregister(key.fileobj)
                    continue
                buf = bufs[key.fileobj]
                if len(buf) + len(data) > limit:
                    return bytes(bufs[proc.stdout]), bytes(bufs[proc.stderr]), True
                buf += data
    finally:
        sel.close()
        proc.stdout.close()
        proc.stderr.close()
    proc.wait(timeout=max(0.0, deadline - time.monotonic()))
    return bytes(bufs[proc.stdout]), bytes(bufs[proc.stderr]), False


def _kill_tree(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    # The direct child too, unconditionally: with killpg unable to reach the
    # group, waiting on a spinning candidate would never return.
    proc.kill()
    proc.wait()


def _launch(cmd, tmp, timeout):
    """Start a candidate in its own session, sandboxed, and read it bounded.

    Returns (out, err, returncode, failure); `failure` is set when the run was
    KILLED rather than finished, and no output may be trusted in that case.
    Shared by every language so a bash or CMake candidate gets exactly the
    Python path's temp dir, RLIMITs, scrubbed env, netns and process-group kill.
    """
    if _netns_available():
        cmd = ["unshare", "-rn", "prlimit", f"--nproc={RLIMIT_NPROC}"] + cmd
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "TMPDIR": tmp,
    }
    proc = subprocess.Popen(
        cmd,
        cwd=tmp,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=_candidate_rlimits,
        start_new_session=True,
    )
    try:
        out, err, overflowed = _communicate_bounded(proc, timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return "", "", None, f"timed out after {timeout}s (likely an infinite loop)"
    if overflowed:
        _kill_tree(proc)
        return "", "", None, f"output exceeded {OUTPUT_LIMIT_BYTES} bytes"
    return (
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
        proc.returncode,
        None,
    )


def run_candidate(
    code, tests, timeout=15, forbidden=None, stdlib_only=False, lang="python"
):
    """Execute the generated solution against the tests, in its own language.

    Returns (ok, detail, credit). `ok` still means "every assertion passed" --
    partial credit is reported alongside, not used to lower the bar. A language
    whose tool is missing returns ok=False with credit["skipped"] set: a skip is
    never a pass (see _skipped).
    """
    if not code.strip():
        return False, "no code found in reply", {"passed": 0, "total": 0}
    runner = _RUNNERS.get(lang)
    if runner is None:
        return False, f"unsupported lang {lang!r}", {"passed": 0, "total": 0}
    return runner(code, tests, timeout, forbidden, stdlib_only)


def _run_python(code, tests, timeout=15, forbidden=None, stdlib_only=False):
    """The Python path.

    The candidate runs in the same process as the harness, so the harness's
    verdict travels over a channel the candidate can write to. That is why:
    the marker is a per-run nonce, the LAST occurrence is read, and a row
    count that differs from the number of assertions in the tests is a
    failure, never a pass -- `print("__ASSERTIONS__[]")` used to score
    "all assertions passed". None of this makes the grader adversary-proof
    (the candidate can read its own file); it stops the cheap fakes. The
    trust boundary stays where the README puts it.
    """
    violation = check_forbidden(code, forbidden)
    if violation:
        return False, violation, {"passed": 0, "total": 0}
    if stdlib_only:
        extra = _nonstd_imports(code)
        if extra:
            return (
                False,
                (
                    f"imported {', '.join(extra)}, which is not in the standard "
                    f"library the prompt restricted it to"
                ),
                {"passed": 0, "total": 0},
            )
    harness, expected = _assertion_harness(tests)
    marker = "__ASSERTIONS_" + secrets.token_hex(8) + "__"
    # __name__ is not "__main__": a demo left under the usual guard must not
    # run, read argv, or call input() against the harness's stdin.
    program = (
        f"_MARKER = {marker!r}\n__name__ = 'candidate'\n" + code + "\n\n" + harness
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "candidate.py")
        with open(path, "w") as f:
            f.write(program)
        # Own session (a timeout kills the whole tree), scrubbed env, rlimits
        # and a netns where the kernel allows. Not a sandbox: see README § Trust.
        out, err, rc, failure = _launch([sys.executable, "-I", path], tmp, timeout)
    if failure:
        return False, failure, {"passed": 0, "total": 0}
    return _python_marker_verdict(out, err, rc, marker, expected)


def _python_marker_verdict(out, err, rc, marker, expected):
    """Read the harness's nonce-marked row report out of a Python candidate."""
    if marker in out:
        try:
            payload = json.loads(out.rsplit(marker, 1)[1].splitlines()[0])
            rows, setup = payload["rows"], payload["setup"]
        except (ValueError, KeyError, IndexError, TypeError):
            return False, "harness output corrupted", {"passed": 0, "total": 0}
        if len(rows) != expected:
            return (
                False,
                (
                    f"harness output corrupted: {len(rows)} rows for "
                    f"{expected} assertions"
                ),
                {"passed": 0, "total": 0},
            )
        passed = sum(1 for _, ok, _ in rows if ok)
        detail_rows = [{"index": i, "passed": ok, "error": e} for i, ok, e in rows]
        credit = {
            "passed": passed,
            "total": len(rows),
            "assertions": detail_rows,
            "setup_failures": setup,
        }
        if setup:
            return False, f"test setup raised: {setup[0][:80]}", credit
        if passed == len(rows):
            return True, "all assertions passed", credit
        first = next((r for r in rows if not r[1]), None)
        return (
            False,
            (
                f"{passed}/{len(rows)} assertions passed; first failure: "
                f"{first[2][:80]}"
                if first
                else "failed"
            ),
            credit,
        )

    # The program did not even reach the harness -- a syntax error, or the code
    # raised at import time. Name the exception, not whatever the last line of
    # output happened to be (unittest's "OK" was being reported as the reason).
    lines = (err or out or "").strip().splitlines()
    named = [
        ln for ln in lines if re.match(r"^\w+(Error|Exception|Exit)\b", ln.strip())
    ]
    detail = named[-1] if named else (lines[-1] if lines else f"exit {rc}")
    return False, detail[:120], {"passed": 0, "total": 0}


# ── Other languages ───────────────────────────────────────────────────────────
# Each runner keeps the Python path's sandbox exactly. Why non-Python tasks
# exist at all: docs/llm-benchmark-review-2026-09-05.md § R5.

KINDS = ("spec-transcription", "from-examples", "bug-fix", "design")
LANGS = ("python", "bash", "cmake", "dockerfile")


def tool_available(name):
    return shutil.which(name) is not None


def _skipped(reason):
    """Not a pass and not a fail: the tool that would judge this is absent.

    Returned as ok=False with credit["skipped"] set, so a host without cmake
    cannot turn an ungraded task into a green row -- the whole point of the
    visible skip. evaluate() excludes these from the denominator and prints
    them, exactly like a transport error.
    """
    return False, f"SKIPPED: {reason}", {"passed": 0, "total": 0, "skipped": reason}


# One counted check per call at a line start. Loops are not supported: the
# expected count is static, so a variable row count would read as forgery.
_SHELL_ASSERT = re.compile(r"^[ \t]*assert_(?:eq|ok|fail)\b", re.M)

_BASH_PRELUDE = r"""
__BENCH_ROWS=()
__bench_ok() { __BENCH_ROWS+=("P"); }
# One row is one LINE: a message carrying a newline would be read back as
# extra rows and look like a forged report.
__bench_no() {
    local __m="$*"
    __BENCH_ROWS+=("F ${__m//$'\n'/ | }")
}

# assert_eq <expected> <actual> [label]
assert_eq() {
    if [ "$1" = "$2" ]; then
        __bench_ok
    else
        __bench_no "${3:-assert_eq}: expected [$1] got [$2]"
    fi
}

# assert_ok <label> <command...> -- the command must exit 0. Output is
# captured rather than redirected: only the status is under test.
assert_ok() {
    local __label="$1"; shift
    local __rc=0
    local __out=""
    __out=$("$@" 2>&1) || __rc=$?
    if [ "$__rc" -eq 0 ]; then
        __bench_ok
    else
        __bench_no "$__label: exit $__rc"
    fi
}

# assert_fail <label> <command...> -- the command must exit non-zero
assert_fail() {
    local __label="$1"; shift
    local __rc=0
    local __out=""
    __out=$("$@" 2>&1) || __rc=$?
    if [ "$__rc" -ne 0 ]; then
        __bench_ok
    else
        __bench_no "$__label: expected a non-zero exit"
    fi
}

# On EXIT, so a candidate killed by `set -e` halfway still reports what it
# reached: fewer rows is a partial, more is forgery (see _marker_rows).
__bench_report() {
    local __rc=$?
    printf '%s\n' "$__BENCH_MARKER"
    if [ "${#__BENCH_ROWS[@]}" -gt 0 ]; then
        printf '%s\n' "${__BENCH_ROWS[@]}"
    fi
    printf '%s\n' "$__BENCH_MARKER"
    return "$__rc"
}
trap __bench_report EXIT
"""

_CMAKE_PRELUDE = r"""cmake_minimum_required(VERSION 3.16)
set(__BENCH_ROWS)
# A macro, not a function: it must append in the CALLER's scope. One row is one
# line, so newlines and the list separator are flattened out of the message.
macro(assert_eq __expected __actual __label)
  if("${__expected}" STREQUAL "${__actual}")
    list(APPEND __BENCH_ROWS "P")
  else()
    set(__bench_msg "F ${__label}: expected [${__expected}] got [${__actual}]")
    string(REPLACE "\n" " | " __bench_msg "${__bench_msg}")
    string(REPLACE ";" "," __bench_msg "${__bench_msg}")
    list(APPEND __BENCH_ROWS "${__bench_msg}")
  endif()
endmacro()
"""

_CMAKE_REPORT = """
message("${__BENCH_MARKER}")
foreach(__row IN LISTS __BENCH_ROWS)
  message("${__row}")
endforeach()
message("${__BENCH_MARKER}")
"""

# The candidate text is data here, never executed: the checks are Python
# assertions over the parsed instruction list.
_DOCKERFILE_PREAMBLE = '''
def _parse_dockerfile(text):
    """[(VERB, argument text)], continuations joined, comments and blanks gone."""
    out, pending = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\\\"):
            pending += line[:-1].strip() + " "
            continue
        line, pending = pending + line, ""
        verb, _, rest = line.partition(" ")
        out.append((verb.upper(), rest.strip()))
    if pending.strip():
        verb, _, rest = pending.strip().partition(" ")
        out.append((verb.upper(), rest.strip()))
    return out


INSTRUCTIONS = _parse_dockerfile(DOCKERFILE)


def instructions(verb):
    return [args for v, args in INSTRUCTIONS if v == verb.upper()]


def verbs():
    return [v for v, _ in INSTRUCTIONS]
'''


def _marker_rows(out, marker, expected):
    """Rows between the LAST pair of nonce markers.

    (rows, failure). More rows than the tests contain is a forged report and
    fails outright; fewer means the script died partway and is scored on what
    it did reach.
    """
    parts = out.split(marker)
    if len(parts) < 3:
        return None, None
    rows = []
    for line in parts[-2].splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append((True, "") if line == "P" else (False, line[1:].strip()[:80]))
    if len(rows) > expected:
        return None, (
            f"harness output corrupted: {len(rows)} rows for {expected} assertions"
        )
    return rows, None


def _credit_from_rows(rows, expected):
    passed = sum(1 for ok, _ in rows if ok)
    return passed, {
        "passed": passed,
        "total": expected,
        "assertions": [
            {"index": i, "passed": ok, "error": e} for i, (ok, e) in enumerate(rows)
        ],
        "setup_failures": [],
    }


def _rows_verdict(rows, expected, credit, passed, rc, err, note):
    """The shared pass/partial/died verdict for the marker-protocol languages."""
    if passed == expected and len(rows) == expected:
        return True, f"all assertions passed{note}", credit
    first = next((e for ok, e in rows if not ok), "")
    if len(rows) < expected:
        tail = (err or "").strip().splitlines()
        died = tail[-1][:60] if tail else f"exit {rc}"
        return (
            False,
            (
                f"{passed}/{expected} assertions passed; the script "
                f"stopped after {len(rows)}: {died}{note}"
            ),
            credit,
        )
    return (
        False,
        (f"{passed}/{expected} assertions passed; first failure: {first[:80]}{note}"),
        credit,
    )


def shellcheck_note(path, timeout=60):
    """(failure, note) for `shellcheck -S error`, or a VISIBLE skip note.

    An absent shellcheck must never read as a clean one: the note travels into
    the result detail and into the report, so a run on a host without it says
    so on every bash row.
    """
    if not tool_available("shellcheck"):
        return None, " [shellcheck SKIPPED: not on PATH]"
    try:
        p = subprocess.run(
            ["shellcheck", "-S", "error", "-s", "bash", path],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f" [shellcheck SKIPPED: {type(e).__name__}]"
    if p.returncode != 0:
        first = next(
            (ln.strip() for ln in (p.stdout + p.stderr).splitlines() if ln.strip()),
            "shellcheck reported an error",
        )
        return f"shellcheck -S error: {first[:100]}", " [shellcheck FAILED]"
    return None, " [shellcheck clean]"


def _run_bash(code, tests, timeout=15, forbidden=None, stdlib_only=False):
    """bash -euo pipefail, plus `shellcheck -S error` where it exists."""
    violation = _check_forbidden_text(code, forbidden)
    if violation:
        return False, violation, {"passed": 0, "total": 0}
    if not tool_available("bash"):
        return _skipped("bash not on PATH")
    expected = len(_SHELL_ASSERT.findall(tests))
    marker = "__ASSERTIONS_" + secrets.token_hex(8) + "__"
    # The verdict rides on __bench_report, so re-arm it after the candidate (a
    # top-level `trap ... EXIT` of its own replaced ours) and call it outright.
    program = (
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"__BENCH_MARKER={marker!r}\n"
        + _BASH_PRELUDE
        + "\n# ── candidate ──\n"
        + code
        + "\n\ntrap __bench_report EXIT\n"
        + "\n# ── checks ──\n"
        + tests
        + "\n__bench_report\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "candidate.sh")
        with open(path, "w") as f:
            f.write(program)
        lint = os.path.join(tmp, "solution.sh")
        with open(lint, "w") as f:
            f.write("#!/usr/bin/env bash\nset -euo pipefail\n" + code + "\n")
        failure, note = shellcheck_note(lint)
        if failure:
            return False, failure, {"passed": 0, "total": 0, "shellcheck": note.strip()}
        out, err, rc, killed = _launch(["bash", path], tmp, timeout)
    if killed:
        return (
            False,
            killed + note,
            {"passed": 0, "total": 0, "shellcheck": note.strip()},
        )
    rows, corrupt = _marker_rows(out, marker, expected)
    if corrupt:
        return (
            False,
            corrupt + note,
            {"passed": 0, "total": 0, "shellcheck": note.strip()},
        )
    if rows is None:
        lines = (err or out or "").strip().splitlines()
        detail = lines[-1][:120] if lines else f"exit {rc}"
        return (
            False,
            f"the harness report never printed: {detail}{note}",
            {"passed": 0, "total": 0, "shellcheck": note.strip()},
        )
    passed, credit = _credit_from_rows(rows, expected)
    credit["shellcheck"] = note.strip()
    return _rows_verdict(rows, expected, credit, passed, rc, err, note)


def _run_cmake(code, tests, timeout=15, forbidden=None, stdlib_only=False):
    """cmake -P, with the checks asserting on what the script computed."""
    violation = _check_forbidden_text(code, forbidden)
    if violation:
        return False, violation, {"passed": 0, "total": 0}
    if not tool_available("cmake"):
        return _skipped("cmake not on PATH")
    expected = len(_SHELL_ASSERT.findall(tests))
    marker = "__ASSERTIONS_" + secrets.token_hex(8) + "__"
    program = (
        _CMAKE_PRELUDE
        + f'set(__BENCH_MARKER "{marker}")\n'
        + "\n# candidate\n"
        + code
        + "\n\n# checks\n"
        + tests
        + "\n"
        + _CMAKE_REPORT
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "candidate.cmake")
        with open(path, "w") as f:
            f.write(program)
        out, err, rc, killed = _launch(["cmake", "-P", path], tmp, timeout)
    if killed:
        return False, killed, {"passed": 0, "total": 0}
    # message() in script mode writes to stderr, so both streams are searched.
    rows, corrupt = _marker_rows(out + err, marker, expected)
    if corrupt:
        return False, corrupt, {"passed": 0, "total": 0}
    if rows is None:
        lines = (err or out or "").strip().splitlines()
        detail = lines[-1][:120] if lines else f"exit {rc}"
        return False, detail, {"passed": 0, "total": 0}
    passed, credit = _credit_from_rows(rows, expected)
    return _rows_verdict(rows, expected, credit, passed, rc, err, "")


def hadolint_note(path, timeout=60):
    """(failure, note) for hadolint at error severity, or a VISIBLE skip note."""
    if not tool_available("hadolint"):
        return None, " [hadolint SKIPPED: not on PATH]"
    try:
        p = subprocess.run(
            ["hadolint", "--failure-threshold", "error", path],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f" [hadolint SKIPPED: {type(e).__name__}]"
    if p.returncode != 0:
        first = next(
            (ln.strip() for ln in (p.stdout + p.stderr).splitlines() if ln.strip()),
            "hadolint reported an error",
        )
        return f"hadolint: {first[:100]}", " [hadolint FAILED]"
    return None, " [hadolint clean]"


def _run_dockerfile(code, tests, timeout=15, forbidden=None, stdlib_only=False):
    """hadolint where it exists, plus a structural check that always runs.

    The structural half is Python assertions over the parsed instruction list,
    so a host without hadolint still grades the task -- and says on the row
    that the linter did not run.
    """
    violation = _check_forbidden_text(code, forbidden)
    if violation:
        return False, violation, {"passed": 0, "total": 0}
    harness, expected = _assertion_harness(tests)
    marker = "__ASSERTIONS_" + secrets.token_hex(8) + "__"
    program = (
        f"_MARKER = {marker!r}\n__name__ = 'candidate'\n"
        f"DOCKERFILE = {code!r}\n" + _DOCKERFILE_PREAMBLE + "\n" + harness
    )
    with tempfile.TemporaryDirectory() as tmp:
        dockerfile = os.path.join(tmp, "Dockerfile")
        with open(dockerfile, "w") as f:
            f.write(code if code.endswith("\n") else code + "\n")
        failure, note = hadolint_note(dockerfile)
        if failure:
            return False, failure, {"passed": 0, "total": 0, "hadolint": note.strip()}
        path = os.path.join(tmp, "structure.py")
        with open(path, "w") as f:
            f.write(program)
        out, err, rc, killed = _launch([sys.executable, "-I", path], tmp, timeout)
    if killed:
        return False, killed, {"passed": 0, "total": 0, "hadolint": note.strip()}
    ok, detail, credit = _python_marker_verdict(out, err, rc, marker, expected)
    credit["hadolint"] = note.strip()
    return ok, detail + note, credit


_RUNNERS = {
    "python": _run_python,
    "bash": _run_bash,
    "cmake": _run_cmake,
    "dockerfile": _run_dockerfile,
}


def ask(base_url, model, prompt, max_tokens, timeout=1800, deadline=None, entry=None):
    """One streamed request.

    Returns (text, ttft_s, wall_s, chunks, prompt_tokens, think, finish,
    completion_tokens, gave_up) -- completion_tokens is None when the server
    reports no usage, and `gave_up` says the wall-clock deadline stopped this,
    not the model.

    `timeout` is urlopen's, which is PER SOCKET READ, not for the request. A
    model that keeps emitting tokens therefore never trips it: measured
    2026-09-05, a 4B spent **over an hour** on one task under an 8000-token
    budget, delivering deltas the whole time, and blocked the sweep. `deadline`
    is the missing total-duration cap: when the wall-clock passes it the stream
    is abandoned and what arrived so far is returned, flagged. A run that never
    ends is not a measurement, and it must not be able to take the sweep with
    it.
    Reasoning served as a separate delta field (`reasoning_content` on
    llama.cpp/vLLM, `reasoning` on Ollama) is collected too: dropping it made
    `out=` undercount, `think=` read 0 % and, worst, a reply that spent its
    whole budget reasoning arrive as text='' chunks=0 -- graded "wrong" when
    it was cut off. `finish` is the server's finish_reason, so a cut the
    server itself reports ("length") no longer depends on counting deltas.
    `entry` is the backends.json entry: its api_key_env, headers and
    request_extra are applied by bench_cli.post_json, so a hosted endpoint is
    measurable and a per-lane knob is configured in one place.
    """
    body = {
        "model": model,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
        # Ask for usage so the cap is judged on a real token count where the
        # server provides one; the delta count is a proxy that is exact on
        # GenieX (one delta per token) and wrong on servers that batch.
        "stream_options": {"include_usage": True},
    }
    ttft = None
    parts, think_parts = [], []
    prompt_tokens = None
    completion_tokens = None
    finish = None
    gave_up = False
    with bench_cli.post_json(
        f"{base_url}/v1/chat/completions",
        body,
        entry=entry,
        stream=True,
        timeout=timeout,
        deadline=deadline,
    ) as resp:
        # One clock: post_json starts it before the request is sent, so a slow
        # prefill counts against the deadline and against ttft alike.
        started = resp.started
        for line in resp.lines():
            # A fault inside an already-200 stream is a transport error, not an
            # empty answer: dropped, it graded FAIL "no code found in reply".
            if line.startswith("error:"):
                raise RuntimeError(f"server error in stream: {line[6:].strip()[:200]}")
            if not line.startswith("data:"):
                continue
            payload = line[5:].lstrip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(chunk, dict) and chunk.get("error"):
                raise RuntimeError(
                    f"server error in stream: {json.dumps(chunk['error'])[:200]}"
                )
            if chunk.get("usage"):
                prompt_tokens = chunk["usage"].get("prompt_tokens")
                completion_tokens = chunk["usage"].get(
                    "completion_tokens", completion_tokens
                )
            choices = chunk.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                finish = choices[0].get("finish_reason") or finish
                thought = delta.get("reasoning_content") or delta.get("reasoning")
                if thought:
                    if ttft is None:
                        ttft = time.monotonic() - started
                    think_parts.append(thought)
                piece = delta.get("content")
                if piece:
                    if ttft is None:
                        ttft = time.monotonic() - started
                    parts.append(piece)
        gave_up = resp.gave_up
    text = "".join(parts)
    return (
        text,
        ttft,
        time.monotonic() - started,
        len(parts) + len(think_parts),
        prompt_tokens,
        "".join(think_parts),
        finish,
        completion_tokens,
        gave_up,
    )


_OVERFLOW_BODY = re.compile(
    r"context|too (?:long|many tokens)"
    r"|(?:context|prompt|input)[^.]{0,40}exceed"
    r"|max(?:imum)?_? ?(?:tokens|length)"
    r"|(?:prompt|input|request) (?:is )?too",
    re.I,
)
# A throttled or unpaid lane says "exceeded" too. Calling that a context
# overflow publishes a false cause for rows nothing measured.
_NOT_OVERFLOW_BODY = re.compile(r"rate.?limit|quota|billing", re.I)


def _overflow_reason(exc):
    """A 4xx whose body says the prompt did not fit: the same 4096 ceiling that
    reads CUT when the server streams it. Returns the body excerpt, or None.

    A 429, or a body naming a rate limit, quota or billing, is NOT this: it
    falls through to the honest `errored` path.
    """
    if not isinstance(exc, urllib.error.HTTPError) or not 400 <= exc.code < 500:
        return None
    if exc.code == 429:
        return None
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        body = ""
    if _NOT_OVERFLOW_BODY.search(body):
        return None
    return f"HTTP {exc.code}: {body[:160]}" if _OVERFLOW_BODY.search(body) else None


def _measured(r):
    """Did this attempt observe the model? Errors, cuts, overflows and the
    skipped rows of a language whose tool is absent did not."""
    return not (
        r.get("errored") or r.get("truncated") or r.get("overflow") or r.get("skipped")
    )


def rates_by(results, tasks, field):
    """Pass rate per `kind` or per `lang`, over MEASURED attempts only.

    A single number over a mixed set hides the thing R5 exists to expose: a
    model at 27/27 on Python functions and 0/4 on bash reads as 27/31.
    """
    by_task = {
        t["name"]: t.get(field, "python" if field == "lang" else "unknown")
        for t in tasks
    }
    out = {}
    for r in results:
        group = out.setdefault(
            by_task.get(r["task"], "unknown"),
            {"passed": 0, "measured": 0, "skipped": 0, "excluded": 0},
        )
        if r.get("skipped"):
            group["skipped"] += 1
        elif not _measured(r):
            group["excluded"] += 1
        else:
            group["measured"] += 1
            group["passed"] += int(bool(r["passed"]))
    return dict(sorted(out.items()))


def evaluate(
    base_url,
    model,
    label,
    max_tokens,
    keep_output=False,
    repeats=1,
    context_tokens=0,
    warmup=True,
    deadline=None,
    entry=None,
    backend=None,
):
    """Run every task against one model. Returns a report dict.

    `entry` is the backends.json entry (auth, headers, request_extra) and
    `backend` its registry name, recorded on the row so a ranking can tell a
    control endpoint from a candidate without re-resolving anything.

    `repeats` matters more than it looks: GenieX ignores `temperature` (it
    honours `max_tokens` since v0.6.1), so the llama.cpp lanes SAMPLE at 0 --
    five identical requests to the 2B produced five different answers, four
    passing and one failing the same task. A single run therefore measures one
    draw, not the model. (The QAIRT/NPU path is deterministic: four identical
    requests, one unique output, so repeats there only cost time.)
    """
    if warmup:
        # Without this the FIRST task of each model carries its load time and
        # the ranking partly ranks load order: ~34s of the 27B's 128.7s total
        # was loading, 26% of its score-deciding number.
        try:
            ask(
                base_url,
                model,
                "Reply with the single word: ready.",
                16,
                timeout=1800,
                entry=entry,
            )
        except Exception as e:  # noqa: BLE001 — a failed warmup is not fatal
            print(
                f"    (warmup failed: {type(e).__name__}; first task may "
                f"include model load time)",
                flush=True,
            )

    context = build_context(context_tokens)
    if context:
        print(f"\n  === {label}  [+{context_tokens} tokens of context] ===", flush=True)
    else:
        print(f"\n  === {label} ===", flush=True)
    results = []
    for task in TASKS:
        for attempt in range(repeats):
            suffix = f" [{attempt + 1}/{repeats}]" if repeats > 1 else ""
            prompt = task["prompt"]
            if context:
                prompt = (
                    "Here is context from a repository, for style reference only:\n\n"
                    f"{context}\n\n---\n\nNow, independently of the above:\n" + prompt
                )
            try:
                text, ttft, wall, chunks, ptok, think, finish, ctok, gave_up = ask(
                    base_url, model, prompt, max_tokens, deadline=deadline, entry=entry
                )
            except Exception as e:  # noqa: BLE001 — one dead task must not end the sweep
                overflow = _overflow_reason(e)
                if overflow:
                    # The prompt did not fit the context: unmeasured like a cut,
                    # recorded apart from it -- the model never saw the task.
                    print(
                        f"    {task['name']:15s}{suffix} OVERFLOW {overflow[:60]}",
                        flush=True,
                    )
                    results.append(
                        {
                            "task": task["name"],
                            "attempt": attempt,
                            "passed": False,
                            "overflow": True,
                            "truncated": False,
                            "errored": False,
                            "detail": f"OVERFLOW ({overflow}) - not graded as wrong",
                            "wall_s": None,
                            "prompt_tokens": None,
                        }
                    )
                    continue
                print(
                    f"    {task['name']:15s}{suffix} ERROR {type(e).__name__}: {e}",
                    flush=True,
                )
                # A transport failure is not evidence about the model; excluding it
                # keeps a dropped connection from permanently lowering the score.
                results.append(
                    {
                        "task": task["name"],
                        "attempt": attempt,
                        "passed": False,
                        "errored": True,
                        "detail": f"request failed: {e}",
                        "wall_s": None,
                        "prompt_tokens": None,
                    }
                )
                continue
            lang = task.get("lang", "python")
            code = extract_code(
                text, want=task.get("function") or _want_from_prompt(task), lang=lang
            )
            ok, detail, credit = run_candidate(
                code,
                task["tests"],
                forbidden=task.get("forbidden"),
                stdlib_only=task.get("stdlib_only", False),
                lang=lang,
            )
            skipped = credit.get("skipped")
            count = ctok if ctok is not None else chunks
            # A deadline hit is UNMEASURED for the same reason a server cut is: the
            # model never finished. Named apart from it, because the cause differs
            # -- one is a budget, the other is a model that does not terminate.
            truncated = (
                (not ok)
                and (not skipped)
                and (
                    gave_up
                    or looks_truncated(
                        text, count, code, finish, cap=generation_cap(max_tokens)
                    )
                )
            )
            if skipped:
                detail = f"SKIPPED ({skipped}) - not graded either way"
            elif gave_up:
                detail = (
                    f"GAVE UP after {wall:.0f}s ({count} tokens and still "
                    f"generating) - not graded as wrong"
                )
            elif truncated:
                unit = "tokens" if ctok is not None else "deltas"
                # Not "the server cap" any more: since GenieX v0.6 the ceiling is
                # whichever binds first -- the request's own max_tokens, or the
                # model's context. A QAIRT bundle stopped at 3961 output tokens
                # under a 8000-token budget because 136 prompt + 3961 = its
                # compiled 4096 context, which no budget can raise.
                room = f"{ptok} prompt + {count} out" if ptok else f"{count} {unit}"
                detail = (
                    f"CUT OFF at {count} {unit} ({room}; budget {max_tokens}) "
                    f"- not graded as wrong"
                )
            think_share = 0.0
            if think:
                think_share = len(think) / (len(think) + len(text))
            elif "</think>" in text:
                think_share = 1 - len(text.split("</think>")[-1]) / len(text)
            verdict = (
                "PASS"
                if ok
                else ("SKIP" if skipped else ("CUT " if truncated else "FAIL"))
            )
            # Partial credit beside the verdict: 6-of-7 assertions is not the same
            # result as 0-of-7, and pass/fail alone cannot tell them apart.
            partial = (
                f"{credit['passed']}/{credit['total']} asserts  "
                if credit["total"] and not ok
                else ""
            )
            pt = f"  ptok={ptok:5d}" if ptok else ""
            print(
                f"    {task['name']:15s}{suffix} {verdict}  "
                f"{wall:6.1f}s  ttft={ttft or 0:5.2f}s{pt}  out={chunks:5d}  "
                f"think={100 * think_share:3.0f}%  {partial}{'' if ok else detail[:58]}",
                flush=True,
            )
            # `row`, never `entry`: `entry` is this function's backends.json
            # parameter and rebinding it here stripped auth from every later ask().
            row = {
                "task": task["name"],
                "attempt": attempt,
                "kind": task.get("kind", "unknown"),
                "lang": lang,
                "passed": ok,
                "truncated": truncated,
                "gave_up": gave_up,
                "skipped": skipped,
                "detail": detail,
                "linter": credit.get("shellcheck") or credit.get("hadolint"),
                "output_sha256": hashlib.sha256((think + text).encode()).hexdigest(),
                "assertions_passed": credit["passed"],
                "assertions_total": credit["total"],
                "wall_s": round(wall, 2),
                "ttft_s": round(ttft, 3) if ttft else None,
                "tokens": count,
                "tokens_estimated": ctok is None,
                "prompt_tokens": ptok,
                "thinking_char_share": round(think_share, 3),
            }
            if keep_output:
                row["code"] = code
            results.append(row)

    # Wall statistics over MEASURED attempts only: a cut attempt's seconds
    # decided the rank tiebreak while being excluded from the rate.
    done = [r for r in results if r["wall_s"] is not None and _measured(r)]
    unmeasured_wall = sum(
        r["wall_s"] for r in results if r["wall_s"] is not None and not _measured(r)
    )
    passed = sum(1 for r in results if r["passed"])
    cut = sum(1 for r in results if r.get("truncated"))
    errored = sum(1 for r in results if r.get("errored"))
    overflow = sum(1 for r in results if r.get("overflow"))
    # A language whose tool is absent was never graded. Counting those rows as
    # misses would rank a host without cmake as a worse MODEL.
    skipped = sum(1 for r in results if r.get("skipped"))
    # A cut attempt is UNMEASURED and is treated exactly like a transport
    # error: listed, but not a trial. It used to be printed as "not failed"
    # and then counted as a miss in the rate, the interval and the rank.
    attempts = len(TASKS) * repeats - errored - cut - overflow - skipped
    wrong = attempts - passed
    total_wall = sum(r["wall_s"] for r in done)
    walls = [r["wall_s"] for r in done]

    # How many INDEPENDENT observations are behind that score? On a
    # deterministic endpoint repeats return the identical answer, so counting
    # them as independent trials inflates the apparent sample without adding
    # information. Determinism is detected from the OUTPUT (one hash per task
    # across its measured repeats), not from pass/fail agreement -- a sampling
    # endpoint that fails a task on every draw agrees on the verdict too.
    # Errored and cut attempts do not vote: one dropped connection used to flip
    # a deterministic lane to "sampling" and narrow the printed interval.
    measured = [r for r in results if _measured(r)]
    per_task_hash, per_task_outcome = {}, {}
    for r in measured:
        per_task_hash.setdefault(r["task"], set()).add(r["output_sha256"])
        per_task_outcome.setdefault(r["task"], set()).add(r["passed"])
    deterministic = (
        repeats > 1
        and bool(per_task_hash)
        and all(len(v) == 1 for v in per_task_hash.values())
    )
    repeats_agreed = (
        repeats > 1
        and bool(per_task_outcome)
        and all(len(v) == 1 for v in per_task_outcome.values())
    )
    # Never round a ratio into a count. When the lane is deterministic the
    # unit is the task: n = tasks with at least one measured attempt, k =
    # tasks that passed. round(passed * n / total) printed 8/9 for a model
    # that passed 7 tasks, and "passed" a task that was never observed.
    if deterministic:
        effective_n = len(per_task_outcome)
        effective_k = sum(1 for v in per_task_outcome.values() if v == {True})
    else:
        effective_n, effective_k = attempts, passed

    abandoned = sum(1 for r in results if r.get("gave_up"))
    extra = f", {cut} cut off (unmeasured, excluded)" if cut else ""
    if abandoned:
        extra += f" of which {abandoned} ABANDONED at the deadline"
    if overflow:
        extra += f", {overflow} OVERFLOW (prompt did not fit the context, excluded)"
    if errored:
        extra += f", {errored} EXCLUDED (transport errors)"
    if skipped:
        reasons = sorted({r["skipped"] for r in results if r.get("skipped")})
        extra += f", {skipped} SKIPPED ({'; '.join(reasons)})"
    unit = "attempts" if repeats > 1 else "tasks"
    unmeasured = (
        f" (+{unmeasured_wall:.1f}s in unmeasured attempts)" if unmeasured_wall else ""
    )
    print(
        f"    -> {passed}/{attempts} {unit} pass{extra}, {total_wall:.1f}s total{unmeasured}",
        flush=True,
    )
    if walls:
        print(
            f"       per attempt: median {statistics.median(walls):.1f}s, "
            f"min {min(walls):.1f}s, max {max(walls):.1f}s"
            + (f", stdev {statistics.stdev(walls):.1f}s" if len(walls) > 1 else ""),
            flush=True,
        )
    if deterministic:
        print(
            f"       NOTE: every repeat produced byte-identical output — this "
            f"endpoint is deterministic, so the effective sample is "
            f"{effective_n} tasks, not {attempts} attempts.",
            flush=True,
        )
    elif repeats_agreed:
        print(
            f"       NOTE: every repeat agreed on pass/fail but the outputs "
            f"differed — a sampling endpoint; attempts are counted as trials.",
            flush=True,
        )
    by_kind = rates_by(results, TASKS, "kind")
    by_lang = rates_by(results, TASKS, "lang")
    if len(by_lang) > 1 or len(by_kind) > 1:
        print(
            "       "
            + "  ".join(
                f"{name}={g['passed']}/{g['measured']}"
                + (f" (+{g['skipped']} skipped)" if g["skipped"] else "")
                for name, g in list(by_lang.items()) + list(by_kind.items())
            ),
            flush=True,
        )
    return {
        "label": label,
        "model": model,
        "base_url": base_url,
        # The registry name, so suspect_cases() can find the control row.
        "backend": backend,
        "passed": passed,
        "wrong": wrong,
        "truncated": cut,
        "total": attempts,
        "repeats": repeats,
        "tasks": len(TASKS),
        "errored": errored,
        "skipped": skipped,
        "by_kind": by_kind,
        "by_lang": by_lang,
        "abandoned": abandoned,
        "overflow": overflow,
        "deterministic": deterministic,
        "repeats_agreed": repeats_agreed,
        "effective_n": effective_n,
        "effective_k": effective_k,
        "context_tokens": context_tokens,
        "total_wall_s": round(total_wall, 2),
        # The same seconds under the name bench_compare's timing verdict
        # prefers: wall over MEASURED attempts only.
        "wall_measured_s": round(total_wall, 2),
        "unmeasured_wall_s": round(unmeasured_wall, 2),
        "avg_wall_s": round(total_wall / len(done), 2) if done else None,
        "median_wall_s": round(statistics.median(walls), 2) if walls else None,
        "stdev_wall_s": round(statistics.stdev(walls), 2) if len(walls) > 1 else None,
        "results": results,
    }


def _determinism_extra(candidates):
    """temperature/seed and a two-draw determinism probe for the provenance.

    Measured once per run on the lane provenance names, so "does this lane
    sample at T=0" is evidence in the file rather than a per-run rediscovery.
    """
    from orchestrant.benchmark.client import post_json
    from orchestrant.benchmark.provenance import determinism_probe

    if not candidates:
        return {"temperature": 0, "seed": None, "determinism_probe": None}
    first = candidates[0]

    def post(url, payload):
        with post_json(url, payload, entry=first.get("entry"), timeout=120) as r:
            return r.json()

    try:
        probe = determinism_probe(first["base_url"], first["model"], post)
    except SystemExit as e:  # an unset api_key_env must not lose the report
        probe = {
            "deterministic": None,
            "requests": 0,
            "error": f"SystemExit: {e}"[:200],
        }
    return {"temperature": 0, "seed": None, "determinism_probe": probe}


def grader_selfcheck(tasks):
    """Run every task's reference through the real grading path first.

    If `unshare -rn python -I` or an rlimit breaks on a host, every model
    scores identically with the same stderr -- indistinguishable from "the
    models are bad". Returns the record for the report; raises SystemExit
    with the first failure named.
    """
    started = time.monotonic()
    skipped = {}
    for task in tasks:
        lang = task.get("lang", "python")
        want = task.get("function") or _want_from_prompt(task)
        code = extract_code("```\n" + task["reference"] + "\n```", want=want, lang=lang)
        ok, detail, credit = run_candidate(
            code,
            task["tests"],
            forbidden=task.get("forbidden"),
            stdlib_only=task.get("stdlib_only", False),
            lang=lang,
        )
        if credit.get("skipped"):
            # Not a pass: the row says which tasks nothing checked, so a host
            # missing cmake reports a hole rather than a clean self-check.
            skipped[task["name"]] = credit["skipped"]
            continue
        if not ok:
            sys.exit(
                f"GRADER SELF-CHECK FAILED: reference for {task['name']!r} graded "
                f"{detail!r} -- nothing measured below this line would mean anything"
            )
    return {
        "tasks": len(tasks),
        "passed": True,
        "netns": _netns_available(),
        "checked": len(tasks) - len(skipped),
        "skipped": skipped,
        "tools": {
            name: tool_available(name)
            for name in ("bash", "shellcheck", "cmake", "hadolint")
        },
        "seconds": round(time.monotonic() - started, 2),
        "rlimits": {
            "as_bytes": RLIMIT_AS_BYTES,
            "fsize_bytes": RLIMIT_FSIZE_BYTES,
            "nproc": RLIMIT_NPROC,
            "nproc_ceiling": _nproc_ceiling(),
        },
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--backend", default=None, help="Named backend from backends.json")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--label", default=None)
    ap.add_argument(
        "--max-tokens",
        type=int,
        default=3000,
        help="Budget per task. Reasoning models need room; a model cut "
        "off mid-thought produces no code and is reported CUT -- "
        "unmeasured and excluded, not scored 0 (default 3000)",
    )
    ap.add_argument(
        "--compare",
        default=None,
        help="JSON file: [{label, backend|base_url, model}, ...]",
    )
    ap.add_argument(
        "--keep-output",
        action="store_true",
        help="Store the generated code in the report",
    )
    ap.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Run each task N times. The llama.cpp lanes sample even at "
        "temperature=0 (GenieX ignores it), so a single run measures "
        "one draw rather than the model. 3+ for a defensible number.",
    )
    ap.add_argument(
        "--context-tokens",
        type=int,
        default=0,
        help="Prepend roughly N tokens of real repository source before "
        "each task. The short prompts above are not what an agent "
        "sends; prefill and the QAIRT bundles' 4096-token ceiling "
        "only show up under a realistic context.",
    )
    ap.add_argument(
        "--task-set",
        choices=("classic", "novel", "extended", "languages", "all"),
        default="all",
        help="'all' (default) = every task below; 'classic' = three "
        "textbook problems, recall-prone and too small to carry a "
        "ranking; 'extended' = "
        "the 21-task set sized so a regression is provable "
        "(at 6 tasks the smallest provable drop is 100%% -> 17%%); "
        "'novel' = "
        "tasks built from formats invented in this repo, fully "
        "specified in the prompt, which cannot have been memorised; "
        "'languages' = the bash/CMake/Dockerfile tasks, which need "
        "bash, cmake and hadolint on PATH and report a visible SKIP "
        "where one is missing. "
        "Compare classic against novel: a model much better on the "
        "first is recalling rather than reasoning.",
    )
    ap.add_argument(
        "--deadline",
        type=float,
        default=1800,
        help="Give up on one attempt after N seconds of wall-clock and "
        "record it UNMEASURED. urlopen's timeout is per socket "
        "read, so a model that keeps emitting tokens never trips "
        "it -- one blocked a sweep for over an hour (default 1800)",
    )
    ap.add_argument(
        "--no-warmup",
        action="store_true",
        help="Skip the warmup request. The first task then carries the "
        "model load time, which the ranking will attribute to the "
        "model's speed.",
    )
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    global TASKS
    if args.task_set == "novel":
        TASKS = NOVEL_TASKS
    elif args.task_set == "extended":
        TASKS = EXTENDED_TASKS
    elif args.task_set == "languages":
        TASKS = LANGUAGE_TASKS
    elif args.task_set == "all":
        TASKS = TASKS + NOVEL_TASKS + EXTENDED_TASKS + LANGUAGE_TASKS

    selfcheck = grader_selfcheck(TASKS)
    print(
        f"  grader self-check: {selfcheck['checked']}/{selfcheck['tasks']} references "
        f"pass ({selfcheck['seconds']}s, netns={selfcheck['netns']}, "
        f"tools: {', '.join(n for n, ok in selfcheck['tools'].items() if ok) or 'none'})",
        flush=True,
    )
    for name, reason in selfcheck["skipped"].items():
        print(
            f"    SKIPPED {name}: {reason} — this task is NOT graded on this host",
            flush=True,
        )

    from orchestrant.benchmark.client import candidate_rows, entry_config, write_report
    from bench_compare import mark_suspect_cases
    from orchestrant.benchmark.openai_api import resolve_backend, resolve_backend_entry

    candidates = candidate_rows(args, resolve_backend, resolve_backend_entry)

    reports = [
        evaluate(
            c["base_url"],
            c["model"],
            c["label"],
            args.max_tokens,
            args.keep_output,
            repeats=args.repeats,
            context_tokens=args.context_tokens,
            warmup=not args.no_warmup,
            deadline=args.deadline,
            entry=c["entry"],
            backend=c["backend"],
        )
        for c in candidates
    ]

    # A case the CONTROL endpoint also fails is evidence about the CASE. Before
    # the write, so the file and the printed table cannot disagree.
    suspect = mark_suspect_cases(reports)

    if args.output:
        write_report(
            args.output,
            "bench_coding",
            {
                "max_tokens": args.max_tokens,
                "repeats": args.repeats,
                "deadline": args.deadline,
                "context_tokens": args.context_tokens,
                "warmup": not args.no_warmup,
                "task_set": args.task_set,
                "task_kinds": _tally(TASKS, "kind"),
                "task_langs": _tally(TASKS, "lang"),
                # What each backend entry added to every request -- the
                # merged extras and the header NAMES, never a key value.
                "backend_entry": {
                    c["label"]: entry_config(c["entry"]) for c in candidates
                },
                "grader_selfcheck": selfcheck,
            },
            reports,
            candidates[0]["base_url"] if candidates else None,
            (os.path.abspath(__file__), "provenance.py"),
            extra=_determinism_extra(candidates),
        )
        print(f"  Report written to {args.output}")

    # Ranking last: it only prints, and a print must never be able to
    # destroy a completed measurement.

    if len(reports) > 1:
        print("\n" + "=" * 78)
        print(
            "  RANKING — by pass RATE over measured attempts (errors and cuts "
            "excluded),\n  then by attempts measured, then by time to a finished answer"
        )
        print("=" * 78)
        if suspect:
            print(
                f"  {len(suspect)} SUSPECT task(s) the control endpoint also "
                f"fails, excluded from\n  every other candidate's rate, interval "
                f"and rank: {', '.join(suspect)}"
            )
        # Rate, not raw count, then attempts: one lucky surviving attempt must
        # not outrank twenty clean ones.

        # The control is scored on the full case set and the candidates on the
        # reduced one, so it is not a competitor in this table.
        from bench_compare import is_control

        contenders = [r for r in reports if not is_control(r)] or reports
        ranked = sorted(
            contenders,
            key=lambda r: (
                -(r["passed"] / r["total"] if r["total"] else 0.0),
                -r["total"],
                r["total_wall_s"],
            ),
        )
        if len(contenders) < len(reports):
            for r in (x for x in reports if is_control(x)):
                print(
                    f"  calibration only, not ranked: {r['label'][:34]} "
                    f"{r['passed']}/{r['total']} over the FULL case set"
                )
        print(
            f"  {'model':34s} {'pass [95% CI]':>22s} {'wrong':>5s} {'cut':>4s} "
            f"{'total':>9s} {'avg/attempt':>11s}"
        )
        from bench_compare import case_outcomes
        from orchestrant.benchmark.stats import format_score, tiers

        # Rows the paired sign test cannot separate share a tier: the order
        # inside one is not an ordering the data supports.
        tiered = tiers(ranked, case_outcomes)
        for i, group in enumerate(tiered):
            if i:
                print(f"  {'-' * 34} tier {i + 1} " + "-" * 20)
            for r in group:
                n = r.get("effective_n") or r["total"]
                k = r.get("effective_k", r["passed"])
                print(
                    f"  {r['label'][:34]:34s} {format_score(k, n):>22s} "
                    f"{r.get('wrong', 0):5d} {r.get('truncated', 0):4d} "
                    f"{r['total_wall_s']:8.1f}s {r['avg_wall_s'] or 0:10.1f}s"
                )
        if len(tiered) < len(ranked):
            print(
                "  rows within one tier are NOT separated by the paired sign "
                "test — read them as a tie."
            )
        # Per lang and per kind: one aggregate hides exactly what R5 added the
        # tags to expose -- 27/27 Python and 0/4 bash reads as 27/31.
        groups = sorted(
            {
                g
                for r in ranked
                for g in list(r.get("by_lang", {})) + list(r.get("by_kind", {}))
            }
        )
        if len(groups) > 1:
            print("\n  pass rate per lang and per kind (measured attempts only)")
            print(f"  {'model':34s} " + " ".join(f"{g[:14]:>14s}" for g in groups))
            for r in ranked:
                cells = {**r.get("by_lang", {}), **r.get("by_kind", {})}
                row = []
                for g in groups:
                    c = cells.get(g)
                    if not c:
                        row.append(f"{'-':>14s}")
                    elif c["measured"]:
                        row.append(f"{c['passed']}/{c['measured']}".rjust(14))
                    else:
                        row.append(f"{'skipped':>14s}")
                print(f"  {r['label'][:34]:34s} " + " ".join(row))
            if any(
                c.get("skipped")
                for r in ranked
                for c in list(r.get("by_lang", {}).values())
            ):
                print(
                    "\n  'skipped' = the tool that grades that language "
                    "(shellcheck, cmake, hadolint) is not\n  on PATH here. Those "
                    "tasks were NOT graded and are out of every rate above."
                )
        if any(r.get("truncated") for r in ranked):
            print(
                "\n  'cut' = generation stopped at the output budget before the "
                "model\n  finished. Those attempts are UNMEASURED: listed "
                "here, excluded from the rate,\n  the interval and the rank - a "
                "reasoning model can spend the whole budget inside <think>."
            )
        print()


if __name__ == "__main__":
    main()
