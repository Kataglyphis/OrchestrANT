"""The stubs that keep provenance.collect() off this machine's lanes, caches and drivers, for every suite that calls it."""

import pathlib

from orchestrant.benchmark import provenance as bench_provenance, served_models


FAKE_DRIVERS = {"npu": [{"version": "30.0.220.3000"}], "gpu": [], "reason": None}


def stub_probes(monkeypatch, tmp_path):
    """Stub what collect() would read beyond the test; `tmp_path` stands in for the home directory."""
    # collect() probes every registry endpoint; the tests must never leave this machine.
    monkeypatch.setattr(bench_provenance, "busy_lanes", lambda *a, **k: [])
    # runtime_info() may run a local `geniex --version`; tests must not depend on what is installed.
    monkeypatch.setattr(bench_provenance, "runtime_info", lambda *a, **k: None)
    # ...nor on this machine's model caches, driver registry or an exported lane-runtime file.
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(bench_provenance.glob, "glob", lambda pattern: [])
    monkeypatch.setattr(served_models, "driver_versions", lambda: FAKE_DRIVERS)
    monkeypatch.delenv(bench_provenance.LANE_RUNTIMES_ENV, raising=False)
    # No GenieX is installed unless a test says so, so LOCALAPPDATA's real one never runs.
    monkeypatch.setattr(bench_provenance, "_installed_geniex", lambda: None)
