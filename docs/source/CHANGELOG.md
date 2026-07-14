# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `iree` wheel smoke check: compiles a one-op MLIR module through
  `iree.compiler` and executes it on `iree.runtime`'s local-task driver
  (abs(-5)=5) — proving the compiler and runtime wheels interoperate.
  Missing wheels warn (only container lanes ship the source-built IREE);
  installed-but-broken IREE fails.
- `pyav` wheel smoke check: exercises PyAV end-to-end with a real in-memory
  mpeg4 encode through the linked FFmpeg. The software encoder is requested by
  name because the generic `h264` alias can resolve to a hardware encoder
  (`h264_d3d12va`) that cannot open without a GPU device in headless
  containers. A missing wheel is a warning (containers ship a lane-built PyAV
  where PyPI's wheel cannot load); an installed-but-broken PyAV is a failure.
- Unit tests for `SimpleCentroidTracker` (creation, greedy matching, distance
  gate, trail cap, expiry) — pure logic, no camera or GPU required.
- Unit tests for YOLO post-processing (softmax/sigmoid/xywh conversion,
  format heuristics, classification decode, letterbox unscaling, the
  `postprocess()` dispatcher) and for `PerformanceTracker` (camera FPS,
  inference capacity, frame budget, rolling window) with a deterministic
  fake clock. Suite: 41 → 69 tests.
- Placeholder for new features.

### Changed
- `orchestr_ant_ion.pipeline` re-exports now resolve lazily (PEP 562):
  importing light submodules such as `pipeline.types` no longer drags in the
  heavy optional runtime (cv2, DearPyGui), so the unit suite collects on
  machines without the ML extras. The public
  `from orchestr_ant_ion.pipeline import X` API is unchanged.
- Both `SystemMonitor` variants (time-series logger and pipeline per-frame
  snapshotter) now read CPU/RAM/GPU through one shared
  `monitoring/snapshot.py` helper instead of duplicating the collection
  logic; their docstrings cross-reference each other's role.
- Real packaging metadata (description/keywords, `Development Status :: 4 -
  Beta`); streaming HTML template `package_data` moved to
  `[tool.setuptools.package-data]` in pyproject.toml.
- Repo-wide `ruff check` is clean (was 26 errors) and `ruff format` applied;
  `_frame_reader` in the GStreamer capture was decomposed (three duplicated
  short-read paths → one `_read_one_frame` helper).
- `imgui.ini` and generated `output/*.png` are no longer git-tracked.
- Placeholder for changes in existing functionality.

### Deprecated
- Placeholder for soon-to-be removed features.

### Removed
- Placeholder for now removed features.

### Fixed
- `SimpleCentroidTracker` crashed with `NameError: name 'Track' is not
  defined` on its first update with detections — `Track` was imported under
  `TYPE_CHECKING` only but constructed at runtime. This broke the shipped
  `yolo-monitor` entry point the moment anything was detected.
- Cythonized wheels (`CYTHONIZE=1`) shipped without the streaming HTML
  templates — the cython branch's `package_data` replaced the template list
  instead of extending it. Verified fixed by inspecting both wheel variants.
- Placeholder for any bug fixes.

### Security
- Placeholder for vulnerabilities patched.

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
