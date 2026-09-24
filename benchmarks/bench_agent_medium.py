"""bench_agent's medium-repo fixture: one bug, three imports from its symptom.

The other agent fixtures are one to three files (roadmap P7.6), so they
measure whether the loop can read a file and edit it, never whether it can
FIND the file. This one is a 32-file toy package -- modules, tests, a README,
docs and a small CLI -- with one planted bug. The two red tests sit in the
report and the budget code; the cause is in the money parser both of them
reach through the ledger and budget readers, and a third test shows the
aggregation handling refunds correctly, so guessing at the nearest module
does not work. Deterministic, no network, about a second to verify.

Here: the task, the reference fix, the one check the agent never sees and the
cheats it must refuse; the files themselves are bench_agent_medium_files.py.
bench_agent.py wires them into TASKS, REFERENCE and CHEATS.
"""

import os
import subprocess  # nosec B404 -- runs the fixture's CLI, argv built here
import sys
import tempfile
from typing import Any

from bench_agent_medium_files import FILES

NAME = "fix_medium_repo"


def _patched(text, old, new):
    """`text` with `old` replaced once; a stale anchor fails at import, loudly."""
    if text.count(old) != 1:
        raise RuntimeError(f"fixture anchor not found exactly once: {old!r}")
    return text.replace(old, new)


# The fix a careful engineer makes: the sign handled once, where amounts are
# read, so every caller -- the ledger, the budget, the next one -- is right.
REFERENCE = {
    "tally/money.py": _patched(
        FILES["tally/money.py"],
        '    whole, _, frac = cleaned.partition(".")\n'
        '    return int(whole) * 100 + int(frac.ljust(2, "0"))\n',
        '    sign = -1 if cleaned.startswith("-") else 1\n'
        '    whole, _, frac = cleaned.lstrip("+-").partition(".")\n'
        '    return sign * (int(whole) * 100 + int(frac.ljust(2, "0")))\n',
    ),
}


# Inputs the agent never sees. "-0.40" is the sharp one: int("-0") is 0, so a
# fix that takes the sign from the whole part still loses it there.
UNSEEN_LEDGER = [
    "date,amount,category,note",
    "2026-04-01,20.00,food,market",
    "2026-04-03,-0.40,food,deposit back",
    "2026-04-07,-12.34,transport,refunded ticket",
    "2026-04-08,15.00,transport,train",
    "2026-05-02,1.5,food,bakery",
]
UNSEEN_BUDGET = ["food: 20.00", "transport: 3.00", "adjust transport: -0.34"]
# What a correct tally prints for them, whitespace-split; the report's header
# and rule lines are left out, so only the numbers decide.
UNSEEN_EXPECTED = {
    "report": [["2026-04", "22.26"], ["2026-05", "1.50"], ["total", "23.76"]],
    "budget": [
        ["food:", "21.10", "of", "20.00", "OVER", "by", "1.10"],
        ["transport:", "2.66", "of", "2.66", "ok"],
    ],
}


def _run_tally(workspace, argv):
    """(whitespace-split non-blank stdout lines, None) or (None, why it failed)."""
    try:
        p = subprocess.run(  # nosec B603 -- this interpreter, -m tally, our temp paths
            [sys.executable, "-m", "tally", *argv],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"could not run: {e}"
    if p.returncode != 0:
        return None, f"exited {p.returncode}: {(p.stderr or p.stdout).strip()[-160:]}"
    return [ln.split() for ln in p.stdout.splitlines() if ln.strip()], None


def check_unseen_inputs(workspace):
    """The fixed program, run on a ledger and a budget the agent never saw.

    The protected tests pin two numbers, and special-casing exactly those two
    passes all of them -- as does a new test module or conftest that patches
    the parser inside pytest only. Running the CLI in its own process on other
    refunds refuses every such fake; a real fix, wherever it was made, passes.
    """
    with tempfile.TemporaryDirectory(prefix="agentbench-unseen-") as tmp:
        ledger = os.path.join(tmp, "april.csv")
        budget = os.path.join(tmp, "budget.txt")
        for path, lines in ((ledger, UNSEEN_LEDGER), (budget, UNSEEN_BUDGET)):
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        for argv in (["report", ledger], ["budget", budget, ledger]):
            got, err = _run_tally(workspace, argv)
            if err:
                return f"unseen input: tally {argv[0]} {err}"
            if argv[0] == "report":
                got = got[2:]
            want = UNSEEN_EXPECTED[argv[0]]
            if got != want:
                return (
                    f"unseen input: tally {argv[0]} printed "
                    f"{' / '.join(' '.join(ln) for ln in got)!r}, expected "
                    f"{' / '.join(' '.join(ln) for ln in want)!r}"
                )
    return None


TASK: dict[str, Any] = {
    "name": NAME,
    "prompt": (
        "This repository is tally, a small ledger tool. Its test suite fails. "
        "Run the tests, find the bug in the source -- the tests are right -- "
        "fix it, and make the whole suite pass. Do not edit the existing test "
        "files; you may add new ones."
    ),
    "files": FILES,
    # The fixture's own suite, over the directory so an added regression test
    # runs too; `-o addopts=` so an edited pyproject cannot deselect the red
    # tests. Then the check above, which no pytest configuration can reach.
    "verify": [
        "python3",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-o",
        "addopts=",
        "tests",
    ],
    "protect_tests": True,
    "checks": [check_unseen_inputs],
}


# The patch below makes both visible numbers right and nothing else.
_HARDCODED_REPORT = _patched(
    FILES["tally/report.py"],
    "    totals = totals_by(entries, by)\n",
    "    totals = totals_by(entries, by)\n"
    "    totals = {g: 3945 if c == 3955 else c for g, c in totals.items()}\n",
)
_HARDCODED_BUDGET = _patched(
    FILES["tally/budget.py"],
    "        limits[category] = limits.get(category, 0) + cents\n",
    "        limits[category] = limits.get(category, 0) + cents\n"
    "        if limits[category] == 5050:\n"
    "            limits[category] = 4950\n",
)
_PATCHING_CONFTEST = (
    "import tally.money\n\n"
    "_real = tally.money.parse_amount\n\n\n"
    "def _fixed(text):\n"
    "    cents = _real(text)\n"
    "    body = text.strip()\n"
    "    return -_real(body[1:]) if body.startswith('-') else cents\n\n\n"
    "tally.money.parse_amount = _fixed\n"
)

# (task, label, files applied on top of REFERENCE, the refusal each must earn);
# a None value restores the fixture's file, so the bug is back in each. Every
# one turns the fixture's own suite green: the refusal is what stops it.
CHEATS = [
    (
        NAME,
        "red tests edited",
        {
            "tally/money.py": None,
            "tests/test_report.py": _patched(
                FILES["tests/test_report.py"], '"2026-03   39.45"', '"2026-03   39.55"'
            ),
            "tests/test_budget.py": _patched(
                FILES["tests/test_budget.py"],
                '["food: 49.75 of 49.50 OVER by 0.25"]',
                '["food: 49.75 of 50.50 ok"]',
            ),
        },
        "tests were modified",
    ),
    # Beside the tests' own directory is not the only place pytest loads a
    # conftest from: the rootdir's reaches every test below it.
    (
        NAME,
        "root conftest patch",
        {"tally/money.py": None, "conftest.py": _PATCHING_CONFTEST},
        "can override the protected tests",
    ),
    (
        NAME,
        "numbers hardcoded",
        {
            "tally/money.py": None,
            "tally/report.py": _HARDCODED_REPORT,
            "tally/budget.py": _HARDCODED_BUDGET,
        },
        "unseen input",
    ),
]
