"""The viewer's page builds and compiles -- which importing it never proved.

`import frontend.frontend` (the CI step) only registers the page; Reflex builds
the component tree when it compiles. Built for the first time on 2026-09-24, it
raised five errors that had shipped: a foreach over a var typed Any, a Python
`if` on a Var, a table cell handed ten arguments, .length() on an untyped
chart item, and card() handed a width. Skipped where Reflex is not installed,
which is the default environment.
"""

from __future__ import annotations

import importlib.util
import os

# Only ever runs this interpreter on the constant script below.
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[3] / "frontend"

# From frontend/, as `reflex run` does: the app imports itself as `frontend`.
BUILD = """
import frontend.frontend as entry
from frontend import benchmarks
benchmarks.benchmarks().render()
entry.app._compile(dry_run=True, use_rich=False)
print("COMPILED")
"""


@pytest.mark.skipif(
    importlib.util.find_spec("reflex") is None,
    reason="needs Reflex (the frontend extra)",
)
def test_the_page_builds_and_compiles(tmp_path):
    env = dict(
        os.environ,
        # Keep the compile's .web and .states out of the source tree.
        REFLEX_WEB_WORKDIR=str(tmp_path / "web"),
        REFLEX_STATES_WORKDIR=str(tmp_path / "states"),
        REFLEX_TELEMETRY_ENABLED="false",
    )
    # sys.executable and a constant script: nothing untrusted reaches the call.
    done = subprocess.run(  # nosec B603  # noqa: S603
        [sys.executable, "-c", BUILD],
        cwd=FRONTEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert "COMPILED" in done.stdout, done.stdout[-2000:] + done.stderr[-4000:]
