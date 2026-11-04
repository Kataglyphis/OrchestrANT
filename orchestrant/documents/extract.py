"""Text out of office files, PDFs and plain files, with no model; everything read here is untrusted data.

Standard library only, because ``benchmarks/nas_census.py`` loads this file by path on a bare Python.
"""

from __future__ import annotations

import importlib
import re
import xml.etree.ElementTree as ET  # nosec B405 -- parses the user's own NAS files
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast


# Gates from benchmarks/docs/nas-document-ai.md § 6, as constants the mutation gate can bite
TEXT_CHARS_MIN = 50  # stripped chars for a page to count as born-digital
IMAGE_COVER_MIN = 0.95  # one image covering this fraction of the page = a scan
NUL_RUN_MIN = 3  # a run of this many U+0000 marks a broken text layer
BAD_CHAR_RATIO_MAX = 0.20  # > 20 % U+0000/U+FFFD marks a broken text layer
LANG_HITS_MIN = 3  # fewer stopword hits than this decides nothing
MAX_MEMBER_BYTES = 256 * 1024 * 1024

# lowercased function words; disjoint sets, so a hit is a vote for one side
GERMAN_STOPWORDS = frozenset(
    {
        "der",
        "die",
        "das",
        "und",
        "ist",  # codespell:ignore -- a German stopword, not a typo for "is"
        "nicht",
        "mit",
        "für",
        "auf",
        "ein",
        "eine",
        "den",
        "dem",
        "von",
        "zu",
        "im",
        "sich",
    }
)
ENGLISH_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "is",
        "of",
        "to",
        "in",
        "that",
        "for",
        "with",
        "on",
        "as",
        "this",
        "was",
        "are",
        "be",
        "it",
    }
)

_WORD_RE = re.compile(r"\w+")


def is_degenerate_text(text: str) -> bool:
    """True when a text layer exists but is broken: a NUL run, or too many U+0000/U+FFFD."""
    if not text:
        return False
    if "\x00" * NUL_RUN_MIN in text:
        return True
    bad = text.count("\x00") + text.count("�")
    return bad / len(text) > BAD_CHAR_RATIO_MAX


def classify_pdf_page(text: str, image_fractions: list[float], tagged: object) -> str:
    """One page -> 'born_digital' | 'degenerate' | 'image_only' | 'sparse', in the doc's order.

    `image_fractions` are per image: many small logos tiling a page are no scan.
    """
    if tagged:
        return "born_digital"
    if len(text.strip()) >= TEXT_CHARS_MIN:
        if is_degenerate_text(text):
            return "degenerate"
        return "born_digital"
    if any(frac >= IMAGE_COVER_MIN for frac in image_fractions):
        return "image_only"
    return "sparse"


def detect_language(text: str) -> str | None:
    """'de' | 'en' | None by counting function words: a heuristic that may stay undecided."""
    tokens = _WORD_RE.findall(text.lower())
    de = sum(1 for t in tokens if t in GERMAN_STOPWORDS)
    en = sum(1 for t in tokens if t in ENGLISH_STOPWORDS)
    if de >= LANG_HITS_MIN and de > en:
        return "de"
    if en >= LANG_HITS_MIN and en > de:
        return "en"
    return None


class PdfPage(Protocol):
    """The part of a PyMuPDF page this module reads; the optional library is not always there to annotate from."""

    rect: Any

    def get_text(self) -> str:
        """Return the page's text layer."""

    def get_image_info(self) -> list[dict[str, Any]]:
        """Return one entry per image, with its ``bbox``."""


class PdfDocument(Protocol):
    """The part of a PyMuPDF document this module reads."""

    page_count: int

    def load_page(self, number: int) -> PdfPage:
        """Return one page."""

    def close(self) -> None:
        """Close the file."""


class PdfLibrary(Protocol):
    """The part of PyMuPDF this module calls."""

    def open(self, path: str) -> PdfDocument:
        """Open a PDF."""


def image_fractions(page: PdfPage) -> list[float]:
    """Per-image page-area fractions from get_image_info() bboxes."""
    rect = page.rect
    page_area = abs(rect.width * rect.height)
    if not page_area:
        return []
    fractions = []
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        fractions.append(abs((x1 - x0) * (y1 - y0)) / page_area)
    return fractions


def _local_texts(element: ET.Element, local: str = "t") -> list[str]:
    """Text of every element whose namespace-stripped tag is `local`; iter() has no {*}."""
    return [
        el.text
        for el in element.iter()
        if el.text and el.tag.rpartition("}")[2] == local
    ]


def _read_member(zf: zipfile.ZipFile, name: str) -> bytes:
    """Read one member; a zip bomb's declared size can lie, so the read itself is bounded."""
    with zf.open(name) as stream:
        raw = stream.read(MAX_MEMBER_BYTES + 1)
    if len(raw) > MAX_MEMBER_BYTES:
        msg = f"{name} expands past {MAX_MEMBER_BYTES} bytes"
        raise ValueError(msg)
    return raw


def _parse_member(zf: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(_read_member(zf, name))  # nosec B314  # noqa: S314


def _zip_xml_texts(path: str | Path, members: list[str]) -> str:
    """Concatenated text of every t-run across `members` of a zip."""
    parts = []
    with zipfile.ZipFile(path) as zf:
        for member in members:
            parts.extend(_local_texts(_parse_member(zf, member)))
    return " ".join(parts)


def extract_docx_text(path: str | Path) -> str | None:
    """Text of word/document.xml's w:t runs, or None if the file is broken."""
    try:
        return _zip_xml_texts(path, ["word/document.xml"])
    except Exception:
        return None


def _inline_string_texts(root: ET.Element) -> list[str]:
    """The texts of every inline <is> string of one worksheet."""
    parts = []
    for el in root.iter():
        if el.tag.rpartition("}")[2] == "is":
            parts.extend(_local_texts(el))
    return parts


def extract_xlsx_text(path: str | Path) -> str | None:
    """sharedStrings.xml plus inline <is><t> strings, "" without xl/ members, None if broken."""
    try:
        parts = []
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "xl/sharedStrings.xml" in names:
                parts.extend(_local_texts(_parse_member(zf, "xl/sharedStrings.xml")))
            for name in sorted(names):
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    parts.extend(_inline_string_texts(_parse_member(zf, name)))
        return " ".join(parts)
    except Exception:
        return None


def extract_pptx_text(path: str | Path) -> str | None:
    """a:t runs of every ppt/slides/slide*.xml, or None if broken."""
    try:
        with zipfile.ZipFile(path) as zf:
            slides = sorted(
                n
                for n in zf.namelist()
                if n.startswith("ppt/slides/slide") and n.endswith(".xml")
            )
        return _zip_xml_texts(path, slides)
    except Exception:
        return None


OFFICE_EXTRACTORS = {
    "docx": extract_docx_text,
    "xlsx": extract_xlsx_text,
    "pptx": extract_pptx_text,
}

TEXT_EXTENSIONS = frozenset(
    {"txt", "md", "csv", "tsv", "log", "html", "htm", "eml", "json", "xml"}
)


@dataclass
class Extraction:
    """What one file gave: its ``status``, the ``(page, text)`` sections, and why when it gave less."""

    status: str
    sections: list[tuple[int | None, str]] = field(default_factory=list)
    detail: str = ""

    def text(self) -> str:
        """Return every section as one text."""
        return "\n\n".join(section for _, section in self.sections)


def decode_text(raw: bytes) -> str | None:
    """Return the bytes as UTF-8 or else Windows-1252, the old German default; None for a binary file."""
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def _load_fitz() -> PdfLibrary | None:
    """Return PyMuPDF, an optional AGPL dependency this package never installs, or None."""
    for name in ("pymupdf", "fitz"):
        try:
            return cast("PdfLibrary", importlib.import_module(name))
        except ImportError:
            continue
    return None


def _pdf_sections(doc: PdfDocument) -> Extraction:
    tagged = bool(getattr(doc, "is_tagged", False))
    sections: list[tuple[int | None, str]] = []
    scanned = 0
    for number in range(doc.page_count):
        page = doc.load_page(number)
        text = page.get_text()
        verdict = classify_pdf_page(text, image_fractions(page), tagged)
        if verdict in {"image_only", "degenerate"}:
            scanned += 1
        elif text.strip():
            sections.append((number + 1, text))
    if not scanned:
        return Extraction("ok" if sections else "empty", sections)
    detail = f"{scanned} of {doc.page_count} pages have no usable text layer"
    return Extraction("needs_ocr", sections, detail)


def extract_pdf(path: str | Path, fitz: PdfLibrary | None = None) -> Extraction:
    """Return a PDF's born-digital pages; a scanned page is reported as ``needs_ocr``, never read by a model here."""
    fitz = fitz or _load_fitz()
    if fitz is None:
        return Extraction("unsupported", detail="PDF text needs PyMuPDF (fitz)")
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        return Extraction("error", detail=f"pdf open failed: {exc}")
    try:
        if getattr(doc, "needs_pass", False):
            return Extraction("error", detail="pdf is encrypted")
        return _pdf_sections(doc)
    except Exception as exc:
        return Extraction("error", detail=f"pdf read failed: {exc}")
    finally:
        doc.close()


def extract_document(path: str | Path, fitz: PdfLibrary | None = None) -> Extraction:
    """Return the text of one file by its extension; what cannot be read says why."""
    path = Path(path)
    ext = path.suffix.lstrip(".").lower()
    if ext == "pdf":
        return extract_pdf(path, fitz)
    if ext in OFFICE_EXTRACTORS:
        text = OFFICE_EXTRACTORS[ext](path)
        if text is None:
            return Extraction("error", detail=f"{ext} text extraction failed")
    elif ext in TEXT_EXTENSIONS:
        try:
            text = decode_text(path.read_bytes())
        except OSError as exc:
            return Extraction("error", detail=f"read failed: {exc}")
        if text is None:
            return Extraction("error", detail="binary content in a text file")
    else:
        return Extraction("unsupported", detail=f"no extractor for .{ext}")
    return Extraction("ok", [(None, text)]) if text.strip() else Extraction("empty")
