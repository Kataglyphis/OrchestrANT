"""Every task must be solvable, and its tests must reject a wrong solution.

Two failure modes this guards against, both of which silently ruin a ranking:

  * an UNSOLVABLE task (a spec that contradicts its own tests) makes every
    model look incapable, and the benchmark looks decisive while measuring
    nothing;
  * a task whose tests are too weak passes a wrong solution, which is how the
    merge task accepted `return sorted(a + b)` for as long as it did.

So: a reference solution written from the prompt alone must pass, and a
deliberately wrong one must fail.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bench_coding as bc  # noqa: E402
from bench_coding import (
    EXTENDED_TASKS,
    KINDS,
    LANGS,
    LANGUAGE_TASKS,  # noqa: E402
    NOVEL_TASKS,
    TASKS,
    extract_code,
    run_candidate,
)

# What must be on PATH before a language is graded. cmake and hadolint often
# are not, so those rows SKIP visibly; the skip itself is asserted below.
LANG_TOOL = {"python": None, "bash": "bash", "cmake": "cmake", "dockerfile": None}

REFERENCE = {
    "merge_sorted": """
def merge_sorted(a: list, b: list) -> list:
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            out.append(a[i]); i += 1
        else:
            out.append(b[j]); j += 1
    out.extend(a[i:]); out.extend(b[j:])
    return out
""",
    "balanced": """
def balanced(s: str) -> bool:
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
    "parse_version": """
def parse_version(v: str) -> tuple:
    if not isinstance(v, str) or not v.strip():
        raise ValueError("empty")
    t = v.strip()
    if t[:1].lower() == "v":
        t = t[1:]
    parts = t.split(".")
    if len(parts) > 3 or not all(p.isdigit() for p in parts) or not parts[0]:
        raise ValueError("bad version")
    nums = [int(p) for p in parts]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)
""",
    "parse_lane_spec": """
def parse_lane(spec: str) -> tuple:
    marker = ",model="
    if marker not in spec:
        raise ValueError("missing ,model= marker")
    head, model = spec.split(marker, 1)
    if "=" not in head:
        raise ValueError("missing = before the marker")
    name, url = head.split("=", 1)
    return (name.strip(), url.strip().rstrip("/"), model.strip())
""",
    "attempt_verdict": """
def verdict(passed: bool, tokens: int, cap: int, closed_fence: bool) -> str:
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
    "rank_quants": """
def rank_quants(names: list) -> list:
    def digit(n):
        for ch in n:
            if ch.isdigit():
                return int(ch)
        return None
    kept = [n for n in names if digit(n) is not None]
    return sorted(kept, key=lambda n: (-digit(n), 0 if n.startswith("Q") else 1, n))
""",
}

WRONG = {
    # Ignores the stated constraint -- must be rejected by `forbidden`.
    "merge_sorted": "def merge_sorted(a: list, b: list) -> list:\n    return sorted(a + b)\n",
    # Counts brackets without checking nesting: accepts "([)]".
    "balanced": (
        "def balanced(s: str) -> bool:\n"
        "    return s.count('(') == s.count(')') and s.count('[') == s.count(']')\n"
    ),
    # Never raises on garbage.
    "parse_version": (
        "def parse_version(v: str) -> tuple:\n"
        "    p = [int(x) for x in v.lstrip('v').split('.') if x.isdigit()]\n"
        "    return tuple(p + [0] * (3 - len(p)))\n"
    ),
    # Splits on the LAST marker, so a model name containing ',model=' breaks.
    "parse_lane_spec": (
        "def parse_lane(spec: str) -> tuple:\n"
        "    head, model = spec.rsplit(',model=', 1)\n"
        "    name, url = head.split('=', 1)\n"
        "    return (name.strip(), url.strip().rstrip('/'), model.strip())\n"
    ),
    # Checks the cap before `passed`, inverting rule order 1 and 2.
    "attempt_verdict": (
        "def verdict(passed, tokens, cap, closed_fence):\n"
        "    if cap < 1 or tokens < 0: raise ValueError('bad')\n"
        "    if tokens >= cap: return 'CUT'\n"
        "    if passed: return 'PASS'\n"
        "    return 'FAIL' if closed_fence else 'CUT'\n"
    ),
    # Forgets the Q-before-IQ tiebreak.
    "rank_quants": (
        "def rank_quants(names: list) -> list:\n"
        "    def d(n):\n"
        "        for c in n:\n"
        "            if c.isdigit(): return int(c)\n"
        "        return None\n"
        "    return sorted([n for n in names if d(n) is not None],\n"
        "                  key=lambda n: (-d(n), n))\n"
    ),
}

# The extended and language sets carry their own reference/wrong solutions, so
# they need no entry in the dicts above — the guard reads them off the task.
ALL_TASKS = TASKS + NOVEL_TASKS + EXTENDED_TASKS + LANGUAGE_TASKS


def _reference(task):
    return task.get("reference") or REFERENCE[task["name"]]


def _wrong(task):
    return task.get("wrong") or WRONG[task["name"]]


def _grade(task, code):
    """Grade `code` for `task` through the REAL path: extract_code, then the
    language's runner with the task's own flags. Grading the reference string
    directly hid the case where extract_code mangles a correct answer."""
    lang = task.get("lang", "python")
    extracted = extract_code(
        "```\n" + code + "\n```", want=task.get("function"), lang=lang
    )
    return run_candidate(
        extracted,
        task["tests"],
        forbidden=task.get("forbidden"),
        stdlib_only=task.get("stdlib_only", False),
        lang=lang,
    )


def _skip_if_toolless(task, credit):
    """A missing interpreter must SKIP loudly, and say so in the result."""
    tool = LANG_TOOL[task.get("lang", "python")]
    if tool and not bc.tool_available(tool):
        assert credit.get("skipped"), (
            f"{task['name']}: {tool} is not on PATH and the runner did not report "
            f"a skip — an ungraded task must never look like a result"
        )
        assert tool in credit["skipped"]
        pytest.skip(f"{tool} is not on PATH: {credit['skipped']}")
    assert not credit.get("skipped"), (
        f"{task['name']}: {tool} IS on PATH but the runner skipped anyway "
        f"({credit['skipped']})"
    )


@pytest.mark.parametrize("task", ALL_TASKS, ids=lambda t: t["name"])
def test_reference_solution_passes(task):
    """The task is solvable, and its own tests agree with its own prompt."""
    ok, detail, credit = _grade(task, _reference(task))
    _skip_if_toolless(task, credit)
    assert ok, f"{task['name']}: reference solution rejected — {detail}"


@pytest.mark.parametrize("task", ALL_TASKS, ids=lambda t: t["name"])
def test_wrong_solution_is_rejected(task):
    """The tests are strong enough to catch a plausible wrong answer."""
    ok, _, credit = _grade(task, _wrong(task))
    _skip_if_toolless(task, credit)
    assert not ok, f"{task['name']}: a known-wrong solution PASSED — tests too weak"


class TestTaskShape:
    # A Dockerfile has no signature to pin: it is graded on structure.
    SIGNATURE_FREE = {"dockerfile"}

    @pytest.mark.parametrize("task", ALL_TASKS, ids=lambda t: t["name"])
    def test_prompt_states_the_exact_signature(self, task):
        # Grading is mechanical, so the required name must be unambiguous.
        if task.get("lang", "python") in self.SIGNATURE_FREE:
            pytest.skip("structural task: no signature to pin")
        assert "exact signature" in task["prompt"]
        assert task.get("function") or "def " in task["prompt"]

    @pytest.mark.parametrize("task", ALL_TASKS, ids=lambda t: t["name"])
    def test_tests_are_not_empty(self, task):
        assert task["tests"].count("assert") >= 4, "too few assertions to be decisive"

    def test_task_names_are_unique(self):
        names = [t["name"] for t in ALL_TASKS]
        assert len(names) == len(set(names))

    def test_novel_tasks_do_not_duplicate_classic_ones(self):
        assert not ({t["name"] for t in TASKS} & {t["name"] for t in NOVEL_TASKS})


class TestTaskTags:
    """R5. Every task carries an explicit kind and lang.

    Deliberately no default: a task that forgets the tag must fail here rather
    than be silently counted as python/spec-transcription, which would make the
    per-kind and per-lang rates quietly wrong for a whole set.
    """

    @pytest.mark.parametrize("task", ALL_TASKS, ids=lambda t: t["name"])
    def test_kind_and_lang_are_explicit_and_known(self, task):
        assert task.get("kind") in KINDS, f"{task['name']}: kind={task.get('kind')!r}"
        assert task.get("lang") in LANGS, f"{task['name']}: lang={task.get('lang')!r}"

    def test_the_languages_of_this_repository_are_covered(self):
        # The repo is 325 .sh / 29 Dockerfile / 23 CMake against 69 .py: a
        # Python-only suite cannot say whether a model can do the work.
        langs = {t["lang"] for t in ALL_TASKS}
        assert {"bash", "cmake", "dockerfile"} <= langs
        assert sum(1 for t in ALL_TASKS if t["lang"] == "bash") >= 3

    def test_more_than_one_kind_is_represented(self):
        # A single kind makes the per-kind column an expensive way to reprint
        # the total.
        assert len({t["kind"] for t in ALL_TASKS}) >= 2

    @pytest.mark.parametrize(
        "task", [t for t in ALL_TASKS if t["lang"] != "python"], ids=lambda t: t["name"]
    )
    def test_non_python_tasks_name_the_symbol_or_are_structural(self, task):
        # extract_code has no AST outside Python, so it needs the name to find
        # the defining fence; a structural task is matched on its own shape.
        assert task.get("function") or task["lang"] == "dockerfile"

    @pytest.mark.parametrize(
        "task",
        [t for t in ALL_TASKS if t["lang"] in ("bash", "cmake")],
        ids=lambda t: t["name"],
    )
    def test_shell_style_checks_are_one_per_line(self, task):
        # The expected count is static, so a check that is not at a line start
        # (or one inside a loop) would read back as a forged row count.
        calls = len(re.findall(r"assert_(?:eq|ok|fail)\b", task["tests"]))
        counted = len(re.findall(r"(?m)^[ \t]*assert_(?:eq|ok|fail)\b", task["tests"]))
        assert calls == counted >= 4, f"{task['name']}: {counted} counted of {calls}"


class _Proc:
    """Minimal CompletedProcess stand-in for the linter probes."""

    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class TestBashRunner:
    """The bash path: the sandbox, the row protocol and the shellcheck note."""

    FUNC = "f() {\n    printf '%s\\n' \"ok\"\n}"
    TESTS = 'assert_eq "ok" "$(f)" "prints ok"\nassert_ok "returns 0" f\n'

    def test_a_correct_function_passes_and_names_shellcheck(self):
        ok, detail, credit = run_candidate(self.FUNC, self.TESTS, lang="bash")
        assert ok, detail
        assert credit["passed"] == credit["total"] == 2
        # Never silent: the row says whether the linter ran.
        assert "shellcheck" in detail.lower() and "shellcheck" in credit

    def test_a_wrong_function_fails_with_partial_credit(self):
        ok, _detail, credit = run_candidate(
            "f() {\n    printf 'no\\n'\n}", self.TESTS, lang="bash"
        )
        assert not ok
        assert credit["passed"] == 1 and credit["total"] == 2

    def test_rows_the_candidate_forged_are_refused(self):
        # Appending to the row array is the only way to reach the report, and
        # more rows than the tests contain is corruption, never a pass.
        ok, detail, _ = run_candidate(
            self.FUNC,
            self.TESTS + '__BENCH_ROWS+=("P")\n__BENCH_ROWS+=("P")\n',
            lang="bash",
        )
        assert not ok and "corrupted" in detail, detail

    def test_an_infinite_loop_is_killed(self):
        ok, detail, _ = run_candidate(
            "f() {\n    while true; do :; done\n}", self.TESTS, timeout=3, lang="bash"
        )
        assert not ok and "timed out" in detail

    def test_a_script_that_dies_halfway_keeps_the_checks_it_reached(self):
        # `set -e` killing the run must not throw away the passed rows: the
        # EXIT trap reports them, and the verdict says where it stopped.
        tests = 'assert_eq "ok" "$(f)" "one"\nfalse\nassert_eq "ok" "$(f)" "two"\n'
        ok, detail, credit = run_candidate(self.FUNC, tests, lang="bash")
        assert not ok
        assert credit["passed"] == 1 and credit["total"] == 2
        assert "stopped after" in detail, detail

    def test_the_candidate_runs_with_pipefail(self):
        # The bash prompts promise `set -euo pipefail`: without it a failing
        # producer inside a pipeline is invisible to every check.
        ok, detail, _ = run_candidate(
            "f() {\n    false | true\n}",
            'assert_fail "a failing producer fails the pipeline" f\n'
            'assert_fail "and again" f\n',
            lang="bash",
        )
        assert ok, f"pipefail is not set for the candidate: {detail}"

    def test_an_unset_variable_is_reported_not_silently_empty(self):
        # -u: reading an undefined name must show up. It kills the subshell of
        # a command substitution, so the value is empty AND stderr says so.
        ok, detail, _ = run_candidate(
            "f() {\n    printf '%s' \"${nosuchvar}\"\n}", self.TESTS, lang="bash"
        )
        assert not ok, detail

    def test_empty_code_is_not_run(self):
        ok, detail, _ = run_candidate("   ", self.TESTS, lang="bash")
        assert not ok and "no code" in detail

    def test_the_defining_fence_wins_over_a_longer_demo(self):
        # Models answer with a compact function and a longer usage block:
        # picking by length grades the demo and calls a correct model broken.
        reply = (
            "First a helper:\n```bash\n"
            "demo_helper() {\n"
            "    local one two three four five six seven eight nine ten\n"
            "    printf '%s\\n' \"a much longer block than the answer\"\n"
            "    printf '%s\\n' \"padding so this one is the longest\"\n"
            "}\n```\n"
            "And the function itself:\n```bash\n" + self.FUNC + "\n```\n"
        )
        code = extract_code(reply, want="f", lang="bash")
        assert code.startswith("f() {"), code
        assert "demo_helper" not in code

    def test_an_unknown_language_is_refused_loudly(self):
        ok, detail, _ = run_candidate("x", "assert_eq 1 1 x", lang="perl")
        assert not ok and "unsupported" in detail


class TestVisibleSkips:
    """A tool that is absent must produce a SKIP that is visible everywhere.

    This is the failure the language tags would otherwise create: a host
    without cmake grading nothing and reporting a clean, green run.
    """

    def test_cmake_absent_is_a_skip_not_a_pass_and_not_a_fail(self, monkeypatch):
        monkeypatch.setattr(bc.shutil, "which", lambda n: None)
        ok, detail, credit = run_candidate(
            "function(f)\nendfunction()", 'assert_eq("a" "a" "x")\n', lang="cmake"
        )
        assert ok is False
        assert credit["skipped"] == "cmake not on PATH"
        assert detail.startswith("SKIPPED")

    def test_shellcheck_absent_is_named_in_the_result_not_swallowed(self, monkeypatch):
        monkeypatch.setattr(bc, "tool_available", lambda n: n == "bash")
        ok, detail, credit = run_candidate(
            "f() {\n    printf 'ok\\n'\n}", 'assert_eq "ok" "$(f)" "one"\n', lang="bash"
        )
        assert ok
        assert "shellcheck SKIPPED" in detail
        assert credit["shellcheck"] == "[shellcheck SKIPPED: not on PATH]"

    def test_hadolint_absent_still_grades_the_structure(self, monkeypatch):
        monkeypatch.setattr(bc, "tool_available", lambda n: False)
        ok, detail, credit = run_candidate(
            "FROM debian:bookworm-slim\n",
            'assert instructions("FROM")\n',
            lang="dockerfile",
        )
        assert ok, detail
        assert "hadolint SKIPPED" in detail
        assert credit["hadolint"] == "[hadolint SKIPPED: not on PATH]"

    def test_a_failing_linter_fails_the_task(self, monkeypatch):
        monkeypatch.setattr(bc, "tool_available", lambda n: True)
        monkeypatch.setattr(
            bc.subprocess, "run", lambda *a, **k: _Proc(1, "line 2: SC2086 unquoted")
        )
        ok, detail, _credit = run_candidate(
            "f() {\n    printf 'ok\\n'\n}", 'assert_eq "ok" "$(f)" "one"\n', lang="bash"
        )
        assert not ok
        assert "shellcheck -S error" in detail and "SC2086" in detail

    def test_the_grader_self_check_lists_what_it_could_not_check(self, monkeypatch):
        # A skipped reference is NOT counted as a passing one: the record names
        # the task and the run prints it.
        monkeypatch.setattr(bc, "tool_available", lambda n: n != "cmake")
        cmake_task = next(t for t in LANGUAGE_TASKS if t["lang"] == "cmake")
        rec = bc.grader_selfcheck([cmake_task])
        assert rec["tasks"] == 1 and rec["checked"] == 0
        assert rec["skipped"][cmake_task["name"]] == "cmake not on PATH"
        assert rec["tools"]["cmake"] is False


# A stand-in `cmake` that answers the marker protocol, so the runner's own
# plumbing can be proven on a host with no CMake. $2 is the script path.
_READ_MARKER = r"""#!/usr/bin/env bash
m=$(sed -n -e 's/^set(__BENCH_MARKER "\(.*\)")$/\1/p' "$2")
"""


class TestCmakeRunnerPlumbing:
    """cmake is not installed on every host, so the runner's plumbing -- argv,
    the marker protocol, the forged-row refusal -- is proven against a stub
    interpreter. What this does NOT cover is CMake's own semantics: that is
    test_reference_solution_passes, on a host that has cmake.
    """

    TESTS = 'assert_eq("a" "a" "one")\nassert_eq("b" "b" "two")\n'

    def _run(self, monkeypatch, tmp_path, body, tests=None):
        stub = tmp_path / "cmake"
        stub.write_text(_READ_MARKER + body)
        stub.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
        return run_candidate(
            "function(f)\nendfunction()", tests or self.TESTS, lang="cmake"
        )

    def test_two_passing_rows_are_a_pass(self, monkeypatch, tmp_path):
        ok, detail, credit = self._run(
            monkeypatch, tmp_path, 'printf \'%s\\nP\\nP\\n%s\\n\' "$m" "$m" >&2\n'
        )
        assert ok, detail
        assert credit["passed"] == 2 and credit["total"] == 2

    def test_a_failing_row_is_reported_with_its_message(self, monkeypatch, tmp_path):
        ok, detail, credit = self._run(
            monkeypatch,
            tmp_path,
            'printf \'%s\\nP\\nF two: expected [b] got []\\n%s\\n\' "$m" "$m" >&2\n',
        )
        assert not ok
        assert credit["passed"] == 1 and "expected [b] got []" in detail

    def test_more_rows_than_checks_is_refused(self, monkeypatch, tmp_path):
        ok, detail, _ = self._run(
            monkeypatch,
            tmp_path,
            'printf \'%s\\nP\\nP\\nP\\nP\\n%s\\n\' "$m" "$m" >&2\n',
        )
        assert not ok and "corrupted" in detail

    def test_a_script_error_is_reported_not_scored(self, monkeypatch, tmp_path):
        ok, detail, credit = self._run(
            monkeypatch,
            tmp_path,
            'echo "CMake Error at candidate.cmake:9 (normalize_arch)" >&2\nexit 1\n',
        )
        assert not ok and "CMake Error" in detail
        assert credit["total"] == 0


class TestDockerfileStructure:
    """The structural half runs with or without hadolint, so it must be real."""

    GOOD = (
        "FROM debian:bookworm-slim\n"
        "RUN apt-get update \\\n"
        " && apt-get install -y --no-install-recommends python3 \\\n"
        " && apt-get clean\n"
        "USER tool\n"
        "WORKDIR /app\n"
        "COPY tool.py /app/tool.py\n"
        'ENTRYPOINT ["python3", "/app/tool.py"]\n'
    )

    def _grade(self, text, tests):
        return run_candidate(text, tests, lang="dockerfile")

    def test_a_continuation_is_one_instruction(self):
        ok, detail, _ = self._grade(
            self.GOOD,
            'runs = instructions("RUN")\n'
            "assert len(runs) == 1, runs\n"
            'assert "apt-get update" in runs[0] and "apt-get clean" in runs[0]\n'
            'assert verbs().count("RUN") == 1\n'
            'assert instructions("FROM") == ["debian:bookworm-slim"]\n',
        )
        assert ok, detail

    def test_comments_and_blank_lines_are_not_instructions(self):
        ok, detail, _ = self._grade(
            "# a comment\n\nFROM debian:bookworm-slim\n\n# another\nUSER tool\n",
            'assert verbs() == ["FROM", "USER"], verbs()\n'
            "assert len(INSTRUCTIONS) == 2\n"
            'assert "# another" in DOCKERFILE, "the raw text is still available"\n'
            'assert instructions("USER") == ["tool"]\n',
        )
        assert ok, detail

    def test_a_dockerfile_reply_is_not_graded_on_its_build_command(self):
        # The task pins no symbol, so the block is chosen on the language's own
        # shape; by length the build instructions would win.
        reply = (
            "Build and run it with:\n```bash\n"
            "docker build -t tool . \\\n"
            "  && docker run --rm -it tool --help  # a long enough line\n"
            "```\n"
            "```dockerfile\nFROM debian:bookworm-slim\nUSER tool\n```\n"
        )
        code = extract_code(reply, lang="dockerfile")
        assert code.startswith("FROM "), code
        assert "docker build" not in code

    def test_the_task_rejects_a_split_apt_run(self):
        task = next(t for t in LANGUAGE_TASKS if t["lang"] == "dockerfile")
        split = self.GOOD.replace(
            "RUN apt-get update \\\n && apt-get install -y --no-install-recommends "
            "python3 \\\n && apt-get clean\n",
            "RUN apt-get update\nRUN apt-get install -y --no-install-recommends "
            "python3\n",
        )
        ok, detail, _ = self._grade(split, task["tests"])
        assert not ok and "RUN" in detail


class TestPerKindAndPerLangRates:
    """R5's reporting half: one aggregate hides a model that cannot write bash."""

    TASKS = [
        {"name": "py_a", "kind": "spec-transcription", "lang": "python"},
        {"name": "sh_a", "kind": "bug-fix", "lang": "bash"},
        {"name": "cm_a", "kind": "spec-transcription", "lang": "cmake"},
    ]

    def _rows(self):
        return [
            {"task": "py_a", "passed": True},
            {"task": "sh_a", "passed": False},
            {"task": "cm_a", "passed": False, "skipped": "cmake not on PATH"},
        ]

    def test_rates_split_by_lang(self):
        by_lang = bc.rates_by(self._rows(), self.TASKS, "lang")
        assert by_lang["python"] == {
            "passed": 1,
            "measured": 1,
            "skipped": 0,
            "excluded": 0,
        }
        assert by_lang["bash"]["passed"] == 0 and by_lang["bash"]["measured"] == 1

    def test_a_skipped_row_is_not_a_miss(self):
        by_lang = bc.rates_by(self._rows(), self.TASKS, "lang")
        assert by_lang["cmake"] == {
            "passed": 0,
            "measured": 0,
            "skipped": 1,
            "excluded": 0,
        }

    def test_rates_split_by_kind(self):
        by_kind = bc.rates_by(self._rows(), self.TASKS, "kind")
        assert by_kind["bug-fix"]["measured"] == 1
        assert by_kind["spec-transcription"]["passed"] == 1

    def test_an_errored_row_is_excluded_from_both(self):
        rows = self._rows() + [{"task": "py_a", "passed": False, "errored": True}]
        by_lang = bc.rates_by(rows, self.TASKS, "lang")
        assert by_lang["python"]["measured"] == 1 and by_lang["python"]["excluded"] == 1


class TestEvaluateExcludesSkips:
    """A task the host cannot grade must leave the rate, the wall and the rank.

    Counting it as a miss would rank a host without cmake as a worse MODEL --
    the one thing the skip exists to prevent.
    """

    PY_TASK = {
        "name": "py_ok",
        "kind": "spec-transcription",
        "lang": "python",
        "function": "g",
        "prompt": "def g(",
        "tests": "assert g() == 1\n",
    }
    CM_TASK = next(t for t in LANGUAGE_TASKS if t["lang"] == "cmake")

    def _report(self, monkeypatch, tasks, capsys=None):
        monkeypatch.setattr(bc, "TASKS", tasks)
        monkeypatch.setattr(bc, "tool_available", lambda n: n != "cmake")

        def fake_ask(
            base_url, model, prompt, max_tokens, timeout=1800, deadline=None, entry=None
        ):
            body = (
                "def g():\n    return 1\n"
                if "def g(" in prompt
                else "function(normalize_arch out_var arch)\nendfunction()\n"
            )
            return ("```\n" + body + "```", 0.1, 1.0, 10, 5, "", "stop", 10, False)

        monkeypatch.setattr(bc, "ask", fake_ask)
        return bc.evaluate("http://x", "m", "lbl", 3000, warmup=False)

    def test_a_skipped_task_is_out_of_the_denominator(self, monkeypatch):
        rep = self._report(monkeypatch, [self.PY_TASK, self.CM_TASK])
        assert rep["skipped"] == 1
        # 1/1, not 1/2: the cmake task was never graded either way.
        assert rep["passed"] == 1 and rep["total"] == 1 and rep["wrong"] == 0

    def test_a_skipped_task_carries_no_measured_wall(self, monkeypatch):
        rep = self._report(monkeypatch, [self.CM_TASK])
        assert rep["total"] == 0 and rep["passed"] == 0
        assert rep["total_wall_s"] == 0.0, "an ungraded attempt inflated the wall"
        assert rep["unmeasured_wall_s"] == 1.0
        assert rep["avg_wall_s"] is None

    def test_the_row_says_SKIP_and_why(self, monkeypatch, capsys):
        rep = self._report(monkeypatch, [self.CM_TASK])
        out = capsys.readouterr().out
        assert "SKIP" in out and "cmake not on PATH" in out
        row = rep["results"][0]
        assert row["skipped"] == "cmake not on PATH"
        assert row["passed"] is False and row["truncated"] is False

    def test_the_report_splits_the_rate_by_lang(self, monkeypatch):
        rep = self._report(monkeypatch, [self.PY_TASK, self.CM_TASK])
        assert rep["by_lang"]["python"]["passed"] == 1
        assert rep["by_lang"]["cmake"] == {
            "passed": 0,
            "measured": 0,
            "skipped": 1,
            "excluded": 0,
        }
        assert rep["by_kind"]["spec-transcription"]["measured"] == 1


class TestTaskSetSelection:
    """--task-set: 'all' is the default now, and every old name still works."""

    def _tasks_for(self, monkeypatch, argv):
        from orchestrant.benchmark import client as bench_cli

        monkeypatch.setattr(bench_cli, "candidate_rows", lambda args, rb, re=None: [])
        monkeypatch.setattr(
            bc,
            "grader_selfcheck",
            lambda tasks: {
                "tasks": len(tasks),
                "checked": len(tasks),
                "skipped": {},
                "seconds": 0.0,
                "netns": False,
                "tools": {},
            },
        )
        monkeypatch.setattr(sys, "argv", ["bench_coding.py"] + argv)
        original = bc.TASKS
        try:
            bc.main()
            return list(bc.TASKS)
        finally:
            bc.TASKS = original

    def test_the_default_is_every_task(self, monkeypatch):
        # The README itself calls 'classic' recall-prone and too small to carry
        # a ranking; it must not be what a bare invocation measures.
        assert len(self._tasks_for(monkeypatch, [])) == len(ALL_TASKS)

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("classic", len(TASKS)),
            ("novel", len(NOVEL_TASKS)),
            ("extended", len(EXTENDED_TASKS)),
            ("languages", len(LANGUAGE_TASKS)),
            ("all", len(ALL_TASKS)),
        ],
    )
    def test_every_set_name_still_selects_its_own_set(
        self, monkeypatch, name, expected
    ):
        assert len(self._tasks_for(monkeypatch, ["--task-set", name])) == expected

    def test_an_unknown_set_is_refused(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["bench_coding.py", "--task-set", "shell"])
        with pytest.raises(SystemExit):
            bc.main()
