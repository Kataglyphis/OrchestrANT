"""The index: incremental, read-only on the tree, and it never forgets a share that only looks empty."""

from __future__ import annotations

import os
import sqlite3
import sys
from typing import TYPE_CHECKING

import pytest

from orchestrant.documents import index as index_module
from orchestrant.documents.index import (
    DocumentIndex,
    DocumentIndexError,
    IndexLimits,
    match_query,
    split_chunks,
)


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Write a small tree with a German and an English file."""
    root = tmp_path / "share"
    (root / "sub").mkdir(parents=True)
    (root / "rechnung.txt").write_text(
        "Die Rechnungsnummer lautet 4711 für Müller.", encoding="utf-8"
    )
    (root / "sub" / "note.md").write_text("The invoice is overdue.", encoding="utf-8")
    return root


@pytest.fixture
def index(tmp_path: Path) -> Iterator[DocumentIndex]:
    """Open an index outside the tree."""
    opened = DocumentIndex(tmp_path / "index.db")
    yield opened
    opened.close()


def test_a_german_prefix_and_a_folded_umlaut_find_their_chunk(
    index: DocumentIndex, tree: Path
) -> None:
    """A german prefix and a folded umlaut find their chunk."""
    index.update(tree)
    assert [hit.path for hit in index.search("Rechnung")] == ["rechnung.txt"]
    assert [hit.path for hit in index.search("muller")] == ["rechnung.txt"]
    assert [hit.path for hit in index.search("invoice")] == ["sub/note.md"]


def test_paths_stay_relative_so_no_host_is_stored(
    index: DocumentIndex, tree: Path
) -> None:
    """Paths stay relative so no host is stored."""
    index.update(tree)
    assert index.search("invoice")[0].locator() == "sub/note.md"


@pytest.mark.parametrize(
    "query", ['foo" OR "bar', "NEAR(a b)", "text:x", "a AND", "*", "(", '"']
)
def test_a_question_cannot_speak_fts5(
    index: DocumentIndex, tree: Path, query: str
) -> None:
    """A question cannot speak fts5."""
    index.update(tree)
    index.search(query)


def test_a_query_without_a_word_finds_nothing(index: DocumentIndex, tree: Path) -> None:
    """A query without a word finds nothing."""
    index.update(tree)
    assert index.search("?!") == []
    assert match_query("?!") == ""


@pytest.mark.parametrize("top_k", [0, -1])
def test_a_count_below_one_is_refused_not_read_as_none_or_all(
    index: DocumentIndex, tree: Path, top_k: int
) -> None:
    """SQLite takes a negative LIMIT as no limit, and 0 read like an index without the answer."""
    index.update(tree)
    with pytest.raises(DocumentIndexError, match="top_k must be at least 1"):
        index.search("invoice", top_k)
    assert len(index.search("invoice", 1)) == 1


def test_an_unchanged_file_is_not_read_again(
    index: DocumentIndex, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unchanged file is not read again."""
    index.update(tree)

    def refuse(_path: object) -> None:
        pytest.fail("an unchanged file was extracted again")

    monkeypatch.setattr("orchestrant.documents.index.extract_document", refuse)
    report = index.update(tree)
    assert report.unchanged == 2
    assert not report.statuses


def test_a_changed_file_replaces_its_chunks(index: DocumentIndex, tree: Path) -> None:
    """A changed file replaces its chunks."""
    index.update(tree)
    path = tree / "sub" / "note.md"
    path.write_text("Die Mahnung kam, sonst nichts.", encoding="utf-8")
    os.utime(path, (1, 1))
    index.update(tree)
    assert index.search("invoice") == []
    assert [hit.path for hit in index.search("Mahnung")] == ["sub/note.md"]


def test_a_deleted_file_leaves_the_index(index: DocumentIndex, tree: Path) -> None:
    """A deleted file leaves the index."""
    index.update(tree)
    tree.joinpath("sub", "note.md").unlink()
    assert index.update(tree).removed == 1
    assert index.search("invoice") == []


def test_an_empty_tree_cannot_empty_the_index(index: DocumentIndex, tree: Path) -> None:
    """An empty tree cannot empty the index."""
    index.update(tree)
    tree.joinpath("sub", "note.md").unlink()
    tree.joinpath("rechnung.txt").unlink()
    report = index.update(tree)
    assert "empty" in report.purge_refused
    assert report.removed == 0
    assert index.search("invoice")
    assert index.update(tree, IndexLimits(allow_empty=True)).removed == 2
    assert index.search("invoice") == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_an_unreadable_directory_cannot_empty_its_part_of_the_index(
    index: DocumentIndex, tree: Path
) -> None:
    """An unreadable directory cannot empty its part of the index."""
    index.update(tree)
    sub = tree / "sub"
    sub.chmod(0)
    try:
        if os.access(sub, os.R_OK):
            pytest.skip("this user reads every directory")
        report = index.update(tree)
    finally:
        sub.chmod(0o755)
    assert report.walk_errors
    assert report.purge_refused
    assert index.search("invoice")


def test_a_cut_walk_cannot_purge(index: DocumentIndex, tree: Path) -> None:
    """A cut walk cannot purge."""
    index.update(tree)
    report = index.update(tree, IndexLimits(max_files=1))
    assert report.truncated
    assert report.purge_refused
    assert report.removed == 0


def test_a_broken_file_is_reported_and_retried(
    index: DocumentIndex, tree: Path
) -> None:
    """A broken file is reported and retried."""
    (tree / "kaputt.docx").write_bytes(b"no zip")
    report = index.update(tree)
    assert report.statuses["error"] == 1
    assert report.file_errors[0]["path"] == "kaputt.docx"
    assert index.update(tree).statuses["error"] == 1


def test_a_big_file_is_not_read(index: DocumentIndex, tree: Path) -> None:
    """A big file is not read."""
    report = index.update(tree, IndexLimits(max_file_bytes=4))
    assert report.statuses["too_large"] == 2
    assert index.search("invoice") == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
def test_a_link_out_of_the_tree_is_not_followed(
    index: DocumentIndex, tree: Path, tmp_path: Path
) -> None:
    """A link out of the tree is not followed."""
    secret = tmp_path / "secret.txt"
    secret.write_text("Geheimnis", encoding="utf-8")
    (tree / "link.txt").symlink_to(secret)
    outside = tmp_path / "aussen"
    outside.mkdir()
    (outside / "b.txt").write_text("Geheimnis", encoding="utf-8")
    (tree / "dirlink").symlink_to(outside)
    report = index.update(tree)
    assert report.links_skipped == 1
    assert index.search("Geheimnis") == []


def test_snapshot_trees_are_skipped(index: DocumentIndex, tree: Path) -> None:
    """Snapshot trees are skipped."""
    snapshot = tree / "#recycle"
    snapshot.mkdir()
    (snapshot / "alt.txt").write_text("Papierkorb", encoding="utf-8")
    index.update(tree)
    assert index.search("Papierkorb") == []


def test_a_tree_is_never_written(index: DocumentIndex, tree: Path) -> None:
    """A tree is never written."""
    before = sorted(tree.rglob("*"))
    index.update(tree)
    assert sorted(tree.rglob("*")) == before


def test_the_index_cannot_live_on_the_tree(tree: Path) -> None:
    """The index cannot live on the tree."""
    inside = DocumentIndex(tree / "index.db")
    try:
        with pytest.raises(DocumentIndexError, match="must not live inside"):
            inside.update(tree)
    finally:
        inside.close()


def test_one_index_holds_one_tree(
    index: DocumentIndex, tree: Path, tmp_path: Path
) -> None:
    """One index holds one tree."""
    other = tmp_path / "other"
    other.mkdir()
    index.update(tree)
    with pytest.raises(DocumentIndexError, match="new index file"):
        index.update(other)
    assert index.search("invoice")


def test_a_missing_root_is_an_error(index: DocumentIndex, tmp_path: Path) -> None:
    """A missing root is an error."""
    with pytest.raises(DocumentIndexError, match="not a directory"):
        index.update(tmp_path / "nothing")


@pytest.mark.parametrize(
    ("foreign", "named"),
    [("not_sqlite", "file is not a database"), ("other_schema", "has schema 0")],
    ids=["not_sqlite", "other_schema"],
)
def test_a_file_that_is_no_index_is_refused_and_closed(
    foreign: str, named: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file SQLite cannot read raised sqlite3.DatabaseError, and neither refusal closed its connection."""
    db = tmp_path / "i.db"
    if foreign == "not_sqlite":
        db.write_text("not a database", encoding="utf-8")
    else:
        DocumentIndex(db).close()
        other = sqlite3.connect(db)
        with other:
            other.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '0')")
        other.close()
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def recording(path: Path) -> sqlite3.Connection:
        opened.append(real_connect(path))
        return opened[-1]

    monkeypatch.setattr(sqlite3, "connect", recording)
    with pytest.raises(DocumentIndexError, match=named):
        DocumentIndex(db)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux keeps raw name bytes")
def test_a_file_name_sqlite_cannot_store_is_reported(
    index: DocumentIndex, tree: Path
) -> None:
    """A file name sqlite cannot store is reported."""
    name = os.fsdecode(b"bad\xe4.txt")
    try:
        (tree / name).write_text("Inhalt", encoding="utf-8")
    except OSError:
        pytest.skip("this filesystem refuses the name")
    report = index.update(tree)
    assert any("not UTF-8" in row["error"] for row in report.file_errors)


def test_chunks_overlap_and_lose_nothing() -> None:
    """Chunks overlap and lose nothing."""
    words = " ".join(f"wort{n}" for n in range(2000))
    chunks = split_chunks(words, size=300, overlap=50)
    assert all(len(chunk) <= 300 for chunk in chunks)
    joined = " ".join(chunks)
    assert all(f"wort{n}" in joined for n in range(2000))
    assert chunks[0][-20:].split()[-1] in chunks[1]


def test_text_without_a_space_still_ends() -> None:
    """Text without a space still ends."""
    assert len(split_chunks("x" * 1000, size=300, overlap=50)) == 4


def test_a_bad_overlap_is_refused() -> None:
    """A bad overlap is refused."""
    with pytest.raises(ValueError, match="overlap"):
        split_chunks("x", size=10, overlap=10)


def _files_in(db: Path) -> int:
    """Count the files a second connection sees committed."""
    other = sqlite3.connect(db)
    try:
        return other.execute("SELECT count(*) FROM file").fetchone()[0]
    finally:
        other.close()


def test_files_are_committed_in_batches_that_progress_hears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A share of thousands of photos paid a disk sync per file; now a batch commits and the CLI hears it."""
    root = tmp_path / "share"
    root.mkdir()
    for n in range(5):
        (root / f"f{n}.txt").write_text(f"Datei {n}", encoding="utf-8")
    monkeypatch.setattr(index_module, "BATCH_FILES", 2)
    monkeypatch.setattr(index_module, "BATCH_SECONDS", 3600.0)
    db = tmp_path / "index.db"
    heard: list[tuple[int, int]] = []
    opened = DocumentIndex(db)
    try:
        opened.update(
            root, progress=lambda seen, _report: heard.append((seen, _files_in(db)))
        )
    finally:
        opened.close()
    assert heard == [(2, 2), (4, 4), (5, 5)]


def test_an_interrupted_walk_keeps_the_files_it_stored_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Ctrl-C mid-walk keeps every finished file, and the next walk skips them as unchanged."""
    root = tmp_path / "share"
    root.mkdir()
    for n in range(4):
        (root / f"f{n}.txt").write_text(f"Datei {n}", encoding="utf-8")
    real = index_module.extract_document

    def stop_at_the_third(path: Path) -> object:
        if path.name == "f2.txt":
            raise KeyboardInterrupt
        return real(path)

    monkeypatch.setattr(index_module, "extract_document", stop_at_the_third)
    db = tmp_path / "index.db"
    opened = DocumentIndex(db)
    with pytest.raises(KeyboardInterrupt):
        opened.update(root)
    opened.close()
    assert _files_in(db) == 2
    monkeypatch.setattr(index_module, "extract_document", real)
    again = DocumentIndex(db)
    try:
        report = again.update(root)
    finally:
        again.close()
    assert (report.unchanged, _files_in(db)) == (2, 4)
