"""verify() runs the repository a model edited as a coding candidate runs: in sandbox.launch."""

import os
import sys

import pytest

if sys.platform == "win32":
    pytest.skip(
        "the sandbox is POSIX-only: rlimits, process groups", allow_module_level=True
    )

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import bench_agent as ba  # noqa: E402

MARKER = "ORCHESTRANT_VERIFY_MARKER"


def test_a_passing_command_passes_with_its_output(tmp_path):
    passed, detail = ba.verify(str(tmp_path), {"verify": ["bash", "-c", "echo fine"]})
    assert passed and detail.strip() == "fine", detail


def test_a_failing_command_fails_with_its_output(tmp_path):
    passed, detail = ba.verify(
        str(tmp_path), {"verify": ["bash", "-c", "echo broken >&2; exit 3"]}
    )
    assert not passed and "broken" in detail, detail


def test_no_variable_of_the_harness_reaches_the_command(tmp_path, monkeypatch):
    monkeypatch.setenv(MARKER, "leaked")
    check = f"import os, sys; sys.exit({MARKER!r} in os.environ)"
    passed, detail = ba.verify(str(tmp_path), {"verify": [sys.executable, "-c", check]})
    assert passed, detail


def test_its_temp_files_stay_out_of_the_repository(tmp_path):
    check = "import os, sys, tempfile; sys.exit(tempfile.gettempdir().startswith(os.getcwd()))"
    passed, detail = ba.verify(str(tmp_path), {"verify": [sys.executable, "-c", check]})
    assert passed, detail


def test_a_forked_child_dies_with_it_at_the_timeout(tmp_path):
    task = {
        "verify": ["bash", "-c", "sleep 300 & echo $! > child.pid; sleep 300"],
        "verify_timeout": 2,
    }
    passed, detail = ba.verify(str(tmp_path), task)
    assert not passed and detail == "verification timed out", detail
    assert ba._process_gone(int((tmp_path / "child.pid").read_text()))


def test_endless_output_is_cut(tmp_path):
    passed, detail = ba.verify(
        str(tmp_path), {"verify": ["bash", "-c", "yes"], "verify_timeout": 30}
    )
    assert not passed and detail.startswith("verification: output exceeded"), detail


def test_a_command_that_cannot_start_fails_the_trial(tmp_path):
    passed, detail = ba.verify(str(tmp_path), {"verify": ["/no/such/verify-tool"]})
    assert not passed, detail


def test_a_live_process_is_not_gone():
    assert not ba._process_gone(os.getpid())
    assert ba._process_gone(2**22 + 1)


def test_the_self_test_rows_hold(capsys):
    assert ba._self_test_verify_sandbox() is True
    rows = [ln for ln in capsys.readouterr().out.splitlines() if "verify:" in ln]
    assert len(rows) == 3 and all(ln.split()[-1] == "OK" for ln in rows), rows
