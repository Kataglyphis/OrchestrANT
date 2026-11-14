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

- [ ] **One odd reply or a Ctrl-C can lose a lab run** (M, ★★★). In `benchmarks/bench_tools.py` only the HTTP call is
  inside the `try` (1328-1356); grading and the reply hash run after it. `_parse_args` (879-888) returns `None` or a
  number for the arguments `"null"` or `"3"`, and a null `function` or `message` raises in the grader. Every lane writes
  its report only after the last candidate (`bench_tools.py:1654-1702`, `bench_coding.py:2894-2948`,
  `bench_agent.py:1328-1373`, `bench_chat.py:367-388`). Under `bench_sweep.py` the loss is one tool for one candidate; a
  direct `--compare` run loses all of it. Read from the code, not provoked. First, in functions no ratchet freezes:
  `_parse_args` returns an error unless the JSON is an object, `_grade_one_call`, `_call_key` and `_message_hash` read a
  call through one helper that names a non-dict `function` as a failure, and `call_multi` returns
  `choice.get("message") or {}`. Then move "one attempt gives one row" out of `evaluate()` in tools and coding, with the
  `try` and a `<output>.partial.jsonl` row log inside it (the speed runner has one,
  `orchestrant/benchmark/openai_api.py:603-632`), catch Ctrl-C in a small `run_candidates()`, and lower each allow row
  in the same commit. Done when a table test of about 15 malformed assistant messages in
  `benchmarks/tests/test_bench_tools.py` gives one failed row each and no exception, a grader that raises on the second
  case leaves the first row in the partial file, and two `bench.*` mutation recipes prove the tests bite. The edit moves
  `tool_sha256`, so the baselines need one re-record; the owner says when.

- [ ] **The detection loop as users run it has no test on a push** (M, ★★). `tests/unit/test_yolo_monitor_loop.py`
  drives the loop with a fake session and `no_display=True`, so draw, the viewer and the real model never run inside
  it. `_render_output` with a viewer and the two-thread arrangement at the end of `run_yolo_monitor` (both in
  `orchestrant/yolo/monitor.py`) have no test. The real loop on a stand-in camera that plays `two_cats.jpg` exists as
  `check_describe_loop` in `scripts/linux/language_e2e.py:197-249`, but it runs weekly, outside pytest and with
  `--no-display`. First: move that stand-in camera into a shared test helper and add one pytest that runs
  `run_yolo_monitor` on `two_cats.jpg` for N frames, once with `--no-display` and once with a fake viewer (model
  required on CI as today). Done when it asserts both cats, N frames, N renders and exit 0. That needs no new flag.
  `--source`, `--max-frames` and `--report` (the session summary as JSON; `_log_session_summary` writes log text
  only) come when the Pi baseline needs a user-facing command, after `parse_args` is split.

- [ ] **"Preprocess, run, decode" is wired three times, and the golden checks the copy outside the package** (M, ★★).
  `_run_detection_loop` and `run_self_test` in `orchestrant/yolo/monitor.py` and `capture()` in
  `scripts/diagnostics/detector_parity.py` each assemble the sequence. The two-cats golden
  (`tests/unit/detector/test_modelant_contracts.py:88-119`), `scripts/linux/perf_host_run.py:40-57` and
  `scripts/linux/ai_regression_run.py:77-95` go through the diagnostic. All three call the same `preprocess()` and
  `postprocess()`; what can drift is about ten lines of wiring, such as a wrong stretch flag in the loop. First:
  `orchestrant/yolo/core/detector.py` with one `Detector(session)` that reads the input name, input size and stretch
  flag once (and the output layout, when the decoder takes one); the loop and `run_self_test` use it, and
  `MonitorContext` holds it in place of its loose session fields. Point the golden at `Detector`.
  `detector_parity.py` stays the one known outside copy, command line and JSON unchanged, and prefers `Detector` when
  the bundle has it. Done when a recipe that forces `stretch=False` in the loop's call fails the golden, and a source
  test holds the `postprocess(` call sites (`session.run(` will not do: `orchestrant/yolo/cuda_probe.py:61` calls it
  too).

- [ ] **A gst-launch that dies before its first frame is waited for five seconds** (S, ★). `_start_pipeline` waits
  `GST_FRAME_QUEUE_TIMEOUT` on the frame queue although the reader thread already saw the pipe end. First: the reader
  puts an end marker on the queue; done when a fake process that ends after the start-up wait fails the start
  without the five seconds.

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
  (112-150); the shipped model stretches and returns `[1, 300, 6]`. `_run_detection_loop` in
  `orchestrant/yolo/monitor.py` times only `session.run`. The shipped path is timed end to end on two-cats
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

- [ ] **No CI step checks that an installed package detects anything** (S, ★★). Seen by one reviewer only, not
  double-checked. `packaging/app.json:34` sets the self-test to `yolo-monitor --self-test`, which feeds a black frame
  (`_self_test_frame` in `orchestrant/yolo/monitor.py`) and reports only a count named `detections_on_blank_frame`,
  also when `--self-test-image` is given. `scripts/diagnostics/detector_parity.py` can capture with `--device cpu`, but
  no workflow calls it; only the on-request `windows-11-arm` job runs the golden under a bundle's own Python. First, in
  the clean-host job: fetch `two_cats.jpg` with `fetch-data-lock.py` over `scripts/linux/ai-regression.lock`, run the
  installed tar.gz's own Python on `detector_parity.py --device cpu`, and compare `detection-1.json` with
  `tests/unit/detector/two_cats_golden.json` using the hub's `compare_detector_parity.py`. Lighter: let
  `--self-test-image` print class, score and box in the self-test JSON and compare those. The golden keeps its five
  boxes: DEIM rows get no overlap suppression in any of the three detectors, raw YOLO heads keep theirs (owner,
  2026-11-12). Done when the step runs on every push and a recipe that flips the stretch flag in `run_self_test` fails
  it or the golden.

- [ ] **The OpenCV backend never asks for a pixel format** (S, ★★). Seen by one reviewer only, not double-checked.
  `orchestrant/pipeline/capture/opencv.py:33` opens `VideoCapture(index)` with no API choice; 38-42 set size, rate and
  buffer but no `CAP_PROP_FOURCC` and check no return value; 44-46 take the driver's `CAP_PROP_FPS` as the camera's
  rate, which feeds the frame budget; 76 reports "OpenCV DirectShow" on every OS. OpenCV is the default backend and the
  default request is 1920x1080 at 30. Many USB cameras give that only as MJPEG and about 5 fps as raw YUYV; whether
  the monitor then runs at a fraction of the rate is not measured (no camera was used). First: log the negotiated
  FOURCC, size and rate at open, name the backend with `cap.getBackendName()`, and measure on a real camera:
  `v4l2-ctl --list-formats-ext -d /dev/video0`, then `yolo-monitor --no-display` for 10 s with and without MJPG. If it
  shows, ask for MJPG first above 720p (`--pixel-format auto|mjpg|raw`) and warn when the measured rate is under half
  the reported one. Done when a unit test with a fake `cv2.VideoCapture` records the FOURCC asked for before the size,
  and the session summary names the FOURCC and the measured camera FPS.

- [b] **A stream's 200 goes out before its first token** (S, ★; waits for an owner decision). `orchestrant-serve`
  sends a stream's status and headers before `generate` starts, so a failure before the first token is an `error`
  event and `[DONE]` under 200, not its real status (the row "a failure inside a stream" in
  `docs/source/language-models.md` and two tests pin today's answer). Sending the 200 only with the first piece
  changes what clients see. The cancel of a whole answer and the 503 `busy` are done (2026-11-13).

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

- [ ] **Three lanes leave a file that decides their rows out of `tool_sha256`** (M, ★★). `benchmarks/bench_chat.py`
  grades through `answers.from_body`, `split_answer` and `accounting` (87-109) but lists only itself,
  `bench_chat_cases.py` and `determinism.py` (37-41). `bench_coding.py` takes `thinking_share` from `answers.py` (2461)
  and `bench_agent.py` takes `pass_hat_k` and `wilson_95` from `orchestrant/benchmark/stats.py` (1142-1171); neither
  file is listed. Only the chat gap can change a verdict. First: add `answers.py` to chat and coding and `stats.py` to
  agent, update the table in `benchmarks/README.md` (1196-1214) and re-record the baselines once; the owner says when.
  Done when a test shows every lab module a lane imports is in the lane's declared set (always or under a named flag)
  or on a short "decides nothing" list with one reason per row. Smaller, later: `schema_version`
  (`orchestrant/benchmark/provenance.py:36`) is written and never read; write a `result_format` number and let
  `bench_compare.py` refuse one it does not know. `--seed` goes to a lane only where its `seed_deterministic` answer is
  yes.

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

- [ ] **A hung test fails as the lane's time limit, not by name** (S, ★★). `pyproject.toml:295` runs pytest with
  `-ra -q`, and no timeout plugin is in the file. First: `pytest-timeout` in the `test` extra with a default limit.
  The `windows-11-arm` stage installs the test tools offline from the image's wheel store, so the wheel must be there
  first (not checked), and the nested runs in `benchmarks/tests` load no plugins. Done when a test that sleeps past the
  limit fails by name on every lane.

- [ ] **The uv lower bound is the oldest uv seen reading the lock, not the first one** (S, ★). `[tool.uv]
  required-version` in `pyproject.toml` is `>=0.11.3`: that uv syncs the revision-5 `uv.lock` with `--locked` on the
  Pi host, and no file there names the first release that reads it. By the commit messages uv 0.12.17 and older
  wrote revision 3 here and 0.12.23 wrote the first revision 5 (6bb8578). First: look the revision up in uv's
  changelog and raise the bound if a release from 0.11.3 on cannot read the lock. Raise it too in the commit of a
  relock whose format an older uv cannot read.

- [ ] **`monitor.py`, `parse_args` and the loop sit at the size limits** (S, ★). At b54238c
  `orchestrant/yolo/monitor.py` has 789 of 800 lines, `parse_args` (`orchestrant/yolo/cli.py:87-166`) is exactly 80
  lines, and `_run_detection_loop` is 78 of 80. No row of this area is in an allow file, but the next flag or loop
  feature trips a ratchet. First: `orchestrant/yolo/self_test.py` (`run_self_test`, `_self_test_frame`), `startup.py`
  (`build_context` and its helpers) and `loop.py` (the loop, its body as `_process_frame`); `cli.py` gets
  `_add_camera_args`, `_add_model_args` and `_validate`. Move every patch target in the same commit, since a moved
  function looks names up in its new module: `rg 'monitor\.'` over `tests/` (`test_monitor_build_context.py` and
  `test_monitor_describe_hook.py` in `tests/unit` patch names on the module) and
  `scripts/linux/language_e2e.py:237`, which runs only weekly, so run its loop check by hand once. Name the new
  modules in `AGENTS.md` § 1. Do this before any entry here that adds a flag or loop code. The existing ratchets keep
  it.

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

- [ ] **The GPU log line prints a broken degree sign, and the telemetry text is built twice** (S, ★).
  `orchestrant/yolo/resource_log.py:26` holds a double-encoded degree sign (bytes c3 82 c2 b0), so the temperature
  logs with a stray character before the sign. `orchestrant/yolo/ui/draw.py:271-370` builds the same label strings as
  `orchestrant/pipeline/ui/viewmodel.py:76-105` with other separators, and headroom is computed in three places
  (`draw.py:275`, `viewmodel.py:81`, `resource_log.py:54`). A detection is a `dict[str, object]` (built in
  `orchestrant/yolo/core/postprocess.py`), so `draw.py:193-229`, under a `# noqa: C901`, and
  `_update_tracks_if_enabled` in `orchestrant/yolo/monitor.py` re-check every field. First: fix the degree sign. Then
  a `TypedDict` (bbox, score, class_id), so the type checker guards the field names and the ladders and the noqa go; a
  dataclass would break the dict readers (`orchestrant/yolo/describe.py:62`, `capture()` in
  `scripts/diagnostics/detector_parity.py`). The OpenCV panel takes its text from `ViewLabels`, and headroom becomes a
  property of `PerformanceMetrics`. Done when a test shows the panel rows equal to the `ViewLabels` fields and Ruff's
  C901 holds the drawer without the noqa. The rest of the resource log stays as it is.

- [ ] **The Flet viewer never forgets a closed tab, and its tests wait with real sleeps** (S, ★).
  `orchestrant/pipeline/ui/flet_viewer.py` appends each session to `_sessions` (424) and nothing removes it, so every
  page load or reconnect of a long `yolo-monitor --web` run keeps a page and about 30 controls. `render()` adds a trend
  sample per frame (361) while the numbers behind it change every 10 frames (`STATS_UPDATE_INTERVAL_FRAMES`), so the
  120 samples hold about 12 readings. `tests/unit/test_flet_viewer.py:75`, 119 and 143 sleep for real, and 145 asserts
  an exact count after a 0.1 s wait, which can fail on a loaded Pi. First: remove a session when its pump ends, under
  the `_changed` lock; replace the sleeps by a barrier on the loop
  (`asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(5)`); add a trend sample when the stats refresh.
  Done when tests with the existing fake page show a closed session gone and the history growing by one per refresh,
  and a source test finds no `time.sleep` in that test file. One encode per snapshot for several tabs is optional until
  someone runs several (one audit run timed 11.1 ms per browser tab and 1080p frame on a Pi 5; not re-measured).

- [ ] **Python's resize is proven equal to the contract only on pictures of a few pixels** (S, ★). The four
  inference-v1 cases are 2x1 or 3x2 pixels. Rust and C++ sample with integer maths;
  `orchestrant/yolo/core/preprocess.py:92-95` and 108 hand it to `cv2.resize(..., INTER_NEAREST)`. A plain-Python model
  of the classic OpenCV 4 formula agrees for 480 to 1920 wide frames into 640 and differs in 95 of 640 columns for 2304
  and 4608 wide. Nobody ran cv2 (the project asks for OpenCV 5, and riscv64 uses another build), so this is an unproven
  risk, and those widths are not ones the Python monitor normally gets. First: one pytest, no contract change: build
  the `(i * 37) mod 256` frame at 2304x1296, 1920x1080, 1280x720 and 480x659 and compare `preprocess()` with a numpy
  integer-index reference (`x * src // dst`) for stretch and letterbox, on the x64, arm64 and riscv64 lanes. Only if it
  fails, replace `cv2.resize` by an index take and time it with `bench/`. Done when the test runs on the three lanes
  and one `cv.*` recipe that swaps the sampler fails it (`cv.bilinear-resize` proves only the tiny cases today).

## Quality queue

The top 5 of `logs/code-quality/queue.md` (`bash scripts/linux/quality-report.sh`), leaving out `doc-dupes-hub.allow`, which the owner keeps in every AGENTS.md; re-sync when it moves.

- [ ] `file-size.allow:16` budget 3059 (allow_budget_total)
- [ ] `file-size.allow:17` budget 2490 (allow_budget_total)
- [ ] `file-size.allow:18` budget 1759 (allow_budget_total)
- [ ] `file-size.allow:15` budget 1377 (allow_budget_total)
- [ ] `file-size.allow:19` budget 1195 (allow_budget_total)
