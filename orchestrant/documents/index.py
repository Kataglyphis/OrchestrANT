"""A local full-text index of a document tree: read-only on the tree, one SQLite file beside the caller."""

from __future__ import annotations

import fnmatch
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrant.documents.extract import extract_document


if TYPE_CHECKING:
    from collections.abc import Iterator

SCHEMA_VERSION = "1"
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150
MAX_QUERY_TERMS = 32
SHOWN_ERRORS = 20
# NAS snapshot, recycle and thumbnail trees hold copies that would only repeat every answer.
DEFAULT_EXCLUDES = (
    "@eaDir",
    "#recycle",
    "#snapshot",
    ".snapshot",
    ".recycle",
    ".Trash-*",
    ".git",
    "$RECYCLE.BIN",
    "System Volume Information",
    "lost+found",
)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS file(
    path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, status TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS chunk(
    id INTEGER PRIMARY KEY, path TEXT NOT NULL, page INTEGER, ordinal INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS chunk_path ON chunk(path);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_text
    USING fts5(text, tokenize = "unicode61 remove_diacritics 2");
"""
_WORD = re.compile(r"\w+")


class DocumentIndexError(RuntimeError):
    """The index cannot be used as asked; the message says why."""


@dataclass(frozen=True)
class IndexLimits:
    """What one walk may read; ``max_files`` 0 means every file."""

    max_file_bytes: int = 256 * 1024 * 1024
    max_files: int = 0
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES
    allow_empty: bool = False


@dataclass
class IndexReport:
    """One walk's counts by file status, what it dropped, and what it refused to drop."""

    root: str = ""
    statuses: Counter[str] = field(default_factory=Counter)
    removed: int = 0
    unchanged: int = 0
    links_skipped: int = 0
    truncated: bool = False
    walk_errors: list[str] = field(default_factory=list)
    file_errors: list[dict[str, str]] = field(default_factory=list)
    purge_refused: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON a command prints."""
        return {
            "root": self.root,
            "statuses": dict(sorted(self.statuses.items())),
            "unchanged": self.unchanged,
            "removed": self.removed,
            "links_skipped": self.links_skipped,
            "truncated": self.truncated,
            "walk_errors": self.walk_errors[:SHOWN_ERRORS],
            "file_errors": self.file_errors[:SHOWN_ERRORS],
            "purge_refused": self.purge_refused,
        }


@dataclass(frozen=True)
class Hit:
    """One retrieved chunk: where it came from and its text."""

    path: str
    page: int | None
    ordinal: int
    score: float
    text: str

    def locator(self) -> str:
        """Return the file, with its page when it has one."""
        return f"{self.path} p.{self.page}" if self.page else self.path


def split_chunks(
    text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Cut ``text`` into overlapping pieces, at a whitespace where one is near the end."""
    if overlap >= size:
        msg = f"overlap {overlap} must be under size {size}"
        raise ValueError(msg)
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            space = text.rfind(" ", start + size // 2, end)
            end = space if space > 0 else end
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def match_query(query: str) -> str:
    """Return ``query`` as an FTS5 expression of quoted terms, so a question cannot speak FTS5's syntax."""
    terms = list(dict.fromkeys(_WORD.findall(query)))[:MAX_QUERY_TERMS]
    # A prefix finds German compounds: "rechnung" reaches "Rechnungsnummer".
    return " OR ".join(f'"{t}"*' if len(t) >= 4 else f'"{t}"' for t in terms)


def _excluded(name: str, excludes: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in excludes)


def _utf8(name: str) -> bool:
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


class DocumentIndex:
    """One SQLite index; a walk only ever reads the tree it indexes."""

    def __init__(self, db_path: str | Path) -> None:
        """Open or create the index at ``db_path``."""
        self.db_path = Path(db_path)
        self._db = sqlite3.connect(self.db_path)
        try:
            with self._db:
                self._db.executescript(_SCHEMA)
        except sqlite3.OperationalError as exc:
            self._db.close()
            msg = f"{self.db_path}: {exc}; this needs SQLite with FTS5"
            raise DocumentIndexError(msg) from exc
        self._check_schema()

    def _meta(self, key: str) -> str | None:
        row = self._db.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def _check_schema(self) -> None:
        found = self._meta("schema")
        if found not in {None, SCHEMA_VERSION}:
            msg = f"{self.db_path} has schema {found}; this reads {SCHEMA_VERSION}"
            raise DocumentIndexError(msg)

    def close(self) -> None:
        """Close the database."""
        self._db.close()

    def _bind_root(self, root: Path) -> None:
        if self.db_path.resolve().is_relative_to(root):
            msg = f"the index {self.db_path} must not live inside the tree {root}"
            raise DocumentIndexError(msg)
        bound = self._meta("root")
        if bound is not None and bound != str(root):
            msg = f"{self.db_path} indexes {bound}, not {root}; use a new index file"
            raise DocumentIndexError(msg)
        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO meta VALUES (?, ?)",
                [("root", str(root)), ("schema", SCHEMA_VERSION)],
            )

    def _forget(self, path: str) -> None:
        self._db.execute(
            "DELETE FROM chunk_text WHERE rowid IN "
            "(SELECT id FROM chunk WHERE path = ?)",
            (path,),
        )
        self._db.execute("DELETE FROM chunk WHERE path = ?", (path,))

    def _store(self, path: str, stat: os.stat_result, status: str, detail: str, sections: list[tuple[int | None, str]]) -> None:  # fmt: skip
        with self._db:
            self._forget(path)
            self._db.execute(
                "INSERT OR REPLACE INTO file VALUES (?, ?, ?, ?, ?)",
                (path, stat.st_size, stat.st_mtime_ns, status, detail),
            )
            ordinal = 0
            for page, text in sections:
                for piece in split_chunks(text):
                    row = self._db.execute(
                        "INSERT INTO chunk(path, page, ordinal) VALUES (?, ?, ?)",
                        (path, page, ordinal),
                    )
                    self._db.execute(
                        "INSERT INTO chunk_text(rowid, text) VALUES (?, ?)",
                        (row.lastrowid, piece),
                    )
                    ordinal += 1

    def _unchanged(self, path: str, stat: os.stat_result) -> bool:
        row = self._db.execute(
            "SELECT size, mtime_ns, status FROM file WHERE path = ?", (path,)
        ).fetchone()
        return (
            row == (stat.st_size, stat.st_mtime_ns, row[2]) and row[2] != "error"
            if row
            else False
        )

    def _index_file(
        self, path: Path, name: str, limits: IndexLimits, report: IndexReport
    ) -> None:
        try:
            stat = path.lstat()
        except OSError as exc:
            report.statuses["error"] += 1
            report.file_errors.append({"path": name, "error": f"stat failed: {exc}"})
            return
        if self._unchanged(name, stat):
            report.unchanged += 1
            return
        if stat.st_size > limits.max_file_bytes:
            status, detail, sections = "too_large", f"{stat.st_size} bytes", []
        else:
            found = extract_document(path)
            status, detail, sections = found.status, found.detail, found.sections
        report.statuses[status] += 1
        if status == "error":
            report.file_errors.append({"path": name, "error": detail})
        self._store(name, stat, status, detail, sections)

    def _walk(self, root: Path, limits: IndexLimits, report: IndexReport) -> Iterator[tuple[Path, str]]:  # fmt: skip
        def failed(error: OSError) -> None:
            report.walk_errors.append(str(error))

        for current, dirs, files in os.walk(root, onerror=failed, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not _excluded(d, limits.excludes))
            for name in sorted(files):
                path = Path(current) / name
                relative = path.relative_to(root).as_posix()
                if path.is_symlink():
                    report.links_skipped += 1
                elif not _utf8(relative):
                    report.statuses["error"] += 1
                    report.file_errors.append(
                        {"path": ascii(relative), "error": "file name is not UTF-8"}
                    )
                else:
                    yield path, relative

    def update(
        self, root: str | Path, limits: IndexLimits | None = None
    ) -> IndexReport:
        """Bring the index up to the tree: new and changed files in, gone files out, unless the walk cannot be trusted."""
        limits = limits or IndexLimits()
        root = Path(root).resolve()
        if not root.is_dir():
            msg = f"{root} is not a directory"
            raise DocumentIndexError(msg)
        self._bind_root(root)
        report = IndexReport(root=str(root))
        seen: set[str] = set()
        for path, name in self._walk(root, limits, report):
            if limits.max_files and len(seen) >= limits.max_files:
                report.truncated = True
                break
            seen.add(name)
            self._index_file(path, name, limits, report)
        self._purge(seen, limits, report)
        return report

    def _purge(self, seen: set[str], limits: IndexLimits, report: IndexReport) -> None:
        known = {row[0] for row in self._db.execute("SELECT path FROM file")}
        gone = known - seen
        if not gone:
            return
        report.purge_refused = _purge_refusal(seen, limits, report)
        if report.purge_refused:
            return
        with self._db:
            for path in gone:
                self._forget(path)
                self._db.execute("DELETE FROM file WHERE path = ?", (path,))
        report.removed = len(gone)

    def search(self, query: str, top_k: int = 5) -> list[Hit]:
        """Return the best chunks by BM25; a query without a word finds nothing."""
        expression = match_query(query)
        if not expression:
            return []
        rows = self._db.execute(
            "SELECT c.path, c.page, c.ordinal, bm25(chunk_text), t.text "
            "FROM chunk_text AS t JOIN chunk AS c ON c.id = t.rowid "
            "WHERE chunk_text MATCH ? ORDER BY bm25(chunk_text), c.path, c.ordinal "
            "LIMIT ?",
            (expression, top_k),
        ).fetchall()
        return [Hit(row[0], row[1], row[2], -row[3], row[4]) for row in rows]


def _purge_refusal(seen: set[str], limits: IndexLimits, report: IndexReport) -> str:
    """Return why the missing files must stay: an unmounted share looks exactly like a tree that was emptied."""
    if report.walk_errors:
        return "the walk hit unreadable directories"
    if report.truncated:
        return "--max-files cut the walk short"
    if not seen and not limits.allow_empty:
        return "the tree is empty; an unmounted share looks the same (--allow-empty)"
    return ""
