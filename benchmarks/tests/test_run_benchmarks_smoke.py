"""Smoke checks for run_benchmarks.sh.

The sweep script imported ``benchmark_openai_api`` for weeks after that module
moved into ``orchestrant.benchmark``; nothing in CI executed the import, so it
only failed for whoever next ran the sweep. Parse the script and resolve the
import here so the next move fails in the benchmarks lane instead.
"""

import os
import re
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO_ROOT, "benchmarks", "run_benchmarks.sh")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_run_benchmarks_sh_parses():
    # A relative POSIX path: a Windows checkout hands bash a drive-letter path
    # it cannot open.
    subprocess.run(
        ["bash", "-n", "benchmarks/run_benchmarks.sh"], check=True, cwd=REPO_ROOT
    )


def test_run_benchmarks_sh_imports_resolve_backend_from_the_package():
    # The script resolves the backend URL through an inline `python3 -c`; run
    # the same import in a fresh interpreter with the repo root on PYTHONPATH,
    # exactly as the script sets it up.
    with open(SCRIPT, encoding="utf-8") as fh:
        script = fh.read()
    match = re.search(
        r"^from (orchestrant\.benchmark\.openai_api) import resolve_backend$",
        script,
        re.M,
    )
    assert match, "run_benchmarks.sh no longer imports resolve_backend from the package"
    env = dict(os.environ, PYTHONPATH=REPO_ROOT)
    subprocess.run(
        [sys.executable, "-c", f"from {match.group(1)} import resolve_backend"],
        check=True,
        env=env,
    )
