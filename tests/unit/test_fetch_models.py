"""Unit tests for scripts/fetch_models.py, against file:// URLs and no network."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

from orchestrant import paths


# Loaded from its file: scripts/ is run by path, not imported as a package.
_SPEC = importlib.util.spec_from_file_location(
    "fetch_models",
    Path(__file__).resolve().parents[2] / "scripts" / "fetch_models.py",
)
assert _SPEC is not None
assert _SPEC.loader is not None
fetch_models = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fetch_models)

PAYLOAD = b"not really an onnx model\n"
SHA = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture
def cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An empty cache the fetcher is pointed at."""
    path = tmp_path / "cache"
    monkeypatch.setenv("KATAGLYPHIS_MODEL_CACHE", str(path))
    return path


def _lock(tmp_path: Path, url: str, sha: str = SHA) -> Path:
    lock = tmp_path / "models.lock"
    lock.write_text(
        f"# sha256  file  url\n\n{sha}  model.onnx  {url}\n", encoding="utf-8"
    )
    return lock


def _source(tmp_path: Path, data: bytes = PAYLOAD) -> str:
    source = tmp_path / "release" / "model.onnx"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(data)
    return source.as_uri()


def _missing(tmp_path: Path) -> str:
    return (tmp_path / "nowhere" / "model.onnx").as_uri()


class TestCacheDir:
    """One cache per user, shared with OxidANT's fetch-models.sh."""

    def test_env_wins(self, cache: Path) -> None:
        """KATAGLYPHIS_MODEL_CACHE overrides every default."""
        assert fetch_models.cache_dir() == cache

    def test_platform_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """LOCALAPPDATA on Windows, XDG_CACHE_HOME elsewhere."""
        monkeypatch.delenv("KATAGLYPHIS_MODEL_CACHE", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        base = tmp_path / ("local" if sys.platform == "win32" else "xdg")
        assert fetch_models.cache_dir() == base / "kataglyphis-models"


class TestFetch:
    """Each file lands checked, from the cache when it holds the checksum."""

    def test_downloads_into_dest_and_cache(self, cache: Path, tmp_path: Path) -> None:
        """A fresh fetch writes the target and keeps the bytes under their checksum."""
        dest = tmp_path / "models"
        code = fetch_models.main(
            ["--lock", str(_lock(tmp_path, _source(tmp_path))), "--dest", str(dest)]
        )
        assert code == 0
        assert (dest / "model.onnx").read_bytes() == PAYLOAD
        assert (cache / SHA).read_bytes() == PAYLOAD
        assert not list(dest.glob("*.part"))
        assert not list(cache.glob(".part.*"))
        if os.name == "posix":
            assert stat.S_IMODE((dest / "model.onnx").stat().st_mode) == 0o644

    def test_sha_mismatch_fails_and_writes_nothing(
        self, cache: Path, tmp_path: Path
    ) -> None:
        """Bytes that are not the lock's never reach the target or the cache."""
        dest = tmp_path / "models"
        lock = _lock(tmp_path, _source(tmp_path, b"tampered\n"))
        assert fetch_models.main(["--lock", str(lock), "--dest", str(dest)]) == 1
        assert not (dest / "model.onnx").exists()
        assert list(cache.iterdir()) == []

    def test_cache_hit_skips_the_download(self, cache: Path, tmp_path: Path) -> None:
        """A cached file with the right checksum is copied; the URL is never opened."""
        cache.mkdir()
        (cache / SHA).write_bytes(PAYLOAD)
        dest = tmp_path / "models"
        written = fetch_models.fetch(_lock(tmp_path, _missing(tmp_path)), dest, cache)
        assert written == [dest / "model.onnx"]
        assert (dest / "model.onnx").read_bytes() == PAYLOAD

    def test_corrupt_cache_entry_is_downloaded_again(
        self, cache: Path, tmp_path: Path
    ) -> None:
        """A cache entry whose bytes changed is not trusted."""
        cache.mkdir()
        (cache / SHA).write_bytes(b"bit rot\n")
        dest = tmp_path / "models"
        fetch_models.fetch(_lock(tmp_path, _source(tmp_path)), dest, cache)
        assert (cache / SHA).read_bytes() == PAYLOAD
        assert (dest / "model.onnx").read_bytes() == PAYLOAD

    def test_correct_target_is_kept(self, cache: Path, tmp_path: Path) -> None:
        """A target that already matches is left alone: no download, no copy."""
        dest = tmp_path / "models"
        dest.mkdir()
        (dest / "model.onnx").write_bytes(PAYLOAD)
        before = (dest / "model.onnx").stat()
        written = fetch_models.fetch(_lock(tmp_path, _missing(tmp_path)), dest, cache)
        assert written == []
        after = (dest / "model.onnx").stat()
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
        assert list(cache.iterdir()) == []

    def test_wrong_target_is_replaced(self, cache: Path, tmp_path: Path) -> None:
        """A stale target, an older model say, is overwritten with the lock's file."""
        dest = tmp_path / "models"
        dest.mkdir()
        (dest / "model.onnx").write_bytes(b"older model\n")
        fetch_models.fetch(_lock(tmp_path, _source(tmp_path)), dest, cache)
        assert (dest / "model.onnx").read_bytes() == PAYLOAD

    @pytest.mark.usefixtures("cache")
    def test_unreadable_url_fails_after_retries(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A URL that cannot be read is retried, then fails with exit code 1."""
        monkeypatch.setattr(fetch_models, "RETRY_DELAY_SECONDS", 0.0)
        lock = _lock(tmp_path, _missing(tmp_path))
        dest = tmp_path / "models"
        assert fetch_models.main(["--lock", str(lock), "--dest", str(dest)]) == 1
        assert not (dest / "model.onnx").exists()


class TestLockAndArguments:
    """The lock's format and the command line."""

    @pytest.mark.usefixtures("cache")
    def test_dest_defaults_to_the_locks_directory(self, tmp_path: Path) -> None:
        """Without --dest the files land beside the lock, as with fetch-models.sh."""
        lock = _lock(tmp_path, _source(tmp_path))
        assert fetch_models.main(["--lock", str(lock)]) == 0
        assert (tmp_path / "model.onnx").read_bytes() == PAYLOAD

    def test_missing_lock_is_a_usage_error(self, tmp_path: Path) -> None:
        """Exit code 2, as with fetch-models.sh."""
        assert fetch_models.main(["--lock", str(tmp_path / "absent.lock")]) == 2

    @pytest.mark.parametrize(
        "row",
        [f"{SHA}  model.onnx", f"{SHA}  ../model.onnx  file:///x"],
    )
    def test_malformed_rows_are_refused(self, tmp_path: Path, row: str) -> None:
        """A row without three fields, or naming a path, is refused before any download."""
        lock = tmp_path / "models.lock"
        lock.write_text(row + "\n", encoding="utf-8")
        with pytest.raises(fetch_models.FetchError):
            fetch_models.read_lock(lock)

    def test_repo_lock_carries_the_default_model(self) -> None:
        """The tracked lock names the model paths.py loads, with its LICENSE and NOTICE."""
        rows = fetch_models.read_lock(fetch_models.DEFAULT_LOCK)
        names = {name for _sha, name, _url in rows}
        stem = Path(paths.MODEL_NAME).stem
        assert names == {paths.MODEL_NAME, f"{stem}.LICENSE", f"{stem}.NOTICE"}
        assert all(url.startswith("https://") for _sha, _name, url in rows)
