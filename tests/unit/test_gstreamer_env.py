"""The environment the GStreamer subprocess gets: the allowlist, plus every GST_ variable."""

from __future__ import annotations

import pytest

from orchestrant.pipeline.capture import gstreamer


def test_gst_variables_reach_the_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """A package's launcher sets GST_PLUGIN_PATH and an empty system path; both must arrive."""
    monkeypatch.setattr(gstreamer.platform, "system", lambda: "Linux")
    monkeypatch.setenv("GST_PLUGIN_PATH", "/opt/orchestrant/runtime/lib/gstreamer-1.0")
    monkeypatch.setenv("GST_PLUGIN_SYSTEM_PATH", "")
    monkeypatch.setenv("GST_DEBUG", "2")
    monkeypatch.setenv("ORCHESTRANT_TEST_SECRET", "not for gst-launch")
    env = gstreamer.get_gstreamer_env()
    assert env["GST_PLUGIN_PATH"] == "/opt/orchestrant/runtime/lib/gstreamer-1.0"
    assert env["GST_PLUGIN_SYSTEM_PATH"] == ""
    assert env["GST_DEBUG"] == "2"
    assert "ORCHESTRANT_TEST_SECRET" not in env


def test_without_gst_variables_only_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing beyond PATH, HOME, USER, TMPDIR and TEMP when the caller sets no GST_ variable."""
    monkeypatch.setattr(gstreamer.platform, "system", lambda: "Linux")
    for name in [k for k in gstreamer.os.environ if k.startswith("GST_")]:
        monkeypatch.delenv(name)
    assert set(gstreamer.get_gstreamer_env()) == {"PATH", "HOME", "USER", "TMPDIR", "TEMP"}
