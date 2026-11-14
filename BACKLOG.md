# Backlog

- [ ] **LAI-P3: the rest of the NAS document AI** (L, ★★). Hub language-AI milestone 2. `orchestrant-docs` indexes, searches
  and answers from a tree's text with no network (`docs/source/language-models.md` § The NAS document AI). Left: a run on a
  real share (only a synthetic tree has run), OCR and the VLM lane for `needs_ocr` pages, images, tables and layout, chunked
  summaries of long documents, and semantic search. For that last one the owner chooses only after recall@k and MRR on real German
  chunks for `bge-m3` and `multilingual-e5-large-instruct`, before the re-crawl (`benchmarks/docs/nas-document-ai.md` § 10; no real
  chunks on the build host). It also needs a released model with `hidden_states` for hub LAI-O5's `embed`, or an endpoint. No answer quality has been measured: the tiny model proves plumbing.
  Before the run on a real share: `_store` in `orchestrant/documents/index.py:205-223` commits once per file, also for
  files with no extractor such as photos, and the walk prints no progress. Batch the commits (every N files or every few
  seconds; a restart still skips unchanged files), print a progress line and add `--max-file-mb`. Then record files per
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

- [ ] **Nothing shows dropped or stale frames, and two frame slots never run with the GIL off** (L, ★★). Each capture
  backend answers a slow consumer its own way: OpenCV has no reader thread and ignores the result of its buffer-size
  hint (`orchestrant/pipeline/capture/opencv.py:41-42`), GStreamer drops the older of two queued frames and counts
  nothing (`orchestrant/pipeline/capture/gstreamer.py:112`, 219-228), and
  `orchestrant/pipeline/metrics/performance.py:24-34` has no drop count and no frame age. The "newest value, number,
  Condition" slot is written twice (`orchestrant/streaming/capture.py:51-109`,
  `orchestrant/pipeline/ui/flet_viewer.py:358-365` and 428-437). `tests/unit/test_thread_safety.py:210` and 240-243
  skip both slot tests where cv2 or flet is missing, so the free-threaded leg never drives them, although `AGENTS.md`
  (section 4) lists them as proven. Staleness is not measured. In steps, stopping when the numbers say so: (1, S) one
  pure-Python `LatestSlot` for `FrameCapture` and `FletViewer`, tested in `test_thread_safety.py` without cv2;
  (2, S) stamp frames at capture and report `frame_age_ms` and dropped frames in the log and the session summary;
  (3) only if step 2 shows stale frames: for OpenCV a reader that calls `grab()` while the loop calls `retrieve()`
  under a lock, for GStreamer the CPU of `gst-launch` at `--fps 30` against a lower rate before any `videorate`. Done
  when a slow-consumer test with numbered frames shows the consumer gets the newest one and dropped equals produced
  minus consumed. Picamera2 as a `yolo-monitor` backend and one shared frame pump are separate, optional work.

- [ ] **The benches time paths the shipped model does not use, and the loop times only inference** (M, ★★).
  `bench/demo_pytest_benchmark.py` times a letterbox on 1280x720 (94-109) and a YOLOv8-shaped `(1, 84, 8400)` decode
  (112-150); the shipped model stretches and returns `[1, 300, 6]`. `Detector.detect` in
  `orchestrant/yolo/core/detector.py` times only `session.run`. The shipped path is timed end to end on two-cats
  (`quality-report.toml:79-87`, the weekly `ai-regression-x64` history); missing are the split per stage, a 1080p
  input, draw and viewer. One audit run timed the glue on a Pi 5 (stretch preprocessing of a 1080p frame 4.1 ms,
  decode 0.15 ms, draw 0.3 to 1.9 ms; not re-measured, inference not measured), so the glue is probably not the
  bottleneck today; nothing would notice if that changed. First (S): two ungated bench rows beside the old ones,
  `detector_preprocess_stretch_1080p` and `detector_decode_e2e_300x6`. Then (S): stage timers in `PerformanceTracker`
  (capture wait, preprocess, inference, decode, draw, present) with p50 and p95 in the session summary. Done when both
  rows are in bench-history and a fake-clock test shows every timer filled once per frame. Nothing gates here by
  decision (`.github/workflows/linux-x64.yml:40`). A same-runner A/B of `detector_preprocess` and `detector_decode`
  against the previous `develop` commit would respect its reason, and a ratio to a plain copy needs its spread
  recorded for a few weeks first; whether either gates is the owner's call.

- [ ] **No perf baseline for the Pi's CPU** (M, ★★). `perf/` holds only
  `baseline-amd-ryzen-9-5950x-16-core-processor.json`; `quality-report.toml` already declares the commands. First: the
  owner records one with `quality-report.sh --perf-host --update-baseline` on the Cortex-A76 in a quiet window, as one
  `systemd-run --user` unit. Its two detector rows also show whether "usable CPUs minus one"
  (`orchestrant/yolo/providers.py:54-62`, 3 threads on 4 cores) beats ORT's default there; that is not measured. Two
  measurements go in before that run, so the baseline holds them: model load seconds and peak memory in the text
  command of `scripts/linux/perf_host_run.py:60-71` (every tool loads its own copy of the model; nothing records either),
  and detector p50 and p95 while a description decodes (not double-checked: `orchestrant/genai/engine.py:227-241` loads
  the model with no thread setting, and `--describe` decodes inside the camera process,
  `orchestrant/yolo/describe.py:215-217`). Only if the numbers show a problem: a thread cap for the describe engine,
  and an endpoint backend for `yolo-monitor --describe`. The document commands stay local. Done when the Pi's
  `perf/baseline-*.json` is tracked and `--perf-host` grades against it.

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

- [ ] **The rest of the lab's copied wire logic** (L, ★★). The stream readers are one since 2026-11-14 (`answers.stream_chunks`); left: the context-overflow rule
  exists twice (`benchmarks/bench_chat.py:47-68`, "copied" from `bench_coding.py:2233-2262`) and `bench_tools.py` has
  none, so the same HTTP 400 is `errored` there (683-691). `benchmarks/bench_coding_stream.py:19-41` is a fourth stream
  reader, and `_determinism_extra` is the same text in `bench_tools.py:1508-1529` and `bench_coding.py:2673-2698`.
  First: one `classify_failure()` in `orchestrant/benchmark`, used by coding, chat and tools. Last, as one batch:
  `bench_coding.ask` on `answers.read_stream` and `bench_coding_stream.py` removed. That adds `answers.py` to the
  coding fingerprint, so every coding baseline is re-recorded, with a live run before and after as Phase 4b did; the
  owner says when. Done when a source test holds SSE parsing and `urlopen` to `client.py` and `answers.py`, with a
  short allow-list (`provenance.py`, `gateway.py`, `openai_api.py:251-253`, `geniex_toolcall_shim.py`). Shared flags
  and one ranking table are left out: cosmetic.

- [ ] **The lab's graders have no mutation recipe and no coverage number** (M, ★★). One recipe in
  `scripts/linux/tests/package-license-mutations.json` targets `orchestrant/benchmark/` (the report write), none
  targets a grader or `benchmarks/`, and coverage
  is taken with `--cov=orchestrant` only, so `benchmarks/*.py` (about 15,000 lines) is outside the 60 % floor. First:
  about ten `bench.*` recipes on the rules that decide a verdict, each with its existing test file: an errored attempt
  leaves the denominator (`benchmarks/bench_tools.py:1392-1395`), a cut is not wrong (`bench_coding.py:2437-2438`), a
  call written as text fails a no-call case (`bench_tools.py:1010-1020`), agreeing repeats count once (1417-1421), the
  50 ms decode floor and the in-stream error (`orchestrant/benchmark/answers.py`), the control marks suspect cases, an
  agent cheat is refused (`bench_agent.py:144-167`), the Wilson interval and the sign test (`stats.py`). Done when the
  mutation workflow runs them and `AGENTS.md` (section 5) asks a test and a recipe for a new verdict rule too. A coverage
  number for `benchmarks/` needs the hub's driver to report a percentage per source; measure the combined number once
  before any floor, and gate on Linux x64 only.

- [ ] **The agent lane's `verify()` runs unbounded in the repository the model edited** (M, ★★). The agent's
  environment is allow-listed since 2026-11-13 (owner decision; `agent_base_env` in `benchmarks/bench_agent.py`).
  Then run `verify()` through the coding lane's
  launcher once it has moved to `benchmarks/sandbox.py`, which is also the first step of the tracked
  `bench_coding.py` split. Done when `bench_agent --self-test` shows a marker variable not arriving, a forked child
  gone after the timeout and endless output cut, and a recipe that removes the allow-list fails. A mount namespace
  stays a later opt-in. This lane runs on the lab host, not on the Pi.

- [ ] **No scored lane runs end to end over HTTP in CI** (M, ★★). Seen by one reviewer only, not double-checked.
  `scripts/linux/ai-regression.sh:38-50` serves the tiny model and SmolLM2 and runs only `orchestrant-bench contract`
  and `speed`. The lab suite stays offline (`benchmarks/tests/conftest.py:1-12`) and its `main()` tests stub the
  request (`benchmarks/tests/test_bench_integration.py:386-457`). So `bench_tools`, `bench_coding` and `bench_chat`
  never meet a real socket, a real stream and a real report write; 867d75e fixed a fault of that kind. First: the
  weekly run also starts `bench_chat`, `bench_tools` and `bench_coding --task-set classic` against `orchestrant-serve`
  on the tiny model and checks plumbing only: exit code, report written, each row exactly one of passed, errored,
  overflow or truncated, and `bench_compare` of the report against itself says unchanged. No score is read from it.
  Done when `scripts/linux/ai_regression_verdicts.py` has one verdict line per lane, so a crash or a missing report
  turns the run red.

- [ ] **Repeated defaults, two dead constants and a 45 W power guess** (M, ★). The camera defaults 1920, 1080 and 30
  are written in `orchestrant/yolo/cli.py:118-133` and again in `orchestrant/pipeline/types.py:32-36`, and the 0.5
  confidence in `cli.py` and `orchestrant/pipeline/constants.py:17`. `build_context` always passes the CLI's values, so
  the second copies are defaults nobody in the monitor uses. `DEFAULT_QUEUE_SIZE` and `DEFAULT_CAPTURE_INTERVAL`
  (`constants.py:30-31`) are used nowhere. With no RAPL on ARM, `orchestrant/pipeline/monitoring/power.py:118-123`
  estimates CPU power from the 45 W desktop default (`constants.py:29`), so "Power (est)" on a Pi can read several
  times too high (not measured). The loop reads the raw `argparse.Namespace` (`MonitorContext.args`), so a
  loop test fakes seven flags. First: delete the two constants, let `parse_args` take its camera defaults from
  `CameraConfig` and its confidence from the one constant, and with no readable power source and no
  `KATAGLYPHIS_CPU_TDP_WATTS` print no estimate, or a default that fits the CPU (the owner picks). Then hand the loop a
  small typed object in place of the Namespace. Done when a test shows `parse_args([])` equal to the `CameraConfig`
  defaults and every public name in `constants.py` imported somewhere. `--config` keeps its one key, and
  `orchestrant/smoke/ort_profile.py`, which calls `parse_args([])`, keeps working.

- [ ] **The telemetry text is built twice, and a detection is an untyped dict** (S, ★). The GPU log's
  double-encoded degree sign is fixed since 2026-11-14 (and one in `monitoring/plotting.py`;
  `tests/unit/test_no_double_encoded_text.py` scans the tree). `orchestrant/yolo/ui/draw.py:271-370` builds the same label strings as
  `orchestrant/pipeline/ui/viewmodel.py:76-105` with other separators, and headroom is computed in three places
  (`draw.py:275`, `viewmodel.py:81`, `resource_log.py:54`). A detection is a `dict[str, object]` (built in
  `orchestrant/yolo/core/postprocess.py`), so `draw.py:193-229`, under a `# noqa: C901`, and
  `_update_tracks_if_enabled` in `orchestrant/yolo/loop.py` re-check every field. First: a `TypedDict` (bbox, score, class_id), so the type checker guards the field names and the ladders and the noqa go; a
  dataclass would break the dict readers (`orchestrant/yolo/describe.py:62`, `capture()` in
  `scripts/diagnostics/detector_parity.py`). The OpenCV panel takes its text from `ViewLabels`, and headroom becomes a
  property of `PerformanceMetrics`. Done when a test shows the panel rows equal to the `ViewLabels` fields and Ruff's
  C901 holds the drawer without the noqa. The rest of the resource log stays as it is.

## Quality queue

The top 5 of `logs/code-quality/queue.md` (`bash scripts/linux/quality-report.sh`), leaving out `doc-dupes-hub.allow`, which the owner keeps in every AGENTS.md; re-sync when it moves.

- [ ] `file-size.allow:16` budget 3059 (allow_budget_total)
- [ ] `file-size.allow:17` budget 2490 (allow_budget_total)
- [ ] `file-size.allow:18` budget 1759 (allow_budget_total)
- [ ] `file-size.allow:15` budget 1377 (allow_budget_total)
- [ ] `file-size.allow:19` budget 1195 (allow_budget_total)
