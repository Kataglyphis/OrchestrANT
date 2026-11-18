# Backlog

- [ ] **LAI-P3: the rest of the NAS document AI** (L, ★★). Hub language-AI milestone 2. `orchestrant-docs` indexes, searches
  and answers from a tree's text with no network (`docs/source/language-models.md` § The NAS document AI). Left: a run on a
  real share (only a synthetic tree has run), OCR and the VLM lane for `needs_ocr` pages, images, tables and layout, chunked
  summaries of long documents, and semantic search. For that last one the owner chooses only after recall@k and MRR on real German
  chunks for `bge-m3` and `multilingual-e5-large-instruct`, before the re-crawl (`benchmarks/docs/nas-document-ai.md` § 10; no real
  chunks on the build host). It also needs a released model with `hidden_states` for hub LAI-O5's `embed`, or an endpoint. No answer quality has been measured: the tiny model proves plumbing.
  Before the run on a real share: the index commits in batches (200 files or 5 s, each file whole in a
  savepoint) and `orchestrant-docs index` prints a progress line on stderr since 2026-11-17; left: `--max-file-mb`. Then record files per
  second and peak memory on a synthetic tree (10,000 small files, a 200 MB text file, a spreadsheet with a 200 MB sheet,
  a 2,000-page PDF) and decide about `iterparse` and a child process from the numbers. None of this is measured.
  Lowering the 256 MiB default changes a documented status and is the owner's call.

- **Recorded probe, llguidance on the hub's tiny GenAI model (ai-fleet clause 12).** GenAI 0.17.0 in
  `:latest` (index `7d4d4129`) ACCEPTS the tiny model's byte-level tokenizer: `set_guidance("json_schema", ...)`
  then greedy decoding of `abc` under
  `{"type":"object","properties":{"a":{"type":"string","maxLength":3}},"required":["a"],"additionalProperties":false,"x-guidance":{"whitespace_flexible":false}}`
  yields `{"a":"#$%"}`, ids `[123, 34, 97, 34, 58, 34, 35, 36, 37, 34, 125]`, then end token 256.
  So the schema tests use the tiny model and need no SmolLM2-135M fallback; SmolLM2 accepts the same
  schema too (`{"a":"par"}`). A schema without `maxLength` and with flexible whitespace makes the tiny
  model loop on `\t\n` until the budget, which is why the tests pin the tight one. Seeds 1 to 4 at
  temperature 1.0 give two distinct 24-token sequences on the tiny model (seed 1 differs). The Windows
  GenAI builds without llguidance (hub LAI-H2), so a schema there is `json_schema_unsupported`.
  OxidANT (254b7ea) and AccelerANTgine (2fb3ab7) found the new Windows chain image's GenAI built
  with llguidance; not re-probed for the Python wheel here. If it holds, the sentence before this
  one, `docs/source/language-models.md:117` and the docstring at
  `tests/unit/genai/test_options.py:68` are stale.

- [ ] **Prove the CPU and GPU release inventories on the installers.** The tracked
  `packaging/THIRD-PARTY-NOTICES.txt` carries the GPU labels since 6f37d0d, one per vendor on
  Windows (`windows-x64-directml-amd`, `-nvidia`). Release dry run 41491524238 passed the
  licence jobs of the Windows CPU and both DirectML builds. Left: confirm the original model and
  native license texts in the DEB, AppImage, MSI and MSIX of a real release round, and regenerate
  the notice for that round when an inventory moves. The `inventory-only` workflow input produces
  separately labelled, unverified ZIPs for this without exposing a candidate as an installer.

- [ ] **The NVIDIA packages have not run on a GPU they support** (S, ★★★). The Linux and Windows packages are built and
  passed on the CPU, and hosted runners have no GPU. Done on an RTX 30xx or newer when
  `compare_detector_parity.py` passes against the CPU and the profile shows the provider; the hub's
  `docs/gpu-artifact-receipts.md` has the commands. DirectML on an NVIDIA card is unproven too.
  The owner runs this on a machine with an NVIDIA GPU. No release until this and the Linux AMD check are done.

- [ ] **AMD's MIGraphX plugin has not run on a card** (S, ★★★). Hub CON231 step 2. Since hub `ffdbcf1c` the ROCm image
  has no in-tree MIGraphX provider, and `--device migraphx` registers AMD's module `libmigraphx-ep.so`
  (`docs/source/getting-started.md` § AMD's MIGraphX plugin). No AMD package of that form has been built, and nothing
  here has run with AMD's module: the build host is arm64 without an AMD GPU and the ROCm image is x64 only. Under WSL
  on the RX 9070 XT even a plain HIP copy does not return (hub CON202), so this takes native Linux: the owner runs it
  under Ubuntu on that machine. Done when a package built from `:latest-rocm` passes `compare_detector_parity.py`
  against the CPU and `orchestrant-smoke --ort-profile migraphx --verify-default` counts model operators on
  `MIGraphX`; the hub's `docs/gpu-artifact-receipts.md` has the commands. What is most likely to stop the first run:
  - The hub's bundle gate requires `MIGraphX` in the self-test's `registered_plugin_eps`, and the hub also says AMD's
    registration fails without a GPU (HIP error 100). If that holds, the hosted AMD build is red at the gate and only
    a builder with a card passes; the report's `plugin_ep_errors` then gives the reason. AMD's registration without a
    card was not run.
  - Three things read in AMD's source and never observed: the session and the profile say `MIGraphX`, `device_id` is
    the only option needed, and the N-th device offered is HIP device N.
  - The hub's `shared/python/verify_ort_profile.py` expects `MIGraphX` for `--device migraphx` since hub 366b8259,
    and the pin here is past it; no trace from a card has been checked with it.
  - `packaging/THIRD-PARTY-NOTICES.txt` still lists `onnxruntime-migraphx==1.30.0` for `linux-x86_64-amd`; generate
    it again from that round's packages. The tracked `onnxruntime-ep-amdgpu-*` texts come from the archives the hub
    pins and were never compared with the image's `${ORT_AMDGPU_EP_ROOT}/licenses`; compare them once.

- [ ] **CUDA still loads by provider name; check it on the hub's new NVIDIA image** (S, ★). Blocked on the hub
  publishing that image, the one whose CUDA provider is ONNX Runtime's plugin build. Owner, 2026-11-12: only AMD
  moves now. The hub's contract (`docs/consumer-image-contract.md` § The CUDA plugin EP) says Python is unchanged
  there: the `onnxruntime-gpu` wheel registers the module on import, and `providers=["CUDAExecutionProvider"]` keeps
  working.
  When the image is out, run the NVIDIA candidate's self-test and `cuda_probe` on it. If the name no longer loads in
  a bundle, CUDA becomes one more row of `PLUGINS` in `orchestrant/yolo/plugin_ep.py` (the name
  `CUDAExecutionProvider`, a root variable, `libonnxruntime_providers_cuda.so`) and takes the path MIGraphX takes.

- [ ] **Stale frames are reported but not yet measured on a camera** (M, ★★). Steps 1 and 2 are done (2026-11-15):
  one `LatestSlot` behind `FrameCapture` and `FletViewer`, and the GStreamer backend counts delivered and dropped
  frames and stamps each one, so `yolo-monitor`'s session summary prints `Frame age at read: p50, p95` and `Dropped
  frames: N of M the camera delivered` (OpenCV: `not counted by this backend`, as it has no reader thread). Left,
  only if the numbers show stale frames: run `yolo-monitor --no-display` for 10 s on a camera no service holds
  (himbeere2's belongs to `omni-observant`) with each backend at `--fps 30`; then for OpenCV a reader that calls
  `grab()` while the loop calls `retrieve()` under a lock, for GStreamer the CPU of `gst-launch` against a lower rate
  before any `videorate`. Picamera2 as a backend and one shared frame pump are separate, optional work.

- [x] **No perf baseline for the Pi's CPU** (M, ★★). `perf/` holds only
  `baseline-amd-ryzen-9-5950x-16-core-processor.json`. The owner records one with
  `quality-report.sh --perf-host --update-baseline` on the Cortex-A76 in a quiet window, as one `systemd-run --user`
  unit. Since 2026-11-14 the text row also records `load_s` and `peak_rss_mb`, and `detector_describing` times the
  detector while a description decodes on `--describe-threads`' one thread, so the baseline holds them. A first
  look in the CI image on the Pi (three cores, not quiet, no baseline): detector p50 238 ms alone, 570 ms beside a
  one-thread description; text 0.66 s to load, 287 MiB peak. Before that run: `perf-host.sh` builds `smollm2-perf`
  with seven threads, the Ryzen's choice; on the Pi that oversubscribes (34 tokens per second against 104 on one
  thread), so the text row would record the wrong configuration. Done when the Pi's `perf/baseline-*.json` is
  tracked and `--perf-host` grades against it.
  **Done 2026-11-18:** `perf/baseline-cortex-a76.json` (e69c4c5), recorded at night with the text thread rule; `--perf-host` grades against it.

- [ ] **The OpenCV backend never asks for a pixel format** (S, ★★). Seen by one reviewer only, not double-checked.
  `orchestrant/pipeline/capture/opencv.py` opens `VideoCapture(index)` with no API choice and sets size, rate and
  buffer but no `CAP_PROP_FOURCC`. OpenCV is the default backend and the default request is 1920x1080 at 30. Many
  USB cameras give that only as MJPEG and about 5 fps as raw YUYV; whether the monitor then runs at a fraction of the
  rate is not measured (no camera was used). Since 2026-11-14 the open logs the negotiated FOURCC, size and rate and
  names the backend with `getBackendName()`, and `get_info` carries `fourcc`. Next: measure on a real camera that no
  service holds (himbeere2's belongs to `omni-observant`): `v4l2-ctl --list-formats-ext -d /dev/videoN`, then
  `yolo-monitor --no-display` for 10 s with and without MJPG. If it shows, ask for MJPG first above 720p
  (`--pixel-format auto|mjpg|raw`) and warn when the measured rate is under half the reported one. Done when a unit
  test with a fake `cv2.VideoCapture` records the FOURCC asked for before the size, and the session summary names the
  FOURCC and the measured camera FPS.

- [ ] **The rest of the lab's copied wire logic** (L, ★★). The stream readers are one since 2026-11-14
  (`answers.stream_chunks`), and the context-overflow rule since 2026-11-15 (`orchestrant/benchmark/failures.py`,
  `classify_failure`, used by coding, chat and tools; owner decision: change now, re-record the lab baselines at the
  next lab session, as the three fingerprints moved). `_determinism_extra` is one since 2026-11-17
  (`determinism.determinism_extra`). Left: `benchmarks/bench_coding_stream.py:19-41` is a fourth stream reader. Last, as one
  batch: `bench_coding.ask` on `answers.read_stream` and `bench_coding_stream.py` removed, with a live run before and
  after as Phase 4b did. Done when a source test holds SSE parsing and `urlopen` to `client.py` and `answers.py`, with
  a short allow-list (`provenance.py`, `gateway.py`, `openai_api.py:251-253`, `geniex_toolcall_shim.py`). Shared
  flags and one ranking table are left out: cosmetic.

## Quality queue

The top 5 of `logs/code-quality/queue.md` (`bash scripts/linux/quality-report.sh`), leaving out `doc-dupes-hub.allow`, which the owner keeps in every AGENTS.md; re-sync when it moves.

- [ ] `file-size.allow:16` budget 3059 (allow_budget_total)
- [ ] `file-size.allow:17` budget 2490 (allow_budget_total)
- [ ] `file-size.allow:18` budget 1759 (allow_budget_total)
- [ ] `file-size.allow:15` budget 1377 (allow_budget_total)
- [ ] `file-size.allow:19` budget 1195 (allow_budget_total)
