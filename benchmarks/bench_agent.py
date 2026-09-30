#!/usr/bin/env python3
"""End-to-end: drive the real agent (opencode) against a real repository.

A trial passes only if the repository's tests pass afterwards; --self-test
proves the fixtures and the refusals of edited tests, missing tests and aliases.

    python3 bench_agent.py --model geniex-cpu/empero-ai/Qwen3.8-9B-Distill-GGUF:Q4_K_M
    python3 bench_agent.py --list
    python3 bench_agent.py --model ... --repeats 3   # adds pass^1..pass^3

--model takes an opencode <provider>/<model> id. Use a GGUF lane: a QAIRT
bundle's 4096-token context is smaller than opencode's own preamble.
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
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# Standalone runs (not a package) need the repo root on sys.path for orchestrant.benchmark.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import bench_agent_medium as medium_repo
import bench_agent_medium_files as medium_repo_files
from orchestrant.benchmark.stats import format_score, pass_hat_k, wilson_interval

OPENCODE = os.path.expanduser("~/.opencode/bin/opencode")
# tool_sha256 covers the medium fixture too: it is graded code as much as this file.
TOOL_FILES = (
    os.path.abspath(__file__),
    os.path.abspath(medium_repo.__file__),
    os.path.abspath(medium_repo_files.__file__),
)
# What "do not edit the tests" protects, the bash check script and the C test included.
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

# Only these mean "the prompt never fitted"; a bare 'context' matched Go's 'context canceled'.
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
    """Which of a task's required tools (a tuple: any one of them) are not on PATH."""
    missing = []
    for need in task.get("requires", ()):
        options = (need,) if isinstance(need, str) else tuple(need)
        if not any(shutil.which(o) for o in options):
            missing.append("/".join(options))
    return missing


def is_test_file(path):
    name = os.path.basename(path)
    return any(fnmatch.fnmatch(name, pat) for pat in TEST_FILE_PATTERNS)


# Files that CONFIGURE a python test run: added, they can patch or deselect the red test.
OVERRIDE_FILES = (
    "conftest.py",
    "sitecustomize.py",
    "pytest.ini",
    ".pytest.ini",
    "tox.ini",
    "setup.cfg",
    "pyproject.toml",
)


def _dir_and_ancestors(path):
    """'tests/unit' -> {'tests/unit', 'tests', ''}: the workspace root included."""
    out = {path}
    while path:
        path = os.path.dirname(path)
        out.add(path)
    return out


def added_overrides(added, fixture_tests):
    """Untracked files that can shadow or configure the protected tests."""
    bases = {os.path.basename(n) for n in fixture_tests}
    dirs = set()
    for n in fixture_tests:
        dirs |= _dir_and_ancestors(os.path.dirname(n))
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
    """'Do not edit the tests', enforced through git: None if untouched, else the detail."""
    fixture_tests = [n for n in task["files"] if is_test_file(n)]
    if not fixture_tests:
        # An EMPTY pathspec means every path, which would reject the fix itself.
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


# Tests that pass with clamp() swapped for one of these did not test the prompt.
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
    """Where `name` is still bound or referenced, on the syntax tree (text if unparsable)."""
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


# Deliberately small: this measures whether the agent LOOP works, not engineering strength.
TASKS: list[dict[str, Any]] = [
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
        # average([]) raises; the fixture's docstring already states the intent.
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
        # A check the agent never sees; then the agent's tests must kill CLAMP_MUTANTS.
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
        # Verified on the NEW name with the old one gone: an alias is not a rename.
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
    # The family's code is mostly shell and CMake; Python alone would not measure the work.
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
        # ctest exits 0 with NO tests, so the count is asserted; both summary spellings pass.
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
    # The bug sits two imports away from its red tests: finding the file is measured too.
    medium_repo.TASK,
]


# What a correct agent leaves behind: --self-test proves the harness with it; never shown to a model.
REFERENCE: dict[str, dict[str, Any]] = {
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
    medium_repo.NAME: medium_repo.REFERENCE,
}


# What a cheating agent leaves behind, on top of REFERENCE; None restores the fixture's file.
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
    # The red test deselected by config, the test file untouched.
    (
        "fix_failing_test",
        "pytest config added",
        {
            "calc.py": None,
            "pyproject.toml": "[tool.pytest.ini_options]\naddopts = \"-k 'not empty'\"\n",
        },
        "can override the protected tests",
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
    # A check script rewritten to always exit 0 is the shell version of editing the red test.
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
    *medium_repo.CHEATS,
]


def _write_file(root, name, content):
    """`name` may be nested ('tally/money.py'); its directories are created."""
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def apply_files(ws, files):
    """Write `files` into the workspace; a None value restores the fixture."""
    for name, content in files.items():
        if content is None:
            subprocess.run(
                ["git", "checkout", "-q", "HEAD", "--", name], cwd=ws, check=False
            )
        else:
            _write_file(ws, name, content)
    # Stale bytecode from the pre-fix import would mask the change, in every package dir.
    for root, dirs, _ in os.walk(ws):
        if "__pycache__" in dirs:
            shutil.rmtree(os.path.join(root, "__pycache__"), ignore_errors=True)
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]


def _self_test_row(name, good, detail):
    """good=None means SKIPPED: not checked, and never counted as checked."""
    verdict = "SKIPPED" if good is None else ("OK" if good else "BROKEN")
    print(
        f"    {name:48s} {verdict:8s}"
        f"{'' if good else '  ' + detail[:80].replace(chr(10), ' ')}"
    )
    return good


def self_test():
    """Prove each fixture fails before and passes after its reference, and the cheat refusals."""
    ok = True
    skipped = {}
    for task in TASKS:
        needs = missing_tools(task)
        if needs:
            # Never counted as validated: a host without cmake reports a hole.
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
        _write_file(path, name, content)
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
    """Env for the agent: data and state land in `scratch_home`; config and cache stay shared."""
    env = dict(os.environ)
    data = os.path.join(scratch_home, "data")
    os.makedirs(os.path.join(data, "opencode"), exist_ok=True)
    real = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    auth = os.path.join(real, "opencode", "auth.json")
    if os.path.exists(auth):
        shutil.copy(auth, os.path.join(data, "opencode", "auth.json"))
    env["XDG_DATA_HOME"] = data
    # Shared state holds model.json, whose recent list opencode falls back to without --model.
    state = os.path.join(scratch_home, "state")
    os.makedirs(state, exist_ok=True)
    env["XDG_STATE_HOME"] = state
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
    # Own session, so a timeout also kills opencode's bash children.
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
        # Keep what the run produced before the deadline, or it reads as "never started".
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
    """Errors the agent hit: CONTEXT (blocked) before any tool or step, else CONTEXT_GROWTH."""
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
    """Turn, step and tool counts; unrecognised events count as unknown, never dropped."""
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
    """Did the repository actually change as required? Commands decide, never the transcript."""
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


def run_task(
    task, model, timeout, keep, env=None, keep_output=False, attempt=0, repeats=1
):
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
            # The first error derailed the run; later ones are usually its echo.
            status = errors[0][0]
            detail = errors[0][1]
        else:
            status = "FAIL"
        blocked = status == "CONTEXT"
        diff = workspace_diff(workspace)
        name = task["name"] + (f" [{attempt + 1}/{repeats}]" if repeats > 1 else "")
        print(
            f"    {name:24s} {status:8s} {wall:7.1f}s  "
            f"events={counts['total_events']:4d} tools={counts['tool_events']:3d}  "
            f"{'' if passed else detail[:60].replace(chr(10), ' ')}",
            flush=True,
        )
        row = {
            "task": task["name"],
            # 0-based, as bench_coding and bench_tools count it.
            "attempt": attempt,
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


def run_trial(task, attempt, args, config_path):
    """One trial: a fresh scratch repository AND a fresh opencode home, for independent draws."""
    home = tempfile.mkdtemp(prefix="agentbench-home-")
    try:
        env = opencode_env(home, config_path)
        return run_task(
            task,
            args.model,
            args.timeout,
            args.keep,
            env,
            args.keep_output,
            attempt=attempt,
            repeats=args.repeats,
        )
    finally:
        if args.keep:
            print(f"      opencode data kept at {home}", flush=True)
        else:
            shutil.rmtree(home, ignore_errors=True)


def run_trials(tasks, args, config_path):
    """Every task `args.repeats` times, round-robin, so lane drift spreads over every task."""
    return [
        run_trial(task, attempt, args, config_path)
        for attempt in range(args.repeats)
        for task in tasks
    ]


def summarise_trials(results, repeats):
    """Per-task (passes, attempts), pass^1..pass^N and Wilson; a blocked trial is no attempt."""
    cases = {}
    for r in results:
        passes, attempts = cases.get(r["task"], (0, 0))
        if not r["blocked"]:
            passes, attempts = passes + int(bool(r["passed"])), attempts + 1
        cases[r["task"]] = (passes, attempts)
    passed = sum(c for c, _ in cases.values())
    attempted = sum(n for _, n in cases.values())
    pass_hat = []
    for k in range(1, repeats + 1):
        value = pass_hat_k(cases, k)
        pass_hat.append(
            {
                "k": k,
                "value": None if value is None else round(value, 4),
                "tasks": sum(1 for _, n in cases.values() if n >= k),
            }
        )
    return {
        "per_task": {t: {"passes": c, "attempts": n} for t, (c, n) in cases.items()},
        "pass_hat_k": pass_hat,
        # 0/0 stores no interval rather than the uninformative [0, 1].
        "wilson_95": (
            [round(x, 4) for x in wilson_interval(passed, attempted)]
            if attempted
            else None
        ),
    }


def print_trials(summary):
    """Per-task passes and pass^k: what one draw per task could not say."""
    print(
        "       per task: "
        + ", ".join(
            f"{task} {v['passes']}/{v['attempts']}"
            for task, v in summary["per_task"].items()
        ),
        flush=True,
    )
    print(
        "       every one of k trials passes, mean over tasks: "
        + "  ".join(
            f"pass^{p['k']} "
            + ("n/a" if p["value"] is None else f"{100 * p['value']:.0f}%")
            for p in summary["pass_hat_k"]
        ),
        flush=True,
    )


def _at_least_one(text):
    """argparse type for --repeats: 0 would run nothing and report 0/0."""
    try:
        value = int(text)
    except ValueError:
        # Otherwise argparse names this function: "invalid _at_least_one value".
        raise argparse.ArgumentTypeError(
            f"must be a whole number, got {text!r}"
        ) from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


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
        "--repeats",
        type=_at_least_one,
        default=1,
        help="Trials per task, each with a fresh repository and opencode home; "
        "the report adds per-task passes and pass^1..pass^N (default 1)",
    )
    ap.add_argument(
        "--keep",
        action="store_true",
        help="Keep the scratch workspaces and opencode data dirs for inspection",
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
    # Announce and drop fixtures whose tools are absent: never score the host as the model.
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
    from orchestrant.benchmark.client import run_start, write_report

    # No determinism probe runs here, so no determinism.py in the hash.
    run = run_start(TOOL_FILES, base_url)

    print(f"\n  === {label} ===", flush=True)
    results = run_trials(tasks, args, config_path)
    trials = summarise_trials(results, args.repeats)

    passed = sum(1 for r in results if r["passed"])
    blocked = [r for r in results if r["blocked"]]
    # Blocked runs never reached the model: they leave the denominator and the wall.
    wall = sum(r["wall_s"] for r in results if not r["blocked"])
    attempted = len(results) - len(blocked)
    print(
        f"    -> {format_score(passed, attempted)} attempted "
        f"{'trials' if args.repeats > 1 else 'tasks'} completed, {wall:.1f}s total",
        flush=True,
    )
    if args.repeats > 1:
        print_trials(trials)
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
                "repeats": args.repeats,
            },
            [
                {
                    "label": label,
                    "model": args.model,
                    "passed": passed,
                    "total": attempted,
                    "repeats": args.repeats,
                    "tasks_run": len(tasks),
                    "trials_run": len(results),
                    "blocked_on_context": len(blocked),
                    "skipped_tasks": [
                        {"task": t["name"], "needs": m} for t, m in skipped
                    ],
                    "total_wall_s": round(wall, 2),
                    **trials,
                    "results": results,
                }
            ],
            base_url,
            TOOL_FILES,
            extra=extra,
            run_start=run,
        )
        print(f"  Report written to {args.output}")


if __name__ == "__main__":
    main()
