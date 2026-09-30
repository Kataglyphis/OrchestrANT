"""bench_compare's split into compare_suspect.py and compare_dirs.py: names and import order."""

import argparse
import ast
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bench_compare  # noqa: E402
import compare_dirs  # noqa: E402
import compare_suspect  # noqa: E402

# Names the suspect-case block took along; producers and tests import them from bench_compare.
SUSPECT_NAMES = (
    "is_control",
    "suspect_cases",
    "_case_key",
    "mark_suspect_cases",
    "_recount_sample",
    "_recount_groups",
    "_recount_walls",
    "measured",
)
# The --dir half, as bench_sweep, upgrade_check and main() read it.
DIRS_NAMES = ("pair_directories", "baseline_path", "BASELINE_DIR")

# One deterministic two-case bench_tools report: compared, and unchanged.
REPORT = {
    "benchmark": "bench_tools",
    "provenance": {},
    "reports": [
        {
            "label": "m",
            "passed": 2,
            "total": 2,
            "deterministic": True,
            "results": [{"case": "a", "passed": True}, {"case": "b", "passed": True}],
        }
    ],
}


def _run_dirs(tmp_path):
    """Two run directories sharing a.json; returns [old, new]."""
    dirs = []
    for side in ("old", "new"):
        (tmp_path / side).mkdir()
        (tmp_path / side / "a.json").write_text(json.dumps(REPORT))
        dirs.append(str(tmp_path / side))
    return dirs


def _imports_of(module):
    """Every module name `module`'s source imports, at the top or in a body."""
    with open(module.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _fresh(code):
    """Run `code` in a fresh interpreter with the lab importable; return its stdout's words."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((HERE, REPO)))
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=HERE,
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


class TestEveryMovedNameIsServedFromBenchCompare:
    """Each moved name, served from bench_compare, is the owner's own object, not a copy."""

    @pytest.mark.parametrize("name", SUSPECT_NAMES)
    def test_a_suspect_case_name(self, name):
        assert getattr(bench_compare, name) is getattr(compare_suspect, name)

    @pytest.mark.parametrize("name", DIRS_NAMES)
    def test_a_directory_name(self, name):
        assert getattr(bench_compare, name) is getattr(compare_dirs, name)

    def test_the_baselines_stay_where_save_baseline_wrote_them(self):
        # Derived from the moved module's own path, it must still be benchmarks/baselines.
        where = compare_dirs.BASELINE_DIR
        assert where == os.path.join(HERE, "baselines")


class TestTheSplitModulesAreLeaves:
    """Neither new module imports bench_compare, at the top or in a body (cycle)."""

    @pytest.mark.parametrize(
        "module", [compare_suspect, compare_dirs], ids=lambda m: m.__name__
    )
    def test_no_reach_back(self, module):
        assert "bench_compare" not in _imports_of(module)

    @pytest.mark.parametrize("first", ["compare_suspect", "compare_dirs"])
    def test_each_imports_first_in_a_fresh_interpreter(self, first):
        code = f"import {first}, sys; print('bench_compare' in sys.modules)"
        assert _fresh(code) == ["False"]

    def test_a_dir_run_as_a_script_loads_one_copy(self, tmp_path):
        # upgrade_check runs `bench_compare.py --dir` as a step.
        old, new = _run_dirs(tmp_path)
        script = os.path.join(HERE, "bench_compare.py")
        code = (
            "import runpy, sys\n"
            f"sys.argv = ['bench_compare.py', '--dir', {old!r}, {new!r}]\n"
            "try:\n"
            f"    runpy.run_path({script!r}, run_name='__main__')\n"
            "except SystemExit as done:\n"
            "    print('exit', done.code)\n"
            "print('second-copy', 'bench_compare' in sys.modules)\n"
        )
        assert _fresh(code)[-4:] == ["exit", "0", "second-copy", "False"]


class TestTheDirectoryLoopIsJudgedByBenchCompare:
    """A patch of bench_compare.compare still decides --dir: it is handed in at call time."""

    def test_a_patched_compare_decides_the_exit(self, monkeypatch, tmp_path, capsys):
        def judged(old, new, tolerance, seen, allow):
            seen.update(compared=1, paired=[], withheld=[])
            return ["judged by the patch"], True

        monkeypatch.setattr(bench_compare, "compare", judged)
        args = argparse.Namespace(reports=_run_dirs(tmp_path), time_tolerance=0.25)
        assert bench_compare._compare_directories(args) == 1
        out = capsys.readouterr().out
        assert "    judged by the patch" in out and "    REGRESSION" in out
