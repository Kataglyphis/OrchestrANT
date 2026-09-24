"""The medium-repo agent fixture (P7.6): a 32-file package, one planted bug.

Its worth rests on three claims, each pinned here: it is red for exactly the
planted reason and green after the reference fix; the fix is not where the
failures are; and every cheap way to turn its suite green -- editing the red
tests, patching the parser from a conftest, hardcoding the two numbers -- is
refused. Tests that go through bench_agent.verify run the fixture's suite
with `python3`, as the Linux lab host does, and are skipped on Windows; the
rest run the same command under this interpreter.
"""

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import bench_agent as ba
import bench_agent_medium as medium

TASK = medium.TASK
RED = {
    "tests/test_report.py::test_a_refund_reduces_its_month",
    "tests/test_budget.py::test_a_negative_adjustment_tightens_a_limit",
}
linux_verify = pytest.mark.skipif(
    sys.platform == "win32",
    reason="verify runs `python3 -m pytest`, as on the Linux lab host",
)


def materialise(tmp_path, files):
    ws = tmp_path / "ws"
    for name, text in files.items():
        (ws / name).parent.mkdir(parents=True, exist_ok=True)
        (ws / name).write_text(text, encoding="utf-8", newline="\n")
    return str(ws)


def with_cheat(files):
    """REFERENCE plus a cheat's files, a None restoring the fixture's."""
    out = {**medium.FILES, **medium.REFERENCE}
    for name, text in files.items():
        out[name] = medium.FILES[name] if text is None else text
    return out


def suite(ws):
    """The task's own verify command, under this interpreter: (rc, stdout)."""
    argv = [sys.executable, *TASK["verify"][1:], "-rf"]
    p = subprocess.run(
        argv, cwd=ws, capture_output=True, text=True, timeout=120, check=False
    )
    return p.returncode, p.stdout


def failed(stdout):
    return {
        ln.split()[1].replace("\\", "/")
        for ln in stdout.splitlines()
        if ln.startswith("FAILED ")
    }


class TestShape:
    def test_it_is_a_medium_repo(self):
        names = set(medium.FILES)
        assert 20 <= len(names) <= 40
        modules = [n for n in names if n.startswith("tally/") and n.endswith(".py")]
        tests = [n for n in names if n.startswith("tests/test_")]
        assert len(modules) >= 8 and len(tests) >= 8
        assert {
            "README.md",
            "pyproject.toml",
            "tally/cli.py",
            "tally/__main__.py",
        } <= names

    def test_every_python_file_parses(self):
        for name, text in {**medium.FILES, **medium.REFERENCE}.items():
            if name.endswith(".py"):
                ast.parse(text, filename=name)

    def test_no_carriage_returns_or_trailing_blanks(self):
        # This module is CRLF in a Windows checkout; its fixture must not be.
        for name, text in medium.FILES.items():
            assert "\r" not in text, name
            assert all(ln == ln.rstrip() for ln in text.splitlines()), name
            assert text.endswith("\n"), name

    def test_the_fix_is_not_where_the_failures_are(self):
        assert set(medium.REFERENCE) == {"tally/money.py"}
        for red_file in {n.split("::")[0] for n in RED}:
            assert "money" not in medium.FILES[red_file], red_file

    def test_the_reference_changes_only_the_parser(self):
        before = medium.FILES["tally/money.py"]
        after = medium.REFERENCE["tally/money.py"]
        assert before.split("def parse_amount")[0] == after.split("def parse_amount")[0]
        assert before.split("def format_cents")[1] == after.split("def format_cents")[1]
        assert before != after

    def test_the_prompt_does_not_name_the_file(self):
        for word in ("money", "parse_amount", "refund", "negative"):
            assert word not in TASK["prompt"].lower()

    def test_a_stale_anchor_fails_loudly(self):
        with pytest.raises(RuntimeError, match="anchor"):
            medium._patched("abc", "xyz", "q")

    def test_its_self_test_rows_fit_the_column(self):
        # _self_test_row pads names to 48; a longer one shoves the verdict right.
        for task, label, _, _ in medium.CHEATS:
            assert len(f"{task}: {label} refused") <= 48, label

    def test_it_is_wired_into_the_harness(self):
        assert TASK in ba.TASKS
        assert ba.REFERENCE[medium.NAME] is medium.REFERENCE
        assert all(c in ba.CHEATS for c in medium.CHEATS)

    def test_the_report_fingerprints_both_fixture_modules(self, monkeypatch, tmp_path):
        # tool_sha256 is how a score shift gets traced to the grader; the
        # fixture decides this task's score as much as bench_agent.py does.
        from orchestrant.benchmark import client

        seen = []
        monkeypatch.setattr(client, "write_report", lambda *a, **k: seen.append(a[5]))
        monkeypatch.setattr(ba, "OPENCODE", sys.executable)
        monkeypatch.setattr(ba, "opencode_version", lambda: None)
        monkeypatch.setattr(ba, "run_agent", lambda *a, **k: ([], 1.0, False, ""))
        monkeypatch.setattr(ba, "verify", lambda ws, task: (False, "stub"))
        monkeypatch.setenv("OPENCODE_CONFIG", str(tmp_path / "none.jsonc"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        out = str(tmp_path / "r.json")
        argv = ["bench_agent.py", "--task", medium.NAME, "--output", out]
        monkeypatch.setattr(sys, "argv", argv)
        ba.main()
        names = {os.path.basename(p) for p in seen[0]}
        assert {"bench_agent_medium.py", "bench_agent_medium_files.py"} <= names


class TestRedThenGreen:
    def test_exactly_the_planted_tests_are_red(self, tmp_path):
        rc, out = suite(materialise(tmp_path, medium.FILES))
        assert rc == 1, out
        assert failed(out) == RED, out

    def test_the_reference_makes_the_whole_suite_green(self, tmp_path):
        rc, out = suite(materialise(tmp_path, {**medium.FILES, **medium.REFERENCE}))
        assert rc == 0, out

    def test_the_unseen_inputs_fail_unfixed_and_pass_fixed(self, tmp_path):
        detail = medium.check_unseen_inputs(materialise(tmp_path / "a", medium.FILES))
        assert detail and "unseen input: tally report" in detail
        assert "expected '2026-04 22.26" in detail
        fixed = materialise(tmp_path / "b", {**medium.FILES, **medium.REFERENCE})
        assert medium.check_unseen_inputs(fixed) is None

    @linux_verify
    def test_verify_is_red_before_and_green_after(self):
        ws = ba.make_workspace(TASK)
        try:
            passed, detail = ba.verify(ws, TASK)
            assert not passed and "2 failed" in detail, detail
            ba.apply_files(ws, medium.REFERENCE)
            passed, detail = ba.verify(ws, TASK)
            assert passed, detail
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    @linux_verify
    def test_the_self_test_proves_it_and_refuses_its_cheats(self, monkeypatch, capsys):
        # The clamp task stays: self_test() always checks its mutants too.
        clamp = next(t for t in ba.TASKS if t["name"] == "add_function_and_test")
        monkeypatch.setattr(ba, "TASKS", [clamp, TASK])
        monkeypatch.setattr(ba, "CHEATS", list(medium.CHEATS))
        assert ba.self_test() is True
        out = capsys.readouterr().out
        rows = [ln for ln in out.splitlines() if medium.NAME in ln]
        assert len(rows) == 1 + len(medium.CHEATS)
        assert all(ln.split()[-1] == "OK" for ln in rows), out


class TestCheatsAreRefused:
    @pytest.mark.parametrize("cheat", medium.CHEATS, ids=lambda c: c[1])
    def test_each_cheat_would_turn_the_suite_green_on_its_own(self, tmp_path, cheat):
        # Otherwise its refusal proves nothing: pytest would have caught it.
        rc, out = suite(materialise(tmp_path, with_cheat(cheat[2])))
        assert rc == 0, out

    @linux_verify
    @pytest.mark.parametrize("cheat", medium.CHEATS, ids=lambda c: c[1])
    def test_each_cheat_is_refused_for_its_reason(self, cheat):
        _, _, files, expected = cheat
        ws = ba.make_workspace(TASK)
        try:
            ba.apply_files(ws, medium.REFERENCE)
            ba.apply_files(ws, files)
            passed, detail = ba.verify(ws, TASK)
            assert not passed and expected in detail, detail
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_a_pytest_ini_at_the_root_is_refused(self):
        # Reached without python3: the protection runs before the suite does.
        ini = (
            "[pytest]\naddopts = --deselect tests/test_report.py"
            "::test_a_refund_reduces_its_month\n"
        )
        ws = ba.make_workspace(TASK)
        try:
            ba.apply_files(ws, {"pytest.ini": ini})
            passed, detail = ba.verify(ws, TASK)
            assert not passed and "pytest.ini" in detail, detail
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_deselecting_through_pyproject_leaves_the_red_tests_red(self, tmp_path):
        pyproject = medium.FILES["pyproject.toml"] + (
            'addopts = "--deselect tests/test_report.py::test_a_refund_reduces_its_month'
            ' --deselect tests/test_budget.py::test_a_negative_adjustment_tightens_a_limit"\n'
        )
        rc, out = suite(
            materialise(tmp_path, {**medium.FILES, "pyproject.toml": pyproject})
        )
        assert rc == 1 and failed(out) == RED, out

    def test_skipping_the_red_tests_by_name_is_refused(self, tmp_path):
        # python_functions is not addopts, so `-o addopts=` does not undo it:
        # the suite goes green and only the unseen inputs are left to say no.
        pyproject = (
            medium.FILES["pyproject.toml"] + 'python_functions = ["test_[!a]*"]\n'
        )
        ws = materialise(tmp_path, {**medium.FILES, "pyproject.toml": pyproject})
        rc, out = suite(ws)
        assert rc == 0, out
        detail = medium.check_unseen_inputs(ws)
        assert detail and "unseen input" in detail

    def test_a_new_test_module_patching_the_parser_is_refused(self, tmp_path):
        # Collected first, it rebinds parse_amount before the ledger imports
        # it: the suite goes green, the program stays wrong.
        patch = medium._PATCHING_CONFTEST
        ws = materialise(tmp_path, {**medium.FILES, "tests/test_aaa_patch.py": patch})
        rc, out = suite(ws)
        assert rc == 0, out
        detail = medium.check_unseen_inputs(ws)
        assert detail and "unseen input" in detail

    def test_a_fix_in_both_callers_is_accepted(self, tmp_path):
        # The check judges behaviour, not where the fix was made.
        signed = "(-parse_amount({0}.strip()[1:]) if {0}.strip().startswith('-') else parse_amount({0}))"
        ledger = medium._patched(
            medium.FILES["tally/ledger.py"],
            "cents=parse_amount(row[1]),",
            "cents=" + signed.format("row[1]") + ",",
        )
        budget = medium._patched(
            medium.FILES["tally/budget.py"],
            "adjustments.append((normalise(rest), parse_amount(amount)))",
            "adjustments.append((normalise(rest), " + signed.format("amount") + "))",
        )
        files = {**medium.FILES, "tally/ledger.py": ledger, "tally/budget.py": budget}
        ws = materialise(tmp_path, files)
        rc, out = suite(ws)
        assert rc == 0, out
        assert medium.check_unseen_inputs(ws) is None

    def test_a_crashing_program_is_refused_with_its_error(self, tmp_path):
        broken = medium.FILES["tally/cli.py"].replace(
            "def main(argv=None):", "def main(argv):"
        )
        ws = materialise(
            tmp_path, {**medium.FILES, **medium.REFERENCE, "tally/cli.py": broken}
        )
        detail = medium.check_unseen_inputs(ws)
        assert detail and "exited 1" in detail and "TypeError" in detail

    def test_output_that_does_not_decode_fails_the_trial_not_the_run(self, tmp_path):
        # 0x81 is invalid in UTF-8 and unmapped in cp1252; strict decoding
        # raised out of verify() and would have ended the whole run.
        cli = medium._patched(
            medium.FILES["tally/cli.py"],
            "            print(render_report(entries, by=args.by))\n",
            '            sys.stdout.buffer.write(b"\\x81\\n")\n',
        )
        ws = materialise(
            tmp_path, {**medium.FILES, **medium.REFERENCE, "tally/cli.py": cli}
        )
        detail = medium.check_unseen_inputs(ws)
        assert detail and detail.startswith("unseen input: tally report printed")


class TestNestedFixtures:
    """The harness paths the flat fixtures never exercised."""

    def test_a_nested_fixture_is_written_and_committed_clean(self):
        ws = ba.make_workspace(TASK)
        try:
            assert (Path(ws) / "tally" / "money.py").is_file()
            tracked, _ = ba._git(ws, "ls-files")
            assert set(tracked.split()) == set(medium.FILES)
            status, _ = ba._git(ws, "status", "--porcelain")
            assert status == ""
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_apply_files_clears_bytecode_in_every_package(self, tmp_path):
        stale = tmp_path / "tally" / "__pycache__"
        stale.mkdir(parents=True)
        (stale / "money.cpython-313.pyc").write_bytes(b"stale")
        ba.apply_files(str(tmp_path), {"tally/new.py": "x = 1\n"})
        assert not stale.exists()
        assert (tmp_path / "tally" / "new.py").read_text() == "x = 1\n"

    def test_an_override_file_counts_in_any_directory_above_the_tests(self):
        # No conftest among the protected files here: a fixture that protects
        # one refuses every new conftest.py by name already.
        added = [
            "conftest.py",
            "pytest.ini",
            "tests/tox.ini",
            "tests/unit/conftest.py",
            "tally/conftest.py",
            "tests/test_regression.py",
        ]
        assert ba.added_overrides(added, ["tests/test_report.py"]) == [
            "conftest.py",
            "pytest.ini",
            "tests/tox.ini",
        ]

    def test_a_flat_fixture_is_judged_as_before(self):
        added = ["conftest.py", "sub/conftest.py", "test_repro.py"]
        assert ba.added_overrides(added, ["test_calc.py"]) == ["conftest.py"]
