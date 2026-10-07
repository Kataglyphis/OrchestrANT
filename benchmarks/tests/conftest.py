"""Suite-wide guards: tests run OFFLINE; only a stub server bound in this process is reachable."""

import os
import pathlib
import socket
import urllib.error

import pytest

# The two modules that reach a live server on purpose; both skip when nothing answers.
_LIVE_ENDPOINT_MODULES = {"test_harness_against_ollama.py", "test_v1_api.py"}

_HERE = pathlib.Path(__file__).resolve().parent

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
def close_http_errors(monkeypatch):
    """Close every HTTPError a test made, whoever read it last."""
    made = []
    real_init = urllib.error.HTTPError.__init__

    def recording_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        made.append(self)

    monkeypatch.setattr(urllib.error.HTTPError, "__init__", recording_init)
    yield
    # One collected after the session warns, and filterwarnings=error fails the run.
    for err in made:
        err.close()


@pytest.fixture(autouse=True)
def no_plugin_autoload_in_subprocesses(monkeypatch):
    """A nested `python -m pytest` grades a toy repo, which needs none of this venv's plugins.

    Loading them cost 49 of the 60 s each nested pytest took under QEMU (pytest-md-report's
    import of chardet alone 32 s), about 2 h of the riscv64 lane; natively 1.0 of 1.2 s.
    """
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")


@pytest.fixture(autouse=True)
def spacers(monkeypatch):
    """Record client.spacer() instead of sending it: it would swallow the no_network refusal."""
    from orchestrant.benchmark import client

    sent = []
    monkeypatch.setattr(
        client, "spacer", lambda base_url, model, entry=None: sent.append(model)
    )
    return sent


@pytest.fixture(autouse=True)
def host_load(monkeypatch):
    """Record hostload.load_snapshot() calls instead of sleeping through a real 3 s window."""
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
        # Not an OSError, so socket.create_connection would not close it either.
        self.close()
        raise NetworkAccessInATest(
            f"{request.node.nodeid} tried to connect to {address!r}, which no "
            f"test in this process is listening on. These tests run offline: "
            f"monkeypatch the request function (urlopen, or bench_cli.post_json), "
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
        # The hook gets the whole session's items; tests/ keeps its ResourceWarning errors.
        if _HERE in item.path.parents:
            item.add_marker(marker)
