# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **The speed runner knows whether an answer arrived.** `answers.py` reads
  every place a server puts thinking (inline `<think>`, `reasoning`,
  `reasoning_content`) and records `finish_reason`, `answered` and `ttfa_s`
  (the first token after any thinking); a reply cut at `max_tokens` has no
  `wall_s_to_answer`, and the summary prints `Answered k/n` and averages
  time to an answer over the answered rows only. The viewer and
  `orchestrant-bench report` follow — their answer column says how many rows
  it covers ("24.8s (8/9 answered)"), since a run that cut more replies would
  otherwise rank fastest — and read an older report's never-closed `<think>`
  as all thinking.
- **`bench_compare` is a speed tripwire too.** `compare_speed.py` pairs decode,
  prefill and TTFT per prompt and flags SLOWER when the median decode ratio
  falls by more than 5 % or the prompts' own scatter; a CPU lane measured over
  0.3 cores of other load is reported, not judged; CPU-rail J/token is
  reported; it derives the load for reports older than `other_cores`, and a
  run that load could explain — slower under load, or faster than a loaded
  baseline — is printed `NOT judged`. A speed report no longer gets a latency
  verdict (its wall moves with `max_tokens` and includes cut replies). A
  contract pair, or any pair with nothing in common, now exits **3**
  (`NOTHING COMPARED`; 2 stays argparse's usage error) instead of "no
  regression detected".
- **`orchestrant-bench contract` — re-check the server behaviours the lab
  depends on, and diff them across runtime upgrades.** Every GenieX release
  moved one of them (v0.6: the output cap, `max_tokens`, tool-call parsing, the
  prefix cache; v0.7: QAIRT stop sequences, `power_mode`, the bundle's system
  prompt), and each was found by hand after it had distorted a number. Eighteen
  checks, each answering `yes`/`no`/`inconclusive`/`error`/`skipped` with
  evidence; `--diff` exits 1 when any answer moved. Its first run found that the
  v0.6.1 QAIRT lane ignores a chat `stop` while reporting
  `finish_reason: "stop"` and caches a conversation extended by a turn but not
  an identical repeat. Two of its checks, `temperature0_is_greedy` and
  `identical_repeat_intact`, name the GenieX defects under **Fixed** below;
  every determinism check puts an unrelated request before each draw.
- **Measured CPU and energy per request on a Windows host.** `hostload.py`
  finds the process listening on the lane's port and reports its CPU-seconds
  over the request (`lane_cpu_s`, `lane_cores`) and everything else the machine
  did meanwhile (`other_cores` — a llama.cpp CPU lane lost about 20–55 % of its decode
  rate to 0.5–1.0 cores of IDE and antivirus work, and no report said so);
  `energy.py` reads the Windows
  Energy Meter Interface through PDH (English counter names, so a German
  install reads the same) and interpolates the CPU-cluster rails' cumulative
  energy onto each request's exact bounds (`cpu_rail_energy_j`,
  `cpu_rail_j_per_token`, and `*_net_*` net of an idle baseline). The
  Snapdragon X exposes `CPU_CLUSTER_0/1` and no NPU or GPU rail, and every
  report's `energy.scope` says so. This replaces the roadmap's "joules are
  not measurable here" for the half of the question that separates the lanes.
- **`benchmarks/docs/geniex-v0.7.0-cpu-npu-2026-09-24.md` — the GenieX
  v0.6.1 → v0.7.0 upgrade, measured on the CPU and NPU lanes**, with every raw
  report under `benchmarks/benchmark_results/2026-09-23-geniex-upgrade/`. The
  upgrade changed two lane behaviours (QAIRT `/v1/completions` stop sequences,
  `power_mode`) and no model output; its NPU slowdown is `--log info` (−13 %
  decode, +21 % CPU-side J/token gross), not the runtime. It also retires
  beliefs of the hub's GenieX page: the QAIRT lane caches a conversation
  extended by a turn, "T=0 samples" is a server defect rather than the models,
  since v0.6 the NPU + CPU pair delivers less than the NPU lane alone (0.54–0.78×), and
  on llama.cpp `4ff829e` one Unsloth Dynamic `IQ3_XXS` answered correctly again
  (a three-question smoke test). Revised the same day after a six-lens,
  adversarially verified review of the lab — the page's "What the review
  corrected" lists every number that moved.
- **Runtime provenance.** Reports record `runtime` — the serving build (GenieX
  CLI, QAIRT and llama.cpp versions from the lane's own binary, or Ollama's
  `/api/version`) and the lane's serve flags — and `interpreter`, the Python
  build's own platform (an x64 Python under emulation still reports ARM64).
  `bench_compare` names `SERVING RUNTIME CHANGED` and changed serve flags
  before any score. Speed reports gained the `provenance` block the Sphinx page
  already claimed they had, and `bench_compare` reads it.
- **The PowerShell lint gate this repo never had.**
  `scripts/windows/Invoke-Lint.ps1` (the dev-box command) and the
  `lint-powershell` job of `.github/workflows/windows-2025.yml` (CI) run
  ANTfrastructure's
  `windows/scripts/Invoke-Lint.ps1` over `scripts/windows/`: a mandatory parse
  pass, an AST-trap pass (comma-attribute quoting, switch shadowing, glued
  parameter tokens) and PSScriptAnalyzer 1.25.0 against the hub's own
  `PSScriptAnalyzerSettings.psd1`, consumed by reference so nothing is copied
  here to drift. `-FailOnAnalyzer` and `-Path` are both passed and neither is
  optional: without the first the analyzer prints findings and exits 0,
  without the second the hub script grades the HUB's trees out of this
  checkout. Before this, ~400 lines of Windows build driver were gated by
  nothing but running them. The gate landed as a standalone
  `.github/workflows/powershell-lint.yml`; that file is gone again in the same
  unreleased cycle, for the reason under **Changed** below.
- **The hub's `--ratchets` measurement gates, with their freeze files seeded and
  committed.** `scripts/linux/run-lint-gates.sh` now passes `--ratchets`
  unconditionally, adding nine `--root` gates: the docs cross-reference gate,
  code size, complexity, dead functions, comment size, stdout returns, masked
  declarations, trailing conditionals and the shellcheck warning ratchet. The
  first run is the seed, so `function-size.allow` (22 rows), `file-size.allow`
  (9), `code-complexity.allow` (28), `comment-size.allow` (7) and
  `dead-functions.allow` (1) land in the same change; four gates need no file
  because this tree has nothing over their limits. Every row carries a reason,
  because the contract is four-way — a frozen number that shrinks, or an entry
  whose subject is gone, fails exactly like new growth.
- **`benchmarks/`, `frontend/`, `bench/` and `examples/` are graded by the
  static-analysis gate.** The hub's `STATIC_ANALYSIS_EXTRA_PATHS` (Linux) and `-ExtraPaths`
  (Windows) reach first-party code that is not `$PACKAGE_NAME`; 48 files of it
  had been outside every analyser because the driver's target list was the
  package, `tests/`, `docs/source/conf.py` and `setup.py`. codespell, vulture,
  ruff check and ruff format are clean over all four; bandit could not reach
  them at all until the pin below. The Windows lane invokes
  the driver through `pwsh -Command` rather than `-File` for this: `-File`
  binds ONE element of an array parameter and silently discards the rest, so
  the lane would have reported green over two trees nothing had read.
- **Decision, 2026-09-15: the NAS document-AI thread moves here from
  ANTfrastructure, beside the benchmark lab that owns the question.**
  `benchmarks/nas_census.py` (the census: walk a document tree and publish the
  four numbers — total PDF pages, scanned fraction, German fraction, table
  density — plus the gate that decides whether a VLM gets budget at all),
  `benchmarks/tests/test_nas_census.py` (44 tests, offline, and green without
  PyMuPDF, which is the environment this repo actually has), and
  `benchmarks/docs/nas-document-ai.md` (the 36-agent review that prescribes
  both: model shortlist, per-file-type routing, and why the Hexagon NPU cannot
  read a page). The hub keeps no copy. The page was written in ANTfrastructure
  and says so, and its links now resolve from here — hub pages through
  `third_party/ANTfrastructure/`, the census tool as a sibling. The census
  needs no dependency this repo does not already have: it is stdlib-only, and
  `pip install pymupdf` upgrades it from an extension census to per-page
  classification, reported as SKIPPED rather than as a fabricated zero when it
  is absent.
- **AMD GPU support across monitoring and the benchmark runner.** `GPUProbe`
  now picks a vendor: NVML for NVIDIA as before, and AMD through ADL on
  Windows (the driver's `atiadlxx.dll`) or the `amdgpu` sysfs counters on
  Linux. The Windows ADL path reads the modern PMLog sensors for utilization,
  temperature and power — the legacy Overdrive5 API that `pyadl` wraps
  returns `ADL_ERR` on RDNA-era cards, verified on an RX 9070 XT — and the
  adapter memory APIs for VRAM; no Python package is required. The
  `gpu-rocm` extra adds `amdsmi`, used only to resolve a Linux card's
  marketing name. AMD devices are ordered largest-VRAM-first, so index 0 is
  the discrete GPU on an APU+dGPU host. `orchestrant-bench` now records
  `hardware.gpu` (vendor, name, VRAM, backend) and per-prompt
  `gpu_utilization_percent`, `gpu_memory_used_gb` and `gpu_power_watts`; the
  viewer renders them as a hardware row, a comparison column and a chart.
- **The benchmark viewer is a Reflex app** in `frontend/` (the `frontend`
  extra), replacing the React/Vite app that travelled with the lab. It reads
  the manifest directly (`ORCHESTRANT_BENCHMARK_MANIFEST` overrides the
  default), so the build/copy step is gone; the table and Wilson-interval
  logic is pure Python in `frontend/frontend/benchmark_data.py`, tested
  without Reflex.
- **`orchestrant.benchmark` and `orchestrant-bench`: the LLM endpoint runner.**
  Ported from ANTfrastructure's `linux/llm-stack` (one request path, backend
  registry, speed/lane measurements, statistics, provenance) so OrchestrANT
  owns the measuring identity the capability benchmarks build on. Stdlib plus
  the already-present `psutil`; named backends come from the serving stack's
  registry, found through `LLM_BACKENDS`, the hub submodule, or a package copy.
  Tests live in `tests/unit/benchmark` and run offline; `requests` joins the
  test extra for the live-contract modules.

### Changed
- **The Python 3.13 test legs are gone, on both lanes.** `test-python-versions`
  in `.github/workflows/ubuntu-26.04-amd64-arm64.yml` and `$PythonVersions` in
  `scripts/windows/Build-Windows.ps1` are now `3.14 3.14t`, and the docs index
  links and includes the 3.14 report instead of the 3.13 one. ANTfrastructure's
  ONNX Runtime single-source rule (2026-09-23) moves a synced venv's ORT onto
  the image's chain wheels, which are cp314 only, so a 3.13 leg inside the
  images fails its sync; the images carry CPython 3.14 only (owner decision).
  The classifiers keep 3.13, and ruff/ty still target it.
- **`third_party/ANTfrastructure` → `49be50f0`, and NO re-sync with it.** The
  shared-config templates did not move between `19286e9f` and this pin, and
  the drift gate is what says so rather than this sentence assuming it:
  `sync-shared-config.sh --repo-root . --check` reports both declared assets
  OK and `Shared config in sync.` What the pin carries for this repo is the
  bandit argument fix and the Windows lane's two new inputs, both below.
- **bandit reaches `benchmarks/`, `frontend/`, `bench/` and `examples/` for the
  first time, and `BANDIT_EXCLUDES` / `-BanditExcludes` owns its `-x` list.**
  Both hub drivers spelled the extra paths as one `-r` per target, and
  bandit's `-r` is a `store_true` flag against a single `nargs='*'` positional,
  so the gate died with `unrecognized arguments: benchmarks frontend bench
  examples` (exit 2) every time the knob was set. Measured here against bandit
  1.9.4, reported upstream, fixed upstream in `9a69214b` on the Linux driver
  and its Windows twin; the two comment blocks that documented the breakage
  are deleted with it. With the knob working, the hub's default exclude list
  leaves 1025 findings — 973 of them in `benchmarks/tests`, 959 of those B101
  (`assert` used in a test). The default already drops this repo's top-level
  `tests/` for that reason and its entries are anchored at the working
  directory, so the lab's own suite has to be named: both lanes now pass the
  hub default plus `benchmarks/tests`, which is the same judgement
  `pyproject.toml` already records for ruff (`benchmarks/tests/*.py` ignores
  `S101`). 52 findings remain (43 low, 9 medium, 0 high, over 18699 lines) and
  they are NOT triaged: 33 in `benchmarks/` and the 19 under
  `orchestrant/benchmark` that the extra-paths entry above already recorded as
  the static-analysis lane's pre-existing red, beside vulture's 18, codespell's
  5 and ty's 6. They are the same owner decision, not a new one, and the shape
  of it is now visible: `pyproject.toml` already ignores ruff's twins of these
  exact codes in these exact trees (`S101`, `S105`, `S108`, `S310`, `S603`,
  `S607` for `benchmarks/*.py`; `S110`, `S112`, `S310`, `S603`, `S607` for
  `orchestrant/benchmark/*.py`) as a frozen port baseline with a tracked
  follow-up. bandit has no per-file-ignore table, so saying the same thing to
  it means either a `--skip` list or ~52 inline `# nosec` twins — a
  suppression-policy call, which an adoption pass does not get to make.
- **The PowerShell lint job is `lint-powershell: true` on the hub's reusable
  Windows lane, and `.github/workflows/powershell-lint.yml` is deleted.** The
  hub grew the `lint-powershell` / `lint-path` inputs (`d97fe91b`) because
  this repo and OxidANT hand-wrote the same job within a week. The deleted
  file agreed with the lane on everything that matters — `windows-2025`, the
  same pinned checkout SHA with `submodules: true` and `fetch-depth: 1`,
  PSScriptAnalyzer pinned to `1.25.0`, the hub's own `Invoke-Lint.ps1` with
  `-FailOnAnalyzer` — and differed only in the directory, which is
  `lint-path: scripts/windows`. Two things the input does not carry were kept
  rather than dropped with the file: the `concurrency` group moves to
  `windows-2025.yml`, where it now also covers the container build that never
  had one, and the gate loses its `paths:` filter, so it runs on every push
  and pull request to main instead of only on `scripts/windows/**` — strictly
  more coverage, never less. `scripts/windows/Invoke-Lint.ps1` stays, for the
  reason `scripts/linux/run-lint-gates.sh` stays: it is the dev-box twin of a
  lane that cannot call it. The separate README badge goes with the workflow.
- **`third_party/ANTfrastructure` → `19286e9f`, and the shared config re-synced
  with it.** The pin carries the knobs above plus the reusable workflows below.
  Hub `e03bbe42` rewrote `shared/linux/templates/antfrastructure.sh`, which this
  repo vendors as `scripts/linux/lib/antfrastructure.sh`, so the shared-config
  drift gate goes red the moment the pin moves: both halves are in one commit.
  The body gained a three-place `ANTFRASTRUCTURE_DIR` search (explicit
  environment answer, submodule, plain `antfrastructure-tools` sibling) and a
  not-found message that branches on whether `.gitmodules` declares the
  submodule at all.
- **`lint-gates.yml` and `submodule-pins.yml` are `uses:` lines now.** Both jobs
  were copies, and both headers said so — the lint one carried the instruction
  verbatim ("replace this job with a `uses:` when ANTfrastructure grows a
  reusable lint lane"). 108 lines of workflow become 16 lines of configuration;
  the runner pins, the SHA-pinned checkout, the uv install, the Pester 3.4.0
  measurement and the pin-suite wiring all move upstream. What stays is this
  repo's own: when each lane runs, and `ratchets: true`.
- **Every code and prose cross-reference names the tree its page is in.** The
  hub's doc-links gate takes `--root` now, so this repo can grade its own pages;
  it found twenty dangling references, all of them already wrong for a reader.
  The hub's pages are spelled `third_party/ANTfrastructure/docs/...` and the
  lab's `benchmarks/docs/...`; `AGENTS.md` no longer points a hub heading at its
  own file; two bare `§ 1n` / `§ 1m` in `benchmarks/README.md` now name the
  GenieX page they belong to; and the two generated-file rules in `.gitignore`
  are anchored (`/docs/source/README.md`), which is both more precise and no
  longer shaped like a pointer.
- **`Build-Windows.ps1`'s `Write-Log` is `Write-LogInfo`.** It shadowed a cmdlet
  PowerShell ships (`PSAvoidOverwritingBuiltInCmdlets`), which is the one
  finding the new PowerShell lane had; fixing it first is what lets the lane
  start green instead of starting with an exception. The name now also matches
  its three siblings.
- **`onnxruntime-genai` / `onnxruntime-genai-cuda` follow ANTfrastructure again:
  `0.14.0` → `0.15.2`.** Three pins carried the comment "keep in sync with
  ANTfrastructure ONNXRUNTIME_GENAI_VERSION" while that key had already moved to
  `v0.15.2`. 0.15.2 resolves `onnxruntime` 1.29.0, matching the hub's
  `ONNXRUNTIME_VERSION=v1.29.0`. `onnxruntime-genai-directml` stays unpinned —
  PyPI publishes nothing at that version for it — and its comment now says so
  instead of naming the superseded `v0.14.0`.
- **`ruff` config: `CPY001` (missing-copyright-notice) is explicitly disabled.**
  It and `PLR0917` left preview in ruff 0.16, so pinning `ruff==0.16.4` took a
  `select = ["ALL"]` project from 3 findings to 63 with no code change. This
  project states its licence once in `LICENSE`, not per file; the four
  `PLR0917` findings were fixed in the code instead. `vulture` now runs at
  `min_confidence = 100`, and `codespell` skips generated Cython `.c` output and
  knows `nd` (`tvm.nd.array`) and `DocumANTation` — each with the reason in
  `pyproject.toml`.
- **Renovate's "moves with ANTfrastructure" rule covers every spelling of those
  pins.** `matchDepNames` named only `ruff` and `onnxruntime-genai-cuda`, so the
  `.pre-commit-config.yaml` rev (which the pre-commit manager reports as
  `astral-sh/ruff-pre-commit`, not `ruff`) and the plain `onnxruntime-genai` pin
  could still be bumped unattended — the exact drift the rule's own description
  says it prevents.

- **Decision, 2026-09-15: the repository declares all four of its topics, and
  nothing is split out.** It is the `orchestrant` package, the family's LLM
  benchmark lab (`benchmarks/` plus the `orchestrant.benchmark` runner), the
  template a new Python AI project starts from, and the Reflex viewer in
  `frontend/`. `pyproject.toml`'s description and keywords, `README.md` § About
  The Project and `AGENTS.md` § 1 now all say so; no code moved.
- **Decision, 2026-09-15: the one large tracked binary is documented, not
  rewritten out of history.** `resources/models/yolov26m.onnx` (78 MiB) stays
  tracked because it is the YOLO monitor's default `--model`; the reason and
  the explicit refusal to run `filter-repo`/`filter-branch`/BFG/LFS are in the
  new `resources/models/README.md`, and `.gitignore` now excludes `*.onnx`,
  `*.gguf`, `*.pt` and `*.pth` with a single exception for that file, so
  nothing new joins it.
- **`Build-Windows.ps1` calls the hub's Windows Python drivers instead of
  re-inlining them.** Static analysis is now
  `third_party/ANTfrastructure/windows/scripts/python/Invoke-CiStaticAnalysis.ps1`
  and both packaging steps are `Invoke-CiPackaging.ps1`, each launched as a
  child process with `-RepoRoot` (and `-PackageName orchestrant`, without which
  the driver would derive the distribution name). Tool-list parity with the
  Linux lane is structural now rather than a rule restated in a comment. The
  pytest matrix stays local: the hub's `Invoke-CiTests.ps1` was removed
  upstream in ANTfrastructure 2eaed40e.
- **The Windows bench demos are soft, like the Linux lane's.**
  `bench/demo_*.py` failures log `<demo> skipped` instead of failing the step,
  matching `ci_tests.sh:113-120`; the unit tests above them stay hard.
- **`.github/copilot-instructions.md` is Copilot's slice only**, in English,
  ~85 lines instead of 200: typing policy, the `uv`/`ruff`/`ty` commands, the
  commit format and Do/Don't, with the gate mechanics linked to `AGENTS.md`
  § 4 rather than retold. The `archive/` directory it named does not exist.
- **`tests/fuzzy/` is gone**, with its `AGENTS.md` entry. It held one
  `.gitkeep` and never a hypothesis or atheris test; git history keeps it.
- **Dependabot is kept deliberately, and its comment is true now.** The claim
  that the config had "never actually opened a PR" was false (#13-#33), and its
  retirement condition — the shared Renovate preset not being on
  ANTfrastructure's default branch — no longer holds. It stays as the only
  unattended watcher, because the Renovate GitHub App is installed nowhere in
  this family.

### Fixed
- **Two GenieX v0.7.0 (and v0.6.1) defects the lab had read as properties of
  the models.** `temperature: 0` is treated as "unset" on both lanes and the
  default sampler runs (`top_k: 1` gives greedy decoding; `temperature: 0.01`
  is honoured as a low temperature); and an
  identical request sent twice in a row takes a cache path that changes the
  reply — the llama.cpp lane prefills 0 tokens and samples the first token
  from the previous reply's logits, the QAIRT lane re-uses part of the dialog.
  Every `--repeats` attempt after the first, the determinism probe and the
  contract's T=0 and seed checks measured that path; the QAIRT lane's
  "order dependence" was this. `bench_tools` and `bench_coding` now send a
  throwaway request between repeats of one case (`client.spacer`, recorded as
  `config.repeat_spacer`), and the probes are spaced. Live probes and their
  output are in the run directory's `v070r2-probes/`.
- **Thinking share and "time to a finished answer" were computed over cut-off
  replies**: an unclosed `<think>` scored 0 %, the summary averaged only the
  rows that closed it, and time to the token cap was the ranking metric. On
  the v0.7.0 CPU lane that printed 86 % where the run was ~95 %, and 12.5 s "to
  a finished answer" for a run in which 6 of 9 replies never left `<think>`.
- **Net energy against one 5-second idle baseline.** Two NPU runs 15 minutes
  apart read 1.26 and 1.84 W, which turned a +21 % gross difference into a
  published "+70 %". The baseline is now taken before and after the requests,
  rows are netted against the mean, and a drift over 0.2 W is reported.
- **Per-request energy edge cases**: the EMI energy and timestamp counters are
  read in one PDH collection (two queries could pair a new stamp with the
  previous second's energy), a counter that went backwards or stood still is
  not a sample, `--idle-seconds 0` no longer divides by zero, and a lane
  process that exits reads as unknown rather than 0 CPU-seconds.
- **`lanes` summed per-lane rates over unequal windows** (published 0.65×/0.66×
  for NPU+CPU; 0.54×/0.56× delivered). The report's aggregate `tok_per_sec` is
  now delivered throughput (the sum stays as `summed_tok_per_sec`, and
  `bench_compare` compares sums when one side is an older report); it sends a
  fresh prompt per phase (the "together" request was a cache hit) and counts
  thinking tokens.
- **Contract answers on insufficient evidence**: an empty `/v1/completions`
  reply was "stop honoured", any 5xx mentioning "context" was a clean
  overflow, a 0.4 s cold prefill decided the cache rows, and thinking in
  `reasoning_content` was "no `<think>`". The first three now answer
  `inconclusive` or `no`; thinking in `reasoning_content`/`reasoning` (or
  counted in `reasoning_tokens`) answers `yes`. `contract --base-url` detected
  the model on the module default URL, not the lane it probed.
  `power_mode_understood` asks whether the value is *validated*, times the
  reload it causes and ends with a plain request so the lane is left as
  launched.
- **`effective_n` counted agreeing repeats as independent trials.** When every
  repeat agrees on pass/fail the case is the unit, whatever the text did.
- **`tool_sha256` depended on line endings** (a Windows and a WSL checkout of
  one commit disagreed); it is normalised for every tool. The speed runner
  also takes it at the start and names a mid-run change; the other tools take
  it at the end only, as before.
- **Energy was recorded for remote lanes**, from this host's rails; the meter
  now runs only when the lane's process is local. A counter that went
  backwards restarts the curve instead of unmetering the rest of the run, and
  a single idle baseline reports `net_reliable: null`, not `true`.
- **`mark_suspect_cases` and the viewer undid the counting rule**: excluding a
  control's suspect cases reset `effective_n` to attempts for a lane whose
  repeats agree, and the viewer rounded `passed × n / total` into a count;
  both now use the cases and `effective_k`.
- **cp1252 crashes on Windows**: a redirected `contract --diff` or
  `bench_compare` died on `→` with exit 1 — their "changed"/"REGRESSION"
  code. Every CLI now writes UTF-8.
- **`bench_tools --turn-growth` dropped `--tools` and `--context-tokens`**, so an
  opencode-preamble turn-growth run measured the eight default tools.
- **`bench_sweep` put the repository's parent on `PYTHONPATH`** for the tools
  it runs.
- **The determinism probe measured the wrong thing.** It sent two identical
  requests back to back, and on GenieX an identical follow-up takes a cache
  path that changes the reply (above): two open-ended T=0 requests to the
  v0.6.1 QAIRT lane came back different, while the old 8-token "ready" reply
  happened to survive it. It now asks for 48 open-ended tokens with an
  unrelated request between the draws. Spaced, the QAIRT lane is reproducible
  — its bundle samples from a fixed seed, re-seeded per request — and the
  probe records it deterministic, so `bench_compare`'s strict per-case mode
  applies there; the llama.cpp lanes, which sample at T=0 ("unset"), record
  not deterministic.
- **`cpu_percent` never saw the request.** It averaged a sample taken before
  the request and one taken after it; a CPU lane pinning 7.5 of 8 cores read
  as idle. It is now integrated over the request wherever the lane shares the
  harness's host (`cpu_percent_method` says which).
- **`orchestrant-bench speed` no longer guesses a model from a listing.**
  GenieX answers `/v1/models` with its whole local cache, so `--base-url`
  alone benchmarked — and made the lane load — whichever cached model sorted
  first. Several listed ids are now a refusal that names them.
- **Windows hosts: ~33 s of dead time per prompt, ~40 s per report, and a
  paid host probed on every run.** A refused localhost connect costs 2–4 s on
  Windows; the sampler asked four Glances URLs twice per prompt and
  `busy_lanes()` asked every registry entry in turn — including the one marked
  `probe: false`, whose discovery request costs money. A failed Glances is now
  asked once per run, the lane probe is parallel and honours `probe: false`,
  and `System Idle Process` (pid 0) no longer ranks as the busiest process.
- **The Linux static-analysis gate could never start, so it graded nothing.**
  Every Linux lane run from 2026-09-12 to 2026-09-15 ended
  `static analysis (orchestrant) FAILED (6 of 6)` with six identical
  `error: Failed to spawn: codespell` / `bandit` / `vulture` / `ruff` /
  `ty` lines — not one finding, on either arch. The 231-package
  `uv sync` landed correctly in `.venv_static_analysis`, because
  `uv_sync_project` reaches it through `_CURRENT_VENV_PATH`; the tools then
  looked somewhere else. `uv_venv_create` deliberately does not activate, and
  this wrapper clears `VIRTUAL_ENV` to keep the sync off the image's
  root-owned `/opt/venv`, so the driver's `uv run --active` had no active
  environment, fell back to the project default `.venv`, created it, installed
  the 28 base dependencies and found none of the analysers — they are declared
  in the `test` EXTRA, which that fallback sync does not install. It could only
  ever pass on a box where `.venv_static_analysis` already existed, since
  `uv_venv_ensure` activates on its reuse branch; on clean CI it could not.
  `scripts/linux/ci_static_analysis.sh` now exports `UV_PROJECT_ENVIRONMENT`
  (the driver's own `VENV_DIR`, computed by mirroring `detect_workspace`) and
  `UV_NO_SYNC=1` — the second is not optional, because `uv run` would
  otherwise re-sync that environment with the DEFAULT extras and uninstall the
  tools it is about to spawn. All six analysers now read this tree. They are
  not silent about it: ruff check and ruff format are clean, and codespell,
  bandit (52 findings — 43 low, 9 medium, 0 high), vulture and ty report real
  ones. Triaging those is a suppression-policy decision and is deliberately
  left open rather than excluded away.
- **The `pytest` console script can collect `tests/unit/frontend` again.**
  `tests/unit/` and `tests/unit/frontend/` are packages but `tests/` is not, so
  under pytest's default prepend import mode the basedir of
  `tests/unit/frontend/test_benchmark_data.py` was `tests/` itself: the repo
  root never reached `sys.path`, and its `from frontend.frontend import
  benchmark_data` raised `ModuleNotFoundError: No module named 'frontend'`.
  That is the two-error collection failure that failed the Windows lane's
  Python 3.13 test step, and it would have failed the Linux lane's too as soon
  as the static-analysis step above stopped aborting the job before the tests
  ran. `python -m pytest` hid it by putting the CWD on `sys.path` regardless —
  which is why `benchmarks.yml` never saw it and the CI drivers, which call
  `uv_run pytest`, always did. Fixed with `pythonpath = ["."]` in
  `[tool.pytest.ini_options]` (and `minversion` raised to the 7.0 that option
  needs, so a pytest 6 cannot ignore it silently). An `__init__.py` in `tests/`
  fixes the same import and is the wrong fix: it renames these modules to
  `tests.unit.*`, which collides with `benchmarks/tests/` — also a package,
  also imported as `tests` — in the single session `benchmarks.yml` runs over
  both trees. Measured rather than assumed: that swap turns the green
  benchmarks job into `ModuleNotFoundError: No module named 'tests.unit'`.
- **CI grades the tree as committed.** The lanes ran `ruff check --fix` and a
  bare `ruff format`, which repair the checkout CI is about to delete: every
  auto-fixable finding was invisible, and formatting could never fail. Both now
  run `ruff check --no-fix` and `ruff format --check --diff`.
- **Real type and correctness findings the silent gate had been hiding.**
  `orchestrant/monitoring/gpu.py` called eleven `pynvml` attributes through a
  `module | None` that the `PYNVML_AVAILABLE` bool never narrowed;
  `pipeline/capture/gstreamer.py` read `.stdout` off a `Popen | None` and
  called `.readinto` behind a `hasattr` that narrows nothing;
  `smoke/checks.py` called `.tolist()` on the non-tensor arms of
  `InferenceSession.run`'s return union; `streaming/app.py` cached its Flask
  app on a function attribute. Several of these were "handled" by
  `# type: ignore[...]` comments in mypy syntax, which `ty` does not read.
- **The viewer `render()` API takes its nine telemetry arguments by keyword.**
  They were positional-or-keyword (ruff `PLR0917`); every call site already
  passed them by keyword except one internal `wx.CallAfter`.


### Security
- `orchestrant/pipeline/capture/gstreamer.py` carries explicit, justified
  `# nosec B404`/`B603` markers on the gst-launch spawn instead of relying on
  bandit's result being discarded. Bandit is now part of the gate, so a NEW
  finding fails CI.


---

## [0.0.28] - 2026-09-06

### Changed
- **Project renamed `Orchestr-ANT-ion` → `OrchestrANT`.** The old name spliced
  `ANT` into "orchestration" with hyphens; every other repo in the family
  (`ANThology`, `OxidANT`, `AccelerANTgine`, `OmniAccelerANT`) capitalises an
  `ANT` the base word already contains, with no separators and no
  `Kataglyphis-` prefix. What moved:
  - distribution `Orchestr-ANT-ion` → `OrchestrANT`
  - import package `orchestr_ant_ion` → `orchestrant`, so
    `from orchestr_ant_ion.pipeline import X` becomes
    `from orchestrant.pipeline import X`
  - console script `orchestr-ant-ion-smoke` → `orchestrant-smoke`
  - repository `Kataglyphis/Kataglyphis-Orchestr-ANT-ion` →
    `Kataglyphis/OrchestrANT`

---

## [0.0.27] - 2026-07-14

### Added
- **IREE** (iree.dev) is now a declared dependency of the `ml-ai*` extras:
  `iree-base-compiler` + `iree-base-runtime` (guarded `platform_machine !=
  'riscv64'`). PyPI ships `cp312-abi3` wheels for x86_64/aarch64 that install on
  Python 3.14, so `iree.compiler` + `iree.runtime` are now actually present for
  the existing `check_iree` smoke to exercise (MLIR compile + local-task run,
  `abs(-5)=5`). On riscv64 there is no PyPI wheel — ANTfrastructure source-builds
  the runtime wheel into `/opt/wheels` (compiler stays absent there, so
  `check_iree` degrades to optional-fail, non-gating). Kept in sync with
  ANTfrastructure `IREE_VERSION` (v3.11.0).

---

## [0.0.26] - 2026-07-14

### Added
- Wheel smoke: `check_opencv` now round-trips **JPEG** (`.jpg`) alongside PNG —
  JPEG is the app's live MJPEG streaming codec, so it is exercised directly
  rather than assumed from the PNG result.
- Wheel smoke: `check_onnxruntime` asserts `CPUExecutionProvider` is in
  `get_available_providers()` (catches an execution provider silently dropped
  from the on-target build).
- Wheel smoke: new optional checks `check_opencv_dnn` (cv2.dnn module +
  protobuf link via `blobFromImage`), `check_opencv_codecs` (TIFF/WEBP/OpenEXR
  round-trip; surfaces per-arch codec drops), and `check_opencv_freetype`
  (cv2.freetype text rendering — validates the source-built freetype on
  riscv64). All three are non-gating (WARN) so a per-arch feature gap is
  visible without failing the smoke.

---

## [0.0.22] - 2026-07-12

### Added
- `orchestr_ant_ion.smoke` — a shipped wheel smoke-test module. Each check does
  real work (torch autograd + a linear forward/backward, torchvision `ops.nms`,
  an embedded ONNX Add inference, an OpenCV encode/decode/cvtColor round-trip,
  Pillow, a torch↔numpy ABI bridge) rather than a bare import, so a mislinked
  compiled extension is caught even when `import` succeeds. Run it with:

      python -m orchestr_ant_ion.smoke        # text report, exit 1 on failure
      python -m orchestr_ant_ion.smoke --json # machine-readable

  Also exposed as the `orchestr-ant-ion-smoke` console script. Container images
  (Kataglyphis-ANTfrastructure) invoke it under emulation to verify the ML stack.
- LiteRT is checked as an **optional** runtime (WARN, not a gate failure) and
  probes both module names (`ai_edge_litert` upstream / `tflite_runtime` custom).

### Changed
- Bumped PyTorch to **2.13.0** and TorchVision to **0.28.0** across every backend
  extra (`pytorch-cpu` / `-cu130` / `-rocm71` / `-custom`, the `torchvision`
  build-deps, and the riscv64 source `git` refs `@v2.13.0` / `@v0.28.0`).
- Regenerated `uv.lock` for the new torch/vision pins.

---

## [0.0.21] - 2026-07-12

### Added
- `pytorch-custom` optional-dependencies extra — a "bring your own wheels" PyTorch
  backend. `torch`/`torchvision` are declared as plain pins with **no** index or git
  source override, so a prebuilt/custom wheelhouse satisfies them directly:

      uv sync --extra pytorch-custom --find-links /path/to/wheels
      # or: export UV_FIND_LINKS=/path/to/wheels && uv sync --extra pytorch-custom

  Ideal for platforms without upstream binaries (e.g. riscv64) or your own optimized
  torch build — zero source builds, and no per-package `--no-install-package` /
  force-reinstall dance. Mutually exclusive with `pytorch-cpu` / `pytorch-cu130` /
  `pytorch-rocm71` (wired into `[tool.uv] conflicts`).

### Changed
- Scoped the resolved lock to the environments whose PyTorch is resolvable from
  public indexes via `[tool.uv] environments` (linux non-riscv64, macOS, Windows).
  riscv64 has no upstream torch wheels, so it now resolves fresh at `uv sync` time
  and picks the custom wheels up from `--find-links`; `uv sync --frozen` falls back
  to a live resolve automatically.
- Regenerated `uv.lock` (resolves 262 packages; verified consistent with
  `uv lock --check`).

---

## [0.0.20] - 2026-07-11

### Added
- Container/runtime-oriented ML dependency extras `ml-ai-webgpu`, `ml-ai-nvidia`,
  and `ml-ai-rocm`, mirroring the ONNX Runtime / PyTorch backend combinations that
  Kataglyphis-ANTfrastructure previously patched into `pyproject.toml` at install
  time. Selecting a backend is now a first-class extra rather than an install-time
  patch.

### Changed
- Regenerated `uv.lock` for the new extras (resolves 261 packages; verified
  consistent with `uv lock --locked`).

---

<!-- Links for diffs -->
[Unreleased]: https://github.com/Kataglyphis/OrchestrANT/compare/v0.0.28...HEAD
[0.0.28]: https://github.com/Kataglyphis/OrchestrANT/releases/tag/v0.0.28
[0.0.27]: https://github.com/Kataglyphis/OrchestrANT/releases/tag/v0.0.27
