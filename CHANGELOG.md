# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Placeholder for new features.

### Changed
- Placeholder for changes in existing functionality.

### Deprecated
- Placeholder for soon-to-be removed features.

### Removed
- Placeholder for now removed features.

### Fixed
- Placeholder for any bug fixes.

### Security
- Placeholder for vulnerabilities patched.

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
  (Kataglyphis-ContainerHub) invoke it under emulation to verify the ML stack.
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
  Kataglyphis-ContainerHub previously patched into `pyproject.toml` at install
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
