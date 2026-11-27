"""The lab lanes import where Python has no resource module (Windows); only the sandbox run itself is POSIX."""

import os
import subprocess  # nosec B404 -- runs this interpreter on a fixed snippet
import sys

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_lanes_import_without_the_resource_module():
    code = (
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
