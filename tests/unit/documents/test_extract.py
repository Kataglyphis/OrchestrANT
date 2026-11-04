"""Text out of files, with no model: every file that gives none says why."""

from __future__ import annotations

import zipfile
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from orchestrant.documents import extract
from orchestrant.documents.extract import (
    decode_text,
    extract_document,
    extract_docx_text,
    extract_pdf,
)


if TYPE_CHECKING:
    from pathlib import Path

DOCX = '<w:document xmlns:w="u"><w:t>Hallo</w:t><w:t>Welt</w:t></w:document>'


def write_docx(path: Path, body: str = DOCX) -> Path:
    """Write a minimal docx."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", body)
    return path


def test_a_docx_gives_its_runs(tmp_path: Path) -> None:
    """A docx gives its runs."""
    found = extract_document(write_docx(tmp_path / "a.DOCX"))
    assert (found.status, found.text()) == ("ok", "Hallo Welt")


def test_old_german_text_decodes(tmp_path: Path) -> None:
    """Old german text decodes."""
    path = tmp_path / "a.txt"
    path.write_bytes("Größe".encode("cp1252"))
    assert extract_document(path).text() == "Größe"


def test_a_utf8_bom_is_no_text() -> None:
    """A utf8 bom is no text."""
    assert decode_text(b"\xef\xbb\xbfhallo") == "hallo"


@pytest.mark.parametrize(
    ("name", "raw", "status"),
    [
        ("a.txt", b"bin\x00ary", "error"),
        ("a.txt", b"  \n", "empty"),
        ("a.docx", b"no zip", "error"),
        ("a.xyz", b"data", "unsupported"),
        ("a.jpg", b"data", "unsupported"),
    ],
)
def test_a_file_without_text_says_so(
    tmp_path: Path, name: str, raw: bytes, status: str
) -> None:
    """A file without text says so."""
    path = tmp_path / name
    path.write_bytes(raw)
    found = extract_document(path)
    assert found.status == status
    assert found.sections == []


def test_a_missing_file_is_an_error_not_an_exception(tmp_path: Path) -> None:
    """A missing file is an error not an exception."""
    assert extract_document(tmp_path / "a.txt").status == "error"


def test_a_member_expanding_past_the_bound_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member expanding past the bound is refused."""
    monkeypatch.setattr(extract, "MAX_MEMBER_BYTES", 16)
    assert extract_docx_text(write_docx(tmp_path / "a.docx")) is None


def _pdf(pages: list[tuple[str, list[float]]], **flags: bool) -> extract.PdfLibrary:
    """Pdf."""

    def page(text: str, boxes: list[float]) -> SimpleNamespace:
        return SimpleNamespace(
            rect=SimpleNamespace(width=1.0, height=1.0),
            get_text=lambda: text,
            get_image_info=lambda: [{"bbox": (0, 0, side, side)} for side in boxes],
        )

    doc = SimpleNamespace(
        page_count=len(pages),
        load_page=lambda number: page(*pages[number]),
        close=lambda: None,
        **flags,
    )
    return cast("extract.PdfLibrary", SimpleNamespace(open=lambda _path: doc))


LONG = "Eine lange Seite mit genug Text für eine echte Textebene hier."


def test_a_pdf_keeps_page_numbers_and_reports_its_scans() -> None:
    """A pdf keeps page numbers and reports its scans."""
    library = _pdf([(LONG, []), ("", [1.0]), ("kurz", [])])
    found = extract_pdf("a.pdf", library)
    assert found.status == "needs_ocr"
    assert found.sections == [(1, LONG), (3, "kurz")]
    assert "1 of 3" in found.detail


def test_a_degenerate_layer_counts_as_a_scan_and_stays_out() -> None:
    """A degenerate layer counts as a scan and stays out."""
    library = _pdf([("\x00\x00\x00" + "x" * 60, [])])
    found = extract_pdf("a.pdf", library)
    assert (found.status, found.sections) == ("needs_ocr", [])


def test_a_clean_pdf_is_ok_and_a_blank_one_is_empty() -> None:
    """A clean pdf is ok and a blank one is empty."""
    assert extract_pdf("a.pdf", _pdf([(LONG, [])])).status == "ok"
    assert extract_pdf("a.pdf", _pdf([("", [])])).status == "empty"


def test_an_encrypted_pdf_is_an_error() -> None:
    """An encrypted pdf is an error."""
    found = extract_pdf("a.pdf", _pdf([(LONG, [])], needs_pass=True))
    assert (found.status, found.detail) == ("error", "pdf is encrypted")


def test_a_pdf_that_will_not_open_is_an_error() -> None:
    """A pdf that will not open is an error."""

    def refuse(_path: str) -> None:
        msg = "broken"
        raise RuntimeError(msg)

    found = extract_pdf("a.pdf", cast("Any", SimpleNamespace(open=refuse)))
    assert found.status == "error"
    assert "broken" in found.detail


def test_without_pymupdf_a_pdf_is_visibly_unsupported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without pymupdf a pdf is visibly unsupported."""
    monkeypatch.setattr(extract, "_load_fitz", lambda: None)
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF")
    found = extract_document(path)
    assert found.status == "unsupported"
    assert "PyMuPDF" in found.detail
