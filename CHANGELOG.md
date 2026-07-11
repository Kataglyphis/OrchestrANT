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
