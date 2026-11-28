"""The tree state a provenance block records: a git status that failed is unknown, never clean."""

import subprocess

import pytest

from orchestrant.benchmark.provenance import collect, compare

from .provenance_offline import stub_probes


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    stub_probes(monkeypatch, tmp_path)


def fake_git(monkeypatch, status):
    """Answer git with a SHA, and `git status` with `status`: its stdout, an exit code, or an exception it raises."""
    real_run = subprocess.run

    def run(argv, *args, **kwargs):
        if argv[0] != "git":
            return real_run(argv, *args, **kwargs)
        if "status" not in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="a" * 40 + "\n")
        if isinstance(status, BaseException):
            raise status
        if isinstance(status, int):
            return subprocess.CompletedProcess(argv, status, stdout="", stderr="fatal")
        return subprocess.CompletedProcess(argv, 0, stdout=status)

    monkeypatch.setattr(subprocess, "run", run)


class TestGitDirty:
    """A git status that failed or timed out was recorded as git_dirty False, a clean tree."""

    @pytest.mark.parametrize(
        ("status", "dirty"),
        [
            ("", False),
            (" M grader.py\n", True),
            (128, None),
            (subprocess.TimeoutExpired(["git", "status"], 10), None),
        ],
        ids=["clean", "dirty", "exit_128", "timeout"],
    )
    def test_a_status_git_could_not_give_is_unknown_and_incomplete(
        self, monkeypatch, status, dirty
    ):
        fake_git(monkeypatch, status)
        p = collect()
        assert p["git_sha"] == "a" * 40
        assert p["git_dirty"] is dirty
        assert ("git_dirty" in p["incomplete"]) is (dirty is None)

    def test_an_unknown_tree_is_named_in_a_comparison_not_taken_as_clean(self):
        clean = collect(extra={"git_dirty": False})
        unknown = collect(extra={"git_dirty": None})
        notes = compare(clean, unknown)
        assert any("tree state is unknown" in n for n in notes)
        assert not any("dirty working tree" in n for n in notes)
