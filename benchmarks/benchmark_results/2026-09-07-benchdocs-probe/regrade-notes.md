<!--
Copyright (c) 2025 Kataglyphis
SPDX-License-Identifier: MIT
-->

# Regrade notes — 2026-09-07 benchdocs probe

`summary.md` / `summary.json` in this directory are the grader-v1 numbers,
produced live by `bench_docs_probe.py.grader-snapshot` (sha256 prefix
`58409e9433894157`). Two families were re-graded afterwards from the archived
raw replies — the replies themselves were never touched. Both regrades follow
the suite's rule: when every candidate fails a case the same way, suspect the
pipeline before the model.

## 1. GLM-OCR `table_csv` (image): 0/5 FAIL → recognition 0.962–1.000

GLM-OCR emits the table with SINGLE spaces between columns; with multi-word
cell values ("Druckerpapier größere Menge") no parser can recover column
boundaries, so grader v1 scored `hyp_rows: 0` → 0.0 despite a byte-perfect
recognition. Truth-guided regrade (all non-empty truth cells of a row found
in order inside one reply line):

| case | rows | cells | score |
|---|---|---|---|
| table_csv_s1 | 7/7 | 26/26 | 1.000 |
| table_csv_s1_jpeg50 | 7/7 | 26/26 | 1.000 |
| table_csv_s1_rot1 | 7/7 | 26/26 | 1.000 |
| table_csv_s2 | 6/7 | 25/26 | 0.962 |
| table_csv_s3 | 7/7 | 26/26 | 1.000 |

Qwen3-VL's 5/5 on the same family needed no regrade — chat style returns real
semicolon CSV, measured under grader v1.

## 2. `transcribe_de` text twins: identical CER 0.022 for BOTH models → label echo

Both models "failed" every transcribe twin at exactly CER 0.022 — identical
failure across models flagged the pipeline. Diff shows both echo the twin
prompt's own framing label `Seiteninhalt:` before an otherwise perfect
transcription. Regrade with the leading label stripped:

| case | GLM-OCR | Qwen3-VL-4B |
|---|---|---|
| s1_twin | CER 0.135 → FAIL | CER 0.0000 → PASS |
| s2_twin | CER 0.020 → FAIL | CER 0.0000 → PASS |
| s3_twin | CER 0.995 → FAIL | CER 0.0000 → PASS |

For Qwen the twin failures were pure artifact; for GLM they are real —
consistent with every other twin family: GLM-OCR is a recogniser, not an
instruction model, and its text-input behaviour is undefined.

No other rows were re-graded.
