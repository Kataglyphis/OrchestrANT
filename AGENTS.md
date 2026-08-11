# AGENTS.md

Guidance for coding agents (and new contributors) working in Orchestr-ANT-ion.

Laid out per ContainerHub's
[`shared/templates/AGENTS.md.template`](ExternalLib/Kataglyphis-ContainerHub/shared/templates/README.md).
The rule that shapes it: *would this still be true in a different project?* If
yes, ContainerHub owns it and § 2 links to it. If no, it is written out in § 3.

## 1. What this project is

A Python package for AI workload orchestration — camera pipelines, YOLO
monitoring, streaming, and system/GPU metrics. Python ≥ 3.11, managed with `uv`.

| Path | What lives there |
| --- | --- |
| `orchestr_ant_ion/` | The package: `pipeline/`, `yolo/`, `streaming/`, `monitoring/`, `smoke/` |
| `tests/` | `unit/`, `integration/`, `fuzzy/` |
| `scripts/linux/` | Four ~15–30 line wrappers over ContainerHub's Python CI drivers |
| `scripts/windows/` | `Build-Windows.ps1` + the `Resolve-BuildModule.ps1` bootstrap |
| `docs/` | Sphinx documentation |
| `ExternalLib/Kataglyphis-ContainerHub` | The submodule owning every reusable script, module and doc |

**The distribution name is not the module name.** `pyproject.toml` declares
`name = "Orchestr-ANT-ion"` while the importable package is `orchestr_ant_ion`.
Anything deriving one from the other is wrong — see § 3.

## 2. What ContainerHub owns — links only

**Do not restate these procedures here.** Start at
[`ExternalLib/Kataglyphis-ContainerHub/docs/INDEX.md`](ExternalLib/Kataglyphis-ContainerHub/docs/INDEX.md),
which maps topic → owning document, so these links survive upstream
reorganisation.

| Topic | Where |
| --- | --- |
| Wiring this repo to ContainerHub — resolver, actions, libraries | `docs/adopting-in-a-new-project.md` |
| Linux container builds | `docs/linux-build-basics.md` |
| Running Linux containers on a Windows host | `docs/rancher-desktop-linux-containers.md` |
| The Windows image, its entrypoint and known traps | `docs/windows-builds.md` |
| Bind mount vs tar-pipe, Dev Drive filter setup, container reuse | `docs/windows-container-build-performance.md` |
| Opting a commit into the heavy CI lanes | `docs/ci-build-triggers.md` |
| The five shell-safety bug classes | ContainerHub `AGENTS.md` § *Shell safety conventions* |

**The four `scripts/linux/ci_*.sh` are wrappers, not implementations.** Each
sources `scripts/linux/lib/containerhub.sh` and calls `containerhub_exec` into
`ExternalLib/Kataglyphis-ContainerHub/linux/scripts/02-toolchain/python/`. When
behaviour needs to change, change it **upstream** — a fix made in the wrapper is
a fix the other Python consumers never get.

`lib/containerhub.sh` is a verbatim copy of ContainerHub's
[`shared/linux/templates/containerhub.sh`](ExternalLib/Kataglyphis-ContainerHub/shared/linux/templates/README.md)
— the bash twin of `Resolve-BuildModule.ps1`, and the only other file that
cannot live upstream because it is what *finds* the submodule. Do not hand-edit
it; sync from upstream. It owns the not-found guard and the `WORKSPACE_ROOT`
export that every wrapper used to repeat.

| Wrapper | Upstream driver |
| --- | --- |
| `ci_tests.sh` | `python/ci_tests.sh` |
| `ci_static_analysis.sh` | `python/ci_static_analysis.sh` |
| `ci_build_docs.sh` | `python/ci_build_docs.sh` |
| `ci_packaging.sh` | `python/ci_packaging.sh` |

Two upstream facts repeated here only because they bite before you reach a doc:

- Every ContainerHub PowerShell module declares `#requires -Version 7.0`, so
  `Build-Windows.ps1` does too — launch with `pwsh`, never `powershell`. Under
  5.1 it fails as an opaque `Import-Module` error.
- Composite actions resolve at `@main`, so a ContainerHub change a workflow
  depends on must be pushed **before** the consumer change.

**This repo's glue** (deliberately thin):

- `scripts/windows/Resolve-BuildModule.ps1` — the one file that cannot live
  upstream, because it is what *finds* the submodule. There are no local
  PowerShell modules; `Build-Windows.ps1` imports `WindowsScripts.Shared`,
  `WindowsBuild.Common` and `WindowsUv.Common` from ContainerHub.
- Nested imports inside a `.psm1` are **module-private**. `WindowsBuild.Common`
  importing `WindowsScripts.Shared` does not re-export it, so every module you
  call into must be named in the `Import-BuildModule` list explicitly.

## 3. Pitfalls specific to this project

Everything here is false or meaningless in another repo — that is why it is
written out rather than linked.

- **`PACKAGE_NAME` must be exported explicitly.** The upstream drivers default it
  from the distribution name, which here is `Orchestr-ANT-ion` — not an
  importable module. `ci_tests.sh` and `ci_static_analysis.sh` therefore export
  `PACKAGE_NAME=orchestr_ant_ion` before delegating. Remove that and coverage and
  the analysis target silently point at a directory that does not exist.
- **`WORKSPACE_ROOT` is handled for you — do not remove it.** Upstream derives it
  relative to the driver, which for a *delegated* driver resolves inside
  `ExternalLib/Kataglyphis-ContainerHub/` rather than this repo. `containerhub_exec`
  pins it to the repo root before handing off (it used to be repeated in every
  wrapper). That is upstream's concern now, listed here only because a wrapper
  that stops going through `containerhub_exec` loses it silently.
- **The torch backend is an extra, and the choice is yours to make.**
  `uv sync --extra pytorch-cpu` (default), `--extra pytorch-cu130` (CUDA 13.0,
  Linux/Windows wheels only — hence the darwin exclusion),
  `--extra pytorch-rocm71`, or `--extra pytorch-custom` with `--find-links`
  pointing at your own wheelhouse. Pinned at `torch==2.13.0` /
  `torchvision==0.28.0` across all of them.
- **riscv64 is deliberately not in the lock.** It has no public torch wheels, so
  `[tool.uv] environments` excludes it and it resolves fresh at `uv sync` time
  (`--frozen` falls back to a live resolve automatically). `pytorch-custom`
  carries **no** `[tool.uv.sources]` override on purpose, so a local wheel wins;
  `pytorch-cpu`'s riscv64 git source would shadow one. Do not "fix" the lock to
  cover riscv64 unless a resolvable torch source exists.
- **Generated C files sit next to the Python.** `orchestr_ant_ion/` contains
  `__init__.c`, `dummy.c`, `logging_config.c` alongside their `.py` sources.
  Tooling that globs the package directory must not treat them as source.

## 4. Build, run, test

```bash
uv sync --extra pytorch-cpu          # or pytorch-cu130 / pytorch-rocm71 / pytorch-custom

bash scripts/linux/ci_tests.sh           # pytest + coverage
bash scripts/linux/ci_static_analysis.sh # lint + type check
bash scripts/linux/ci_build_docs.sh      # Sphinx
bash scripts/linux/ci_packaging.sh       # wheel + sdist
```

Windows:

```powershell
pwsh -NoProfile -File .\scripts\windows\Build-Windows.ps1
```

CI lanes: `.github/workflows/ubuntu-24.04-amd64-arm64.yml` (native x86-64 and
arm64) and `.github/workflows/windows-2025.yml`.

## 5. Docs owned by this repo

- Sphinx sources in `docs/`, published to <https://pythoninference.jonasheinle.de/>.
- `CHANGELOG.md` and `VERSION.txt` — `pyproject.toml` reads the version from
  `VERSION.txt`, so bump it there, not in the TOML.
- Update docs in the same PR as user-facing behaviour changes.
