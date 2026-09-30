#!/usr/bin/env python3
"""The census of what is ON the NAS: THE FOUR NUMBERS, and the VLM gate they decide."""

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET  # nosec B405 -- parses the user's own NAS files
import zipfile

try:
    import fitz  # PyMuPDF — optional; its absence is a visible skip, not a crash
except ImportError:  # pragma: no cover - exercised via monkeypatching in tests
    fitz = None

# Gates from benchmarks/docs/nas-document-ai.md § 6, as constants the mutation gate can bite
TEXT_CHARS_MIN = 50  # stripped chars for a page to count as born-digital
IMAGE_COVER_MIN = 0.95  # one image covering this fraction of the page = a scan
GATE_SCANNED_MIN = 0.10  # below this scanned+image-only fraction, VLM = footnote

NUL_RUN_MIN = 3  # a run of this many U+0000 marks a broken text layer
BAD_CHAR_RATIO_MAX = 0.20  # > 20 % U+0000/U+FFFD marks a broken text layer

LANG_HITS_MIN = 3  # fewer stopword hits than this decides nothing
LANG_SAMPLE_CHARS = 4000  # language runs over the first N chars per document

DEFAULT_PAGE_SAMPLE = 40  # PDFs with more pages are sampled evenly
ERROR_PATHS_SHOWN = 10  # summary shows this many error paths, then "and N more"

VERDICTS = ("born_digital", "degenerate", "image_only", "sparse")

GATE_FOOTNOTE = (
    "scanned+image-only under 10% of classified pages — the VLM is a "
    "footnote; redirect the budget to extraction + embeddings + retrieval"
)
GATE_MEASURE_FIRST = (
    "scanned+image-only at or above 10% of classified pages — OCR is a "
    "first-class problem; the bake-off moves to the front of the week"
)
GATE_NOT_EVALUATED = "not evaluated — no PDF pages were classified"

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

CATEGORY_EXTENSIONS = {
    "office": {"docx", "xlsx", "pptx", "odt", "ods", "doc", "xls", "ppt"},
    "pdf": {"pdf"},
    "image": {"jpg", "jpeg", "png", "gif", "webp", "tif", "tiff", "bmp", "heic"},
    "text": {"txt", "md", "csv", "tsv", "html", "htm", "eml", "json", "xml"},
    "archive": {"zip", "7z", "rar", "tar", "gz"},
}
_CATEGORY_OF = {ext: cat for cat, exts in CATEGORY_EXTENSIONS.items() for ext in exts}

_WORD_RE = re.compile(r"\w+")


# Pure classification functions: no I/O, testable without fitz or files
def is_degenerate_text(text):
    """True when a text layer exists but is broken: a NUL run, or too many U+0000/U+FFFD."""
    if not text:
        return False
    if "\x00" * NUL_RUN_MIN in text:
        return True
    bad = text.count("\x00") + text.count("�")
    return bad / len(text) > BAD_CHAR_RATIO_MAX


def classify_pdf_page(text, image_fractions, tagged):
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


def detect_language(text):
    """'de' | 'en' | None by counting function words: a heuristic that may stay undecided."""
    tokens = _WORD_RE.findall(text.lower())
    de = sum(1 for t in tokens if t in GERMAN_STOPWORDS)
    en = sum(1 for t in tokens if t in ENGLISH_STOPWORDS)
    if de >= LANG_HITS_MIN and de > en:
        return "de"
    if en >= LANG_HITS_MIN and en > de:
        return "en"
    return None


def scanned_fraction(counts):
    """(degenerate + image_only) / classified pages, or None, never a fabricated 0.0."""
    classified = sum(counts.get(v, 0) for v in VERDICTS)
    if classified == 0:
        return None
    return (counts.get("degenerate", 0) + counts.get("image_only", 0)) / classified


def gate_verdict(fraction):
    """The doc's § 6 gate, verbatim: under GATE_SCANNED_MIN the VLM is a footnote."""
    if fraction is None:
        return GATE_NOT_EVALUATED
    if fraction < GATE_SCANNED_MIN:
        return GATE_FOOTNOTE
    return GATE_MEASURE_FIRST


def sample_page_indices(page_count, sample_n):
    """Evenly spaced page indices: all pages when they fit, else sample_n of them."""
    if sample_n <= 0 or page_count <= sample_n:
        return list(range(page_count))
    step = page_count / sample_n
    return [int(i * step) for i in range(sample_n)]


def extrapolate_counts(counts, sampled_pages, total_pages):
    """Scale sampled verdict counts to the page total; rounding drift lands on the largest bucket."""
    if sampled_pages <= 0 or sampled_pages >= total_pages:
        return dict(counts)
    scale = total_pages / sampled_pages
    scaled = {k: round(v * scale) for k, v in counts.items()}
    drift = total_pages - sum(scaled.values())
    if drift and scaled:
        largest = max(scaled, key=lambda k: (scaled[k], k))
        scaled[largest] += drift
    return scaled


# Office text, stdlib only for a bare NAS: ET (S314) parses the owner's files, not network input.
def _local_texts(element, local="t"):
    """Text of every element whose namespace-stripped tag is `local`; iter() has no {*}."""
    return [
        el.text
        for el in element.iter()
        if el.text and el.tag.rpartition("}")[2] == local
    ]


def _zip_xml_texts(path, members):
    """Concatenated text of every t-run across `members` of a zip."""
    parts = []
    with zipfile.ZipFile(path) as zf:
        for member in members:
            parts.extend(_local_texts(ET.fromstring(zf.read(member))))  # nosec B314  # noqa: S314
    return " ".join(parts)


def extract_docx_text(path):
    """Text of word/document.xml's w:t runs, or None if the file is broken."""
    try:
        return _zip_xml_texts(path, ["word/document.xml"])
    except Exception:
        return None


def extract_xlsx_text(path):
    """sharedStrings.xml plus inline <is><t> strings, "" without xl/ members, None if broken."""
    # Before flattening this nesting, pin the member-missing case to "" (docx returns None).
    try:
        parts = []
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "xl/sharedStrings.xml" in names:
                # The suppression sits on the ET call: the formatter may move a closing paren.
                shared = ET.fromstring(zf.read("xl/sharedStrings.xml"))  # nosec B314  # noqa: S314
                parts.extend(_local_texts(shared))
            for name in sorted(names):
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    root = ET.fromstring(zf.read(name))  # nosec B314  # noqa: S314
                    for el in root.iter():
                        if el.tag.rpartition("}")[2] == "is":
                            parts.extend(_local_texts(el))
        return " ".join(parts)
    except Exception:
        return None


def extract_pptx_text(path):
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


# The walker
def _new_report(root, page_sample, want_tables, max_files):
    if not want_tables:
        tables_note = "not measured (--tables)"
    elif fitz is None:
        tables_note = "not measured (PyMuPDF missing)"
    else:
        tables_note = None
    return {
        "schema_version": 1,
        "generated_at": time.time(),
        "argv": list(sys.argv),
        "root": os.path.abspath(root),
        "fitz_available": fitz is not None,
        "totals": {
            "files": 0,
            "bytes": 0,
            "truncated": False,
            "max_files": max_files,
            "page_sample": page_sample,
        },
        "categories": {
            cat: {"count": 0, "bytes": 0}
            for cat in [*sorted(CATEGORY_EXTENSIONS), "other"]
        },
        "extensions": {},
        "pdf": {
            "pages_total": 0,
            "classified": 0,
            "sampled_pdfs": 0,
            "verdicts": dict.fromkeys(VERDICTS, 0),
            "classification": (
                "ok" if fitz is not None else "SKIPPED: PyMuPDF (fitz) not installed"
            ),
            "tables": {
                "measured": want_tables and fitz is not None,
                "found": 0,
                "pages": 0,
                "density": None,
                "note": tables_note,
            },
        },
        "languages": {"de": 0, "en": 0, "unknown": 0},
        "errors": [],
    }


def _tally_language(report, text):
    """Per-DOCUMENT language tally over the first LANG_SAMPLE_CHARS chars of text-bearing docs."""
    if not text or not text.strip():
        return
    lang = detect_language(text[:LANG_SAMPLE_CHARS])
    report["languages"][lang or "unknown"] += 1


def _image_fractions(page):
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


def _census_pdf(report, path, page_sample, want_tables):
    """Classify one PDF's pages; tallies land only once every sampled page succeeded."""
    try:
        doc = fitz.open(path)
    except Exception as exc:
        report["errors"].append({"path": path, "error": f"pdf open failed: {exc}"})
        return
    try:
        if getattr(doc, "needs_pass", False):
            report["errors"].append({"path": path, "error": "pdf is encrypted"})
            return
        tagged = bool(getattr(doc, "is_tagged", False))
        page_count = doc.page_count
        indices = sample_page_indices(page_count, page_sample)
        counts = dict.fromkeys(VERDICTS, 0)
        lang_parts, lang_chars = [], 0
        tables_found, tables_pages = 0, 0
        for idx in indices:
            page = doc.load_page(idx)
            text = page.get_text()
            verdict = classify_pdf_page(text, _image_fractions(page), tagged)
            counts[verdict] += 1
            if verdict == "born_digital" and lang_chars < LANG_SAMPLE_CHARS:
                lang_parts.append(text)
                lang_chars += len(text)
            if want_tables and hasattr(page, "find_tables"):
                tables_found += len(page.find_tables().tables)
                tables_pages += 1
    except Exception as exc:
        report["errors"].append({"path": path, "error": f"pdf read failed: {exc}"})
        return
    finally:
        doc.close()
    if len(indices) < page_count:
        counts = extrapolate_counts(counts, len(indices), page_count)
        report["pdf"]["sampled_pdfs"] += 1
    for verdict, n in counts.items():
        report["pdf"]["verdicts"][verdict] += n
    report["pdf"]["pages_total"] += page_count
    report["pdf"]["classified"] += sum(counts.values())
    if tables_pages:
        # The loop's own per-page hasattr, never fitz.Page, which a stub or exotic build may lack.
        report["pdf"]["tables"]["found"] += tables_found
        report["pdf"]["tables"]["pages"] += tables_pages
    _tally_language(report, "".join(lang_parts))


def _census_file(report, path, ext, page_sample, want_tables):
    """One file into the totals; per-type deep inspection where possible."""
    try:
        # os.path throughout this module, so no lone Path() for one size.
        size = os.path.getsize(path)  # noqa: PTH202
    except OSError as exc:
        report["errors"].append({"path": path, "error": f"stat failed: {exc}"})
        return
    report["totals"]["files"] += 1
    report["totals"]["bytes"] += size
    key = ext or "(none)"
    row = report["extensions"].setdefault(key, {"count": 0, "bytes": 0})
    row["count"] += 1
    row["bytes"] += size
    category = _CATEGORY_OF.get(ext, "other")
    report["categories"][category]["count"] += 1
    report["categories"][category]["bytes"] += size
    if ext == "pdf" and fitz is not None:
        _census_pdf(report, path, page_sample, want_tables)
    elif ext in OFFICE_EXTRACTORS:
        text = OFFICE_EXTRACTORS[ext](path)
        if text is None:
            report["errors"].append(
                {"path": path, "error": f"{ext} text extraction failed"}
            )
        else:
            _tally_language(report, text)


def run_census(root, page_sample=DEFAULT_PAGE_SAMPLE, want_tables=False, max_files=0):
    """Walk `root` and return the census report dict (the JSON schema)."""
    report = _new_report(root, page_sample, want_tables, max_files)
    done = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            if max_files and report["totals"]["files"] >= max_files:
                report["totals"]["truncated"] = True
                done = True
                break
            ext = name.rsplit(".", 1)[1].lower() if "." in name[1:] else ""
            _census_file(
                report, os.path.join(dirpath, name), ext, page_sample, want_tables
            )
        if done:
            break
    tables = report["pdf"]["tables"]
    if tables["measured"] and tables["pages"]:
        tables["density"] = tables["found"] / tables["pages"]
    fraction = scanned_fraction(report["pdf"]["verdicts"])
    report["gate"] = {"scanned_fraction": fraction, "verdict": gate_verdict(fraction)}
    return report


# Presentation
def _printable(s):
    """Terminal-safe text: backslashreplace only the lone surrogates of undecodable names."""
    return s.encode("utf-8", "backslashreplace").decode("utf-8")


def _human_bytes(n):
    # TiB is the floor, returned however large; outside the loop so none can fall off the end.
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TiB"


def _four_numbers(report):
    """THE FOUR NUMBERS as summary lines; a missing one says why, loudly."""
    pdf, langs = report["pdf"], report["languages"]
    fraction = report["gate"]["scanned_fraction"]
    if report["fitz_available"]:
        pages = f"{pdf['pages_total']}"
        scanned = (
            f"{fraction:.1%} (degenerate + image-only over "
            f"{pdf['classified']} classified pages)"
            if fraction is not None
            else "n/a — no PDF pages classified"
        )
    else:
        pages = scanned = (
            "SKIPPED — PyMuPDF (fitz) not installed; "
            "`pip install pymupdf` enables page classification"
        )
    lang_total = sum(langs.values())
    german = (
        f"{langs['de'] / lang_total:.1%} ({langs['de']} de / {langs['en']} en / "
        f"{langs['unknown']} undecided, of {lang_total} documents with text)"
        if lang_total
        else "n/a — no document text found to detect"
    )
    tables = pdf["tables"]
    if tables["density"] is not None:
        density = (
            f"{tables['density']:.2f} tables/page "
            f"({tables['found']} over {tables['pages']} pages)"
        )
    else:
        density = tables["note"] or "not measured (no PDF pages examined)"
    return [
        f"  total PDF pages : {pages}",
        f"  scanned fraction: {scanned}",
        f"  German fraction : {german}",
        f"  table density   : {density}",
    ]


def format_summary(report):
    """The human report: four numbers and the gate first, detail after."""
    lines = [f"NAS census — {report['root']}", "", "THE FOUR NUMBERS"]
    lines += _four_numbers(report)
    lines += [
        "",
        f"GATE (vs {GATE_SCANNED_MIN:.0%} scanned+image-only): "
        f"{report['gate']['verdict']}",
        "",
    ]
    totals = report["totals"]
    trunc = (
        f"  TRUNCATED at --max-files {totals['max_files']} — this census is PARTIAL"
        if totals["truncated"]
        else ""
    )
    lines.append(f"files: {totals['files']} ({_human_bytes(totals['bytes'])}){trunc}")
    lines.append("per category:")
    for cat, row in report["categories"].items():
        lines.append(f"  {cat:<8} {row['count']:>8}  {_human_bytes(row['bytes'])}")
    lines.append("per extension:")
    for ext in sorted(report["extensions"]):
        row = report["extensions"][ext]
        lines.append(f"  {ext:<8} {row['count']:>8}  {_human_bytes(row['bytes'])}")
    pdf = report["pdf"]
    if report["fitz_available"]:
        verdicts = ", ".join(f"{v}: {pdf['verdicts'][v]}" for v in VERDICTS)
        lines.append(f"pdf pages: {verdicts}")
        lines.append(
            f"page-sampled PDFs: {pdf['sampled_pdfs']} "
            f"(counts extrapolated to each document's page total)"
        )
    else:
        lines.append(f"pdf page classification: {pdf['classification']}")
    errors = report["errors"]
    lines.append(f"errors: {len(errors)}")
    for entry in errors[:ERROR_PATHS_SHOWN]:
        lines.append(f"  {_printable(entry['path'])} — {entry['error']}")
    if len(errors) > ERROR_PATHS_SHOWN:
        lines.append(
            f"  ... and {len(errors) - ERROR_PATHS_SHOWN} more "
            f"(the full list is in --output JSON)"
        )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Census a document tree: THE FOUR NUMBERS and the VLM gate "
        "of benchmarks/docs/nas-document-ai.md § 6, before any "
        "model is chosen."
    )
    parser.add_argument("root", help="directory to walk (e.g. /mnt/nas)")
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="write the full JSON report here (the archive; "
        "the terminal summary truncates error lists)",
    )
    parser.add_argument(
        "--page-sample",
        type=int,
        default=DEFAULT_PAGE_SAMPLE,
        metavar="N",
        help="sample N pages evenly from larger PDFs and "
        "extrapolate (default %(default)s; 0 = every page)",
    )
    parser.add_argument(
        "--tables",
        action="store_true",
        help="measure table density via find_tables (slow); "
        "without it density is reported as not measured",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        metavar="N",
        help="stop after N files (0 = all); truncation is announced, never silent",
    )
    args = parser.parse_args(argv)
    if not os.path.isdir(args.root):
        print(
            f"nas_census: root does not exist or is not a directory: {args.root}",
            file=sys.stderr,
        )
        return 2
    report = run_census(
        args.root,
        page_sample=args.page_sample,
        want_tables=args.tables,
        max_files=args.max_files,
    )
    if argv is not None:
        # The argv this run was given, not the host process's sys.argv.
        report["argv"] = list(argv)
    print(format_summary(report))
    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"\nJSON report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
