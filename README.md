<div align="center">
  <a href="https://jonasheinle.de">
    <img src="https://raw.githubusercontent.com/Kataglyphis/OrchestrANT/develop/images/logo.svg" alt="OrchestrANT logo: an ant conducting with a baton" width="200" />
  </a>

# OrchestrANT

  <h4>👨🏻‍💻 Fast python AI prototyping 🐍 </h4>
</div>

[![Linux x64 · build + test](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/linux-x64.yml/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/linux-x64.yml)
[![Linux arm64 · build + test](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/linux-arm64.yml/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/linux-arm64.yml)
[![Linux riscv64 · build + test](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/linux-riscv64.yml/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/linux-riscv64.yml)
[![Windows x64 · build + test](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/windows-x64.yml/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/windows-x64.yml)
[![Windows arm64 · cross build + test](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/windows-arm64-cross.yml/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/windows-arm64-cross.yml)
[![Lint gates](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/lint-gates.yml/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/lint-gates.yml)
[![Submodule pins](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/submodule-pins.yml/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/submodule-pins.yml)
[![Benchmarks](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/benchmarks.yml/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/benchmarks.yml)
[![CodeQL](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/github-code-scanning/codeql)
[![TopLang](https://img.shields.io/github/languages/top/Kataglyphis/OrchestrANT)](https://github.com/Kataglyphis/OrchestrANT)
[![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg)](https://www.paypal.com/paypalme/JonasHeinle)
[![Twitter](https://img.shields.io/twitter/follow/Cataglyphis_?style=social)](https://twitter.com/Cataglyphis_)
[![YouTube](https://img.shields.io/youtube/channel/subscribers/UC3LZiH4sZzzaVBCUV8knYeg?style=social)](https://www.youtube.com/channel/UC3LZiH4sZzzaVBCUV8knYeg)

[**Docs site**](https://orchestr-ant-ion.jonasheinle.de/)

OrchestrANT is the family's Python AI package and its LLM benchmark lab. The `orchestrant`
package orchestrates AI workloads: camera pipelines, detection with `yolo-monitor`, streaming,
and system and GPU metrics. Beside it live the lab (`benchmarks/` and the `orchestrant-bench`
runner), the Flet viewer for its results, and the template a new Python AI project copies its
`uv`/`ruff`/`ty` setup, Cython wheel build, Sphinx docs and CI wiring from. The four stay in one
repository by owner decision (2026-09-15).

Dependency updates: run `bash scripts/linux/renovate-local.sh` to report, `--apply --dry-run` to plan,
and `--apply` to update. A real apply initializes a missing pinned hub and follows each direct
submodule's remote default branch. CI checks the wrapper and config; see
[the shared Renovate guide](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/dependency-updates.md).
Torch/vision updates require the accepted hub pair to be pinned and committed first.

## Highlights

- **Installable packages with their own Python**: MSI, MSIX and zip for Windows x64/arm64; `.deb`, AppImage and tarball for Linux x86_64/arm64.
- **`yolo-monitor`** runs ModelANT's DEIM-D-FINE-N (Apache-2.0) on CUDA, DirectML or CPU; `--self-test` needs no camera.
- **`orchestrant-bench`** measures throughput, time-to-first-token, decode rate and correctness of any OpenAI-compatible endpoint.
- **GPU monitoring for two vendors** through one `GPUProbe`: NVML for NVIDIA, ADL or amdgpu sysfs for AMD.
- **The shipped wheels compile every module with Cython**; the free-threaded cp314t wheel runs them with the GIL off.
- **Static analysis is a gate**: ruff, ty, bandit, vulture and codespell fail CI on any finding.

## Quick start

```bash
git clone --recurse-submodules --branch develop https://github.com/Kataglyphis/OrchestrANT.git
cd OrchestrANT
python third_party/ANTfrastructure/linux/scripts/lib/fetch-data-lock.py --lock resources/models/models.lock   # once: the model
uv run --extra app yolo-monitor --self-test    # one inference on a blank frame, a JSON report
```

The packages, the library install, a camera, the tests and the development setup:
[docs/source/getting-started.md](https://github.com/Kataglyphis/OrchestrANT/blob/develop/docs/source/getting-started.md).

## Documentation

| Topic | Read |
| --- | --- |
| Getting started: packages, the library, demos, tests, development | [docs/source/getting-started.md](https://github.com/Kataglyphis/OrchestrANT/blob/develop/docs/source/getting-started.md) |
| The package, `yolo-monitor`, the model and the extras | [docs/source/overview.md](https://github.com/Kataglyphis/OrchestrANT/blob/develop/docs/source/overview.md) |
| `orchestrant-bench`, the LLM endpoint runner | [docs/source/benchmark.rst](https://github.com/Kataglyphis/OrchestrANT/blob/develop/docs/source/benchmark.rst) |
| System and GPU monitoring | [docs/source/monitoring.md](https://github.com/Kataglyphis/OrchestrANT/blob/develop/docs/source/monitoring.md) |
| The LLM benchmark lab: tools, results, the gateway | [benchmarks/README.md](https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md) |
| The lab's roadmap | [benchmarks/docs/llm-benchmark-roadmap.md](https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/docs/llm-benchmark-roadmap.md) |
| Connecting a model to the NAS | [benchmarks/docs/nas-document-ai.md](https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/docs/nas-document-ai.md) |
| The model weights and their lock | [resources/models/README.md](https://github.com/Kataglyphis/OrchestrANT/blob/develop/resources/models/README.md) |
| CI quality reports, Ruff/ty, PowerShell checks and refactor previews | [Shared quality tooling](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/code-quality-tooling.md) |
| Windows stage reuse test and direct-submodule checkout | [Getting started: Tests](https://github.com/Kataglyphis/OrchestrANT/blob/develop/docs/source/getting-started.md#tests) |
| Rules for agents and contributors | [AGENTS.md](https://github.com/Kataglyphis/OrchestrANT/blob/develop/AGENTS.md) |
| Changes | [CHANGELOG.md](https://github.com/Kataglyphis/OrchestrANT/blob/develop/CHANGELOG.md) |

## Contributing and contact

OrchestrANT is one of the [Kataglyphis repositories](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/repository-family.md),
which share one way to contribute and one contact. Licence: MIT, see [LICENSE](https://github.com/Kataglyphis/OrchestrANT/blob/develop/LICENSE).
