"""The viewer job's environment holds Flet and pytest, not the package.

benchmarks.yml's `viewer` job runs this directory under `uv run --no-project
--with flet --with pytest`. `import orchestrant` runs the package's
__init__, which imports its monitoring and with it loguru and psutil, so a
viewer test that imports the package fails to collect there
and passes everywhere the project is installed. OPS-6's first version of
test_benchmark_data.py did exactly that (`from orchestrant.benchmark import
speed_summary`: "No module named 'loguru'"), and only that job would have
said so.
"""

from __future__ import annotations

import re

# Only ever runs this interpreter on the constant script below.
import subprocess  # nosec B404
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_the_jobs_flet_is_the_locked_flet():
    # Reflex's job pin said it mirrored uv.lock and sat a minor release behind it.
    job = (ROOT / ".github/workflows/benchmarks.yml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    locked = re.search(r'^name = "flet"\r?\nversion = "([^"]+)"', lock, re.MULTILINE)
    assert locked is not None
    assert set(re.findall(r'--with "flet==([^"]+)"', job)) == {locked.group(1)}


# A None in sys.modules makes every `import orchestrant...` raise ImportError.
COLLECT = """
import sys
sys.modules["orchestrant"] = None
import pytest
sys.exit(pytest.main(["--collect-only", "-q", "-p", "no:cacheprovider", sys.argv[1]]))
"""


def test_the_viewer_tests_collect_without_the_package():
    # sys.executable and a constant script: nothing untrusted reaches the call.
    done = subprocess.run(  # nosec B603  # noqa: S603
        [sys.executable, "-c", COLLECT, str(Path(__file__).parent)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
