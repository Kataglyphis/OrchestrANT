"""Smoke checks for run_benchmarks.sh: it parses, and its inline import resolves."""

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
    # Relative: bash cannot open a Windows drive-letter path.
    subprocess.run(
        ["bash", "-n", "benchmarks/run_benchmarks.sh"], check=True, cwd=REPO_ROOT
    )


def test_run_benchmarks_sh_imports_resolve_backend_from_the_package():
    # The script's inline `python3 -c` import, run as the script sets up PYTHONPATH.
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
