#!/usr/bin/env python3
"""End-to-end: drive the real agent against a real repository (P3.1).

Everything else in this suite measures an ENDPOINT. You run an AGENT. Nothing
connected the two, and the proxies have already disagreed twice in one session:
the coding winner was the tool-calling loser until a system prompt fixed it, and
a "model family" explanation survived two rounds of documentation before a
non-Qwen model refuted it.

So: give opencode a scratch git repository and a task with a *verifiable*
outcome, let it work, then check the repository — not the transcript. Success is
"the tests pass afterwards", which no amount of confident prose can fake.

Each task starts from a fresh copy of its fixture, so a run cannot be helped by
the previous one, and the verification command is run in that copy. The three
cheap ways to fake a pass -- editing the red test, writing no tests, aliasing
the old name -- are refused; --self-test proves that along with the fixtures.
See docs/llm-benchmark-review-2026-09-05.md (R1, R4, R6).

    python3 bench_agent.py --model geniex-cpu/empero-ai/Qwen3.8-9B-Distill-GGUF:Q4_K_M
    python3 bench_agent.py --list

--model takes an OPENCODE <provider>/<model> id, so the provider key must exist
in opencode.jsonc. Use a GGUF lane: the QAIRT bundle's compiled 4096-token
context is smaller than opencode's own preamble, so it fails every task before
reading one (docs/geniex-local-ai-setup.md 1m).
"""

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# Standalone runs of these scripts (they are not a package) need the repo
# root on sys.path; the runner lives in orchestrant.benchmark.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from orchestrant.benchmark.stats import format_score

OPENCODE = os.path.expanduser("~/.opencode/bin/opencode")
# What "do not edit the tests" protects. Not Python only since the bash and
# CMake fixtures landed: their check script and their C test are the red bar.
TEST_FILE_PATTERNS = (
    "test_*.py",
    "*_test.py",
    "conftest.py",
    "check*.sh",
    "test_*.sh",
    "*_test.sh",
    "test_*.c",
    "*_test.c",
)
DIFF_LIMIT = 20_000
GIT_EXCLUDES = "__pycache__/\n.pytest_cache/\n*.pyc\n"

# Only these read as "the prompt never fitted". A bare 'context' matched Go's
# 'context canceled' and a mid-run overflow after twelve tool calls.
CONTEXT_MARKERS = (
    "context_length_exceeded",
    "prompt too long",
    "maximum context length",
    "input prompt too long",
)


def _git(cwd, *args):
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    if p.returncode != 0:
        return None, (p.stderr or p.stdout).strip()[-200:]
    return p.stdout, None


def missing_tools(task):
    """Which of a task's required tools are not on PATH.

    A tuple entry is a set of alternatives (any generator will do). A fixture
    whose tools are missing is SKIPPED and said so out loud -- running it and
    scoring the failure would blame the model for the host.
    """
    missing = []
    for need in task.get("requires", ()):
        options = (need,) if isinstance(need, str) else tuple(need)
        if not any(shutil.which(o) for o in options):
            missing.append("/".join(options))
    return missing


def is_test_file(path):
    name = os.path.basename(path)
    return any(fnmatch.fnmatch(name, pat) for pat in TEST_FILE_PATTERNS)


# Files that CONFIGURE a python test run rather than being one: added beside a
# protected test, they can monkeypatch the module the test imports.
OVERRIDE_FILES = ("conftest.py", "sitecustomize.py", "pytest.ini", "tox.ini")


def added_overrides(added, fixture_tests):
    """Untracked files that can shadow or configure the protected tests.

    Not every new test-shaped file: the verify command names explicit paths, so
    a stray `test_repro.py` is never collected and refusing it failed correct
    work. A C fixture is the exception -- its CMakeLists.txt is editable and
    can be pointed at another test_*.c.
    """
    bases = {os.path.basename(n) for n in fixture_tests}
    dirs = {os.path.dirname(n) for n in fixture_tests}
    c_fixture = any(n.endswith(".c") for n in fixture_tests)
    out = set()
    for n in added:
        base = os.path.basename(n)
        if base in bases:
            out.add(n)
        elif base in OVERRIDE_FILES and os.path.dirname(n) in dirs:
            out.add(n)
        elif c_fixture and n.endswith(".c") and is_test_file(n):
            out.add(n)
    return sorted(out)


def protected_tests_changed(workspace, task):
    """'Do not edit the tests', enforced: None if untouched, else the detail.

    Deleting, skipping or inverting the red test all print the same '2 passed'
    a real fix prints, so pytest alone cannot tell them apart; the fixture is
    committed, so git can.
    """
    fixture_tests = [n for n in task["files"] if is_test_file(n)]
    if not fixture_tests:
        # `git diff -- ` with an EMPTY pathspec means every path, which would
        # reject the fix itself; a task matching no pattern is a harness bug.
        return (
            f"{task['name']} declares protect_tests but none of its files "
            f"match {TEST_FILE_PATTERNS}"
        )
    changed, err = _git(workspace, "diff", "--name-only", "HEAD", "--", *fixture_tests)
    added, err2 = _git(workspace, "ls-files", "--others", "--exclude-standard")
    if changed is None or added is None:
        return f"could not check whether the tests were modified: {err or err2}"
    names = sorted(set(changed.split()))
    if names:
        return "tests were modified: " + ", ".join(names)
    overrides = added_overrides(added.split(), fixture_tests)
    if overrides:
        return (
            "a new file was added that can override the protected tests: "
            + ", ".join(overrides)
        )
    return None


# Tests that pass with clamp() swapped for one of these did not test the
# prompt. The reference tests kill all four, the fixture's original none.
CLAMP_MUTANTS = {
    "does not clamp at all": ("\n\ndef clamp(value, low, high):\n    return value\n"),
    "never raises": (
        "\n\ndef clamp(value, low, high):\n    return max(low, min(value, high))\n"
    ),
    "ignores the upper bound": (
        "\n\ndef clamp(value, low, high):\n"
        "    if low > high:\n        raise ValueError('low > high')\n"
        "    return max(low, value)\n"
    ),
    "ignores the lower bound": (
        "\n\ndef clamp(value, low, high):\n"
        "    if low > high:\n        raise ValueError('low > high')\n"
        "    return min(value, high)\n"
    ),
}


def check_clamp_tests_kill_mutants(workspace):
    """The agent's tests must FAIL (pytest exit 1) against every mutant."""
    for name, body in CLAMP_MUTANTS.items():
        with tempfile.TemporaryDirectory(prefix="agentbench-mutant-") as tmp:
            copy = os.path.join(tmp, "ws")
            shutil.copytree(
                workspace,
                copy,
                ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
            )
            with open(os.path.join(copy, "utils.py"), "a") as f:
                f.write(body)
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                    cwd=copy,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return f"tests timed out against a clamp that {name}"
            if r.returncode != 1:
                return f"the tests do not catch a clamp that {name}"
    return None


def old_name_uses(workspace, name):
    """Where `name` is still bound or referenced, decided on the syntax tree.

    A comment or docstring that mentions the old name is not a use; a wrapper
    def, an alias, an import, an attribute or a string handed to globals() is.
    A file that does not parse falls back to a text scan.
    """
    hits = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache")]
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            path = os.path.join(root, fname)
            rel = os.path.relpath(path, workspace)
            with open(path, encoding="utf-8", errors="replace") as f:
                src = f.read()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                if name in src:
                    hits.append(f"{rel} (does not parse)")
                continue
            docstrings = {
                id(n.value)
                for n in ast.walk(tree)
                if isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == name:
                    what = "name"
                elif isinstance(node, ast.Attribute) and node.attr == name:
                    what = "attribute"
                elif (
                    isinstance(
                        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                    )
                    and node.name == name
                ):
                    what = "definition"
                elif isinstance(node, ast.alias) and name in (node.name, node.asname):
                    what = "import"
                elif (
                    isinstance(node, ast.Constant)
                    and node.value == name
                    and id(node) not in docstrings
                ):
                    what = "string"
                else:
                    continue
                hits.append(f"{rel}:{getattr(node, 'lineno', '?')} ({what})")
    return hits


def check_rename_complete(workspace):
    hits = old_name_uses(workspace, "fetch_data")
    if hits:
        return "old name fetch_data still used: " + ", ".join(hits[:5])
    return None


# Tasks are deliberately small. This measures whether the LOOP works — read a
# file, decide, edit, stop — not whether the model is a strong engineer. A task
# a competent junior finishes in two minutes is the right size: if the loop is
# broken, it fails here too, and if the loop works, a harder task only measures
# the model again, which the other benchmarks already do.
TASKS = [
    {
        "name": "fix_failing_test",
        "prompt": (
            "The test suite in this repository fails. Run it, find the bug "
            "in the source, fix it, and make the tests pass. Do not edit "
            "the tests."
        ),
        "files": {
            "calc.py": (
                "def average(values):\n"
                '    """Return the arithmetic mean, or 0.0 for an empty list."""\n'
                "    return sum(values) / len(values)\n"
            ),
            "test_calc.py": (
                "from calc import average\n\n\n"
                "def test_average():\n"
                "    assert average([1, 2, 3]) == 2\n\n\n"
                "def test_empty_returns_zero():\n"
                "    assert average([]) == 0.0\n"
            ),
        },
        # The bug: average([]) raises ZeroDivisionError. The docstring already
        # states the intended behaviour, so the task is unambiguous.
        "verify": ["python3", "-m", "pytest", "-q", "test_calc.py"],
        "protect_tests": True,
    },
    {
        "name": "add_function_and_test",
        "prompt": (
            "Add a function `clamp(value, low, high)` to utils.py that "
            "returns value limited to the range [low, high], and raises "
            "ValueError if low > high. Then add tests for it in "
            "test_utils.py covering both ends of the range and the error "
            "case. Make sure the whole test suite passes."
        ),
        "files": {
            "utils.py": (
                "def slugify(text):\n"
                '    """Lowercase and hyphenate."""\n'
                "    return text.strip().lower().replace(' ', '-')\n"
            ),
            "test_utils.py": (
                "from utils import slugify\n\n\n"
                "def test_slugify():\n"
                "    assert slugify('  Hello World ') == 'hello-world'\n"
            ),
        },
        # Verified by a check the agent never sees, so it cannot be satisfied by
        # writing a vacuous test; then the agent's tests must kill CLAMP_MUTANTS.
        "verify": [
            "python3",
            "-c",
            (
                "import subprocess,sys;"
                "from utils import clamp;"
                "assert clamp(5,1,10)==5 and clamp(0,1,10)==1 and clamp(99,1,10)==10;"
                '\nexec(\'try:\\n clamp(1,10,1)\\n raise SystemExit("no ValueError")\\n'
                "except ValueError:\\n pass')\n"
                "r=subprocess.run([sys.executable,'-m','pytest','-q'],capture_output=True);"
                "sys.exit(r.returncode)"
            ),
        ],
        "checks": [check_clamp_tests_kill_mutants],
    },
    {
        "name": "multi_file_rename",
        "prompt": (
            "The function `fetch_data` in client.py is misnamed — it does "
            "not fetch anything, it formats a record. Rename it to "
            "`format_record` everywhere it is used, including in the "
            "tests, and make sure the tests still pass."
        ),
        "files": {
            "client.py": (
                "def fetch_data(record):\n"
                "    return f\"{record['id']}: {record['name']}\"\n"
            ),
            "report.py": (
                "from client import fetch_data\n\n\n"
                "def build(records):\n"
                "    return [fetch_data(r) for r in records]\n"
            ),
            "test_report.py": (
                "from report import build\n"
                "from client import fetch_data\n\n\n"
                "def test_build():\n"
                "    assert build([{'id': 1, 'name': 'a'}]) == ['1: a']\n\n\n"
                "def test_direct():\n"
                "    assert fetch_data({'id': 2, 'name': 'b'}) == '2: b'\n"
            ),
        },
        # Three files. Verified on the NEW name, with the old one gone from the
        # tree: renaming in one place and aliasing in another is not a rename.
        "verify": [
            "python3",
            "-c",
            (
                "import subprocess,sys;"
                "from client import format_record;"
                "assert format_record({'id':3,'name':'c'})=='3: c';"
                "r=subprocess.run([sys.executable,'-m','pytest','-q'],capture_output=True);"
                "sys.exit(r.returncode)"
            ),
        ],
        "checks": [check_rename_complete],
    },
    # The repository is 325 .sh / 23 CMake against 69 .py, and a model that
    # scores here on Python alone has not been measured on the work.
    {
        "name": "fix_bash_quoting",
        "prompt": (
            "The script list-files.sh prints the byte size of each file "
            "named on its command line. It is wrong for any path that "
            "contains a space or a glob character. Run ./check.sh to see "
            "the failure, fix list-files.sh, and make ./check.sh pass. "
            "Do not edit check.sh."
        ),
        "files": {
            "list-files.sh": (
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "\n"
                "# Print '<path>: <bytes>' for every path given on the command line.\n"
                "sizes() {\n"
                "    for f in $@; do\n"
                "        printf '%s: %s\\n' $f $(wc -c < $f)\n"
                "    done\n"
                "}\n"
                "\n"
                'sizes "$@"\n'
            ),
            "check.sh": (
                "#!/usr/bin/env bash\n"
                "# Plain bash on purpose: no bats, no framework to install.\n"
                "set -euo pipefail\n"
                'cd "$(dirname "$0")"\n'
                "\n"
                "work=$(mktemp -d)\n"
                "trap 'rm -rf -- \"$work\"' EXIT\n"
                "\n"
                "printf 'abc' > \"$work/plain.txt\"\n"
                "printf 'de' > \"$work/with space.txt\"\n"
                "printf 'f' > \"$work/star*.txt\"\n"
                "\n"
                "fail() {\n"
                "    printf 'FAIL: %s\\n' \"$1\" >&2\n"
                "    exit 1\n"
                "}\n"
                "\n"
                'got=$(bash list-files.sh "$work/plain.txt")\n'
                'if [ "$got" != "$work/plain.txt: 3" ]; then\n'
                '    fail "a plain path printed [$got]"\n'
                "fi\n"
                "\n"
                'got=$(bash list-files.sh "$work/with space.txt")\n'
                'if [ "$got" != "$work/with space.txt: 2" ]; then\n'
                '    fail "a path with a space printed [$got]"\n'
                "fi\n"
                "\n"
                'lines=$(bash list-files.sh "$work/with space.txt" | wc -l)\n'
                'if [ "$lines" != "1" ]; then\n'
                '    fail "a path with a space produced $lines lines, not 1"\n'
                "fi\n"
                "\n"
                'got=$(bash list-files.sh "$work/star*.txt")\n'
                'if [ "$got" != "$work/star*.txt: 1" ]; then\n'
                '    fail "a path with a glob printed [$got]"\n'
                "fi\n"
                "\n"
                'got=$(bash list-files.sh "$work/plain.txt" "$work/with space.txt")\n'
                'if [ "$got" != "$work/plain.txt: 3\n'
                '$work/with space.txt: 2" ]; then\n'
                '    fail "two paths printed [$got]"\n'
                "fi\n"
                "\n"
                "printf 'ok\\n'\n"
            ),
        },
        # Verified by running the check script, never by reading the transcript.
        "verify": ["bash", "check.sh"],
        "protect_tests": True,
        "requires": ["bash", "wc"],
    },
    {
        "name": "fix_cmake_link",
        "prompt": (
            "This CMake project does not build its test: `test_math` uses "
            "`math_add` from the `mathlib` library but is never linked "
            "against it. Fix CMakeLists.txt so the project configures, "
            "builds and `ctest` passes. Do not edit test_math.c."
        ),
        "files": {
            "CMakeLists.txt": (
                "cmake_minimum_required(VERSION 3.16)\n"
                "project(mathlib C)\n"
                "enable_testing()\n"
                "\n"
                "include_directories(${CMAKE_CURRENT_SOURCE_DIR})\n"
                "\n"
                "add_library(mathlib STATIC mathlib.c)\n"
                "\n"
                "add_executable(test_math test_math.c)\n"
                "\n"
                "add_test(NAME math_add COMMAND test_math)\n"
            ),
            "mathlib.h": (
                "#ifndef MATHLIB_H\n"
                "#define MATHLIB_H\n"
                "int math_add(int a, int b);\n"
                "#endif\n"
            ),
            "mathlib.c": (
                '#include "mathlib.h"\n'
                "\n"
                "int math_add(int a, int b) {\n"
                "    return a + b;\n"
                "}\n"
            ),
            "test_math.c": (
                "#include <stdio.h>\n"
                '#include "mathlib.h"\n'
                "\n"
                "int main(void) {\n"
                "    if (math_add(2, 3) != 5) {\n"
                '        printf("math_add(2, 3) != 5\\n");\n'
                "        return 1;\n"
                "    }\n"
                "    if (math_add(-1, 1) != 0) {\n"
                '        printf("math_add(-1, 1) != 0\\n");\n'
                "        return 1;\n"
                "    }\n"
                '    printf("ok\\n");\n'
                "    return 0;\n"
                "}\n"
            ),
        },
        # The agent never sees this. `ctest` exits 0 when it finds NO tests, so the
        # count is asserted too: deleting add_test() must not read as a pass.
        # Both summary spellings accepted: newer ctest (26.04 image) drops the
        # ", 0 tests failed" clause; a zero-test run prints no "out of 1" at all.
        "verify": [
            "bash",
            "-c",
            (
                "set -euo pipefail\n"
                "cmake -S . -B build > cmake-configure.log 2>&1\n"
                "cmake --build build > cmake-build.log 2>&1\n"
                "cd build\n"
                "out=$(ctest --output-on-failure 2>&1)\n"
                "printf '%s\\n' \"$out\"\n"
                "printf '%s\\n' \"$out\" | grep -q -E '100% tests passed(, 0 tests failed)? out of 1'\n"
            ),
        ],
        "protect_tests": True,
        "requires": ["cmake", "ctest", "cc", ("make", "ninja")],
    },
]


# What a correct agent would leave behind. These exist so the harness can prove
# ITSELF before it judges anything: with no strong control model reachable, a
# row of failures is otherwise unreadable -- broken fixture or weak model, no
# way to tell. --self-test applies these by hand and asserts the verification
# is red before and green after. Never shown to a model.
REFERENCE = {
    "fix_failing_test": {
        "calc.py": (
            "def average(values):\n"
            '    """Return the arithmetic mean, or 0.0 for an empty list."""\n'
            "    if not values:\n"
            "        return 0.0\n"
            "    return sum(values) / len(values)\n"
        ),
    },
    "add_function_and_test": {
        "utils.py": (
            "def slugify(text):\n"
            '    """Lowercase and hyphenate."""\n'
            "    return text.strip().lower().replace(' ', '-')\n\n\n"
            "def clamp(value, low, high):\n"
            "    if low > high:\n"
            "        raise ValueError('low > high')\n"
            "    return max(low, min(value, high))\n"
        ),
        "test_utils.py": (
            "import pytest\n"
            "from utils import slugify, clamp\n\n\n"
            "def test_slugify():\n"
            "    assert slugify('  Hello World ') == 'hello-world'\n\n\n"
            "def test_clamp():\n"
            "    assert clamp(5, 1, 10) == 5\n"
            "    assert clamp(0, 1, 10) == 1\n"
            "    assert clamp(99, 1, 10) == 10\n\n\n"
            "def test_clamp_bad_range():\n"
            "    with pytest.raises(ValueError):\n"
            "        clamp(1, 10, 1)\n"
        ),
    },
    "multi_file_rename": {
        "client.py": (
            "def format_record(record):\n"
            "    return f\"{record['id']}: {record['name']}\"\n"
        ),
        "report.py": (
            "from client import format_record\n\n\n"
            "def build(records):\n"
            "    return [format_record(r) for r in records]\n"
        ),
        "test_report.py": (
            "from report import build\n"
            "from client import format_record\n\n\n"
            "def test_build():\n"
            "    assert build([{'id': 1, 'name': 'a'}]) == ['1: a']\n\n\n"
            "def test_direct():\n"
            "    assert format_record({'id': 2, 'name': 'b'}) == '2: b'\n"
        ),
    },
    "fix_bash_quoting": {
        "list-files.sh": (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "\n"
            "# Print '<path>: <bytes>' for every path given on the command line.\n"
            "sizes() {\n"
            "    local f\n"
            '    for f in "$@"; do\n'
            '        printf \'%s: %s\\n\' "$f" "$(wc -c < "$f")"\n'
            "    done\n"
            "}\n"
            "\n"
            'sizes "$@"\n'
        ),
    },
    "fix_cmake_link": {
        "CMakeLists.txt": (
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(mathlib C)\n"
            "enable_testing()\n"
            "\n"
            "include_directories(${CMAKE_CURRENT_SOURCE_DIR})\n"
            "\n"
            "add_library(mathlib STATIC mathlib.c)\n"
            "\n"
            "add_executable(test_math test_math.c)\n"
            "target_link_libraries(test_math PRIVATE mathlib)\n"
            "\n"
            "add_test(NAME math_add COMMAND test_math)\n"
        ),
    },
}


# What a cheating agent would leave behind, and the refusal each must earn.
# Applied on top of REFERENCE; a None value restores the fixture's file.
CHEATS = [
    (
        "fix_failing_test",
        "test file edited",
        {
            "calc.py": None,
            "test_calc.py": "from calc import average\n\n\ndef test_average():\n"
            "    assert average([1, 2, 3]) == 2\n",
        },
        "tests were modified",
    ),
    (
        "add_function_and_test",
        "no tests written",
        {"test_utils.py": None},
        "the tests do not catch",
    ),
    (
        "multi_file_rename",
        "old name aliased",
        {
            "client.py": REFERENCE["multi_file_rename"]["client.py"]
            + "\n\nfetch_data = format_record\n"
        },
        "fetch_data still used",
    ),
    # Rewriting the check script so it always exits 0 is the shell version of
    # editing the red test, and prints the same "ok" a real fix prints.
    (
        "fix_bash_quoting",
        "check script edited",
        {"check.sh": "#!/usr/bin/env bash\nprintf 'ok\\n'\n"},
        "tests were modified",
    ),
    # Same move in C: a test that returns 0 whatever math_add does.
    (
        "fix_cmake_link",
        "C test edited",
        {"test_math.c": "int main(void) {\n    return 0;\n}\n"},
        "tests were modified",
    ),
]


def apply_files(ws, files):
    """Write `files` into the workspace; a None value restores the fixture."""
    for name, content in files.items():
        if content is None:
            subprocess.run(
                ["git", "checkout", "-q", "HEAD", "--", name], cwd=ws, check=False
            )
        else:
            with open(os.path.join(ws, name), "w") as f:
                f.write(content)
    # Stale bytecode from the pre-fix import would mask the change.
    shutil.rmtree(os.path.join(ws, "__pycache__"), ignore_errors=True)


def _self_test_row(name, good, detail):
    """good=None means SKIPPED: not checked, and never counted as checked."""
    verdict = "SKIPPED" if good is None else ("OK" if good else "BROKEN")
    print(
        f"    {name:48s} {verdict:8s}"
        f"{'' if good else '  ' + detail[:80].replace(chr(10), ' ')}"
    )
    return good


def self_test():
    """Prove the fixtures, their verification and the cheat refusals; no model.

    A task is only usable if it starts FAILING and its reference solution makes
    it PASS. A fixture that already passes measures nothing; one that fails even
    when solved correctly would blame every model for the harness's own bug.
    """
    ok = True
    skipped = {}
    for task in TASKS:
        needs = missing_tools(task)
        if needs:
            # Never counted as validated: the row says the fixture was not
            # checked at all, so a host without cmake reports a hole.
            skipped[task["name"]] = needs
            _self_test_row(task["name"], None, "needs " + ", ".join(needs))
            continue
        ws = make_workspace(task)
        try:
            before, _ = verify(ws, task)
            apply_files(ws, REFERENCE[task["name"]])
            after, detail = verify(ws, task)
            ok &= _self_test_row(
                task["name"],
                (not before) and after,
                f"unsolved={'pass' if before else 'fail'} "
                f"solved={'pass' if after else 'fail'} {detail}",
            )
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    for task_name, label, files, expected in CHEATS:
        task = next(t for t in TASKS if t["name"] == task_name)
        if task_name in skipped:
            _self_test_row(
                f"{task_name}: {label} refused",
                None,
                "needs " + ", ".join(skipped[task_name]),
            )
            continue
        ws = make_workspace(task)
        try:
            apply_files(ws, REFERENCE[task_name])
            apply_files(ws, files)
            passed, detail = verify(ws, task)
            ok &= _self_test_row(
                f"{task_name}: {label} refused",
                (not passed) and expected in detail,
                "cheat was accepted" if passed else detail,
            )
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    clamp = next(t for t in TASKS if t["name"] == "add_function_and_test")
    ws = make_workspace(clamp)
    try:
        apply_files(ws, {"utils.py": REFERENCE[clamp["name"]]["utils.py"]})
        ok &= _self_test_row(
            "clamp mutants: fixture tests kill none",
            check_clamp_tests_kill_mutants(ws) is not None,
            "a mutant died against the untouched fixture",
        )
        apply_files(ws, REFERENCE[clamp["name"]])
        detail = check_clamp_tests_kill_mutants(ws)
        ok &= _self_test_row(
            "clamp mutants: reference tests kill all", detail is None, detail or ""
        )
    finally:
        shutil.rmtree(ws, ignore_errors=True)

    note = ""
    if skipped:
        note = f" — {len(skipped)} fixture(s) SKIPPED and NOT checked: " + "; ".join(
            f"{n} needs {', '.join(v)}" for n, v in skipped.items()
        )
    print(
        f"\n  Harness {'validated' if ok else 'IS BROKEN -- fix before trusting any result'}"
        f"{note}"
    )
    return ok


def make_workspace(task):
    """A fresh scratch repo. Fresh per run so nothing carries over."""
    path = tempfile.mkdtemp(prefix=f"agentbench-{task['name']}-")
    for name, content in task["files"].items():
        with open(os.path.join(path, name), "w") as f:
            f.write(content)
    subprocess.run(["git", "init", "-q"], cwd=path, check=False)
    os.makedirs(os.path.join(path, ".git", "info"), exist_ok=True)
    with open(os.path.join(path, ".git", "info", "exclude"), "w") as f:
        f.write(GIT_EXCLUDES)
    subprocess.run(["git", "add", "-A"], cwd=path, check=False)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=b@b",
            "-c",
            "user.name=b",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=path,
        check=False,
    )
    return path


def workspace_diff(workspace):
    """Everything the agent changed, new files included, as one patch."""
    subprocess.run(
        ["git", "add", "-A"], cwd=workspace, check=False, capture_output=True
    )
    out, err = _git(workspace, "diff", "--cached", "HEAD")
    return out if out is not None else f"<diff unavailable: {err}>"


def opencode_config_path():
    explicit = os.environ.get("OPENCODE_CONFIG")
    if explicit:
        return explicit
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    for name in ("opencode.jsonc", "opencode.json", "config.json"):
        path = os.path.join(base, "opencode", name)
        if os.path.exists(path):
            return path
    return None


def load_jsonc(text):
    """JSON with // and /* */ comments and trailing commas, as opencode reads it."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            out.append("" if j < n and text[j] in "}]" else ",")
            i += 1
        else:
            out.append(c)
            i += 1
    return json.loads("".join(out))


def read_opencode_config(path):
    """(config dict or None, sha256 or None, note or None)."""
    if not path or not os.path.exists(path):
        return None, None, f"opencode config not found: {path}"
    with open(path, "rb") as f:
        raw = f.read()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        return load_jsonc(raw.decode("utf-8")), digest, None
    except (ValueError, UnicodeDecodeError) as e:
        return None, digest, f"opencode config did not parse: {e}"


def resolve_base_url(config, model):
    """The lane behind `provider/model`, in this suite's no-/v1 form."""
    if not config or not model or "/" not in model:
        return None
    provider = model.split("/", 1)[0]
    url = config.get("provider", {}).get(provider, {}).get("options", {}).get("baseURL")
    if not isinstance(url, str) or not url:
        return None
    return re.sub(r"/v1/?$", "", url.rstrip("/"))


def opencode_version():
    try:
        p = subprocess.run(
            [OPENCODE, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() or None if p.returncode == 0 else None


def opencode_env(scratch_home, config_path):
    """Env for the agent: sessions land in `scratch_home`, not ~/.local/share.

    opencode reads XDG_DATA_HOME for its data dir and OPENCODE_CONFIG for an
    explicit config (verified against the 1.18.25 binary). auth.json lives in
    the data dir, so it is copied along; the config dir is left alone because
    provider packages are installed there and would be re-fetched.
    """
    env = dict(os.environ)
    data = os.path.join(scratch_home, "data")
    os.makedirs(os.path.join(data, "opencode"), exist_ok=True)
    real = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    auth = os.path.join(real, "opencode", "auth.json")
    if os.path.exists(auth):
        shutil.copy(auth, os.path.join(data, "opencode", "auth.json"))
    env["XDG_DATA_HOME"] = data
    if config_path:
        env["OPENCODE_CONFIG"] = config_path
    return env


def run_agent(workspace, model, prompt, timeout, env=None):
    """One opencode session. Returns (events, wall_s, timed_out, stderr)."""
    cmd = [OPENCODE, "run", "--format", "json", "--dir", workspace]
    if model:
        cmd += ["-m", model]
    cmd += [prompt]

    def parse(stdout):
        out = []
        for line in (stdout or "").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def text(stream):
        if stream is None:
            return ""
        return (
            stream.decode("utf-8", "replace") if isinstance(stream, bytes) else stream
        )

    started = time.monotonic()
    # Own session, so a timeout kills opencode's bash children too; they used
    # to outlive the deadline and have the workspace deleted under them.
    proc = subprocess.Popen(
        cmd,
        cwd=workspace,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.kill()
        # Keep what the run produced before the deadline: "0 tool calls" for
        # an agent that made twenty and ran long reads as "it never started".
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = text(e.stdout), text(e.stderr)
        return parse(text(out)), time.monotonic() - started, True, text(err)[-400:]
    return parse(text(out)), time.monotonic() - started, False, text(err)[-400:]


def _event_kind(e):
    kind = str(e.get("type") or e.get("event") or "").lower()
    if "tool" in kind:
        return "tool"
    if "step" in kind:
        return "step"
    if "message" in kind or "text" in kind:
        return "message"
    return "unknown"


def agent_errors(events):
    """Errors the agent itself hit, classified.

    A prompt that never fitted the context is CONTEXT -- blocked, not a
    capability result -- only when no tool or step event was seen. The same
    marker after the model had started working is CONTEXT_GROWTH: the P3.3
    failure the roadmap names, and a real FAIL.
    """
    reached = any(_event_kind(e) in ("tool", "step") for e in events)
    out = []
    for e in events:
        if str(e.get("type", "")).lower() != "error":
            continue
        msg = json.dumps(e.get("error", {}))
        low = msg.lower()
        if any(marker in low for marker in CONTEXT_MARKERS):
            if reached:
                out.append(
                    ("CONTEXT_GROWTH", "context overflowed after the agent started")
                )
            else:
                out.append(("CONTEXT", "prompt exceeds the model's context"))
        elif "tool" in low:
            out.append(("TOOLS", "tool-call handling failed"))
        else:
            out.append(("ERROR", msg[:160]))
    return out


def summarise_events(events):
    """Turn, step and tool counts from the event stream, defensively.

    The event schema is opencode's, not ours, so anything unrecognised is
    counted as unknown rather than silently dropped — a zero here must mean
    "none happened", not "we could not tell".
    """
    counts = {"tool": 0, "step": 0, "message": 0, "unknown": 0}
    for e in events:
        counts[_event_kind(e)] += 1
    return {
        "tool_events": counts["tool"],
        "step_events": counts["step"],
        "message_events": counts["message"],
        "unrecognised_events": counts["unknown"],
        "total_events": len(events),
    }


def verify(workspace, task):
    """Did the repository actually change as required?

    Checked by running a command in the workspace, never by reading the
    transcript: an agent that says it fixed the bug and did not is the failure
    this whole benchmark exists to catch. Then the task's own checks run --
    the ones that refuse an edited test, an untested clamp, an aliased rename.
    """
    if task.get("protect_tests"):
        detail = protected_tests_changed(workspace, task)
        if detail:
            return False, detail
    try:
        proc = subprocess.run(
            task["verify"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "verification timed out"
    except Exception as e:  # noqa: BLE001
        return False, f"verification could not run: {e}"
    detail = (proc.stdout + proc.stderr)[-300:]
    if proc.returncode != 0:
        return False, detail
    for check in task.get("checks", ()):
        failure = check(workspace)
        if failure:
            return False, failure
    return True, detail


def run_task(task, model, timeout, keep, env=None, keep_output=False):
    workspace = make_workspace(task)
    try:
        events, wall, timed_out, stderr = run_agent(
            workspace, model, task["prompt"], timeout, env
        )
        passed, detail = (
            (False, "agent timed out") if timed_out else verify(workspace, task)
        )
        counts = summarise_events(events)
        errors = agent_errors(events)
        if passed:
            status = "PASS"
        elif timed_out:
            status = "TIMEOUT"
        elif errors:
            # The first error is the one that derailed the run; later ones are
            # usually its echo.
            status = errors[0][0]
            detail = errors[0][1]
        else:
            status = "FAIL"
        blocked = status == "CONTEXT"
        diff = workspace_diff(workspace)
        print(
            f"    {task['name']:24s} {status:8s} {wall:7.1f}s  "
            f"events={counts['total_events']:4d} tools={counts['tool_events']:3d}  "
            f"{'' if passed else detail[:60].replace(chr(10), ' ')}",
            flush=True,
        )
        row = {
            "task": task["name"],
            "passed": passed,
            "timed_out": timed_out,
            "status": status,
            "blocked": blocked,
            # errored: a comparer must skip a row the model never saw.
            "errored": blocked,
            "wall_s": round(wall, 2),
            "detail": detail[:400],
            "errors": [e[0] for e in errors],
            "stderr": stderr[:200],
            "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
            "diff_bytes": len(diff.encode()),
            **counts,
        }
        if keep_output:
            row["diff"] = diff[:DIFF_LIMIT]
            row["diff_truncated"] = len(diff) > DIFF_LIMIT
        return row
    finally:
        if keep:
            print(f"      workspace kept at {workspace}", flush=True)
        else:
            shutil.rmtree(workspace, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--model",
        default=None,
        help="opencode <provider>/<model> id from opencode.jsonc, "
        "e.g. geniex-cpu/empero-ai/Qwen3.8-9B-Distill-GGUF:Q4_K_M. "
        "A GGUF lane -- the QAIRT bundle cannot run opencode at all",
    )
    ap.add_argument("--label", default=None)
    ap.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Per-task ceiling in seconds (default 900)",
    )
    ap.add_argument(
        "--task",
        default=None,
        choices=[t["name"] for t in TASKS],
        help="Run only this task",
    )
    ap.add_argument(
        "--keep",
        action="store_true",
        help="Keep the scratch workspaces and opencode data dir for inspection",
    )
    ap.add_argument(
        "--keep-output",
        action="store_true",
        help="Store each workspace's git diff in the report (first 20 kB)",
    )
    ap.add_argument("--list", action="store_true")
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="Verify the fixtures against known-good solutions; no model",
    )
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    if args.list:
        for t in TASKS:
            print(f"  {t['name']:24s} {t['prompt'][:70]}...")
        return

    if args.self_test:
        raise SystemExit(0 if self_test() else 1)

    if not os.path.exists(OPENCODE):
        raise SystemExit(f"opencode not found at {OPENCODE}")

    tasks = [t for t in TASKS if not args.task or t["name"] == args.task]
    if not tasks:
        raise SystemExit(f"--task {args.task!r} selected nothing")
    # A fixture whose tools are absent is announced and dropped, never run and
    # scored: a host without cmake would otherwise report the MODEL as failing.
    skipped = [(t, missing_tools(t)) for t in tasks]
    skipped = [(t, m) for t, m in skipped if m]
    names = {t["name"] for t, _ in skipped}
    tasks = [t for t in tasks if t["name"] not in names]
    for task, needs in skipped:
        print(
            f"    {task['name']:24s} SKIPPED  needs {', '.join(needs)} on PATH "
            f"— NOT graded on this host",
            flush=True,
        )
    if not tasks:
        raise SystemExit(
            "every selected task needs a tool this host does not have: "
            + "; ".join(f"{t['name']} needs {', '.join(m)}" for t, m in skipped)
        )
    label = args.label or args.model or "default model"

    config_path = opencode_config_path()
    config, config_sha, note = read_opencode_config(config_path)
    base_url = resolve_base_url(config, args.model)
    scratch_home = tempfile.mkdtemp(prefix="agentbench-home-")
    env = opencode_env(scratch_home, config_path)

    print(f"\n  === {label} ===", flush=True)
    try:
        results = [
            run_task(t, args.model, args.timeout, args.keep, env, args.keep_output)
            for t in tasks
        ]
    finally:
        if args.keep:
            print(f"      opencode data kept at {scratch_home}", flush=True)
        else:
            shutil.rmtree(scratch_home, ignore_errors=True)

    passed = sum(1 for r in results if r["passed"])
    blocked = [r for r in results if r["blocked"]]
    # A model that never received the task did not fail it: blocked runs leave
    # the denominator and the wall, and an all-blocked report is 0/0 ("n/a").
    wall = sum(r["wall_s"] for r in results if not r["blocked"])
    attempted = len(results) - len(blocked)
    print(
        f"    -> {format_score(passed, attempted)} attempted tasks completed, "
        f"{wall:.1f}s total",
        flush=True,
    )
    if blocked:
        print(
            f"       {len(blocked)} never reached the model: the prompt did not fit "
            f"the context. Not a capability result -- excluded from the score.",
            flush=True,
        )
    if skipped:
        print(
            f"       {len(skipped)} fixture(s) were SKIPPED for a missing tool and "
            f"are not in that score.",
            flush=True,
        )

    if args.output:
        from orchestrant.benchmark.client import write_report

        incomplete = [] if base_url else ["base_url"]
        if note:
            incomplete.append("opencode_config")
        extra = {
            "opencode_version": opencode_version(),
            "opencode_config_path": config_path,
            "opencode_config_sha256": config_sha,
            "opencode_config_note": note,
            "tools_disabled": (config or {}).get("tools"),
            "instructions": (config or {}).get("instructions"),
            "incomplete": incomplete,
        }
        write_report(
            args.output,
            "bench_agent",
            {
                "model": args.model,
                "timeout": args.timeout,
                "keep_output": args.keep_output,
            },
            [
                {
                    "label": label,
                    "model": args.model,
                    "passed": passed,
                    "total": attempted,
                    "tasks_run": len(results),
                    "blocked_on_context": len(blocked),
                    "skipped_tasks": [
                        {"task": t["name"], "needs": m} for t, m in skipped
                    ],
                    "total_wall_s": round(wall, 2),
                    "results": results,
                }
            ],
            base_url,
            (os.path.abspath(__file__), "provenance.py"),
            extra=extra,
        )
        print(f"  Report written to {args.output}")


if __name__ == "__main__":
    main()
