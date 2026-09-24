"""Suite-wide guards.

The rule is that these tests run OFFLINE. It is not self-enforcing. Renaming
one seam in bench_coding's main() -- `resolve_candidates` -> `candidate_rows`
-- silently un-patched three tests, and instead of failing they connected to a
real Ollama on localhost:11434 and hung the run: no output, no failure, just a
suite that never finished. A test that reaches a server it did not start is
either lying about what it proves or waiting on a machine that is not there.

Loopback is NOT the line -- the hang was to 127.0.0.1. The line is who owns the
listener: a stub HTTP server the test binds in this process is the intended way
to exercise a real socket path, so its port is allowed and every other is
refused by name.
"""

import os
import socket

import pytest


# The two modules that talk to a live server on purpose; both skip themselves
# when nothing answers.
_LIVE_ENDPOINT_MODULES = {"test_harness_against_ollama.py", "test_v1_api.py"}

# Ports bound by this process: a stub server a test started itself.
_OWN_PORTS = set()
_REAL_BIND = socket.socket.bind


def _recording_bind(self, address):
    _REAL_BIND(self, address)
    try:
        _OWN_PORTS.add(self.getsockname()[1])
    except OSError:  # a non-IP socket has no port
        pass


socket.socket.bind = _recording_bind


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "inference: tests that require model inference (slow, needs model loaded)",
    )


class NetworkAccessInATest(RuntimeError):
    """A test tried to reach a server it did not start."""


@pytest.fixture(autouse=True)
def host_load(monkeypatch):
    """Record hostload.load_snapshot() instead of taking it.

    Every tool's main() takes one at its run start, and the real one sleeps
    through a 3 s window and reads whatever this machine is doing. The list of
    calls (seconds, lane) is the evidence tests assert on.
    """
    from orchestrant.benchmark import hostload

    taken = []

    def fake(seconds=3, lane=None):
        taken.append({"seconds": seconds, "lane": lane})
        return {
            "busy_cores": 0.2,
            "lane_cores": None,
            "other_cores": 0.2,
            "seconds": 0.0,
            "cpus": 8,
            "note": "stubbed by conftest",
        }

    monkeypatch.setattr(hostload, "load_snapshot", fake)
    return taken


@pytest.fixture(autouse=True)
def no_interop(monkeypatch):
    """Refuse powershell.exe through WSL interop unless a test stubs it itself.

    From WSL2 the real load_snapshot() reads the Windows host that way: in a
    WSL test run it would sleep through a real window and read whatever this
    machine is doing, which is the host_load stub's reason too.
    """
    from orchestrant.benchmark import winhost

    def refuse(argv, timeout):
        msg = f"a test ran {argv[0]} through WSL interop; stub winhost._run"
        raise RuntimeError(msg)

    monkeypatch.setattr(winhost, "_run", refuse)


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    if os.path.basename(str(request.node.fspath)) in _LIVE_ENDPOINT_MODULES:
        return
    if request.node.get_closest_marker("inference"):
        return
    real_connect = socket.socket.connect

    def guarded(self, address):
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        if port in _OWN_PORTS:
            return real_connect(self, address)
        raise NetworkAccessInATest(
            f"{request.node.nodeid} tried to connect to {address!r}, which no "
            f"test in this process is listening on. These tests run offline: "
            f"monkeypatch the request function (urlopen, or orchestrant.benchmark.client.post_json), "
            f"or bind your own stub server. If it genuinely needs a real "
            f"backend, mark it @pytest.mark.inference."
        )

    monkeypatch.setattr(socket.socket, "connect", guarded)


def pytest_collection_modifyitems(items):
    marker = pytest.mark.filterwarnings(
        "ignore::ResourceWarning",
        "ignore::pytest.PytestUnraisableExceptionWarning",
    )
    for item in items:
        item.add_marker(marker)
