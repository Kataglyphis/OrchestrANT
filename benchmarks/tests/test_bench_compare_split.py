"""bench_compare's split: compare_suspect.py and compare_dirs.py.

bench_compare.py reached 1011 lines, frozen in file-size.allow with the split
its row named: the suspect-case block into one module, the --dir driver into
another. A move can break what no comparison test would notice: a caller that
imports a moved name from bench_compare, and an import order -- a new module
imported first, or bench_compare.py run as a script.
"""

import ast
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bench_compare  # noqa: E402
import compare_suspect  # noqa: E402

# Every name the suspect-case block took with it; the producers and the tests
# import them from bench_compare.
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


def _fresh(code, cwd=HERE):
    """Run `code` in a new interpreter that can import the lab and the package."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((HERE, REPO)))
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


class TestEveryMovedNameIsServedFromBenchCompare:
    """bench_tools, bench_coding, bench_chat, bench_sweep, upgrade_check and
    the tests import the moved names from bench_compare. Each must be the
    owner's own object: a copy would keep the old rule after an edit."""

    @pytest.mark.parametrize("name", SUSPECT_NAMES)
    def test_a_suspect_case_name(self, name):
        assert getattr(bench_compare, name) is getattr(compare_suspect, name)


class TestTheSplitModulesAreLeaves:
    """Neither new module imports bench_compare, at the top or in a body.

    bench_compare imports both, so a reach back closes a cycle: at the top it
    breaks whichever module is imported first; inside a function it makes
    `python bench_compare.py` load bench_compare.py a second time, as a module
    beside __main__. bench_variants.py keeps the same rule for the same cycle.
    """

    @pytest.mark.parametrize("module", [compare_suspect], ids=lambda m: m.__name__)
    def test_no_reach_back(self, module):
        assert "bench_compare" not in _imports_of(module)

    def test_each_imports_first_in_a_fresh_interpreter(self):
        code = "import compare_suspect, sys; print('bench_compare' in sys.modules)"
        assert _fresh(code) == ["False"]
