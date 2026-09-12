"""Guarantees repaired after the 2026-09-04 audit of the coding benchmark.

Every test here pins something that was demonstrated wrong: a correct answer
graded FAIL, a fake PASS, a number the docs could not have produced. The
reproductions come from that audit, not from imagination.
"""

import io
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import bench_coding as bc  # noqa: E402
from bench_coding import (
    _assertion_harness,
    extract_code,
    looks_truncated,  # noqa: E402
    run_candidate,
)

MERGE = next(t for t in bc.TASKS if t["name"] == "merge_sorted")
GOOD = (
    "def merge_sorted(a, b):\n    out, i, j = [], 0, 0\n"
    "    while i < len(a) and j < len(b):\n"
    "        if a[i] <= b[j]:\n            out.append(a[i]); i += 1\n"
    "        else:\n            out.append(b[j]); j += 1\n"
    "    return out + a[i:] + b[j:]\n"
)
STUB = "def merge_sorted(a, b):\n    return []\n"


class TestFences:
    @pytest.mark.parametrize(
        "fence", ["```python3", "``` python", "```Python", "```py3"]
    )
    def test_language_variants_are_all_python(self, fence):
        # "```python3" matched nothing and graded a correct answer SyntaxError.
        # A prose line with "=" at column 0 sits BEFORE the fence: if the fence
        # goes unrecognised, the bare-code fallback anchors on that line, which
        # is the only way this test can tell the two apart -- for a pure "def"
        # answer the fallback alone now yields the same code (the mutation
        # gate proved that version of this test vacuous).
        text = f"Complexity = O(n + m).\n\n{fence}\n{GOOD}```"
        assert extract_code(text, want="merge_sorted") == GOOD.strip()

    def test_crlf_fence_still_extracts(self):
        text = "```python\r\n" + GOOD.replace("\n", "\r\n") + "```"
        code = extract_code(text, want="merge_sorted")
        assert code.startswith("def merge_sorted") and "```" not in code
        assert run_candidate(code, MERGE["tests"], forbidden=MERGE["forbidden"])[0]

    def test_bare_reply_keeps_its_imports(self):
        # The old fallback sliced from the first "def " and threw the import away.
        text = "from itertools import chain\n\n" + GOOD.replace(
            "out + a[i:] + b[j:]", "list(chain(out, a[i:], b[j:]))"
        )
        code = extract_code(text, want="merge_sorted")
        assert code.startswith("from itertools import chain")
        assert run_candidate(code, MERGE["tests"], forbidden=MERGE["forbidden"])[0]

    def test_bare_fallback_does_not_start_inside_prose(self):
        assert extract_code("Below is a Python def for it: nothing here") == ""


class TestHarnessCannotBeFakedCheaply:
    def test_printing_the_old_public_marker_no_longer_passes(self):
        # A stub scoring 1/7 used to become "all assertions passed" by printing
        # the (fixed, public) marker first.
        forged = STUB + 'print("__ASSERTIONS__[]")\n'
        ok, detail, credit = run_candidate(
            forged, MERGE["tests"], forbidden=MERGE["forbidden"]
        )
        assert not ok
        assert credit["total"] == 7 and credit["passed"] == 1

    def test_the_last_marker_line_wins_not_the_first(self):
        # The nonce is visible to the candidate (same file), so it can print a
        # perfectly shaped forgery BEFORE the harness prints the truth. Reading
        # the first occurrence took the forgery; the harness line comes last.
        rows = json.dumps({"rows": [[i, True, ""] for i in range(7)], "setup": []})
        forged = STUB + f"print(_MARKER + {rows!r})\n"
        ok, detail, credit = run_candidate(
            forged, MERGE["tests"], forbidden=MERGE["forbidden"]
        )
        assert not ok
        assert credit["passed"] == 1 and credit["total"] == 7

    def test_zero_rows_is_never_a_pass(self):
        # The harness serialises with json.dumps at the very end, so a candidate
        # that swaps sys.modules['json'] controls the LAST marker line. Zero
        # rows for seven assertions used to be "all assertions passed".
        forged = (
            STUB + "import sys, types\n_m = types.ModuleType('json')\n"
            '_m.dumps = lambda r: \'{"rows": [], "setup": []}\'\n'
            "sys.modules['json'] = _m\n"
        )
        ok, detail, _ = run_candidate(
            forged, MERGE["tests"], forbidden=MERGE["forbidden"]
        )
        assert not ok and "corrupted" in detail

    def test_the_marker_is_a_nonce(self):
        a, _ = _assertion_harness("assert 1")
        assert "_MARKER" in a and "__ASSERTIONS__" not in a


class TestSetupStatements:
    def test_setup_lines_are_not_counted_as_assertions(self):
        program, n = _assertion_harness(
            "x = [1, 2]\ndef helper(): return 1\nassert x\nassert helper()"
        )
        assert n == 2

    def test_a_raising_setup_denies_pass_but_keeps_partial_credit(self):
        # A candidate whose result makes `out[0][0] = 99` raise must not be
        # scored on the aliasing check that then passes trivially.
        tests = "out = f()\nout[0] = 99\nassert out == [99]\nassert True"
        ok, detail, credit = run_candidate("def f():\n    return (1,)\n", tests)
        assert not ok and "setup raised" in detail
        assert credit["total"] == 2
        assert credit["setup_failures"]

    def test_helper_defs_are_wrapped_not_counted(self):
        tests = "def _bad(s):\n    try:\n        f(s)\n    except ValueError:\n        return True\n    return False\nassert _bad('x')"
        ok, _, credit = run_candidate("def f(s):\n    raise ValueError\n", tests)
        assert ok and credit["total"] == 1


class TestAstGrouping:
    @pytest.mark.parametrize(
        "tests",
        [
            "@staticmethod\ndef g(): return 1\nassert g() == 1",
            "x = [\n1,\n2]\nassert x == [1, 2]",
            "y = 1 + \\\n2\nassert y == 3",
            "try:\n    pass\nexcept:\n    pass\nassert True",
            "try:\n    pass\n# comment between\nexcept Exception:\n    pass\nassert True",
            's = """a\nb\nc"""\nassert s.count("\\n") == 2',
            "for i in range(2):\n\n    pass\nassert i == 1",
        ],
        ids=[
            "decorator",
            "col0-bracket",
            "backslash",
            "bare-except",
            "comment-in-try",
            "dedented-string",
            "blank-in-for",
        ],
    )
    def test_ordinary_shapes_run_and_score(self, tests):
        # Every one of these produced a 0/0 SyntaxError or a silent miss under
        # the line-prefix grouper.
        ok, detail, credit = run_candidate("def f(): return 1\n", tests)
        assert ok, detail
        assert credit["total"] == 1

    def test_a_broken_test_string_fails_loudly(self):
        with pytest.raises(SyntaxError):
            _assertion_harness("assert (")


class TestSubprocessHardening:
    def test_the_netns_wrapper_is_applied_when_the_namespace_is_available(
        self, monkeypatch
    ):
        # Asserting the EFFECT needs a host where `unshare -rn` works. Where it
        # does not (uid_map: Operation not permitted) _netns_available() is False,
        # the wrap is dead code, and coding.netns SURVIVED because removing dead
        # code changes nothing. Drive the decision instead of the environment, so
        # the mutation bites on every host.
        seen = {}

        class _Stop(RuntimeError):
            pass

        def _popen(cmd, **kw):
            seen["cmd"] = cmd
            raise _Stop

        monkeypatch.setattr(bc, "_netns_available", lambda: True)
        monkeypatch.setattr(bc.subprocess, "Popen", _popen)
        with pytest.raises(_Stop):
            bc._launch(["bash", "-c", "true"], "/tmp", 5)
        assert seen["cmd"][:4] == [
            "unshare",
            "-rn",
            "prlimit",
            f"--nproc={bc.RLIMIT_NPROC}",
        ], seen["cmd"]
        assert seen["cmd"][4:] == ["bash", "-c", "true"], seen["cmd"]

    def test_main_guard_demo_does_not_run(self):
        code = (
            GOOD
            + '\nif __name__ == "__main__":\n    import sys\n    print(sys.argv[1])\n'
        )
        ok, detail, _ = run_candidate(
            code, MERGE["tests"], forbidden=MERGE["forbidden"]
        )
        assert ok, detail

    def test_input_fails_fast_instead_of_waiting_for_the_timeout(self):
        t0 = time.monotonic()
        ok, detail, _ = run_candidate(
            GOOD + "\ninput()\n",
            MERGE["tests"],
            timeout=10,
            forbidden=MERGE["forbidden"],
        )
        assert not ok and "EOFError" in detail
        assert time.monotonic() - t0 < 5

    def test_timeout_kills_the_whole_process_tree(self, tmp_path):
        marker = tmp_path / "grandchild.pid"
        code = (
            "import subprocess, sys, time\n"
            f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
            "while True: pass\n"
            "def f(): return 1\n"
        )
        t0 = time.monotonic()
        ok, detail, _ = run_candidate(code, "assert f() == 1", timeout=2)
        # Bounded: without its own session the group kill misses, and
        # communicate() then waits on the orphan's pipes until it exits on its
        # own -- "dead" 60 s later, which the first version of this test
        # accepted as a pass.
        assert time.monotonic() - t0 < 15, "waited for the orphan instead of killing it"
        assert not ok and "timed out" in detail
        pid = int(marker.read_text())
        time.sleep(0.2)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    def test_unittest_ok_is_not_reported_as_the_failure_reason(self):
        code = GOOD + "\nimport unittest\nunittest.main()\n"
        ok, detail, _ = run_candidate(
            code, MERGE["tests"], forbidden=MERGE["forbidden"]
        )
        assert not ok
        assert detail != "OK"


class TestTruncation:
    def test_server_reported_length_is_a_cut(self):
        assert looks_truncated(
            "def f(): pass", chunks=10, code="def f(): pass", finish="length"
        )

    def test_complete_answer_at_the_cap_is_still_not_forced_to_cut_by_extract(self):
        # extract_code must not change; evaluate's rule stays "cut only if not ok".
        assert extract_code("```python\n" + GOOD + "```", want="merge_sorted")


class _FakeResp:
    """urlopen's return, not ask()'s: the request now goes through
    bench_cli.post_json, which wraps this and closes it."""

    def __init__(self, lines):
        self._lines = [l if isinstance(l, bytes) else l.encode() for l in lines]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass


class TestAskReasoningDeltas:
    def _stream(self, monkeypatch, chunks):
        lines = [b"data: " + json.dumps(c).encode() + b"\n" for c in chunks] + [
            b"data: [DONE]\n"
        ]
        monkeypatch.setattr(
            bc.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(lines)
        )

    def test_reasoning_only_reply_is_seen_and_finish_length_survives(self, monkeypatch):
        chunks = [
            {
                "choices": [
                    {"delta": {"reasoning_content": "hmm"}, "finish_reason": None}
                ]
            }
        ] * 3
        chunks.append({"choices": [{"delta": {}, "finish_reason": "length"}]})
        self._stream(monkeypatch, chunks)
        text, ttft, wall, n, ptok, think, finish, ctok, gave_up = bc.ask(
            "http://x", "m", "p", 10
        )
        assert text == "" and think == "hmmhmmhmm" and n == 3 and finish == "length"
        assert ttft is not None
        assert looks_truncated(text, n, extract_code(text), finish)

    def test_ollama_style_reasoning_field_is_collected(self, monkeypatch):
        self._stream(
            monkeypatch,
            [
                {"choices": [{"delta": {"reasoning": "r"}, "finish_reason": None}]},
                {
                    "choices": [
                        {"delta": {"content": "def f(): pass"}, "finish_reason": "stop"}
                    ]
                },
            ],
        )
        text, _t, _w, _n, _p, think, finish, _c, _g = bc.ask("http://x", "m", "p", 10)
        assert think == "r" and text == "def f(): pass" and finish == "stop"


def _fake_ask_factory(script):
    """script: list of (text, chunks, finish) consumed per call."""
    calls = iter(script)

    def fake(
        base_url, model, prompt, max_tokens, timeout=1800, deadline=None, entry=None
    ):
        text, chunks, finish = next(calls)
        return text, 0.1, 1.0, chunks, 10, "", finish, None, False

    return fake


class TestEvaluateAccounting:
    def _run(self, monkeypatch, script, repeats):
        monkeypatch.setattr(bc, "TASKS", [MERGE])
        monkeypatch.setattr(bc, "ask", _fake_ask_factory(script))
        return bc.evaluate("http://x", "m", "lbl", 100, repeats=repeats, warmup=False)

    def test_identical_output_is_deterministic_and_counts_tasks(self, monkeypatch):
        good = "```python\n" + GOOD + "```"
        r = self._run(monkeypatch, [(good, 5, "stop")] * 3, repeats=3)
        assert r["deterministic"] and r["effective_n"] == 1 and r["effective_k"] == 1

    def test_agreeing_verdicts_with_different_output_are_not_deterministic(
        self, monkeypatch
    ):
        # A sampling endpoint that fails every draw agrees on pass/fail too.
        bad = ["```python\n" + STUB + f"# draw {i}\n```" for i in range(3)]
        r = self._run(monkeypatch, [(b, 5, "stop") for b in bad], repeats=3)
        assert not r["deterministic"] and r["repeats_agreed"]
        assert r["effective_n"] == 3 and r["effective_k"] == 0

    def test_cut_attempts_are_excluded_not_failed(self, monkeypatch):
        good = "```python\n" + GOOD + "```"
        r = self._run(
            monkeypatch, [(good, 5, "stop"), ("<think>", 9999, "length")], repeats=2
        )
        assert (
            r["truncated"] == 1
            and r["total"] == 1
            and r["passed"] == 1
            and r["wrong"] == 0
        )

    def test_effective_k_counts_tasks_and_never_rounds_a_ratio(self, monkeypatch):
        # The audit's reproduction: deterministic lane, 9 tasks x 3 repeats,
        # tasks 0-6 pass every draw, tasks 7-8 fail every draw and each lose
        # one attempt to a transport error. passed=21 of attempts=25 with
        # effective_n=9 -> round(21*9/25) = 8. Seven tasks passed.
        nine = [dict(MERGE, name=f"t{i}") for i in range(9)]
        good, bad = "```python\n" + GOOD + "```", "```python\n" + STUB + "```"
        schedule = []
        for t in range(9):
            for a in range(3):
                schedule.append(good if t < 7 else (None if a == 1 else bad))
        it = iter(schedule)

        def fake(*args, **kw):
            item = next(it)
            if item is None:
                raise ConnectionError("dropped")
            return item, 0.1, 1.0, 5, 10, "", "stop", None, False

        monkeypatch.setattr(bc, "TASKS", nine)
        monkeypatch.setattr(bc, "ask", fake)
        r = bc.evaluate("http://x", "m", "lbl", 100, repeats=3, warmup=False)
        assert r["passed"] == 21 and r["total"] == 25 and r["errored"] == 2
        assert r["deterministic"] and r["effective_n"] == 9
        assert r["effective_k"] == 7, r["effective_k"]

    def test_a_transport_error_does_not_flip_determinism(self, monkeypatch):
        good = "```python\n" + GOOD + "```"
        script = iter([(good, 5, "stop"), None, (good, 5, "stop")])

        def fake(*a, **k):
            item = next(script)
            if item is None:
                raise ConnectionError("dropped")
            text, chunks, finish = item
            return text, 0.1, 1.0, chunks, 10, "", finish, None, False

        monkeypatch.setattr(bc, "TASKS", [MERGE])
        monkeypatch.setattr(bc, "ask", fake)
        r = bc.evaluate("http://x", "m", "lbl", 100, repeats=3, warmup=False)
        assert r["errored"] == 1 and r["deterministic"]
        assert r["effective_n"] == 1 and r["effective_k"] == 1


class TestTaskSets:
    def _tasks_for(self, monkeypatch, flag):
        from orchestrant.benchmark import client as bench_cli

        monkeypatch.setattr(bench_cli, "candidate_rows", lambda args, rb, re=None: [])
        monkeypatch.setattr(sys, "argv", ["bench_coding.py", "--task-set", flag])
        original = bc.TASKS
        try:
            bc.main()
            return list(bc.TASKS)
        finally:
            bc.TASKS = original

    def test_extended_is_the_21_task_set_the_help_describes(self, monkeypatch):
        assert len(self._tasks_for(monkeypatch, "extended")) == 21

    def test_all_is_every_set(self, monkeypatch):
        # Derived, not hard-wired: R5 grew "all" from 27 to 33 and a pinned
        # literal would turn that into a false regression.
        assert len(self._tasks_for(monkeypatch, "all")) == (
            len(bc.TASKS)
            + len(bc.NOVEL_TASKS)
            + len(bc.EXTENDED_TASKS)
            + len(bc.LANGUAGE_TASKS)
        )


class TestKvPairsWhitespace:
    def test_whitespace_only_input_must_raise(self):
        import bench_tasks

        task = next(
            t
            for t in bench_tasks.EXTENDED_TASKS
            if t["name"] == "validation_parse_kv_pairs"
        )
        # The lenient idiom the audit found passing 21/21: strip, then treat
        # whitespace-only like the empty string. Rules 1-2 say only "" is legal.
        lenient = (
            "def validation_parse_kv_pairs(text):\n"
            "    if not text.strip():\n        return {}\n"
            "    return _ref(text)\n"
            + task["reference"].replace("def validation_parse_kv_pairs", "def _ref")
        )
        ok, detail, credit = run_candidate(lenient, task["tests"])
        assert not ok, "whitespace-only input slipped through"
        assert run_candidate(task["reference"], task["tests"])[0]


class TestCompareUsesObservedCounts:
    def test_effective_k_wins_over_a_rounded_ratio(self):
        # Deterministic lane, 9 tasks x 3 repeats, two transport errors that
        # both landed on failing tasks: passed=21 of total=25 attempts, but only
        # 7 tasks actually pass. round(21/25 * 9) = 8 fabricated an eighth.
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import bench_compare
        import test_bench_compare as tbc

        cases = {f"c{i}": [i < 7] * 3 for i in range(9)}
        raw = tbc.report_repeats("m", cases, deterministic=True, config={"repeats": 3})

        def stamp(obj):
            if isinstance(obj, dict):
                if "effective_n" in obj:
                    obj.update(passed=21, total=25, effective_n=9, effective_k=7)
                for v in obj.values():
                    stamp(v)
            elif isinstance(obj, list):
                for v in obj:
                    stamp(v)

        stamp(raw)
        norm = bench_compare.normalise(raw)
        assert all(e.get("effective_k") == 7 for e in norm["entries"]), (
            "effective_k not carried"
        )
        findings, _ = bench_compare.compare(norm, norm)
        line = next(f for f in findings if "->" in f and "/" in f)
        assert "7/9" in line and "8/9" not in line, line

    def test_legacy_report_without_effective_k_is_clamped_not_trusted(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import bench_compare
        import test_bench_compare as tbc

        cases = {f"c{i}": [True] * 4 for i in range(5)}
        norm = bench_compare.normalise(
            tbc.report_repeats("m", cases, deterministic=True, config={"repeats": 4})
        )
        for e in norm["entries"]:
            e.pop("effective_k", None)
        findings, _ = bench_compare.compare(norm, norm)
        line = next(f for f in findings if "->" in f and "/" in f)
        assert "5/5" in line, line


class TestForbiddenTokensAndExits:
    def test_mentioning_sorted_in_a_docstring_is_not_punished(self):
        # README: "merely mentioning sorted() in a docstring is not punished".
        # Stated for a year, never tested.
        # Multi-line on purpose: a one-line docstring is also a plain quoted
        # string, and the single-line stripper removed it even with the
        # triple-quote rule deleted -- the mutation gate caught that.
        code = GOOD.replace(
            "def merge_sorted(a, b):\n",
            'def merge_sorted(a, b):\n    """Merge two sorted lists.\n\n'
            '    Equivalent to sorted(a + b), without calling sorted().\n    """\n',
        )
        ok, detail, _ = run_candidate(
            code, MERGE["tests"], forbidden=MERGE["forbidden"]
        )
        assert ok, detail

    def test_a_docstring_mentioning_an_UNMAPPED_token_is_not_punished(self):
        # The docstring rule now lives only in the TEXT fallback (an unmapped
        # token, or code that does not parse); that fallback still strips them.
        code = (
            "def merge_sorted(a, b):\n"
            '    """Merge two lists.\n\n'
            '    Deliberately avoids itertools.\n    """\n'
            "    return a + b\n"
        )
        assert bc.check_forbidden(code, ["itertools"]) is None

    def test_an_unmapped_token_actually_used_is_still_caught(self):
        # The other half: the fallback must still be a check, not a no-op.
        code = "import itertools\n\n\ndef merge_sorted(a, b):\n    return a + b\n"
        assert "itertools" in (bc.check_forbidden(code, ["itertools"]) or "")

    def test_sys_exit_inside_the_function_keeps_earlier_credit(self):
        # sys.exit raises SystemExit, which is not an Exception. The harness
        # used to die there with no rows printed: 0/7 for a 5/7 answer.
        code = "import sys\n" + GOOD.replace(
            "    return out + a[i:] + b[j:]",
            "    if len(a) + len(b) > 5:\n        sys.exit(3)\n    return out + a[i:] + b[j:]",
        )
        ok, detail, credit = run_candidate(
            code, MERGE["tests"], forbidden=MERGE["forbidden"]
        )
        assert not ok
        assert credit["total"] == 7 and 0 < credit["passed"] < 7, credit


class TestForbiddenOnTheTree:
    @pytest.mark.parametrize(
        "code",
        [
            "def merge_sorted(a, b):\n    s = sorted\n    return s(a + b)\n",
            "import builtins\ndef merge_sorted(a, b):\n    return builtins.sorted(a + b)\n",
            "def merge_sorted(a, b):\n    return getattr(__builtins__, 'sorted')(a + b)\n",
            "def merge_sorted(a, b):\n    c = a + b\n    c.sort()\n    return c\n",
            # Bound, then called: no `.sort(` and no `sorted(` anywhere in the
            # text, so only the Attribute arm of the tree walk sees these.
            "def merge_sorted(a, b):\n    c = a + b\n    m = c.sort\n    m()\n    return c\n",
            "import builtins\ndef merge_sorted(a, b):\n    f = builtins.sorted\n"
            "    return f(a + b)\n",
        ],
        ids=[
            "alias",
            "builtins-attr",
            "getattr-string",
            "list-sort",
            "bound-list-sort",
            "bound-builtins-attr",
        ],
    )
    def test_evasions_are_caught(self, code):
        ok, detail, _ = run_candidate(
            code, MERGE["tests"], forbidden=MERGE["forbidden"]
        )
        assert not ok and "forbids" in detail, detail

    def test_a_variable_named_sorted_out_is_not_sorted(self):
        code = (
            GOOD.replace("out, i, j = [], 0, 0", "sorted_out, i, j = [], 0, 0")
            .replace("out.append", "sorted_out.append")
            .replace("return out +", "return sorted_out +")
        )
        ok, detail, _ = run_candidate(
            code, MERGE["tests"], forbidden=MERGE["forbidden"]
        )
        assert ok, detail

    def test_unparseable_code_falls_back_to_the_text_scan(self):
        from bench_coding import check_forbidden

        assert check_forbidden("def f(:\n    return sorted(x)", ["sorted"])


class TestUnclosedLastFence:
    def test_a_fence_in_prose_does_not_make_a_typo_a_cut(self):
        text = "Wrap it in ``` to run it.\n```python\ndef merge_sorted(a, b)\n    return a\n```"
        code = extract_code(text, want="merge_sorted")
        assert not looks_truncated(text, 60, code)

    def test_an_unclosed_final_block_is_a_cut(self):
        text = "```python\ndef merge_sorted(a, b):\n    return (a +"
        code = extract_code(text, want="merge_sorted")
        assert looks_truncated(text, 60, code)


class TestRealTokenCount:
    def test_server_usage_overrides_the_delta_count(self, monkeypatch):
        bad = "```python\n" + STUB + "```"
        monkeypatch.setattr(bc, "TASKS", [MERGE])
        # The delta count says 5000 -- at or past a 3000-token budget, so the
        # proxy alone would call this a cut. usage says 100 tokens were
        # generated, and usage wins: a server that batches deltas must not turn
        # a short, complete answer into CUT.
        monkeypatch.setattr(
            bc, "ask", lambda *a, **k: (bad, 0.1, 1.0, 5000, 10, "", "stop", 100, False)
        )
        r = bc.evaluate("http://x", "m", "lbl", 3000, warmup=False)
        assert r["truncated"] == 0 and r["results"][0]["tokens"] == 100
        assert r["results"][0]["tokens_estimated"] is False

    def test_no_usage_keeps_the_delta_proxy(self, monkeypatch):
        # No usage and no finish_reason: the delta count against the request's
        # own budget is all there is, and reaching it is a cut.
        bad = "```python\n" + STUB + "```"
        monkeypatch.setattr(bc, "TASKS", [MERGE])
        monkeypatch.setattr(
            bc, "ask", lambda *a, **k: (bad, 0.1, 1.0, 3000, 10, "", None, None, False)
        )
        r = bc.evaluate("http://x", "m", "lbl", 3000, warmup=False)
        assert r["truncated"] == 1 and r["results"][0]["tokens_estimated"] is True

    def test_a_long_legitimate_answer_is_not_cut_by_a_stale_2048(self, monkeypatch):
        # v0.6.1 has no hard ceiling. A wrong-but-complete 2500-token answer
        # under a 3000-token budget must read FAIL, not CUT.
        bad = "```python\n" + STUB + "```"
        monkeypatch.setattr(bc, "TASKS", [MERGE])
        monkeypatch.setattr(
            bc,
            "ask",
            lambda *a, **k: (bad, 0.1, 1.0, 2500, 10, "", "stop", 2500, False),
        )
        r = bc.evaluate("http://x", "m", "lbl", 3000, warmup=False)
        assert r["truncated"] == 0 and r["passed"] == 0 and r["wrong"] == 1

    def test_ask_reads_completion_tokens(self, monkeypatch):
        chunks = [
            {"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 42}},
        ]
        lines = [b"data: " + json.dumps(c).encode() + b"\n" for c in chunks] + [
            b"data: [DONE]\n"
        ]
        monkeypatch.setattr(
            bc.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(lines)
        )
        *_, ctok, _gave = bc.ask("http://x", "m", "p", 10)
        assert ctok == 42


class TestStdlibOnly:
    def _task(self):
        import bench_tasks

        return next(
            t
            for t in bench_tasks.EXTENDED_TASKS
            if t["name"] == "strings_normalize_tag"
        )

    def test_third_party_import_fails_a_stdlib_only_task(self):
        task = self._task()
        assert task.get("stdlib_only") is True
        ok, detail, _ = run_candidate(
            "import yaml\n" + task["reference"], task["tests"], stdlib_only=True
        )
        assert not ok and "standard library" in detail

    def test_stdlib_imports_are_fine(self):
        task = self._task()
        code = "import re\nfrom collections import Counter\n" + task["reference"]
        assert run_candidate(code, task["tests"], stdlib_only=True)[0]

    def test_the_prompts_that_say_so_declare_it(self):
        import bench_tasks

        for t in bench_tasks.EXTENDED_TASKS:
            if "standard library" in t["prompt"].lower():
                assert t.get("stdlib_only") is True, t["name"]


class TestCandidateConfinement:
    def test_environment_is_scrubbed(self):
        code = "import os\ndef f():\n    return 'HOME' in os.environ or 'SSH_AUTH_SOCK' in os.environ\n"
        ok, detail, _ = run_candidate(code, "assert f() is False")
        assert ok, detail

    def test_no_network_interfaces_when_the_kernel_allows_it(self):
        if not bc._netns_available():
            pytest.skip("unshare -rn unavailable here")
        code = "import socket\ndef f():\n    return sorted(n for _, n in socket.if_nameindex())\n"
        ok, detail, _ = run_candidate(code, "assert f() in ([], ['lo'])")
        assert ok, detail


class TestAsciiDigits:
    @pytest.mark.parametrize(
        "name,bad",
        [("validation_parse_seat_code", "١A"), ("validation_parse_range_spec", "٣")],
    )
    def test_the_non_ascii_case_is_tested_and_the_reference_passes(self, name, bad):
        import bench_tasks

        task = next(t for t in bench_tasks.EXTENDED_TASKS if t["name"] == name)
        assert bad in task["tests"]
        assert run_candidate(task["reference"], task["tests"])[0]

    def test_item_list_rejects_non_ascii_and_signed_counts(self):
        import bench_tasks

        task = next(
            t for t in bench_tasks.EXTENDED_TASKS if t["name"] == "parsing_item_list"
        )
        assert run_candidate(task["reference"], task["tests"])[0]
        lenient = task["reference"].replace(
            "all(c in '0123456789' for c in left)", "left.lstrip('+').isdigit()"
        )
        assert lenient != task["reference"]
        assert not run_candidate(lenient, task["tests"])[0]


class TestWallClockDeadline:
    """urlopen's timeout is per socket READ, not for the request.

    A model that keeps emitting tokens never trips it. Measured 2026-09-05: a
    4B under an 8000-token budget generated for over an hour on one task and
    blocked the sweep. A run that never ends is not a measurement, and it must
    not be able to take the sweep with it.
    """

    def _endless(self, monkeypatch, chunks_before_check=10_000):
        one = json.dumps(
            {"choices": [{"delta": {"content": "x"}, "finish_reason": None}]}
        )
        line = b"data: " + one.encode() + b"\n"

        class Endless:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __iter__(self):
                while True:
                    yield line

            def close(self):
                pass

        monkeypatch.setattr(
            bc.urllib.request, "urlopen", lambda req, timeout=0: Endless()
        )

    def test_a_stream_that_never_ends_is_abandoned(self, monkeypatch):
        self._endless(monkeypatch)
        t0 = time.monotonic()
        text, _ttft, wall, n, _p, _th, _f, _c, gave_up = bc.ask(
            "http://x", "m", "p", 10, deadline=0.5
        )
        assert gave_up is True
        assert time.monotonic() - t0 < 15, "the deadline did not stop it"
        assert n > 0 and text, "what arrived before the deadline must be kept"

    def test_without_a_deadline_nothing_changes(self, monkeypatch):
        chunks = [{"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}]
        lines = [b"data: " + json.dumps(c).encode() + b"\n" for c in chunks] + [
            b"data: [DONE]\n"
        ]
        monkeypatch.setattr(
            bc.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(lines)
        )
        *_, gave_up = bc.ask("http://x", "m", "p", 10)
        assert gave_up is False

    def test_evaluate_records_it_unmeasured_not_wrong(self, monkeypatch):
        # Token count deliberately WELL UNDER the budget and no finish_reason,
        # so nothing but `gave_up` can make this unmeasured. The first version
        # said 9999 tokens against a 100-token budget, which the cap rule
        # caught on its own -- and the mutation gate proved that test vacuous.
        monkeypatch.setattr(bc, "TASKS", [MERGE])
        monkeypatch.setattr(
            bc, "ask", lambda *a, **k: ("", 0.1, 3600.0, 5, 10, "", None, None, True)
        )
        r = bc.evaluate("http://x", "m", "lbl", 3000, warmup=False, deadline=1800)
        assert r["truncated"] == 1 and r["abandoned"] == 1
        assert r["passed"] == 0 and r["wrong"] == 0 and r["total"] == 0
        assert "GAVE UP" in r["results"][0]["detail"]


class TestForbiddenPrecision:
    """D6/D7. The constraint check has to catch the evasions and ONLY the
    evasions: a correct answer that happens to contain the string 'sort', or
    that defines its own `sort`, was scored FAIL with 0/0 credit.
    """

    F = MERGE["forbidden"]

    @pytest.mark.parametrize(
        "code",
        [
            "from builtins import sorted as s\ndef merge_sorted(a, b):\n    return s(a + b)\n",
            "def merge_sorted(a, b):\n    return getattr(__builtins__, 'sorted')(a + b)\n",
            "def merge_sorted(a, b):\n    return __builtins__['sorted'](a + b)\n",
        ],
        ids=["import-alias", "getattr-string", "builtins-subscript"],
    )
    def test_the_lookup_evasions_are_still_caught(self, code):
        ok, detail, _ = run_candidate(code, MERGE["tests"], forbidden=self.F)
        assert not ok and "forbids" in detail, detail

    def test_a_string_value_that_happens_to_read_sort_is_not_a_lookup(self):
        # `mode='sort'` is a default, not a name handed to getattr.
        code = (
            "def merge_sorted(a, b, mode='sort'):\n    out, i, j = [], 0, 0\n"
            "    while i < len(a) and j < len(b):\n"
            "        if a[i] <= b[j]:\n            out.append(a[i]); i += 1\n"
            "        else:\n            out.append(b[j]); j += 1\n"
            "    return out + a[i:] + b[j:]\n"
        )
        ok, detail, _ = run_candidate(code, MERGE["tests"], forbidden=self.F)
        assert ok, detail

    def test_a_candidate_that_defines_its_own_sort_is_not_using_list_sort(self):
        # `sort` is not even a builtin; the code binds the name itself.
        code = (
            "def sort(x, y):\n"
            '    """Smaller of the two heads first -- the only comparison made."""\n'
            "    return (x, y) if x <= y else (y, x)\n\n\n"
            "def merge_sorted(a, b):\n    out, i, j = [], 0, 0\n"
            "    while i < len(a) and j < len(b):\n"
            "        lo, _hi = sort(a[i], b[j])\n        out.append(lo)\n"
            "        if a[i] <= b[j]:\n            i += 1\n"
            "        else:\n            j += 1\n"
            "    return out + a[i:] + b[j:]\n"
        )
        ok, detail, credit = run_candidate(code, MERGE["tests"], forbidden=self.F)
        assert ok, f"{detail} {credit}"


class TestInStreamErrors:
    """D3. A fault delivered INSIDE an already-200 stream used to be dropped:
    ask() returned '', and the attempt was recorded wrong=1 with 'no code found
    in reply' instead of excluded as a transport error.
    """

    def _serve(self, monkeypatch, lines):
        monkeypatch.setattr(
            bc.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(lines)
        )

    def test_an_openai_style_error_chunk_raises(self, monkeypatch):
        self._serve(monkeypatch, [b'data: {"error": {"message": "engine crashed"}}\n'])
        with pytest.raises(RuntimeError, match="server error in stream"):
            bc.ask("http://x", "m", "p", 10)

    def test_a_llama_cpp_error_event_line_raises(self, monkeypatch):
        self._serve(monkeypatch, [b"error: slot unavailable\n"])
        with pytest.raises(RuntimeError, match="server error in stream"):
            bc.ask("http://x", "m", "p", 10)

    def test_evaluate_records_errored_not_a_wrong_answer(self, monkeypatch):
        # Whole path: urlopen -> ask -> evaluate. Nothing here opens a socket.
        monkeypatch.setattr(bc, "TASKS", [MERGE])
        self._serve(monkeypatch, [b'data: {"error": {"message": "engine crashed"}}\n'])
        r = bc.evaluate("http://x", "m", "lbl", 3000, warmup=False)
        assert r["errored"] == 1 and r["total"] == 0 and r["wrong"] == 0
        assert "no code found" not in r["results"][0]["detail"]


def _http_error(code, body):
    return urllib.error.HTTPError(
        "http://x/v1/chat/completions", code, "Bad Request", {}, io.BytesIO(body)
    )


class TestContextOverflow:
    """R13. A 4xx saying the prompt did not fit is the same 4096 ceiling that
    reads CUT when the server streams it: unmeasured, and recorded apart from
    both a transport error and a cut.
    """

    def test_a_4xx_naming_the_context_is_an_overflow(self):
        exc = _http_error(
            400, b'{"error":{"message":"maximum context length is 4096 tokens"}}'
        )
        assert "HTTP 400" in bc._overflow_reason(exc)

    def test_an_unrelated_4xx_stays_a_plain_transport_error(self):
        assert (
            bc._overflow_reason(
                _http_error(404, b'{"error":{"message":"unknown model"}}')
            )
            is None
        )

    def test_a_5xx_is_never_an_overflow(self):
        # A 503 whose body happens to say "context too long" is the server
        # failing, not the prompt not fitting.
        assert bc._overflow_reason(_http_error(503, b"context too long")) is None

    def test_evaluate_excludes_it_and_records_it_distinctly(self, monkeypatch):
        exc = _http_error(
            400, b'{"error":{"message":"maximum context length is 4096 tokens"}}'
        )
        monkeypatch.setattr(bc, "TASKS", [MERGE])
        monkeypatch.setattr(bc, "ask", lambda *a, **k: (_ for _ in ()).throw(exc))
        r = bc.evaluate("http://x", "m", "lbl", 3000, warmup=False)
        assert r["overflow"] == 1 and r["total"] == 0 and r["wrong"] == 0
        assert r["errored"] == 0 and r["truncated"] == 0, "an overflow is neither"
        assert "OVERFLOW" in r["results"][0]["detail"]


class TestUnmeasuredWallTime:
    """D8. A cut attempt was excluded from the rate but its wall time -- up to
    the whole 1800 s deadline -- still fed total/avg/median and the rank
    tiebreak, so one abandoned attempt could rank a fast lane last.
    """

    def _lane(self, monkeypatch, script):
        """script: list of (text, wall, finish)."""
        it = iter(script)

        def fake(*a, **k):
            text, wall, finish = next(it)
            return text, 0.1, wall, 5, 10, "", finish, None, False

        monkeypatch.setattr(bc, "TASKS", [MERGE])
        monkeypatch.setattr(bc, "ask", fake)
        return bc.evaluate("http://x", "m", "lbl", 3000, repeats=2, warmup=False)

    def test_wall_statistics_cover_measured_attempts_only(self, monkeypatch):
        good = "```python\n" + GOOD + "```"
        r = self._lane(
            monkeypatch, [(good, 10.0, "stop"), ("<think>truncated", 1800.0, "length")]
        )
        assert r["truncated"] == 1 and r["passed"] == 1 and r["total"] == 1
        assert r["total_wall_s"] == 10.0, "the cut's 1800s must not be in the total"
        assert r["avg_wall_s"] == 10.0 and r["median_wall_s"] == 10.0

    def test_the_unmeasured_wall_time_is_still_reported(self, monkeypatch):
        good = "```python\n" + GOOD + "```"
        r = self._lane(
            monkeypatch, [(good, 10.0, "stop"), ("<think>truncated", 1800.0, "length")]
        )
        assert r["unmeasured_wall_s"] == 1800.0, (
            "dropping it would hide where the hour went"
        )

    def test_a_cut_cannot_flip_the_rank_tiebreak(self, monkeypatch):
        # The audit's reproduction: same rate and same measured count, so the
        # tiebreak is the wall clock — and an abandoned 1800s decided it.
        good = "```python\n" + GOOD + "```"
        fast = self._lane(
            monkeypatch, [(good, 10.0, "stop"), ("<think>truncated", 1800.0, "length")]
        )
        it = iter([(good, 50.0, "stop"), None])

        def fake(*a, **k):
            item = next(it)
            if item is None:
                raise ConnectionError("dropped")
            text, wall, finish = item
            return text, 0.1, wall, 5, 10, "", finish, None, False

        monkeypatch.setattr(bc, "ask", fake)
        slow = bc.evaluate("http://x", "m", "slow", 3000, repeats=2, warmup=False)
        # The key main() ranks by, mirrored here: rate, then measured attempts,
        # then time to a finished answer.
        key = lambda r: (
            -(r["passed"] / r["total"] if r["total"] else 0.0),
            -r["total"],
            r["total_wall_s"],
        )
        assert [r["label"] for r in sorted([slow, fast], key=key)] == ["lbl", "slow"]


class TestCandidateRlimits:
    """R14. An allocating candidate took WSL2 down with it. The ceilings must
    be in force in the CHILD, on both the plain and the `unshare -rn` path.
    """

    def test_the_child_runs_under_the_declared_ceilings(self):
        code = (
            "import resource\n"
            "def limits():\n"
            "    return [resource.getrlimit(r)[0] for r in (resource.RLIMIT_AS,\n"
            "            resource.RLIMIT_FSIZE, resource.RLIMIT_NPROC)]\n"
        )
        # With user namespaces the bucket runs under `unshare -rn` and sees
        # the plain ceiling; without them the ceiling is host-task-aware.
        expected_nproc = (
            bc.RLIMIT_NPROC if bc._netns_available() else bc._nproc_ceiling()
        )
        tests = (
            f"assert limits()[0] == {bc.RLIMIT_AS_BYTES}, limits()\n"
            f"assert limits()[1] == {bc.RLIMIT_FSIZE_BYTES}, limits()\n"
            f"assert limits()[2] == {expected_nproc}, limits()\n"
        )
        ok, detail, _ = run_candidate(code, tests)
        assert ok, detail

    def test_the_file_size_ceiling_actually_bites(self):
        # Not just declared: a candidate filling the disk is killed by the
        # kernel at the limit. 9 MB against an 8 MB ceiling -- cheap either way.
        code = (
            "def fill():\n"
            "    with open('big.bin', 'wb') as f:\n"
            f"        f.write(b'x' * {bc.RLIMIT_FSIZE_BYTES + (1 << 20)})\n"
            "    return True\n"
        )
        ok, detail, _ = run_candidate(code, "assert fill()")
        assert not ok, detail


class TestGraderSelfCheck:
    """R14. If the grading path itself breaks on a host, every model scores
    identically with the same stderr -- indistinguishable from 'the models are
    bad'. The run must stop before it measures anything.
    """

    def test_a_failing_reference_aborts_the_run(self):
        broken = dict(MERGE, reference="def merge_sorted(a, b):\n    return []\n")
        with pytest.raises(SystemExit) as e:
            bc.grader_selfcheck([broken])
        assert "GRADER SELF-CHECK FAILED" in str(e.value)
        assert "merge_sorted" in str(e.value), "the failing task must be named"

    def test_a_passing_reference_records_the_environment(self):
        rec = bc.grader_selfcheck([MERGE])
        assert rec["passed"] and rec["tasks"] == 1
        assert rec["rlimits"] == {
            "as_bytes": bc.RLIMIT_AS_BYTES,
            "fsize_bytes": bc.RLIMIT_FSIZE_BYTES,
            "nproc": bc.RLIMIT_NPROC,
            "nproc_ceiling": bc._nproc_ceiling(),
        }
        assert rec["netns"] is bc._netns_available()

    def test_main_stops_before_benchmarking_anything(self, monkeypatch):
        # Nothing may be measured after a broken grader: candidate_rows is
        # imported only below the self-check, so reaching it is the failure.
        from orchestrant.benchmark import client as bench_cli

        broken = dict(MERGE, reference="def merge_sorted(a, b):\n    return []\n")
        monkeypatch.setattr(bc, "TASKS", [broken])
        monkeypatch.setattr(
            bench_cli,
            "candidate_rows",
            lambda args, rb, re=None: pytest.fail("the sweep started anyway"),
        )
        monkeypatch.setattr(sys, "argv", ["bench_coding.py"])
        with pytest.raises(SystemExit) as e:
            bc.main()
        assert "GRADER SELF-CHECK FAILED" in str(e.value)
