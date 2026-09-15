"""Tests for the NAS census (benchmarks/docs/nas-document-ai.md § 6 day 1).

Everything here runs OFFLINE and without PyMuPDF: the classification gates
are pure functions, the office extractors get real zip fixtures built in
tmp_path, and the fitz-less walker path — the environment this repo actually
has today — is pinned by monkeypatching fitz to None so the test means the
same thing after someone pip-installs pymupdf.

The stakes: the gate decides whether a VLM gets budget at all. A census that
silently reported zero scanned pages because a library was missing, or that
called a NUL-soup text layer born-digital, would settle that question with a
fabricated number.
"""

import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nas_census  # noqa: E402
from nas_census import (
    GATE_FOOTNOTE,
    GATE_MEASURE_FIRST,  # noqa: E402
    GATE_NOT_EVALUATED,
    classify_pdf_page,
    detect_language,
    extract_docx_text,
    extract_pptx_text,
    extract_xlsx_text,
    extrapolate_counts,
    format_summary,
    gate_verdict,
    is_degenerate_text,
    run_census,
    sample_page_indices,
    scanned_fraction,
)

GERMAN = (
    "Der Brief ist nicht mit der Post gekommen und die Rechnung "  # codespell:ignore
    "liegt auf dem Tisch neben dem Umschlag von der Bank."
)
ENGLISH = (
    "The invoice is on the table and it was sent to the office "
    "for review, as this is the usual process."
)

DOCX_XML = (
    '<w:document xmlns:w="http://schemas.openxmlformats.org/'
    'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{}</w:t>'
    "</w:r></w:p></w:body></w:document>"
)
SST_XML = (
    '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
    '2006/main"><si><t>{}</t></si></sst>'
)
SHEET_XML = (
    '<worksheet xmlns="http://schemas.openxmlformats.org/'
    'spreadsheetml/2006/main"><sheetData><row><c t="inlineStr">'
    "<is><t>{}</t></is></c></row></sheetData></worksheet>"
)
SLIDE_XML = (
    '<p:sld xmlns:p="http://schemas.openxmlformats.org/'
    'presentationml/2006/main" xmlns:a="http://schemas.'
    'openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree>'
    "<p:sp><p:txBody><a:p><a:r><a:t>{}</a:t></a:r></a:p>"
    "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
)


def make_docx(path, text):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", DOCX_XML.format(text))
    return path


def make_xlsx(path, shared_text, inline_text):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", SST_XML.format(shared_text))
        zf.writestr("xl/worksheets/sheet1.xml", SHEET_XML.format(inline_text))
    return path


def make_pptx(path, text):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", SLIDE_XML.format(text))
    return path


class TestClassifyPdfPage:
    def test_a_tagged_pdf_short_circuits_to_born_digital(self):
        # Even with no text at all: MarkInfo means software that knew its own
        # structure wrote the file, which is the strongest signal available.
        assert classify_pdf_page("", [], tagged=True) == "born_digital"

    def test_tagged_wins_even_over_a_degenerate_layer(self):
        assert classify_pdf_page("\x00" * 100, [0.99], tagged=True) == "born_digital"

    def test_a_page_at_the_text_gate_is_born_digital(self):
        assert classify_pdf_page("x" * 50, [], tagged=False) == "born_digital"

    def test_a_page_just_under_the_text_gate_is_not_born_digital(self):
        assert classify_pdf_page("x" * 49, [], tagged=False) == "sparse"

    def test_whitespace_does_not_count_toward_the_text_gate(self):
        assert classify_pdf_page(" " * 200 + "hi", [], tagged=False) == "sparse"

    def test_a_long_nul_ridden_text_layer_is_degenerate_not_born_digital(self):
        text = "x" * 30 + "\x00\x00\x00" + "y" * 30
        assert classify_pdf_page(text, [], tagged=False) == "degenerate"

    def test_one_image_covering_the_page_is_image_only(self):
        assert classify_pdf_page("", [0.95], tagged=False) == "image_only"

    def test_an_image_just_under_the_cover_gate_is_not_a_scan(self):
        assert classify_pdf_page("", [0.94], tagged=False) == "sparse"

    def test_many_small_images_do_not_add_up_to_a_scan(self):
        # The cover test is per image: tiled decoration is not a scan.
        assert classify_pdf_page("", [0.5, 0.5, 0.4], tagged=False) == "sparse"

    def test_an_empty_page_is_sparse(self):
        assert classify_pdf_page("", [], tagged=False) == "sparse"


class TestDegenerateText:
    def test_a_run_of_three_nuls_is_degenerate(self):
        assert is_degenerate_text("perfectly fine text " * 5 + "\x00\x00\x00")

    def test_two_isolated_nuls_in_clean_text_are_not(self):
        assert not is_degenerate_text("x" * 20 + "\x00y\x00")

    def test_a_replacement_char_majority_is_degenerate(self):
        assert is_degenerate_text("ab" + "�" * 3)

    def test_the_bad_char_ratio_boundary_is_exclusive(self):
        # exactly 20 % is tolerated; the gate is "more than"
        assert not is_degenerate_text("abcd�")

    def test_clean_german_text_is_not_degenerate(self):
        assert not is_degenerate_text(GERMAN)

    def test_empty_text_is_not_degenerate(self):
        assert not is_degenerate_text("")


class TestDetectLanguage:
    def test_a_german_paragraph_is_de(self):
        assert detect_language(GERMAN) == "de"

    def test_an_english_paragraph_is_en(self):
        assert detect_language(ENGLISH) == "en"

    def test_a_tie_decides_nothing(self):
        assert detect_language("der die das the and is") is None

    def test_too_little_text_decides_nothing(self):
        assert detect_language("der und") is None

    def test_no_stopwords_decides_nothing(self):
        assert detect_language("Faktura 4711 EUR 123,45 IBAN DE02") is None


class TestTheGate:
    def test_under_ten_percent_scanned_makes_the_vlm_a_footnote(self):
        counts = {"born_digital": 91, "image_only": 9}
        fraction = scanned_fraction(counts)
        assert fraction == 0.09
        assert gate_verdict(fraction) == GATE_FOOTNOTE

    def test_ten_percent_or_more_means_measure_ocr_first(self):
        counts = {"born_digital": 85, "degenerate": 5, "image_only": 5, "sparse": 5}
        fraction = scanned_fraction(counts)
        assert fraction == 0.10
        assert gate_verdict(fraction) == GATE_MEASURE_FIRST

    def test_the_gate_flips_exactly_at_ten_percent(self):
        assert gate_verdict(0.0999) == GATE_FOOTNOTE
        assert gate_verdict(0.10) == GATE_MEASURE_FIRST

    def test_no_classified_pages_is_not_a_zero_fraction(self):
        # None, not 0.0 — a fabricated zero would pass the gate on no evidence.
        assert scanned_fraction({}) is None
        assert gate_verdict(None) == GATE_NOT_EVALUATED


class TestSampling:
    def test_a_small_pdf_is_read_in_full(self):
        assert sample_page_indices(10, 40) == list(range(10))

    def test_zero_sample_means_every_page(self):
        assert sample_page_indices(100, 0) == list(range(100))

    def test_a_large_pdf_yields_evenly_spread_unique_indices(self):
        indices = sample_page_indices(400, 40)
        assert len(indices) == 40
        assert len(set(indices)) == 40
        assert indices == sorted(indices)
        assert indices[0] == 0
        assert indices[-1] == 390  # reaches the back of the document

    def test_extrapolation_preserves_the_sampled_ratio(self):
        counts = extrapolate_counts({"born_digital": 30, "image_only": 10}, 40, 400)
        assert counts == {"born_digital": 300, "image_only": 100}

    def test_extrapolated_counts_always_sum_to_the_page_total(self):
        # 3 verdicts over 100 pages round to 33+33+33 = 99; the drift page
        # must land somewhere, not vanish.
        counts = extrapolate_counts({"a": 1, "b": 1, "c": 1}, 3, 100)
        assert sum(counts.values()) == 100

    def test_a_fully_read_pdf_is_not_extrapolated(self):
        assert extrapolate_counts({"a": 5}, 5, 5) == {"a": 5}


class TestOfficeExtraction:
    def test_docx_text_is_extracted(self, tmp_path):
        p = make_docx(tmp_path / "brief.docx", GERMAN)
        assert "Rechnung" in extract_docx_text(str(p))

    def test_xlsx_reads_shared_strings_and_inline_strings(self, tmp_path):
        p = make_xlsx(tmp_path / "tabelle.xlsx", "SharedCell", "InlineCell")
        text = extract_xlsx_text(str(p))
        assert "SharedCell" in text
        assert "InlineCell" in text

    def test_pptx_slide_runs_are_extracted(self, tmp_path):
        p = make_pptx(tmp_path / "deck.pptx", ENGLISH)
        assert "invoice" in extract_pptx_text(str(p))

    def test_a_corrupt_docx_returns_none_not_an_exception(self, tmp_path):
        p = tmp_path / "broken.docx"
        p.write_bytes(b"PK\x03\x04 truncated garbage, not a zip")
        assert extract_docx_text(str(p)) is None

    def test_a_zip_without_the_expected_member_returns_none(self, tmp_path):
        p = tmp_path / "odd.docx"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("unrelated.txt", "hello")
        assert extract_docx_text(str(p)) is None


class TestTheWalker:
    def _tree(self, tmp_path):
        make_docx(tmp_path / "brief.docx", GERMAN)
        make_pptx(tmp_path / "deck.pptx", ENGLISH)
        (tmp_path / "scan.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
        (tmp_path / "broken.docx").write_bytes(b"PK\x03\x04 truncated")
        (tmp_path / "notes.txt").write_text("plain notes")
        return tmp_path

    def test_a_corrupt_file_lands_in_errors_and_the_walk_completes(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(nas_census, "fitz", None)
        report = run_census(str(self._tree(tmp_path)))
        broken = [e for e in report["errors"] if "broken.docx" in e["path"]]
        assert broken, report["errors"]
        assert report["totals"]["files"] == 5  # the broken file still counted

    def test_without_fitz_the_pdf_is_counted_and_classification_skips_visibly(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(nas_census, "fitz", None)
        report = run_census(str(self._tree(tmp_path)))
        assert report["fitz_available"] is False
        assert report["extensions"]["pdf"] == {
            "count": 1,
            "bytes": len(b"%PDF-1.4 not really a pdf"),
        }
        assert "SKIPPED" in report["pdf"]["classification"]
        assert report["gate"]["scanned_fraction"] is None
        assert report["gate"]["verdict"] == GATE_NOT_EVALUATED
        summary = format_summary(report)
        assert "SKIPPED" in summary
        assert "pip install pymupdf" in summary

    def test_languages_are_tallied_per_document(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nas_census, "fitz", None)
        report = run_census(str(self._tree(tmp_path)))
        assert report["languages"]["de"] == 1  # the docx
        assert report["languages"]["en"] == 1  # the pptx

    def test_table_density_is_not_measured_without_the_flag(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(nas_census, "fitz", None)
        report = run_census(str(self._tree(tmp_path)))
        assert report["pdf"]["tables"]["measured"] is False
        assert report["pdf"]["tables"]["density"] is None
        assert "not measured" in format_summary(report)

    def test_max_files_truncation_is_loud_in_summary_and_json(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(nas_census, "fitz", None)
        self._tree(tmp_path)
        report = run_census(str(tmp_path), max_files=2)
        assert report["totals"]["files"] == 2
        assert report["totals"]["truncated"] is True
        assert "TRUNCATED" in format_summary(report)

    def test_two_runs_over_the_same_tree_walk_identically(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nas_census, "fitz", None)
        self._tree(tmp_path)
        first = run_census(str(tmp_path))
        second = run_census(str(tmp_path))
        for key in ("totals", "categories", "extensions", "errors", "gate"):
            assert first[key] == second[key]


class TestTheCli:
    def test_a_missing_root_exits_two(self, tmp_path, capsys):
        rc = nas_census.main([str(tmp_path / "does-not-exist")])
        assert rc == 2

    def test_json_output_carries_the_four_numbers_and_the_gate(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(nas_census, "fitz", None)
        make_docx(tmp_path / "brief.docx", GERMAN)
        (tmp_path / "scan.pdf").write_bytes(b"%PDF-1.4 fake")
        out = tmp_path / "census.json"
        rc = nas_census.main([str(tmp_path), "--output", str(out)])
        assert rc == 0
        report = json.loads(out.read_text())
        # the four numbers, in schema form
        assert report["pdf"]["pages_total"] == 0  # 1: total pages
        assert report["gate"]["scanned_fraction"] is None  # 2: scanned fraction
        assert report["languages"]["de"] == 1  # 3: German fraction
        assert report["pdf"]["tables"]["density"] is None  # 4: table density
        assert report["schema_version"] == 1
        assert report["fitz_available"] is False
        assert report["errors"] == []
        summary = capsys.readouterr().out
        assert "THE FOUR NUMBERS" in summary
        assert "GATE" in summary
