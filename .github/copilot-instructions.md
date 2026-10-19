# Copilot instructions

[`AGENTS.md`](../AGENTS.md) is this repository's rulebook; read it before suggesting a change.
This file only names the five rules a suggestion breaks most easily:

1. **The distribution is `OrchestrANT`, the module is `orchestrant`.** Never derive one from the
   other; the CI wrappers export `PACKAGE_NAME=orchestrant` explicitly
   ([§ 4](../AGENTS.md#4-pitfalls-specific-to-this-project)).
2. **`scripts/linux/*.sh` are wrappers over ANTfrastructure**: change behaviour upstream, never in
   the wrapper. Below their header, `scripts/linux/lib/antfrastructure.sh` and
   `scripts/windows/Resolve-BuildModule.ps1` equal the hub's templates byte for byte; never edit
   them here ([§ 2](../AGENTS.md#2-what-antfrastructure-owns--links-only)).
3. **Module-level mutable state needs a lock or an atomic operation.** Every module is compiled
   with Cython and the cp314t build runs it with the GIL off; drive such state from threads in
   `tests/unit/test_thread_safety.py` (§ 4).
4. **The model is fetched, never tracked**, and runtime files are found through
   `ORCHESTRANT_DATA_DIR` / `orchestrant/paths.py`, never relative to a checkout.
   `yolo-monitor --self-test` keeps printing its JSON report last, between a bare `{` line and a
   bare `}` line: CI proves every package with it (§ 4).
5. **A static-analysis finding fails CI, on both lanes.** The `ruff` pin in `pyproject.toml`,
   `.pre-commit-config.yaml` and `uv.lock` follows the hub's `RUFF_VERSION`, and a gate fails when
   one moves alone ([§ 3](../AGENTS.md#3-critical-invariant-submodule-pins)).
