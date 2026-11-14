"""A hung test fails by its own name within the suite's limit, not as the lane's time limit (owner decision 2026-11-14)."""

from __future__ import annotations

import os
import subprocess  # nosec B404
import sys
import tomllib
from pathlib import Path


def test_a_test_past_the_limit_fails_and_names_itself(tmp_path: Path) -> None:
    """pytest-timeout ends a sleeping test at --timeout and reports it by name, in the venv every lane builds."""
    toy = tmp_path / "test_toy.py"
    toy.write_text(
        "import time\n\n\ndef test_hangs_for_good():\n    time.sleep(60)\n",
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_DISABLE_PLUGIN_AUTOLOAD"}
    # nosec before noqa: ruff reads codes to end-of-line, bandit stops at "#".
    result = subprocess.run(  # nosec B603  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--timeout=1",
            str(toy),
        ],
        capture_output=True,
        text=True,
        timeout=50,
        cwd=tmp_path,
        env=env,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "test_hangs_for_good" in result.stdout
    assert "Timeout" in result.stdout


def test_the_suite_runs_under_a_limit() -> None:
    """The suite's own addopts carry the limit, so a hang anywhere in it fails by name."""
    root = Path(__file__).resolve().parents[2]
    options = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert "--timeout=" in options["tool"]["pytest"]["ini_options"]["addopts"]
