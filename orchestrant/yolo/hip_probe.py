"""One copy to the AMD GPU before MIGraphX starts.

Under WSL on an RX 9070 XT the first copy never returns, and MIGraphX would wait for it without
end: hub BACKLOG CON202. The copy runs in a child process: a hung copy would also stop this
process from exiting, as the runtime's teardown waits for it.
"""

from __future__ import annotations

import subprocess  # nosec B404 -- runs this interpreter, argv built here
import sys
import threading


ANSWER_LIMIT_SECONDS = 30.0
COPY_TO_DEVICE = """
import ctypes, sys
try:
    hip = ctypes.CDLL("libamdhip64.so.7")
except OSError:
    sys.exit()
device = ctypes.c_void_p()
value = ctypes.c_uint32(0)
size = ctypes.c_size_t(ctypes.sizeof(value))
if (
    hip.hipInit(0) == 0
    and hip.hipSetDevice(int(sys.argv[1])) == 0
    and hip.hipMalloc(ctypes.byref(device), size) == 0
):
    hip.hipMemcpy(device, ctypes.byref(value), size, 1)
"""


def answers_within(limit_seconds: float, command: list[str]) -> bool:
    """False only when ``command`` outlives the limit: MIGraphX reports every other failure."""
    try:
        child = subprocess.Popen(  # nosec B603  # noqa: S603 -- argv built here
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return True
    try:
        child.wait(limit_seconds)
    except subprocess.TimeoutExpired:
        child.kill()
        # A child stuck in the driver may take its time to die; nothing here waits for that.
        threading.Thread(target=child.wait, daemon=True).start()
        return False
    return True


def require_answer(gpu: int) -> None:
    """Raise when the GPU does not answer. No HIP runtime or no device passes: MIGraphX reports those."""
    if sys.platform != "linux":
        return
    command = [sys.executable, "-c", COPY_TO_DEVICE, str(gpu)]
    if not answers_within(ANSWER_LIMIT_SECONDS, command):
        message = (
            "MIGraphX cannot run: a copy to the AMD GPU took over "
            f"{ANSWER_LIMIT_SECONDS:.0f} seconds. --device cpu runs on the CPU"
        )
        raise RuntimeError(message)
