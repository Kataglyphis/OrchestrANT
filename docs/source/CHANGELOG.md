# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `pyav` wheel smoke check: exercises PyAV end-to-end with a real in-memory
  mpeg4 encode through the linked FFmpeg. The software encoder is requested by
  name because the generic `h264` alias can resolve to a hardware encoder
  (`h264_d3d12va`) that cannot open without a GPU device in headless
  containers. A missing wheel is a warning (containers ship a lane-built PyAV
  where PyPI's wheel cannot load); an installed-but-broken PyAV is a failure.
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
