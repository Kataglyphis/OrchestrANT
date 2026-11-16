"""The sandbox every lab lane runs model-written code in: own session, scrubbed environment, no network, limits.

Moved out of bench_coding.py so the agent lane's verify() runs the repository a model edited the same way a coding
candidate runs. POSIX only, like both lanes.
"""

import os
import resource
import selectors
import signal
import subprocess  # nosec B404 -- runs local tools, argv built here
import sys
import time


_NETNS = None


def _netns_available():
    """Can the candidate be given an empty network namespace, with prlimit
    inside it? Probed once."""
    global _NETNS
    if _NETNS is None:
        try:
            _NETNS = (
                subprocess.run(  # nosec B603 B607 -- argv built here, tool from PATH, no shell
                    ["unshare", "-rn", "prlimit", f"--nproc={RLIMIT_NPROC}", "true"],
                    capture_output=True,
                    timeout=10,
                ).returncode
                == 0
            )
        except Exception:  # noqa: BLE001
            _NETNS = False
    return _NETNS


# Per-run ceilings, generous for a real solution: an allocating candidate once took WSL2 down with it.
RLIMIT_AS_BYTES = 1 << 30
RLIMIT_FSIZE_BYTES = 8 << 20
RLIMIT_NPROC = 64  # forks allowed ABOVE what the user already runs
_NPROC_CEILING = None
OUTPUT_LIMIT_BYTES = 1 << 20


def _nproc_census():
    """This UID's tasks as /proc shows them; a PID namespace hides the rest."""
    uid, mine = os.getuid(), 0
    try:
        entries = list(os.scandir("/proc"))
    except OSError:
        entries = []
    for e in entries:
        # A pid that exits mid-census raises: skip that entry, as a catch around the loop zeroed the count.
        try:
            if not (e.name.isdigit() and e.stat().st_uid == uid):
                continue
            mine += len(os.listdir(f"/proc/{e.name}/task"))
        except OSError:
            continue
    return mine


# The lowest RLIMIT_NPROC a new thread still starts under is the kernel's count of the UID's tasks + 1.
_NPROC_PROBE = """
import resource, sys, threading
R = resource.RLIMIT_NPROC
hard = resource.getrlimit(R)[1]
top = (1 << 22) if hard == resource.RLIM_INFINITY else hard
def starts(n):
    resource.setrlimit(R, (n, hard))
    t = threading.Thread(target=int)
    try:
        t.start()
    except RuntimeError:
        return False
    t.join()
    return True
lo, hi = 0, min(top, max(1, int(sys.argv[1])))
while not starts(hi):
    if hi >= top:
        sys.exit(1)
    lo, hi = hi, min(top, hi * 2)
while hi - lo > 1:
    mid = (lo + hi) // 2
    lo, hi = (lo, mid) if starts(mid) else (mid, hi)
print(hi - 1)
"""
_AS_PROBE = "import resource as r; r.setrlimit(r.RLIMIT_AS, ({n}, {n})); print(r.getrlimit(r.RLIMIT_AS)[0])"


def _probe(code, *args):
    """What `code` prints in a fresh interpreter, or None when it fails."""
    try:
        p = subprocess.run(  # nosec B603 -- argv built here, no shell
            [sys.executable, "-I", "-W", "ignore", "-c", code, *args],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def _nproc_in_use(census):
    """The tasks the kernel charges to this UID, which a PID namespace's /proc undercounts.

    On a GitHub runner uid 1001 is also the runner's own user: its threads made the
    census ceiling too low, and under qemu-user every candidate then died starting
    QEMU's own thread (EAGAIN). None when the probe cannot tell.
    """
    out = _probe(_NPROC_PROBE, str(census))
    return int(out) if out and out.isdigit() else None


def _nproc_ceiling():
    """RLIMIT_NPROC is per-UID, counted live and host-wide, and counts TASKS, not
    processes. A bare 64 therefore limits the DESKTOP, not the candidate: this host
    runs 102 processes but 591 tasks, so the fork fails, bash retries it forever,
    and every bash/CMake row dies at the timeout blaming "likely an infinite loop".
    Count tasks (the kernel's count where /proc shows fewer) and allow RLIMIT_NPROC
    above them. Probed once, like _NETNS, so the ceiling cannot drift between
    launch and assertion.
    """
    global _NPROC_CEILING
    if _NPROC_CEILING is None:
        census = _nproc_census()
        _NPROC_CEILING = max(census, _nproc_in_use(census) or 0) + RLIMIT_NPROC
    return _NPROC_CEILING


_AS_DROPPED = None


def _as_rlimit_dropped():
    """qemu-user accepts a guest's RLIMIT_AS and drops it (it would cap QEMU too). Probed once."""
    global _AS_DROPPED
    if _AS_DROPPED is None:
        out = _probe(_AS_PROBE.format(n=RLIMIT_AS_BYTES))
        _AS_DROPPED = out is not None and out != str(RLIMIT_AS_BYTES)
    return _AS_DROPPED


def _candidate_rlimits(as_bytes=RLIMIT_AS_BYTES, nproc_ceiling=None):
    resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (RLIMIT_FSIZE_BYTES, RLIMIT_FSIZE_BYTES))
    # Checked against the user's host-wide count even inside `unshare -r`, where prlimit sets it instead.
    if nproc_ceiling is not None:
        resource.setrlimit(resource.RLIMIT_NPROC, (nproc_ceiling, nproc_ceiling))


def _communicate_bounded(proc, timeout, limit=OUTPUT_LIMIT_BYTES):
    """communicate() with a byte cap per stream.

    Returns (stdout, stderr, overflowed). A candidate printing without end
    used to be buffered whole into this process; past the cap the read stops
    and the caller kills the tree. Raises TimeoutExpired like communicate().
    """
    deadline = time.monotonic() + timeout
    bufs = {proc.stdout: bytearray(), proc.stderr: bytearray()}
    sel = selectors.DefaultSelector()
    for f in bufs:
        sel.register(f, selectors.EVENT_READ)
    try:
        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(proc.args, timeout)
            for key, _ in sel.select(remaining):
                data = os.read(key.fd, 65536)
                if not data:
                    sel.unregister(key.fileobj)
                    continue
                buf = bufs[key.fileobj]
                if len(buf) + len(data) > limit:
                    return bytes(bufs[proc.stdout]), bytes(bufs[proc.stderr]), True
                buf += data
    finally:
        sel.close()
        proc.stdout.close()
        proc.stderr.close()
    proc.wait(timeout=max(0.0, deadline - time.monotonic()))
    return bytes(bufs[proc.stdout]), bytes(bufs[proc.stderr]), False


def _kill_tree(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    # The direct child too: when killpg cannot reach the group, waiting on a spinning candidate never returns.
    proc.kill()
    proc.wait()


def launch(cmd, cwd, timeout, env_extra=None, as_bytes=RLIMIT_AS_BYTES, tmp=None):
    """Start `cmd` in `cwd` in its own session, sandboxed, and read it bounded; `tmp` is its TMPDIR, `cwd` if unset.

    Returns (out, err, returncode, failure); `failure` is set when the run was
    KILLED rather than finished, and no output may be trusted in that case.
    Shared by every language so a bash or CMake candidate gets exactly the
    Python path's temp dir, RLIMITs, scrubbed env, netns and process-group kill.
    `env_extra` and `as_bytes` exist for runtimes that cannot start under the
    defaults (pwsh: see PWSH_AS_BYTES); everything else stays the same.
    """
    nproc_ceiling = None
    if _netns_available():
        cmd = ["unshare", "-rn", "prlimit", f"--nproc={RLIMIT_NPROC}"] + cmd
    else:
        # Before the fork: its probe is a subprocess, which preexec_fn must not start.
        nproc_ceiling = _nproc_ceiling()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "TMPDIR": tmp or cwd,
        **(env_extra or {}),
    }
    if _as_rlimit_dropped():
        # QEMU confines the guest to this much address space: RLIMIT_AS's ceiling, enforced by the emulator.
        env["QEMU_RESERVED_VA"] = str(as_bytes)
    proc = subprocess.Popen(  # nosec B603 -- argv built here, no shell
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=lambda: _candidate_rlimits(as_bytes, nproc_ceiling),
        start_new_session=True,
    )
    try:
        out, err, overflowed = _communicate_bounded(proc, timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return "", "", None, f"timed out after {timeout}s (likely an infinite loop)"
    if overflowed:
        _kill_tree(proc)
        return "", "", None, f"output exceeded {OUTPUT_LIMIT_BYTES} bytes"
    return (
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
        proc.returncode,
        None,
    )
