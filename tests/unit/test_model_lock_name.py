"""models.lock owns the default model's name; paths.py loads alone so the mutation lane needs no app dependencies."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest


_source = Path(__file__).resolve().parents[2] / "orchestrant" / "paths.py"
if _source.is_file():
    _spec = importlib.util.spec_from_file_location("orchestrant_paths", _source)
    assert _spec is not None
    assert _spec.loader is not None
    paths = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(paths)
else:
    # A device's staged tests have no source tree beside them, only the installed wheel.
    paths = importlib.import_module("orchestrant.paths")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ORCHESTRANT_DATA_DIR", "ORCHESTRANT_MODEL"):
        monkeypatch.delenv(var, raising=False)


class TestModelName:
    """models.lock owns the default model's file name."""

    def test_name_comes_from_the_shipped_lock(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A bundle's own lock wins, and the model is searched under its name."""
        lock = tmp_path / "data" / "models" / paths.LOCK_NAME
        lock.parent.mkdir(parents=True)
        lock.write_text(
            "# sha256 file url\n"
            "aa  other.LICENSE  https://example.invalid/other.LICENSE\n"
            "bb  other.onnx  https://example.invalid/other.onnx\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ORCHESTRANT_DATA_DIR", str(tmp_path / "data"))
        assert paths.default_model_name() == "other.onnx"
        assert paths.model_search_paths()[0] == lock.parent / "other.onnx"

    def test_a_lock_without_a_model_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A commented row is no model, and no later lock answers for a broken one."""
        lock = tmp_path / "data" / "models" / paths.LOCK_NAME
        lock.parent.mkdir(parents=True)
        lock.write_text(
            "#aa  other.onnx  https://example.invalid/x\n", encoding="utf-8"
        )
        monkeypatch.setenv("ORCHESTRANT_DATA_DIR", str(tmp_path / "data"))
        with pytest.raises(FileNotFoundError, match=r"names no \.onnx model"):
            paths.default_model_name()

    def test_no_lock_names_where_it_looked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A wheel outside a checkout cannot guess a name."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(paths, "__file__", str(tmp_path / "pkg" / "paths.py"))
        with pytest.raises(FileNotFoundError, match=r"no models\.lock names"):
            paths.default_model_name()

    def test_an_existing_override_needs_no_lock(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """ORCHESTRANT_MODEL keeps working where no lock is shipped."""
        model = tmp_path / "custom.onnx"
        model.write_bytes(b"onnx")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(paths, "__file__", str(tmp_path / "pkg" / "paths.py"))
        monkeypatch.setenv("ORCHESTRANT_MODEL", str(model))
        assert paths.default_model_path() == model
