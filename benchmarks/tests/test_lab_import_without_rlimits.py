"""The lab lanes import where Python has no resource module (Windows); only the sandbox run itself is POSIX."""

import subprocess  # nosec B404 -- runs this interpreter on a fixed snippet
import sys
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]


def test_the_lanes_import_without_the_resource_module() -> None:
    """The lanes and the sandbox load with `resource` gone, and the sandbox then holds None for it."""
    # psutil's Linux build takes resource first, which its Windows build never does; hidden after it, as on Windows.
    code = (
        "import psutil\n"
        "import sys; sys.modules['resource'] = None\n"
        "import bench_agent, bench_chat, bench_coding, sandbox\n"
        "print('resource', sandbox.resource)\n"
    )
    out = subprocess.run(  # nosec B603 -- argv built here
        [sys.executable, "-c", code],
        cwd=LAB,
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "resource None", out.stdout
