# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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

### Deprecated
- Placeholder for soon-to-be removed features.

### Removed
- Placeholder for now removed features.

### Fixed
- **The static-analysis gate actually gates now, on both lanes.** codespell,
  bandit, vulture, ruff and ty ran in CI but could not fail it: the Linux lane
  delegated to ANTfrastructure's driver, which ends every tool line with
  `|| true`, and the Windows step wrapped each tool in `Invoke-BuildOptional`,
  which records a failure as a non-gating `AllowedFailure` that never reaches
  `Results.Failed` — the only input to the script's `exit 1`. Both lanes were
  green on a tree with 63 ruff findings, 2 bandit findings, a mis-formatted
  file and 31 ty diagnostics, while the contributor docs called the checks
  merge blockers. `scripts/linux/ci_static_analysis.sh` now owns its gating
  (still reusing ANTfrastructure's venv/sync helpers) and `Build-Windows.ps1`
  collects failures and throws. Both run every tool before deciding, so one
  push reports every finding.
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

## [1.0.0] - YYYY-MM-DD

### Added
- Initial release.

<!-- Add past versions below this line -->

<!-- Example:
## [0.9.0] - 2024-01-15

### Added
- Beta release features.
-->

---

<!-- Links for diffs -->
[Unreleased]: https://your.repo.url/compare/v1.0.0...HEAD
[1.0.0]: https://your.repo.url/releases/tag/v1.0.0
