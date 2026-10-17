"""Fetch every file resources/models/models.lock names, checked by SHA-256, through a cache.

Usage: python scripts/fetch_models.py [--dest DIR] [--lock FILE]
The cache is $KATAGLYPHIS_MODEL_CACHE, else kataglyphis-models under
%LOCALAPPDATA% on Windows or $XDG_CACHE_HOME (~/.cache) elsewhere.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "resources" / "models" / "models.lock"
ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5.0
TIMEOUT_SECONDS = 60.0
_CHUNK = 1 << 20


class FetchError(Exception):
    """A file could not be downloaded, or its SHA-256 is not the lock's."""


def cache_dir() -> Path:
    """Where downloads are kept by checksum, shared with OxidANT's fetch-models.sh."""
    env = os.getenv("KATAGLYPHIS_MODEL_CACHE")
    if env:
        return Path(env)
    if sys.platform == "win32":
        local = os.getenv("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "kataglyphis-models"
    xdg = os.getenv("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "kataglyphis-models"


def read_lock(lock: Path) -> list[tuple[str, str, str]]:
    """The lock's (sha256, file, url) rows; blank lines and comments are skipped."""
    rows: list[tuple[str, str, str]] = []
    for line in lock.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        if len(fields) != 3:
            msg = f"{lock}: expected 'sha256 file url', got {line!r}"
            raise FetchError(msg)
        sha, name, url = fields
        if Path(name).name != name:
            msg = f"{lock}: {name!r} is not a plain file name"
            raise FetchError(msg)
        rows.append((sha.lower(), name, url))
    return rows


def sha256_of(path: Path) -> str:
    """The file's SHA-256 as lowercase hex."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _matches(path: Path, sha: str) -> bool:
    return path.is_file() and sha256_of(path) == sha


def _download(url: str, part: Path) -> None:
    if not url.startswith(("https://", "http://", "file:")):
        msg = f"{url}: only https, http and file URLs are fetched"
        raise FetchError(msg)
    for attempt in range(1, ATTEMPTS + 1):
        try:
            # The scheme is checked above; the lock's SHA-256 checks the bytes.
            with (
                urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response,  # noqa: S310  # nosec B310
                part.open("wb") as out,
            ):
                shutil.copyfileobj(response, out, _CHUNK)
        except OSError as exc:
            if attempt == ATTEMPTS:
                msg = f"download failed: {url}: {exc}"
                raise FetchError(msg) from exc
            time.sleep(RETRY_DELAY_SECONDS * attempt)
        else:
            return


def _into_cache(sha: str, url: str, cache: Path) -> Path:
    cached = cache / sha
    if _matches(cached, sha):
        return cached
    handle, name = tempfile.mkstemp(prefix=".part.", dir=cache)
    os.close(handle)
    part = Path(name)
    try:
        _download(url, part)
        got = sha256_of(part)
        if got != sha:
            msg = f"{url}: sha256 {got}, the lock says {sha}"
            raise FetchError(msg)
        part.replace(cached)
    finally:
        part.unlink(missing_ok=True)
    return cached


def fetch(lock: Path, dest: Path, cache: Path) -> list[Path]:
    """Put every file the lock names into dest; returns the ones it had to write."""
    rows = read_lock(lock)
    dest.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for sha, name, url in rows:
        target = dest / name
        if _matches(target, sha):
            continue
        cached = _into_cache(sha, url, cache)
        part = target.with_name(target.name + ".part")
        shutil.copyfile(cached, part)
        # mkstemp's 0600 would follow the copy, and a service user must read the model.
        part.chmod(0o644)
        part.replace(target)
        sys.stdout.write(f"fetched {target} (sha256 {sha[:12]})\n")
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    """Command line entry point; 1 on a failed download or checksum, 2 on bad arguments."""
    parser = argparse.ArgumentParser(
        description="Fetch the models models.lock names, checked by SHA-256."
    )
    parser.add_argument("--dest", type=Path, help="default: the lock's directory")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    args = parser.parse_args(argv)
    if not args.lock.is_file():
        sys.stderr.write(f"no lock file {args.lock}\n")
        return 2
    dest = args.dest if args.dest is not None else args.lock.parent
    try:
        fetch(args.lock, dest, cache_dir())
    except FetchError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
