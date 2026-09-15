<!--
Copyright (c) 2025 Kataglyphis
SPDX-License-Identifier: MIT
-->

# Document AI over the NAS — model choice, routing, and the measurement that settles it

The question this page answers: *"I want to connect a multimodal model to my
NAS so it can automatically look into Word, Excel, images, PDF — which model
should I select?"*

Produced 2026-09-06 by a 36-agent review (four repo auditors, six web-research
dimensions each checked by three adversarial verifiers, four competing
recommendation drafts scored by three judges, then synthesised). Repo claims
were read from the tree at `a8827c2d`; machine claims were probed live on this
host the same day; web claims carry their own flags below — anything marked
*research bottom-line* survived research but was not independently adjudicated.
Line numbers rot; the quoted mechanisms do not.

This page was written in ANTfrastructure and moved here on 2026-09-15 (owner
decision D8), beside the benchmark lab and the census tool it prescribes. The
`a8827c2d` it audits is an ANTfrastructure commit, and every bare `linux/…`,
`windows/…` or `docs/…` path quoted below is one of that repository's — reachable
from this checkout at [`third_party/ANTfrastructure/`](../../third_party/ANTfrastructure/).
Paths in this repository are written out in full (`benchmarks/…`).

**The short answer.** Word and Excel get **no model at all** — parse the OOXML.
PDFs are gated per page; born-digital pages never touch a model. Scanned pages
go through `ocrmypdf -l deu+eng` first, and only the residue escalates to a
VLM. The VLM shortlist is **GLM-OCR 0.9B** (primary — the only candidate with
documented German), **PaddleOCR-VL-1.6** and **LightOnOCR-2-1B**, baked off on
your own pages against tesseract as the zero-model control. **Qwen3-VL-4B**
does photo captioning only. The Hexagon NPU is structurally unable to read a
document page (§ 3) — its jobs here are photo captioning and, if the plumbing
gets built, embeddings.

---

## 1. Does the benchmark already answer this? No — it measures a different construct

Across the benchmark suite's production `.py` files — this repository's
[`benchmarks/`](../README.md) — there is not one occurrence of `image_url`,
`input_audio`, `base64`, `mmproj`, `clip` or `ocr`. Every request-building site hardcodes `"content": <str>`
(`benchmark_openai_api.py:485`, `bench_coding.py:1436`, `bench_tools.py:442`
and `:456`, `bench_lanes.py:56`, `bench_provenance.py:125`). There is no image
or document fixture anywhere, no CER/WER grader, `inspect_gguf.py`'s
`INTERESTING_KV` has no vision key (it cannot even identify a projector), and
`bench_sweep.py`'s `TOOLS` tuple would reject a vision tool by name. No VL
model has ever been pulled: `baselines/geniex-npu-tools.json` lists 12
text-only models. **Zero multimodal measurements exist.**

Worse, the suite's most-quoted headline points the wrong way for this
workload. "The CPU beats the NPU ~2x" is a **decode** measurement on short
prompts. For long inputs the reverse holds — a 3,000-token prompt costs
**3.2 s on the NPU against 34 s on the CPU**
(see [`geniex-local-ai-setup.md`](../../third_party/ANTfrastructure/docs/geniex-local-ai-setup.md) § 1e) — and a
document page is ~100 % input tokens.

What does transfer, and it is a lot: `bench_cli.post_json` serialises whatever
dict it is handed, so an OpenAI multimodal content list passes through
untouched — sending the first image is a ~3-line change at one call site.
`resolve_candidates`, `write_report`, `bench_provenance.collect`,
`bench_stats` (Wilson intervals, `smallest_separable_rate`,
`paired_sign_test`), `bench_compare`'s baselines and `bench_report`'s manifest
all transfer verbatim. The plumbing is done; the measurement is not (§ 5).

One correction to [`llm-benchmark-review-2026-09-05.md`](llm-benchmark-review-2026-09-05.md)
line 480 while here: *"bench_vision.py is an addition to the suite, not a new
harness"* is true of the HTTP plumbing only. The measurement construct —
fixture corpus, hand-keyed ground truth, document graders, image-token
accounting, a VL lane, a resolution sweep — is entirely new code with no
analogue in the tree.

## 2. Corrections to this repo's own recorded assumptions (probed live, 2026-09-06)

- **`summy-server` is this laptop.** WSL2 `/etc/hostname` = `summy-server`,
  the Windows machine name is `SUMMY-SERVER`, and `getent hosts summy-server`
  resolves to this interface under mirrored networking. There is **no LAN
  box**. `backends.json`'s `ollama-lan` **and the `control` calibration
  backend** both point back at this machine, where nothing listens on 11434 —
  **the suite's control backend is currently dead.**
- **The Windows host has 31.6 GiB RAM**, 17.8 GiB free at probe
  (`Win32_ComputerSystem.TotalPhysicalMemory` = 33,896,411,136 B). The
  "16 GB" in the GenieX page's quick-reference table was the *27B model's*
  footprint, not the host's capacity.
- **The 2.93 GiB HTP vmem budget is per context binary, not per model.**
  QAIRT bundles ship as 4-6 separate context binaries (Qwen3-VL-4B is
  `part1..4_of_4` plus a 432 MB `vision_encoder.bin`), which is how a 5.2 GB
  QAIRT Qwen3-8B already runs on this HTP. Do not use 2.93 GiB to rule out a
  7B/8B bundle.
- **Qwen3-VL-8B is not mobile-only.**
  `qwen3_vl_8b_instruct-geniex_qairt-w4a16-qualcomm_snapdragon_x_elite.zip`
  exists (5,256,790,253 B, context 4096, 823.92 tok/s prefill / 12.77 tok/s
  decode published). The QAIRT VLM catalogue for this chipset is **four**
  models, not the two the 2026-09-05 review recorded. But GenieX gates QAIRT
  bundles behind a closed-source registry whose `tests/models.json` defines
  exactly one `qairt_vlm` entry (Qwen2.5-VL-7B) — a published AI Hub bundle
  does not mean GenieX v0.6.1 can load it.

## 3. NPU verdict: it cannot read a page, and no context/RAM/throughput fixes that

Every QAIRT VLM bundle for `qualcomm-snapdragon-x-elite` compiles its vision
encoder to a **fixed shape**. Qwen3-VL-4B/8B: `img-enc-htp.json` says
`vision-param {height:32,width:32}` and `metadata.json` says
`pixel_values [1024,1536]` — with patch 16, temporal patch 2, 3 channels
that is exactly a 32x32 patch grid = **512x512 pixels in, 256 image tokens
out**. Qwen2.5-VL-7B: 336x504 px, 216 tokens. On top of that GenieX's own
`sdk/include/image_utils.h` `resize_and_pad_image(path, size=448)` runs a
plain `ffmpeg scale` — a **non-aspect-preserving squash to a square** for any
aspect ratio between 0.5 and 2.0, which covers A4 (0.707) and US Letter
(0.773). An A4 page reaches the model at roughly **62x44 DPI, 10 pt glyphs
~6 px tall**. It cannot read the document. The `W*H/1024` and `W*H/784`
image-token formulas describe the PyTorch models and **do not apply to the
NPU bundles at all** — the 4096-token context was never the binding
constraint; the encoder was.

Two independent nails: Qualcomm's own `numerics.yaml` prices w4a16 at 66.9
(Grace2) for Qwen3-VL-4B against 88.5 float — a 21.6-point accuracy drop,
where the llama.cpp q4_0 path costs Gemma-4-E2B 2.5 points on the same CRD.
And AI Hub has **no OCR-, document-, layout- or table-specialised model for
this chipset at all** (of 241 model directories, only `trocr` and `easyocr`
are text OCR).

What the NPU should do instead, in priority order:

1. **Photo captioning/tagging.** 512x512 / 256 tokens is a *native* fit for a
   snapshot. Qwen3-VL-4B w4a16 (2.83 GiB, 1286.77 tok/s prefill, 20.82 tok/s
   decode published) on its own port ≈ 9.9 s/image, ~363 images/hour,
   concurrent with the CPU lanes at near-zero cost (the NPU lane is immune to
   contention — § GenieX page).
2. **Keep the text lane** (18184) as is.
3. **Embeddings — the biggest prize, entirely unbuilt.** Embedding is pure
   prefill, and prefill is where the NPU genuinely wins (QAIRT 543-1287 tok/s
   vs llama.cpp CPU 50-214; an independent X Elite study measured 18.1x at
   4.0x lower energy — *research bottom-line, unverified*). But GenieX
   documents **no `/v1/embeddings`**, so realising this means ONNX Runtime
   with the QNN EP on the **Windows** side — not the Linux tooling in
   [`qnn-linux.md`](../../third_party/ANTfrastructure/docs/qnn-linux.md), because `/dev/fastrpc*` does not exist
   inside WSL2.
4. **Possibly line-level OCR** via TrOCR/EasyOCR QAIRT bundles at native crop
   resolution — the only NPU path that escapes the square squash. Unmeasured.

## 4. The recommended stack

Route by file type and by PDF page; buy exactly three models. The single most
important design decision: **OCR is an enrichment of the file, not a stage of
the pipeline** — `ocrmypdf --skip-text` writes the recovered text layer back
into the PDF, so the work is done once and every downstream consumer
(including Nextcloud's Tika path) gets it free. That also makes the one
genuinely irreversible decision — the embedding model — cheap to revisit.

| Role | Model | Size | Licence | Where | Why |
|---|---|---|---|---|---|
| Document OCR, primary | **GLM-OCR 0.9B** (`ggml-org/GLM-OCR-GGUF` Q8_0 + mmproj) | 1.43 GB | MIT | Windows host, own port 18185, or Ollama | Only candidate whose tech report enumerates German (zh/en/fr/es/ru/de/ja/ko). First-party ggml-org GGUF. Caveats: recogniser only (needs a layout stage — its 94.62 headline is a two-stage score); **flash-attention must be OFF**; task prompts, not free-form chat. |
| Document OCR, challenger | PaddleOCR-VL-1.6 (official GGUF) | 1.82 GB | Apache-2.0 | same lane | Top of official OmniDocBench v1.6_full (96.34) and the only candidate with a published real-degradation score (Real5: 93.19). Risk: ggml-org's OCR guide flags it "may have degraded performance" in llama.cpp. |
| Document OCR, challenger | LightOnOCR-2-1B | 1.09 GB | Apache-2.0 | same lane, or WSL2 | Smallest credible footprint; `-bbox` variant for grounding. German undocumented. |
| Zero-model control | tesseract `deu+eng` via ocrmypdf | – | Apache-2.0 | WSL2 | Recipe at [`linux-reference.md`](../../third_party/ANTfrastructure/docs/linux-reference.md) § OCR. Deterministic; produces detectable garbage rather than plausible fiction. **If no VLM beats it on your own pages, ship it.** |
| Text-presence gate | PP-OCRv6-Tiny/Medium | 1.5M/34.5M params | Apache-2.0 | WSL2 CPU | Beats Qwen3-VL-235B on text recognition (83.2 vs 74.9) and on hallucination resistance at 1/6800th the size; gates which images are worth sending onward. |
| Photos | Qwen3-VL-4B-Instruct GGUF Q4_K_M + mmproj | 2.95 GB | Apache-2.0 | CPU lane, or w4a16 on NPU port 18186 | Captioning and search recall only. **Never page transcription** — small VLMs score 43-69 % F1 on page images vs 66-72 % on extracted text. |
| Embeddings (irreversible) | bge-m3, or multilingual-e5-large-instruct | 1.2 / 0.5 GB | MIT | Ollama or bare llama-server in WSL2 — GenieX has no `/v1/embeddings` | *bge-m3 is a research bottom-line, not an adjudicated claim.* Decide via `bench_embeddings.py` with German document triples **before** indexing anything (§ 5). |
| Text answering | `unsloth/Qwen3-4B-GGUF:Q4_0` (23.7 tok/s measured) | 2.2 GB | Apache-2.0 | CPU lane 18184, already running | Step up to 9B-Distill (15.2 tok/s) or 27B (5.62 tok/s) for overnight batch work — the host's 31.6 GiB fits all three. |
| Parse layer (no model) | Docling + openpyxl + PyMuPDF | – | MIT | WSL2 | Docling for `.docx`/`.pptx`; **set `TesseractOcrOptions(lang=['deu','eng'])`** or German scans get English OCR. `.xlsx` gets its own openpyxl path (Docling flattens sheets, drops charts/comments). |

Routing:

| File type | Path | Model |
|---|---|---|
| `.docx` `.pptx` | Docling OOXML parse → Markdown/JSON; embedded images lifted into the image lane | none |
| `.xlsx` | Read the sheet XML directly: formulas, cached values, shared strings, hidden rows, A1 addresses. Never render, never ask an LLM to compute — table detection from spreadsheet *images* scores 25.79 % F1 (GPT-4V) vs **78.17 %** for serialised text; SpreadsheetBench 2 puts the best open self-hostable model at 17.14 %. Handle `<f>` with missing `<v>` (openpyxl-written files carry no cached results). | none |
| `.pdf`, born-digital page | Gate: `/MarkInfo` tagged, or ≥~50 chars of `page.get_text()`. PyMuPDF text + Docling layout/TableFormer. Validate the layer — runs of U+0000 mean broken ligature maps → route as scanned. | none |
| `.pdf`, scanned page | `ocrmypdf --skip-text -l deu+eng` writes the layer back into the file; `--redo-ocr` where a bad layer already exists. Low-confidence pages and table/form/figure regions escalate. | GLM-OCR, one page per request, 200 dpi |
| Photo `.jpg` `.png` | CLIP/SigLIP embedding for search; optional caption. Nextcloud `recognize` already does faces/objects and explicitly no OCR. | Qwen3-VL-4B (captions only) |
| Photographed document | PP-OCRv6 confidence gate → scanned-page lane at native resolution. Genuinely old scans defeat the whole field (best published "Old Scans" score anywhere: 51.9) → human queue. | GLM-OCR |
| Screenshot | OCR at native pixels — never downscale; screenshots sit at the legibility floor. | GLM-OCR |
| Charts/figures in any file | Crop the region (layout stage) and send the crop — the one place a VLM is irreplaceable. For charts inside `.xlsx`/`.docx`, reconstruct from the cells instead. | Qwen3-VL-4B |
| Cross-page tables | No open model stitches across a page break; post-process by column count + header signature. | – |

Placement: NAS via the existing CIFS mount recipe
([`linux-reference.md`](../../third_party/ANTfrastructure/docs/linux-reference.md) § Mounting an SMB/CIFS network share)
— `NEXTCLOUD_MOUNT=/mnt/`
in [`linux/nextcloud-aio/.env.example`](../../third_party/ANTfrastructure/linux/nextcloud-aio/.env.example)
already bind-mounts it into Nextcloud. Census/Docling/ocrmypdf/crawler on the
8 Oryon cores in WSL2 (the dominant cost is CPU PDF rendering, not OCR — NVIDIA
measures multimodal ingestion at 60 % extraction / 33 % embedding / <7 %
vector DB). OCR VLM on the Windows host, own port (a GGUF loaded into a lane
holding a QAIRT bundle is a deterministic crash). Long runs under
`windows/scripts/host/Disable-Sleep.ps1`.

**Three repo blockers, all verified:**

1. `linux/llm-stack/docker-compose.yml:17` sets `OLLAMA_FLASH_ATTENTION: "1"`
   — GLM-OCR requires flash-attention **off** in llama.cpp; this one line
   silently breaks the primary model on the Ollama path. Also `:18` pins 6
   threads on an 8-core box and `:38` `mem_limit: 40g` cites a host this
   machine is not.
2. `windows/scripts/host/Start-GeniexServers.ps1` builds
   `serve --compute --host --nctx --max-tokens --keepalive` — **no
   `--mmproj`**. Whether `geniex pull --model-type vlm` wires the projector
   itself is the one blocking unknown (15-minute probe, § 6 day 3).
3. `.wslconfig` caps WSL2 at 6.09 GiB. With the host at 31.6 GiB, raising to
   12-16 GiB is the highest-leverage config edit — it is what lets Docling +
   Elasticsearch + the crawler co-exist.

**Deliberately not used: Nextcloud Context Chat.** It requires AVX/AVX2 —
x86-only — so it cannot run on this ARM machine at any RAM size; its embedding
model cannot be changed after install without a wipe; it bypasses
`files_accesscontrol` rules in AI answers; and this repo's AIO variant has no
mastercontainer, so the AppAPI deploy path is closed anyway. Build the
pipeline outside Nextcloud; let Nextcloud consume the index.

## 5. The decisive measurement: `bench_docs.py`, not `bench_vision.py`

The review's multimodal design (2026-09-05, § "A multimodal benchmark for the
Snapdragon") is a well-engineered *screenshot* benchmark, not a *document*
benchmark: its default 448 px long edge is **38 DPI on A4** (10 pt x-height
~2.6 px — unreadable by construction), four of its six task families are
developer-workflow tasks, its graders are exact-match where documents need
CER/WER/ANLS/field-F1, `max_tokens 64-256` can never surface the output-cap
truncation a real page extraction hits, and it compares VLMs only to other
VLMs — structurally unable to discover that most of the corpus should never
have been sent to a VLM. Keep its machinery (PASS/FAIL/CUT with cuts excluded,
repeats because the GGUF lanes sample at temperature 0, Wilson intervals,
provenance pinning, and its best idea — the **text-only twin**, which turns an
API that hides encoder time into a measurable image cost and separates
"cannot read the image" from "cannot do the task"). Replace its content:

- **Corpus:** two tiers. Tier A: ~60 of your own NAS pages, hand-keyed once,
  never leave the host, no control backend, all-fail cases adjudicated by
  hand. Tier B: ~20 German PDFs you author (reportlab / headless LibreOffice)
  with real degradations — 0.3-2 degree skew, JPEG Q40-70, speckle,
  bleed-through, illumination gradients, several levels per item — shareable,
  and the only tier a control endpoint sees. Ten pages separate nobody
  (`bench_stats.smallest_separable_rate` exists for exactly this); target
  ≥60 graded cases.
- **Arms, identical pages:** (0) ocrmypdf `deu+eng` — the zero-model control
  every VLM must beat; (1) GLM-OCR; (2) PaddleOCR-VL-1.6; (3) LightOnOCR-2-1B;
  plus a **text-extraction control arm** on every case.
- **Families:** `transcribe_de` (CER/WER), `kie_invoice_de` (Rechnungsnummer,
  Datum, Netto/USt/Brutto, IBAN — per-field P/R/F1), `table_csv` (cell-level
  F1; merged cells, two-row headers, decimal comma), `classify`,
  **`absent_field`** (asking for a field that is genuinely missing — PASS is
  "unknown", inventing a value is FAIL; the restraint pattern already exists
  in `bench_tools`), `reading_order` (multi-column), `text_twin`.
- **Discipline:** every fixture ships its ground truth **and a
  plausible-but-wrong answer the grader must FAIL** (the `bench_tasks.py`
  rule), written before any model is pulled. `--repeats 3` minimum. Record
  `page_px`, dpi, `est_image_tokens`, `prompt_tokens` and the QAIRT-padded
  value side by side, TTFT, `finish_reason`, output hash, a repetition flag,
  mmproj id+hash.
- **Wiring:** `post_json`/`resolve_candidates`/`write_report`/provenance/
  `bench_stats` transfer verbatim; add `"docs"` to `bench_sweep.TOOLS`; extend
  `inspect_gguf.INTERESTING_KV` with `clip.*`/mmproj keys.
- **And fix `bench_embeddings.py` first** — it is orphaned (absent from
  `bench_sweep.TOOLS`, never called by `run_benchmarks.sh`) and grades five
  hardcoded English triples. German document-chunk triples plus a real
  retrieval measurement (recall@k / MRR) protect the one decision that forces
  a full re-crawl to change.

## 6. First week

1. **Day 1 — the census, before any model.** Walk the NAS: counts and bytes
   by extension; per PDF page classify born-digital / degenerate-layer /
   image-only (PyMuPDF); language-detect. Publish four numbers: total pages,
   **scanned fraction**, German fraction, table density. Published corpora
   span 5-44 % scanned; every sizing decision is a linear function of your
   number, and nobody has it. **Gate: if scanned+image-only is under ~10 %,
   the VLM is a footnote** — redirect the budget to extraction + embeddings +
   retrieval. Implemented as
   [`benchmarks/nas_census.py`](../nas_census.py)
   (stdlib-only; `pip install pymupdf` enables the page classification,
   which otherwise skips visibly).
2. **Day 2 — turn on what exists.** Flip the `fulltextsearch` profile in
   `linux/nextcloud-aio/.env` (raise the toy 512M ES heap; note the
   tesseract-OCR app for it has no NC 33+ build — OCR happens outside
   Nextcloud). Keyword search over Word/Excel/PDF text lands here, with zero
   model decisions. Fix the three blockers of § 4.
3. **Day 3 — three probes, one hour.** (A) `geniex pull ggml-org/GLM-OCR-GGUF
   --model-type vlm` + serve on 18185 — does the projector get wired? decides
   GenieX-on-host vs Ollama-in-WSL2. (B) pull the QAIRT Qwen3-VL-4B bundle
   and read `img-enc-htp.json` — closes the NPU-page question on your own
   hardware permanently. (C) does `--max-tokens` raise the 2048 output cap,
   and are multiple `image_url` parts accepted?
4. **Day 3, afternoon — the fabrication canary, before any accuracy number.**
   Render a page carrying a random UUID; assert it comes back; run it every N
   pages. This is the documented paperless failure mode — a stack that
   fabricated plausible German invoices for weeks because the "vision" model
   had no image encoder and nothing errored. Add an n-gram repetition
   detector (Qwen-family OCR loops at temperature 0; GenieX's llama.cpp lanes
   ignore temperature — "temperature: 0 still sampled", unchanged in v0.6.1)
   and an arithmetic check (line items sum to total).
5. **Days 4-5 — ship the zero-model path; build the gold set.** ocrmypdf over
   the image-only pages (the layer persists in the files); hand-key 60 Tier-A
   pages stratified across your real document kinds, including your five
   worst scans; author 20 Tier-B degraded PDFs.
6. **Days 6-7 — measure.** Graders + negative tests first, then the four-arm
   bake-off at 150/200/300 dpi (do not assume 300 — PaddleOCR-VL's own
   pixel budget downscales ~5x from A4@300 regardless), `--repeats 3`; German
   triples through `bench_embeddings.py` before indexing a single document.

## 7. Honest unknowns

The full list is longer; these are the ones that can change the decision:

- **The corpus itself** — the largest unknown and the cheapest to close
  (one evening). Until the census exists this page is a recommendation about
  an unmeasured share of the files.
- **German is documented for exactly one candidate and measured for none.**
  The bake-off may return PaddleOCR-VL first despite its llama.cpp warning —
  or tesseract first, in which case the VLM discussion was overhead.
- **Vision-encoder cost is not observable over HTTP** (`media_time` is
  SDK/geniex-bench only). Every per-page throughput figure here is a floor
  extrapolated from text prefill/decode rates; it could be 2x worse.
- **Can GenieX serve a GGUF + separate mmproj at all?** (§ 6, probe A. The
  llama.cpp vintage is fine — hash `0eadefe` post-dates both OCR-model
  merges by months; the question is packaging.)
- **Is GLM-OCR usable without a layout detector?** Its headline is a
  two-stage score; the bare GGUF scores lower by an unknown amount; the
  Docling-layout + GLM-OCR pairing is untested.
- **The ~90 % content-faithfulness ceiling is structural** (best case on
  ParseBench: one page in ten with dropped or invented content; a 40-row
  statement returned as 38 rows with no gap). CER hides it — errors
  concentrate in IBANs and invoice numbers where the language prior is
  uniform. Plan for it: grounding boxes, arithmetic validators, omission
  checks, a human queue. No model choice removes it.
- **The control backend is dead** and Tier A can never have one — you can
  rank local models against each other, but "good enough in absolute terms
  for unattended indexing" needs either the hosted control (sends document
  content off-box, costs money) or hand adjudication.
- **A second machine may exist.** [`build-parallelism-memory-tuning.md`](../../third_party/ANTfrastructure/docs/build-parallelism-memory-tuning.md)
  describes a 32-core / 62 GB amd64 host (2026-07). If it is real, reachable
  and has a GPU, it is a materially better ingestion home than this laptop —
  and the only place Context Chat could ever run. To be answered by the
  owner, not inferred.
- **Whether semantic search is even wanted.** "Automatically look into" may
  mean keyword findability (Tika + Elasticsearch: no model, no
  irreversibility, mostly already in the repo) or question-answering over the
  corpus (commits to an embedding model). Different projects, different
  budgets.

## 8. Dissent, recorded

The strongest surviving objection to this page: it calls scanned pages "the
residue" without having counted them. Every published scanned-fraction figure
cited above came from business or engineering corpora; a German household NAS
— Behörden letters, Rechnungen, Kontoauszüge, decades of paper scanned once —
may be 60-70 % scans. That would not change the architecture, but it would
invert the emphasis: the OCR bake-off would belong on days 1-2 and everything
else behind it. The census settles this too, which is why it is day 1.

## 9. First measurements (2026-09-07) — the shortlist meets the hardware

The first live document-VLM measurements ever taken on this host. Harness:
a scratchpad probe in the `bench_docs.py` mould (synthetic German corpus,
exact ground truth, self-tested graders with negative tests — 29 checks —
adversarially hardened before any model ran). Corpus: 4 families
(`kie_invoice_de`, `table_csv`, `transcribe_de`, `absent_field`) × 3 seeds,
rot-1.2°/JPEG-45 degradations on seed 1, a text twin per seed — 32 cases per
model, A4 @ 200 dpi, temperature 0, one repeat. Raw replies, summary,
grader snapshot and regrade notes:
[`benchmarks/benchmark_results/2026-09-07-benchdocs-probe/`](../benchmark_results/2026-09-07-benchdocs-probe/).
Serving: **the running GenieX v0.6.1 CPU lane (18184), per-request model
swap** — `geniex pull --model-type vlm` wires the mmproj itself, closing § 7's
blocking unknown. No new lane was needed.

### Quality (image cases, CPU lane)

| family | GLM-OCR 0.9B Q8_0 | Qwen3-VL-4B Q4_K_M |
|---|---|---|
| invoice KIE (field-F1) | **5/5, 1.000** | **5/5, 1.000** |
| table (see regrade notes) | recognition 0.962–1.000, but single-space columns — needs a structuring stage | **5/5 CSV, 1.000 — own extraction** |
| transcribe (CER) | **5/5, 0.010** | **5/5, 0.009** |
| absent-IBAN fabrication trap | **5/5 — nothing invented** | **5/5 — nothing invented** |
| text twins (task without image) | fails — recogniser only | passes (invoice/table 1.000; transcribe artifact-free after regrade) |
| mean s/page (total · TTFT) | **332 · 293** | 460–500 · 399 |

Both models read rotated and JPEG-degraded pages without a single dropped
field, and neither fabricated an IBAN — on this (clean, rendered) corpus the
fabrication trap never fired.

### The compute matrix, measured

| lane | verdict |
|---|---|
| CPU (18184) | the only lane that reads correctly — all numbers above |
| GPU (18182) | **13× faster garbage**: multilingual token salad to the length cap, both models — the documented Adreno "fast garbage" pattern; a throughput-only benchmark would have ranked it best |
| NPU, GGUF VLM (18181) | HTTP 500 in 0.2 s, both models; **the lane survives** (graceful refusal, not the QAIRT/GGUF crash). Encoder-on-HTP remains unmeasured — it needs a lane freshly started on a VLM |
| NPU, QAIRT VLM | not re-tested; structurally blind for pages (§ 3) |
| hybrid (18183) | untested — lane not running |

### GenieX v0.6.1 defect found: byte-identical VLM repeat → empty stream

A repeated, byte-identical VLM request returns an **instant empty SSE stream**
(no delta, no `finish_reason`, ~0.1 s) — the v0.6.0 "reuse VLM KV via
char-level prefix match" path. Reproduced across cases; a fresh image on the
same lane answers normally. Worth filing upstream with this repro. Harness
mitigations, kept: empty-stream-no-finish is graded **ERR** (transport, not
model), and repeats flip one white-adjacent corner pixel so the prefix match
cannot fire.

### What this does and does not change

The § 4 recommendation survives contact with the hardware, with one upgrade in
confidence: **as the single outward GenieX endpoint, Qwen3-VL-4B is now
measured, not argued** — 20/20 image cases including its own extraction, zero
fabrication. GLM-OCR reads as well and ~30 % faster, but is a recogniser: its
output needs the deterministic extraction stage, exactly as § 4 routes it.

Honest limits: n = 5 per family separates nothing subtle (the suite's own
`smallest_separable_rate` lesson); the corpus is rendered DejaVu, not real
scans — real Behörden paper will be harder; one repeat (the lanes sample even
at temperature 0); Qwen3-VL-**8B** and tesseract were not in this round; and
~330–500 s/page on the CPU lane prices bulk OCR at roughly **150–250
pages/day/lane** — reinforcing § 4: born-digital extraction first, VLM only
for the residue.
