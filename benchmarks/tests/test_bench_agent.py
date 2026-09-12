"""Tests for bench_agent — the end-to-end agent harness.

The harness's own correctness matters more here than anywhere else in the
suite: with no strong control model reachable, a row of failures is only
readable if the fixtures and the verification are known-good. Nothing here
starts opencode or contacts a server; the agent is a fake script or a
monkeypatched run_agent.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import bench_agent as ba

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _skip_without_tools(task):
    """A fixture whose tools are absent is SKIPPED here, loudly, never asserted
    against -- the failure would be the host's, reported as the harness's."""
    needs = ba.missing_tools(task)
    if needs:
        pytest.skip(f"{task['name']} needs {', '.join(needs)} on PATH")


def task_named(name):
    return next(t for t in ba.TASKS if t["name"] == name)


def solved_workspace(name, **overrides):
    """Fixture plus REFERENCE plus `overrides` (None deletes the file)."""
    task = task_named(name)
    ws = ba.make_workspace(task)
    ba.apply_files(ws, ba.REFERENCE[name])
    for fname, content in overrides.items():
        path = os.path.join(ws, fname)
        if content is None:
            os.remove(path)
        else:
            with open(path, "w") as f:
                f.write(content)
    shutil.rmtree(os.path.join(ws, "__pycache__"), ignore_errors=True)
    return ws, task


class TestFixtures:
    def test_every_task_has_a_reference_solution(self):
        # Without one, --self-test silently skips the task and reports OK for a
        # fixture it never actually solved.
        for task in ba.TASKS:
            assert task["name"] in ba.REFERENCE, f"{task['name']} has no reference"

    def test_reference_only_touches_files_in_the_fixture(self):
        for name, files in ba.REFERENCE.items():
            task = task_named(name)
            for fname in files:
                assert fname in task["files"], f"{name}: {fname} is not in the fixture"

    def test_task_names_are_unique(self):
        names = [t["name"] for t in ba.TASKS]
        assert len(names) == len(set(names))

    def test_prompt_never_contains_the_solution(self):
        # A prompt that names the fix measures transcription, not engineering.
        for task in ba.TASKS:
            for content in ba.REFERENCE.get(task["name"], {}).values():
                body = [
                    ln.strip()
                    for ln in content.splitlines()
                    if ln.strip() and not ln.strip().startswith(('"', "#"))
                ]
                for line in body:
                    if len(line) > 25:
                        assert line not in task["prompt"], (
                            f"{task['name']} leaks its solution"
                        )

    def test_every_cheat_names_a_task_and_a_refusal(self):
        for task_name, _, files, expected in ba.CHEATS:
            task = task_named(task_name)
            assert all(f in task["files"] for f in files)
            assert expected


class TestWorkspace:
    def test_workspace_gets_the_fixture_files_and_a_git_repo(self, tmp_path):
        task = ba.TASKS[0]
        ws = ba.make_workspace(task)
        try:
            for name in task["files"]:
                assert os.path.exists(os.path.join(ws, name))
            assert os.path.isdir(os.path.join(ws, ".git"))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_workspaces_are_independent(self):
        task = ba.TASKS[0]
        a, b = ba.make_workspace(task), ba.make_workspace(task)
        try:
            assert a != b
            with open(os.path.join(a, list(task["files"])[0]), "w") as f:
                f.write("# clobbered\n")
            other = open(os.path.join(b, list(task["files"])[0])).read()
            assert "clobbered" not in other
        finally:
            shutil.rmtree(a, ignore_errors=True)
            shutil.rmtree(b, ignore_errors=True)

    def test_bytecode_is_not_part_of_the_diff(self):
        # pytest leaves __pycache__ behind; it must not show up as agent work.
        ws, task = solved_workspace("fix_failing_test")
        try:
            ba.verify(ws, task)
            diff = ba.workspace_diff(ws)
            assert "__pycache__" not in diff and "Binary files" not in diff
            assert "+    if not values:" in diff
        finally:
            shutil.rmtree(ws, ignore_errors=True)


class TestVerification:
    """The load-bearing part: verification decides every reported result."""

    @pytest.mark.parametrize("task", ba.TASKS, ids=lambda t: t["name"])
    def test_unsolved_fixture_fails(self, task):
        # Without the skip this passes for the WRONG reason on a host that
        # cannot build the fixture at all.
        _skip_without_tools(task)
        ws = ba.make_workspace(task)
        try:
            passed, _ = ba.verify(ws, task)
            assert not passed, "fixture passes untouched -- it measures nothing"
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    @pytest.mark.parametrize("task", ba.TASKS, ids=lambda t: t["name"])
    def test_reference_solution_passes(self, task):
        _skip_without_tools(task)
        ws, _ = solved_workspace(task["name"])
        try:
            passed, detail = ba.verify(ws, task)
            assert passed, f"correct solution rejected: {detail}"
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_missing_command_is_a_failure_not_a_crash(self, tmp_path):
        bogus = {"name": "x", "verify": ["definitely-not-a-real-binary-xyz"]}
        passed, detail = ba.verify(str(tmp_path), bogus)
        assert not passed
        assert "could not run" in detail


class TestFixTaskProtectsItsTests:
    """D12: deleting, skipping or inverting the red test used to print the same
    '2 passed' a real fix prints, and score PASS."""

    FIX = "fix_failing_test"
    UNFIXED = task_named(FIX)["files"]["calc.py"]

    def _refused(self, expect="tests were modified", **overrides):
        ws, task = solved_workspace(self.FIX, **overrides)
        try:
            passed, detail = ba.verify(ws, task)
            assert not passed
            assert expect in detail, detail
            return detail
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_deleted_test_is_refused(self):
        detail = self._refused(
            **{
                "calc.py": self.UNFIXED,
                "test_calc.py": "from calc import average\n\n\ndef test_average():\n"
                "    assert average([1, 2, 3]) == 2\n",
            }
        )
        assert "test_calc.py" in detail

    def test_skipped_test_is_refused(self):
        self._refused(
            **{
                "calc.py": self.UNFIXED,
                "test_calc.py": "import pytest\nfrom calc import average\n\n\n"
                "@pytest.mark.skip\ndef test_empty_returns_zero():\n"
                "    assert average([]) == 0.0\n",
            }
        )

    def test_inverted_assertion_is_refused(self):
        self._refused(
            **{
                "calc.py": self.UNFIXED,
                "test_calc.py": "import pytest\nfrom calc import average\n\n\n"
                "def test_empty_returns_zero():\n"
                "    with pytest.raises(ZeroDivisionError):\n"
                "        average([])\n",
            }
        )

    def test_deleted_test_file_is_refused(self):
        self._refused(**{"test_calc.py": None})

    def test_added_conftest_is_refused(self):
        # A conftest can monkeypatch calc.average; the fix is then not in calc.py.
        self._refused(
            expect="can override the protected tests",
            **{
                "calc.py": self.UNFIXED,
                "conftest.py": "import calc\ncalc.average = lambda v: 0.0 if not v else 2\n",
            },
        )

    def test_an_edited_test_is_refused_even_when_the_fix_is_real(self):
        # Whitespace-only change: pytest is green, the rule was still broken.
        self._refused(
            **{"test_calc.py": task_named(self.FIX)["files"]["test_calc.py"] + "\n"}
        )

    def test_unrelated_new_files_are_allowed(self):
        ws, task = solved_workspace(self.FIX, **{"NOTES.md": "fixed\n"})
        try:
            passed, detail = ba.verify(ws, task)
            assert passed, detail
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_a_broken_git_is_a_refusal_not_a_pass(self):
        # The check that cannot run must not read as "untouched".
        ws, task = solved_workspace(self.FIX)
        try:
            shutil.rmtree(os.path.join(ws, ".git"))
            with open(os.path.join(ws, ".git"), "w") as f:
                f.write("not a gitfile\n")
            passed, detail = ba.verify(ws, task)
            assert not passed and "could not check" in detail
        finally:
            shutil.rmtree(ws, ignore_errors=True)


class TestClampTaskRequiresRealTests:
    """D13: a correct clamp with test_utils.py untouched used to PASS."""

    ADD = "add_function_and_test"
    IMPORTS = "import pytest\nfrom utils import slugify, clamp\n\n\n"
    SLUG = "def test_slugify():\n    assert slugify('  Hello World ') == 'hello-world'\n\n\n"
    RANGE = (
        "def test_clamp():\n    assert clamp(5, 1, 10) == 5\n"
        "    assert clamp(0, 1, 10) == 1\n    assert clamp(99, 1, 10) == 10\n\n\n"
    )
    ERROR = (
        "def test_clamp_bad_range():\n    with pytest.raises(ValueError):\n"
        "        clamp(1, 10, 1)\n"
    )

    def _verify(self, tests):
        ws, task = solved_workspace(self.ADD, **{"test_utils.py": tests})
        try:
            return ba.verify(ws, task)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_correct_clamp_with_no_tests_written_is_refused(self):
        fixture_tests = task_named(self.ADD)["files"]["test_utils.py"]
        passed, detail = self._verify(fixture_tests)
        assert not passed and "do not catch" in detail, detail

    def test_vacuous_test_is_refused(self):
        passed, detail = self._verify(
            self.IMPORTS + self.SLUG + "def test_clamp():\n    assert True\n"
        )
        assert not passed and "do not catch" in detail

    def test_missing_error_case_is_refused(self):
        passed, detail = self._verify(self.IMPORTS + self.SLUG + self.RANGE)
        assert not passed and "never raises" in detail

    def test_missing_range_tests_is_refused(self):
        passed, detail = self._verify(self.IMPORTS + self.SLUG + self.ERROR)
        assert not passed and "bound" in detail

    def test_only_one_end_of_the_range_is_refused(self):
        one_end = "def test_clamp():\n    assert clamp(0, 1, 10) == 1\n\n\n"
        passed, detail = self._verify(self.IMPORTS + self.SLUG + one_end + self.ERROR)
        assert not passed and "upper bound" in detail

    def test_full_tests_pass(self):
        passed, detail = self._verify(
            self.IMPORTS + self.SLUG + self.RANGE + self.ERROR
        )
        assert passed, detail

    def test_clamp_task_rejects_a_vacuous_test(self):
        # The verification runs its own assertions, so writing `assert True` in
        # test_utils.py must not earn a pass even before the mutants run.
        task = task_named(self.ADD)
        ws = ba.make_workspace(task)
        try:
            with open(os.path.join(ws, "utils.py"), "a") as f:
                f.write("\n\ndef clamp(value, low, high):\n    return value\n")
            with open(os.path.join(ws, "test_utils.py"), "a") as f:
                f.write("\n\ndef test_clamp():\n    assert True\n")
            shutil.rmtree(os.path.join(ws, "__pycache__"), ignore_errors=True)
            passed, _ = ba.verify(ws, task)
            assert not passed, "a clamp that does not clamp was accepted"
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_reference_tests_kill_every_mutant_and_fixture_tests_kill_none(self):
        ws, _ = solved_workspace(self.ADD)
        try:
            assert ba.check_clamp_tests_kill_mutants(ws) is None
            with open(os.path.join(ws, "test_utils.py"), "w") as f:
                f.write(task_named(self.ADD)["files"]["test_utils.py"])
            assert ba.check_clamp_tests_kill_mutants(ws) is not None
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_no_tests_at_all_is_not_counted_as_a_kill(self):
        # pytest exits 5 with nothing collected; only exit 1 is "tests failed".
        passed, _ = self._verify("")
        assert not passed


class TestRenameTaskDecidesOnTheTree:
    """D14: a substring scan failed a correct rename over a comment."""

    RENAME = "multi_file_rename"

    def _verify(self, **overrides):
        ws, task = solved_workspace(self.RENAME, **overrides)
        try:
            return ba.verify(ws, task)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_comment_mentioning_the_old_name_passes(self):
        client = (
            ba.REFERENCE[self.RENAME]["client.py"] + "\n# renamed from fetch_data\n"
        )
        passed, detail = self._verify(**{"client.py": client})
        assert passed, detail

    def test_docstring_mentioning_the_old_name_passes(self):
        client = (
            "def format_record(record):\n"
            '    """Format one record. Was fetch_data, which fetched nothing."""\n'
            "    return f\"{record['id']}: {record['name']}\"\n"
        )
        passed, detail = self._verify(**{"client.py": client})
        assert passed, detail

    def test_a_bare_docstring_of_exactly_the_old_name_passes(self):
        # The docstring exclusion fires only for a Constant EQUAL to the old
        # name; every test above used a sentence, so the guard went unreached.
        client = (
            "def format_record(record):\n"
            '    """fetch_data"""\n'
            "    return f\"{record['id']}: {record['name']}\"\n"
        )
        passed, detail = self._verify(**{"client.py": client})
        assert passed, detail

    def test_identifier_containing_the_old_name_passes(self):
        client = ba.REFERENCE[self.RENAME]["client.py"] + "\n\nprefetch_database = 1\n"
        passed, detail = self._verify(**{"client.py": client})
        assert passed, detail

    def test_rename_task_rejects_an_alias(self):
        # Keeping the old name as an alias satisfies the tests but is not the
        # rename that was asked for.
        client = (
            ba.REFERENCE[self.RENAME]["client.py"] + "\n\nfetch_data = format_record\n"
        )
        passed, detail = self._verify(**{"client.py": client})
        assert not passed and "fetch_data still used" in detail

    def test_wrapper_def_is_rejected(self):
        client = (
            ba.REFERENCE[self.RENAME]["client.py"]
            + "\n\ndef fetch_data(record):\n    return format_record(record)\n"
        )
        passed, detail = self._verify(**{"client.py": client})
        assert not passed and "definition" in detail

    def test_globals_lookup_is_rejected(self):
        client = (
            ba.REFERENCE[self.RENAME]["client.py"]
            + "\n\nglobals()['fetch_data'] = format_record\n"
        )
        passed, detail = self._verify(**{"client.py": client})
        assert not passed and "string" in detail

    def test_import_alias_is_rejected(self):
        report = (
            "from client import format_record as fetch_data\n\n\n"
            "def build(records):\n    return [fetch_data(r) for r in records]\n"
        )
        passed, detail = self._verify(**{"report.py": report})
        assert not passed and "import" in detail

    def test_attribute_use_is_rejected(self):
        # On old_name_uses, not on `not passed`: verify() returns False from
        # the pytest branch here and never reaches the AST check.
        report = (
            "import client\n\n\n"
            "def build(records):\n    return [client.fetch_data(r) for r in records]\n"
        )
        passed, _ = self._verify(**{"report.py": report})
        assert not passed
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "report.py"), "w") as f:
                f.write(report)
            found = ba.old_name_uses(tmp, "fetch_data")
        assert found and "(attribute)" in " ".join(found)

    def test_unparseable_file_falls_back_to_text(self, tmp_path):
        (tmp_path / "junk.py").write_text("def fetch_data(:\n")
        assert ba.old_name_uses(str(tmp_path), "fetch_data")

    def test_detail_is_never_empty(self):
        # The old check exited 1 silently; the row was a bare FAIL.
        client = (
            ba.REFERENCE[self.RENAME]["client.py"] + "\n\nfetch_data = format_record\n"
        )
        passed, detail = self._verify(**{"client.py": client})
        assert not passed and "client.py" in detail


class TestErrorClassification:
    """D15: only explicit markers, and only before the model did any work."""

    def _err(self, message):
        return {"type": "error", "error": {"data": {"message": message}}}

    def test_context_overflow_is_not_a_capability_result(self):
        events = [self._err('Value: {"error":"SDKError(Input prompt too long)"}')]
        assert ba.agent_errors(events)[0][0] == "CONTEXT"

    @pytest.mark.parametrize("marker", ba.CONTEXT_MARKERS)
    def test_every_marker_is_recognised(self, marker):
        assert ba.agent_errors([self._err(f"xx {marker} yy")])[0][0] == "CONTEXT"

    def test_overflow_after_tool_calls_is_context_growth_not_blocked(self):
        # The P3.3 failure the roadmap names: it must be scored, not excluded.
        events = [
            {"type": "step_start"},
            {"type": "tool", "name": "read"},
            self._err("maximum context length exceeded"),
        ]
        kind, detail = ba.agent_errors(events)[0]
        assert kind == "CONTEXT_GROWTH"
        assert detail

    def test_a_step_alone_means_the_model_was_reached(self):
        events = [{"type": "step_start"}, self._err("context_length_exceeded")]
        assert ba.agent_errors(events)[0][0] == "CONTEXT_GROWTH"

    @pytest.mark.parametrize(
        "message",
        [
            "context canceled",
            "context deadline exceeded",
            "context 16384 too large for VRAM",
            "request took too long",
        ],
    )
    def test_a_bare_context_or_too_long_is_a_plain_error(self, message):
        kind, detail = ba.agent_errors([self._err(message)])[0]
        assert kind == "ERROR" and message in detail

    def test_plain_errors_keep_their_message(self):
        kind, detail = ba.agent_errors([self._err("kaboom")])[0]
        assert kind == "ERROR" and "kaboom" in detail

    def test_no_errors_is_empty(self):
        assert ba.agent_errors([{"type": "step_start"}]) == []

    def test_non_error_events_are_never_classified(self):
        # A tool result mentioning "context" must not read as a context overflow.
        events = [{"type": "tool", "error": {"message": "prompt too long"}}]
        assert ba.agent_errors(events) == []


class TestEventSummary:
    def test_unrecognised_events_are_counted_not_dropped(self):
        # A zero must mean "none happened", never "we could not tell".
        c = ba.summarise_events([{"type": "wat"}, {"type": "tool_call"}])
        assert c["unrecognised_events"] == 1
        assert c["tool_events"] == 1
        assert c["total_events"] == 2

    def test_counts_add_up(self):
        events = [
            {"type": "tool"},
            {"type": "message"},
            {"type": "zzz"},
            {"type": "step_start"},
        ]
        c = ba.summarise_events(events)
        assert c["step_events"] == 1
        assert (
            c["tool_events"]
            + c["step_events"]
            + c["message_events"]
            + c["unrecognised_events"]
        ) == c["total_events"]


class TestCli:
    def test_self_test_passes(self):
        # The gate that makes every other row in a report readable: fixtures
        # red-then-green, and the three cheats refused.
        r = subprocess.run(
            [sys.executable, "bench_agent.py", "--self-test"],
            cwd=HERE,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Harness validated" in r.stdout
        for _, label, _, _ in ba.CHEATS:
            assert f"{label} refused" in r.stdout

    def test_self_test_reports_a_cheat_that_slips_through(self, monkeypatch, capsys):
        monkeypatch.setattr(ba, "verify", lambda ws, task: (True, "2 passed"))
        assert ba.self_test() is False
        assert "cheat was accepted" in capsys.readouterr().out

    def test_list_needs_no_server(self):
        r = subprocess.run(
            [sys.executable, "bench_agent.py", "--list"],
            cwd=HERE,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert r.returncode == 0
        for task in ba.TASKS:
            assert task["name"] in r.stdout

    def test_unknown_task_exits_nonzero_and_writes_no_report(self, tmp_path):
        # D18: a typo used to run nothing, print 0/0, exit 0 and write a report
        # bench_compare then passed.
        out = tmp_path / "r.json"
        r = subprocess.run(
            [
                sys.executable,
                "bench_agent.py",
                "--task",
                "fix_failing_tset",
                "--output",
                str(out),
            ],
            cwd=HERE,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert r.returncode != 0
        assert "fix_failing_tset" in r.stderr
        assert not out.exists()


def fake_opencode(tmp_path, body):
    """A stand-in for the opencode binary: a shell script, args ignored."""
    script = tmp_path / "opencode"
    script.write_text("#!/usr/bin/env bash\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


class TestTimeoutKeepsEvidence:
    """A timed-out run must still report what it managed to do, and D17: its
    grandchildren must die with it."""

    def test_partial_output_survives_a_timeout_and_the_tree_dies(
        self, monkeypatch, tmp_path
    ):
        marker = tmp_path / "grandchild.pid"
        monkeypatch.setattr(
            ba,
            "OPENCODE",
            fake_opencode(
                tmp_path,
                (
                    'echo \'{"type":"step_start"}\'\n'
                    'echo \'{"type":"tool","name":"read"}\'\n'
                    'echo \'{"type":"tool","name":"edit"}\'\n'
                    "echo partial-stderr >&2\n"
                    "sleep 60 &\n"
                    f"echo $! > {marker}\n"
                    "wait\n"
                ),
            ),
        )
        t0 = time.monotonic()
        events, wall, timed_out, stderr = ba.run_agent(str(tmp_path), "m", "p", 2)
        assert time.monotonic() - t0 < 15, "waited for the orphan instead of killing it"
        assert timed_out and wall >= 2
        assert ba.summarise_events(events)["tool_events"] == 2
        assert "partial-stderr" in stderr
        pid = int(marker.read_text())
        time.sleep(0.2)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    def test_no_output_at_all_is_still_safe(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ba, "OPENCODE", fake_opencode(tmp_path, "sleep 60\n"))
        events, _, timed_out, stderr = ba.run_agent(str(tmp_path), "m", "p", 1)
        assert timed_out and events == [] and stderr == ""

    def test_a_finished_run_is_not_a_timeout(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            ba,
            "OPENCODE",
            fake_opencode(
                tmp_path,
                (
                    'echo \'{"type":"tool"}\'\necho "not json"\necho \'{"type":"text"}\'\n'
                ),
            ),
        )
        events, _, timed_out, _ = ba.run_agent(str(tmp_path), None, "p", 30)
        assert not timed_out
        assert ba.summarise_events(events)["total_events"] == 2

    def test_env_is_passed_to_the_agent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            ba,
            "OPENCODE",
            fake_opencode(
                tmp_path,
                'echo "{\\"type\\":\\"text\\",\\"home\\":\\"$XDG_DATA_HOME\\"}"\n',
            ),
        )
        env = dict(os.environ, XDG_DATA_HOME="/scratch/x")
        events, *_ = ba.run_agent(str(tmp_path), None, "p", 30, env)
        assert events[0]["home"] == "/scratch/x"


class TestOpencodeProvenance:
    """R4: the report must say which lane, which opencode, which config."""

    JSONC = (
        '{\n  // comment\n  "$schema": "x", /* block */\n'
        '  "tools": {"grep": false, "glob": false,},\n'
        '  "instructions": ["/p/tool.md"],\n'
        '  "provider": {\n    "geniex-cpu": {"options": {"baseURL": "http://127.0.0.1:18184/v1",'
        ' "apiKey": "url,}"}, // trailing\n      "models": {"m": {}}},\n'
        '    "ollama": {"options": {"baseURL": "http://summy-server:11434/v1/"}}\n  },\n}\n'
    )

    def test_jsonc_comments_and_trailing_commas_are_tolerated(self):
        cfg = ba.load_jsonc(self.JSONC)
        assert cfg["tools"] == {"grep": False, "glob": False}
        assert cfg["provider"]["geniex-cpu"]["options"]["apiKey"] == "url,}"

    def test_a_url_inside_a_string_is_not_a_comment(self):
        cfg = ba.load_jsonc('{"a": "http://x//y", "b": "/* not */"}')
        assert cfg == {"a": "http://x//y", "b": "/* not */"}

    def test_base_url_resolves_from_the_models_provider(self):
        cfg = ba.load_jsonc(self.JSONC)
        assert (
            ba.resolve_base_url(cfg, "geniex-cpu/empero-ai/X:Q4")
            == "http://127.0.0.1:18184"
        )
        assert ba.resolve_base_url(cfg, "ollama/qwen3") == "http://summy-server:11434"

    def test_unresolvable_is_none_not_a_guess(self):
        cfg = ba.load_jsonc(self.JSONC)
        assert ba.resolve_base_url(cfg, "nope/model") is None
        assert ba.resolve_base_url(cfg, None) is None
        assert ba.resolve_base_url(None, "geniex-cpu/m") is None

    def test_config_is_hashed_even_when_it_does_not_parse(self, tmp_path):
        p = tmp_path / "opencode.jsonc"
        p.write_text("{ nope")
        cfg, sha, note = ba.read_opencode_config(str(p))
        assert cfg is None and sha == hashlib.sha256(b"{ nope").hexdigest()
        assert "did not parse" in note

    def test_missing_config_is_null_with_a_note(self, tmp_path):
        cfg, sha, note = ba.read_opencode_config(str(tmp_path / "none.jsonc"))
        assert cfg is None and sha is None and "not found" in note

    def test_config_path_honours_opencode_config_then_xdg(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCODE_CONFIG", "/explicit.json")
        assert ba.opencode_config_path() == "/explicit.json"
        monkeypatch.delenv("OPENCODE_CONFIG")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert ba.opencode_config_path() is None
        (tmp_path / "opencode").mkdir()
        (tmp_path / "opencode" / "opencode.jsonc").write_text("{}")
        assert ba.opencode_config_path() == str(
            tmp_path / "opencode" / "opencode.jsonc"
        )

    def test_env_points_sessions_at_the_scratch_dir_and_carries_auth(
        self, monkeypatch, tmp_path
    ):
        real = tmp_path / "real"
        (real / "opencode").mkdir(parents=True)
        (real / "opencode" / "auth.json").write_text('{"k": 1}')
        monkeypatch.setenv("XDG_DATA_HOME", str(real))
        scratch = tmp_path / "scratch"
        env = ba.opencode_env(str(scratch), "/cfg/opencode.jsonc")
        assert env["XDG_DATA_HOME"].startswith(str(scratch))
        assert env["OPENCODE_CONFIG"] == "/cfg/opencode.jsonc"
        assert os.path.exists(
            os.path.join(env["XDG_DATA_HOME"], "opencode", "auth.json")
        )
        assert (real / "opencode" / "auth.json").exists()

    def test_env_without_a_config_sets_no_opencode_config(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
        env = ba.opencode_env(str(tmp_path / "s"), None)
        assert "OPENCODE_CONFIG" not in env

    def test_version_is_null_when_the_binary_is_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ba, "OPENCODE", str(tmp_path / "nope"))
        assert ba.opencode_version() is None

    def test_version_is_read_from_the_binary(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ba, "OPENCODE", fake_opencode(tmp_path, "echo 9.9.9\n"))
        assert ba.opencode_version() == "9.9.9"


def run_main(monkeypatch, tmp_path, fake_run_agent, argv, config_text=None):
    """main() with no opencode, no server and no network; returns the report."""
    from orchestrant.benchmark import provenance as bench_provenance

    cfg = tmp_path / "opencode.jsonc"
    cfg.write_text(
        config_text
        or (
            '{"tools": {"grep": false}, "instructions": ["/p.md"], // c\n'
            ' "provider": {"lane": {"options": {"baseURL": "http://lane:1/v1"}}}}'
        )
    )
    monkeypatch.setenv("OPENCODE_CONFIG", str(cfg))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(ba, "OPENCODE", sys.executable)
    monkeypatch.setattr(ba, "opencode_version", lambda: "0.0-test")
    monkeypatch.setattr(ba, "run_agent", fake_run_agent)
    monkeypatch.setattr(bench_provenance, "busy_lanes", lambda: None)
    monkeypatch.setattr(bench_provenance, "_server_models", lambda url, timeout=5: None)
    out = tmp_path / "r.json"
    monkeypatch.setattr(sys, "argv", ["bench_agent.py", "--output", str(out)] + argv)
    ba.main()
    return json.load(open(out))


def blocked_events():
    return [{"type": "error", "error": {"data": {"message": "Input prompt too long"}}}]


def growth_events():
    return [
        {"type": "step_start"},
        {"type": "tool"},
        {"type": "tool"},
        {"type": "error", "error": {"data": {"message": "context_length_exceeded"}}},
    ]


class TestScoreExcludesBlockedRuns:
    """A model that never received the task did not fail it.

    This is the difference between "0% — it cannot code" and "not measurable on
    this lane", and the whole point of the run that produced it.
    """

    def test_stats_layer_renders_nothing_measured_as_na(self):
        from orchestrant.benchmark import stats as bs

        assert bs.format_score(0, 0) == "n/a"
        lo, hi = bs.wilson_interval(0, 0)
        assert (lo, hi) == (0.0, 1.0), "no data must mean no information"

    def test_blocked_rows_are_errored_and_out_of_the_denominator_and_wall(
        self, monkeypatch, tmp_path
    ):
        # Pinned to three fixtures: this measures the blocked accounting, not
        # how many fixtures the suite happens to carry.
        monkeypatch.setattr(ba, "TASKS", ba.TASKS[:3])
        seen = []

        def fake(workspace, model, prompt, timeout, env=None):
            seen.append(prompt)
            if len(seen) == 1:
                return blocked_events(), 7.0, False, ""
            return [{"type": "step_start"}], 3.0, False, ""

        rep = run_main(monkeypatch, tmp_path, fake, ["--model", "lane/m"])["reports"][0]
        assert rep["tasks_run"] == 3 and rep["blocked_on_context"] == 1
        assert rep["total"] == rep["tasks_run"] - rep["blocked_on_context"]
        assert rep["total_wall_s"] == 6.0, (
            "blocked wall must not inflate per-attempt time"
        )
        blocked = [r for r in rep["results"] if r["status"] == "CONTEXT"]
        assert len(blocked) == 1
        assert blocked[0]["errored"] is True and blocked[0]["blocked"] is True
        assert all(
            r["errored"] is False for r in rep["results"] if r["status"] != "CONTEXT"
        )

    def test_context_growth_is_a_real_fail_in_the_denominator(
        self, monkeypatch, tmp_path
    ):
        def fake(workspace, model, prompt, timeout, env=None):
            return growth_events(), 5.0, False, ""

        rep = run_main(
            monkeypatch,
            tmp_path,
            fake,
            ["--model", "lane/m", "--task", "fix_failing_test"],
        )["reports"][0]
        row = rep["results"][0]
        assert row["status"] == "CONTEXT_GROWTH"
        assert row["errored"] is False and row["blocked"] is False
        assert rep["total"] == 1 and rep["blocked_on_context"] == 0
        assert rep["total_wall_s"] == 5.0

    def test_score_is_printed_with_its_interval(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(ba, "TASKS", ba.TASKS[:3])

        def fake(workspace, model, prompt, timeout, env=None):
            name = next(t["name"] for t in ba.TASKS if t["prompt"] == prompt)
            ba.apply_files(workspace, ba.REFERENCE[name])
            return [{"type": "step_start"}, {"type": "tool"}], 1.0, False, ""

        rep = run_main(monkeypatch, tmp_path, fake, ["--model", "lane/m"])["reports"][0]
        assert rep["passed"] == 3 and rep["total"] == 3
        out = capsys.readouterr().out
        assert "3/3 = 100% [44-100%]" in out


class TestReportProvenance:
    """R4: the fields an audit of a published agent number needs."""

    def _pass_all(self, workspace, model, prompt, timeout, env=None):
        name = next(t["name"] for t in ba.TASKS if t["prompt"] == prompt)
        ba.apply_files(workspace, ba.REFERENCE[name])
        return [{"type": "step_start"}], 1.0, False, ""

    def test_provenance_carries_opencode_and_lane(self, monkeypatch, tmp_path):
        d = run_main(monkeypatch, tmp_path, self._pass_all, ["--model", "lane/m"])
        prov = d["provenance"]
        assert prov["base_url"] == "http://lane:1"
        assert prov["opencode_version"] == "0.0-test"
        assert prov["opencode_config_path"] == str(tmp_path / "opencode.jsonc")
        assert (
            prov["opencode_config_sha256"]
            == hashlib.sha256((tmp_path / "opencode.jsonc").read_bytes()).hexdigest()
        )
        assert prov["tools_disabled"] == {"grep": False}
        assert prov["instructions"] == ["/p.md"]
        assert "base_url" not in prov["incomplete"]

    def test_unresolvable_lane_is_null_and_listed_incomplete(
        self, monkeypatch, tmp_path
    ):
        d = run_main(monkeypatch, tmp_path, self._pass_all, ["--model", "other/m"])
        assert d["provenance"]["base_url"] is None
        assert "base_url" in d["provenance"]["incomplete"]

    def test_broken_config_is_listed_incomplete(self, monkeypatch, tmp_path):
        d = run_main(
            monkeypatch,
            tmp_path,
            self._pass_all,
            ["--model", "lane/m"],
            config_text="{ broken",
        )
        assert "opencode_config" in d["provenance"]["incomplete"]
        assert d["provenance"]["opencode_config_sha256"]

    def test_diff_sha_is_always_stored_and_the_diff_only_on_request(
        self, monkeypatch, tmp_path
    ):
        d = run_main(
            monkeypatch,
            tmp_path,
            self._pass_all,
            ["--model", "lane/m", "--task", "fix_failing_test"],
        )
        row = d["reports"][0]["results"][0]
        assert len(row["diff_sha256"]) == 64 and row["diff_bytes"] > 0
        assert "diff" not in row
        d = run_main(
            monkeypatch,
            tmp_path,
            self._pass_all,
            ["--model", "lane/m", "--task", "fix_failing_test", "--keep-output"],
        )
        row = d["reports"][0]["results"][0]
        assert "+    if not values:" in row["diff"]
        assert row["diff_truncated"] is False
        assert hashlib.sha256(row["diff"].encode()).hexdigest() == row["diff_sha256"]
        assert d["config"]["keep_output"] is True

    def test_diff_is_bounded(self, monkeypatch, tmp_path):
        def huge(workspace, model, prompt, timeout, env=None):
            with open(os.path.join(workspace, "big.txt"), "w") as f:
                f.write("x" * (ba.DIFF_LIMIT * 2))
            return [], 1.0, False, ""

        d = run_main(
            monkeypatch,
            tmp_path,
            huge,
            ["--model", "lane/m", "--task", "fix_failing_test", "--keep-output"],
        )
        row = d["reports"][0]["results"][0]
        assert len(row["diff"]) == ba.DIFF_LIMIT and row["diff_truncated"] is True
        assert row["diff_bytes"] > ba.DIFF_LIMIT

    def test_scratch_data_home_is_removed_after_the_run(self, monkeypatch, tmp_path):
        homes = []

        def spy(workspace, model, prompt, timeout, env=None):
            homes.append(env["XDG_DATA_HOME"])
            return [], 1.0, False, ""

        run_main(
            monkeypatch,
            tmp_path,
            spy,
            ["--model", "lane/m", "--task", "fix_failing_test"],
        )
        assert homes and homes[0] != os.environ.get("XDG_DATA_HOME")
        assert not os.path.exists(homes[0])


class TestWriteReportExtra:
    """bench_cli.write_report(extra=): the one seam bench_agent adds there.

    Lives here rather than in test_bench_cli.py because this change unit owns
    only the agent files; move it if that file's owner prefers.
    """

    def _write(self, tmp_path, monkeypatch, extra):
        from orchestrant.benchmark import client as bench_cli
        from orchestrant.benchmark import provenance as bench_provenance

        monkeypatch.setattr(bench_provenance, "busy_lanes", lambda: None)
        monkeypatch.setattr(
            bench_provenance,
            "collect",
            lambda base_url, tool_files, extra=None: {"incomplete": ["host"]},
        )
        out = str(tmp_path / "r.json")
        bench_cli.write_report(out, "b", {}, [], None, ("bench_cli.py",), extra=extra)
        return json.load(open(out))["provenance"]

    def test_extra_fields_land_in_the_provenance_block(self, tmp_path, monkeypatch):
        prov = self._write(tmp_path, monkeypatch, {"opencode_version": "1.0"})
        assert prov["opencode_version"] == "1.0"

    def test_incomplete_extends_rather_than_replaces(self, tmp_path, monkeypatch):
        prov = self._write(tmp_path, monkeypatch, {"incomplete": ["base_url"]})
        assert prov["incomplete"] == ["host", "base_url"]

    def test_no_extra_changes_nothing(self, tmp_path, monkeypatch):
        assert self._write(tmp_path, monkeypatch, None) == {"incomplete": ["host"]}


class TestOtherLanguageFixtures:
    """R5. The two non-Python fixtures, and the skip that keeps them honest.

    The repository the agent edits is 325 .sh / 23 CMake against 69 .py, so a
    Python-only fixture set cannot say whether the loop works on the work.
    """

    def test_the_fixture_set_is_not_python_only(self):
        names = {t["name"] for t in ba.TASKS}
        assert {"fix_bash_quoting", "fix_cmake_link"} <= names

    @pytest.mark.parametrize("name", ["fix_bash_quoting", "fix_cmake_link"])
    def test_each_new_fixture_declares_the_tools_it_needs(self, name):
        # Without `requires` the fixture would run on a host that cannot build
        # it and the failure would be charged to the model.
        assert task_named(name).get("requires")

    def test_missing_tools_lists_only_what_is_absent(self, monkeypatch):
        monkeypatch.setattr(
            ba.shutil, "which", lambda n: None if n == "cmake" else "/x"
        )
        assert ba.missing_tools(task_named("fix_cmake_link")) == ["cmake"]

    def test_alternatives_are_satisfied_by_either(self, monkeypatch):
        # make OR ninja: a host with only ninja is not missing a generator.
        monkeypatch.setattr(
            ba.shutil,
            "which",
            lambda n: "/x" if n in ("cmake", "ctest", "cc", "ninja") else None,
        )
        assert ba.missing_tools(task_named("fix_cmake_link")) == []

    def test_a_missing_alternative_group_is_named_in_full(self, monkeypatch):
        monkeypatch.setattr(
            ba.shutil, "which", lambda n: None if n in ("make", "ninja") else "/x"
        )
        assert ba.missing_tools(task_named("fix_cmake_link")) == ["make/ninja"]

    def test_a_task_with_no_requires_needs_nothing(self):
        assert ba.missing_tools(task_named("fix_failing_test")) == []

    def test_the_bash_fixture_starts_red(self):
        task = task_named("fix_bash_quoting")
        ws = ba.make_workspace(task)
        try:
            passed, detail = ba.verify(ws, task)
            assert not passed, "an already-passing fixture measures nothing"
            assert "FAIL" in detail
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_the_bash_reference_turns_it_green(self):
        ws, task = solved_workspace("fix_bash_quoting")
        try:
            passed, detail = ba.verify(ws, task)
            assert passed, detail
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_quoting_only_half_the_bug_still_fails(self):
        # A model that quotes "$@" but leaves $f unquoted has not fixed it.
        half = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "sizes() {\n"
            '    for f in "$@"; do\n'
            "        printf '%s: %s\\n' $f $(wc -c < \"$f\")\n"
            "    done\n"
            "}\n"
            "\n"
            'sizes "$@"\n'
        )
        ws, task = solved_workspace("fix_bash_quoting", **{"list-files.sh": half})
        try:
            passed, _ = ba.verify(ws, task)
            assert not passed, "the check script accepts a half-fixed script"
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_editing_the_check_script_is_refused(self):
        # The shell version of editing the red test: check.sh is protected.
        always_ok = "#!/usr/bin/env bash\nprintf 'ok\\n'\n"
        ws, task = solved_workspace("fix_bash_quoting", **{"check.sh": always_ok})
        try:
            passed, detail = ba.verify(ws, task)
            assert not passed and "tests were modified" in detail
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_the_c_test_of_the_cmake_fixture_is_protected(self):
        # Reached without cmake: the protection runs before the build does.
        ws, task = solved_workspace(
            "fix_cmake_link", **{"test_math.c": "int main(void) { return 0; }\n"}
        )
        try:
            passed, detail = ba.verify(ws, task)
            assert not passed and "tests were modified" in detail
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_the_cmake_verify_refuses_a_run_with_no_tests(self):
        # `ctest` exits 0 when it finds NO tests, so deleting add_test() would
        # otherwise be a free pass. The command asserts the count itself - via
        # the format-tolerant pattern (newer ctest drops ", 0 tests failed"),
        # whose "out of 1" tail is what a zero-test run can never print.
        command = " ".join(task_named("fix_cmake_link")["verify"])
        assert "100% tests passed(, 0 tests failed)? out of 1" in command

    def test_the_cmake_fixture_is_missing_exactly_the_link_line(self):
        task = task_named("fix_cmake_link")
        before = task["files"]["CMakeLists.txt"]
        after = ba.REFERENCE["fix_cmake_link"]["CMakeLists.txt"]
        assert "target_link_libraries" not in before
        assert "target_link_libraries(test_math PRIVATE mathlib)" in after
        # Nothing else changes: the fixture is red for one reason only.
        assert [
            ln for ln in after.splitlines() if "target_link_libraries" not in ln
        ] == before.splitlines()


class TestProtectionIsNotVacuous:
    def test_protect_tests_without_a_test_file_fails_loudly(self, monkeypatch):
        # `git diff --` with an empty pathspec means EVERY path, so a task whose
        # files match no pattern would reject the correct fix as a cheat.
        monkeypatch.setattr(ba, "TEST_FILE_PATTERNS", ("nothing_matches_*.xyz",))
        ws, task = solved_workspace("fix_bash_quoting")
        try:
            detail = ba.protected_tests_changed(ws, task)
            assert detail and "protect_tests" in detail
            assert "tests were modified" not in detail
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_a_task_with_a_test_file_is_checked_normally(self):
        ws, task = solved_workspace("fix_bash_quoting")
        try:
            assert ba.protected_tests_changed(ws, task) is None
        finally:
            shutil.rmtree(ws, ignore_errors=True)


class TestSkipsAreVisible:
    """A fixture the host cannot build must never look like a model result."""

    @staticmethod
    def _which_without(absent):
        return lambda n: None if n in absent else "/usr/bin/" + n

    def test_self_test_skips_rather_than_failing_when_cmake_is_absent(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(ba.shutil, "which", self._which_without({"cmake", "ctest"}))
        assert ba.self_test() is True
        out = capsys.readouterr().out
        assert "fix_cmake_link" in out and "SKIPPED" in out
        # And the summary must say the harness was NOT fully checked.
        assert "SKIPPED and NOT checked" in out

    def test_a_skipped_fixture_is_never_reported_as_validated(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(ba.shutil, "which", self._which_without({"cmake", "ctest"}))
        ba.self_test()
        rows = [
            ln for ln in capsys.readouterr().out.splitlines() if "fix_cmake_link" in ln
        ]
        assert rows
        assert all("SKIPPED" in ln and "OK" not in ln for ln in rows)

    def test_a_broken_fixture_still_fails_while_another_is_skipped(
        self, monkeypatch, capsys
    ):
        # The skip must not become a way for a broken fixture to pass.
        monkeypatch.setattr(ba.shutil, "which", self._which_without({"cmake", "ctest"}))
        monkeypatch.setattr(ba, "verify", lambda ws, task: (True, "green already"))
        assert ba.self_test() is False
        assert "SKIPPED" in capsys.readouterr().out

    def test_selecting_only_an_unbuildable_task_exits_nonzero(self, monkeypatch):
        # D18's lesson: running nothing, printing 0/0 and exiting 0 is worse
        # than an error, because bench_compare then reads it as a result.
        monkeypatch.setattr(ba.shutil, "which", self._which_without({"cmake", "ctest"}))
        monkeypatch.setattr(ba, "OPENCODE", sys.executable)
        monkeypatch.setattr(sys, "argv", ["bench_agent.py", "--task", "fix_cmake_link"])
        with pytest.raises(SystemExit) as e:
            ba.main()
        assert "cmake" in str(e.value)

    def test_the_report_records_which_fixtures_were_skipped(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(ba.shutil, "which", self._which_without({"cmake", "ctest"}))

        def fake(workspace, model, prompt, timeout, env=None):
            return [], 1.0, False, ""

        row = run_main(monkeypatch, tmp_path, fake, [])["reports"][0]
        skipped = {s["task"] for s in row["skipped_tasks"]}
        assert skipped == {"fix_cmake_link"}
        assert row["skipped_tasks"][0]["needs"] == ["cmake", "ctest"]
        assert row["tasks_run"] == len(ba.TASKS) - 1

    def test_nothing_is_skipped_when_every_tool_is_present(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ba.shutil, "which", self._which_without(set()))

        def fake(workspace, model, prompt, timeout, env=None):
            return [], 1.0, False, ""

        row = run_main(monkeypatch, tmp_path, fake, [])["reports"][0]
        assert row["skipped_tasks"] == []
        assert row["tasks_run"] == len(ba.TASKS)
