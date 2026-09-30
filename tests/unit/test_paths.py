"""Unit tests for where an installed OrchestrANT finds its model and writes its logs."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from orchestrant import paths


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ORCHESTRANT_DATA_DIR", "ORCHESTRANT_MODEL", "ORCHESTRANT_LOG_DIR"):
        monkeypatch.delenv(var, raising=False)


class TestBundleDataDir:
    """A packaged install is recognised by its data dir, never by guesswork."""

    def test_env_wins(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """ORCHESTRANT_DATA_DIR names the data dir even when it does not exist yet."""
        monkeypatch.setenv("ORCHESTRANT_DATA_DIR", str(tmp_path / "data"))
        assert paths.bundle_data_dir() == tmp_path / "data"

    def test_bundle_layout_beside_the_interpreter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """<root>/runtime holds the interpreter and <root>/share/orchestrant the data."""
        (tmp_path / "share" / "orchestrant").mkdir(parents=True)
        monkeypatch.setattr(sys, "prefix", str(tmp_path / "runtime"))
        assert paths.bundle_data_dir() == tmp_path / "share" / "orchestrant"

    def test_plain_venv_is_not_a_bundle(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A venv has no share/orchestrant beside it."""
        monkeypatch.setattr(sys, "prefix", str(tmp_path / ".venv"))
        assert paths.bundle_data_dir() is None


class TestModelPath:
    """The default model is searched in a fixed order."""

    def test_env_first_then_bundle_then_checkout(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """ORCHESTRANT_MODEL, the bundle's models dir, then resources/models."""
        monkeypatch.setenv("ORCHESTRANT_MODEL", str(tmp_path / "custom.onnx"))
        monkeypatch.setenv("ORCHESTRANT_DATA_DIR", str(tmp_path / "data"))
        found = paths.model_search_paths()
        assert found[0] == tmp_path / "custom.onnx"
        assert found[1] == tmp_path / "data" / "models" / paths.MODEL_NAME
        assert found[2].as_posix() == f"resources/models/{paths.MODEL_NAME}"

    def test_first_existing_candidate_wins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A missing ORCHESTRANT_MODEL falls through to the bundle's model."""
        model = tmp_path / "data" / "models" / paths.MODEL_NAME
        model.parent.mkdir(parents=True)
        model.write_bytes(b"onnx")
        monkeypatch.setenv("ORCHESTRANT_MODEL", str(tmp_path / "missing.onnx"))
        monkeypatch.setenv("ORCHESTRANT_DATA_DIR", str(tmp_path / "data"))
        assert paths.default_model_path() == model

    def test_nothing_found_names_the_first_candidate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The error then points at the place the user most likely meant."""
        monkeypatch.setenv("ORCHESTRANT_MODEL", str(tmp_path / "missing.onnx"))
        monkeypatch.setattr(
            paths, "model_search_paths", lambda _name=None: [tmp_path / "missing.onnx"]
        )
        assert paths.default_model_path() == tmp_path / "missing.onnx"


class TestLogDir:
    """A packaged install must not log into its possibly read-only install dir."""

    def test_checkout_keeps_relative_logs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Outside a bundle nothing changes: logs/ in the working directory."""
        monkeypatch.setattr(sys, "prefix", str(tmp_path / ".venv"))
        assert paths.default_log_dir().as_posix() == "logs"

    def test_env_wins(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """ORCHESTRANT_LOG_DIR overrides every default."""
        monkeypatch.setenv("ORCHESTRANT_LOG_DIR", str(tmp_path / "logs"))
        assert paths.default_log_dir() == tmp_path / "logs"

    def test_bundle_logs_to_the_user_state_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """LOCALAPPDATA on Windows, XDG_STATE_HOME elsewhere."""
        monkeypatch.setenv("ORCHESTRANT_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        expected = (
            tmp_path / "local" / "OrchestrANT" / "logs"
            if sys.platform == "win32"
            else tmp_path / "state" / "orchestrant" / "logs"
        )
        assert paths.default_log_dir() == expected
