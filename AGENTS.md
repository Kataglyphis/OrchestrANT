# AGENTS.md

Guidance for coding agents (and new contributors) working in OrchestrANT.

Laid out per ANTfrastructure's
[`shared/templates/AGENTS.md.template`](third_party/ANTfrastructure/shared/templates/README.md).
The rule that shapes it: *would this still be true in a different project?* If
yes, ANTfrastructure owns it and § 2 links to it. If no, it is written out in § 3.

## 1. What this project is

A Python package for AI workload orchestration — camera pipelines, YOLO
monitoring, streaming, and system/GPU metrics. Python ≥ 3.11, managed with `uv`.

| Path | What lives there |
| --- | --- |
| `orchestrant/` | The package: `pipeline/`, `yolo/`, `streaming/`, `monitoring/`, `smoke/` |
| `tests/` | `unit/`, `integration/`, `fuzzy/` |
| `scripts/linux/` | Seven thin wrappers over ANTfrastructure drivers: the four Python CI lanes, plus `run-lint-gates.sh`, `ci-image-ref.sh` and `renovate-local.sh` |
| `scripts/windows/` | `Build-Windows.ps1` + the `Resolve-BuildModule.ps1` bootstrap |
| `docs/` | Sphinx documentation |
| `third_party/ANTfrastructure` | The submodule owning every reusable script, module and doc |

**The distribution name is not the module name.** `pyproject.toml` declares
`name = "OrchestrANT"` while the importable package is `orchestrant`.
Anything deriving one from the other is wrong — see § 3.

## 2. What ANTfrastructure owns — links only

**Do not restate these procedures here.** Start at
[`third_party/ANTfrastructure/docs/INDEX.md`](third_party/ANTfrastructure/docs/INDEX.md),
which maps topic → owning document, so these links survive upstream
reorganisation.

| Topic | Where |
| --- | --- |
| Wiring this repo to ANTfrastructure — resolver, actions, libraries | `docs/adopting-in-a-new-project.md` |
| Linux container builds | `docs/linux-build-basics.md` |
| Running Linux containers on a Windows host | `docs/rancher-desktop-linux-containers.md` |
| The Windows image, its entrypoint and known traps | `docs/windows-builds.md` |
| Bind mount vs tar-pipe, Dev Drive filter setup, container reuse | `docs/windows-container-build-performance.md` |
| Opting a commit into the heavy CI lanes | `docs/ci-build-triggers.md` |
| Dependency upgrades — Renovate as a local CLI, and what `--apply` moves | `docs/dependency-updates.md` |
| The five shell-safety bug classes | ANTfrastructure `AGENTS.md` § *Shell safety conventions* |

**Every `scripts/linux/*.sh` here is a wrapper, not an implementation.** Each
sources `scripts/linux/lib/antfrastructure.sh` and calls `antfrastructure_exec` into
the submodule. When behaviour needs to change, change it **upstream** — a fix
made in the wrapper is a fix the other consumers never get.

`ci_static_analysis.sh` used to be the exception: a 131-line local fork, kept
only because the upstream driver ended every tool line with `|| true` and so
exited 0 whatever ruff, ty, bandit, vulture and codespell found. **That fork is
gone — upstream gates now.** The six suppressions and the four `2>/dev/null`
sinks were removed upstream, the six tools run through ANTfrastructure's
`01-core/gates.sh` and the verdict is raised once by `assert_gates`, and the
`--no-fix` / `--check --diff` flags this repo insisted on are the ones upstream
now uses. The wrapper keeps exactly one local thing: the `PACKAGE_NAME` export.

`run-lint-gates.sh`, `ci-image-ref.sh` and `renovate-local.sh` are the same shape
over three other ANTfrastructure entry points — see § 4.

`lib/antfrastructure.sh` is a verbatim copy of ANTfrastructure's
[`shared/linux/templates/antfrastructure.sh`](third_party/ANTfrastructure/shared/linux/templates/README.md)
— the bash twin of `Resolve-BuildModule.ps1`, and the only other file that
cannot live upstream because it is what *finds* the submodule. Do not hand-edit
it; sync from upstream. It owns the not-found guard and the `WORKSPACE_ROOT`
export that every wrapper used to repeat.

| Wrapper | Upstream driver (under `third_party/ANTfrastructure/linux/scripts/`) |
| --- | --- |
| `ci_tests.sh` | `02-toolchain/python/ci_tests.sh` |
| `ci_static_analysis.sh` | `02-toolchain/python/ci_static_analysis.sh` |
| `ci_build_docs.sh` | `02-toolchain/python/ci_build_docs.sh` |
| `ci_packaging.sh` | `02-toolchain/python/ci_packaging.sh` |
| `run-lint-gates.sh` | `run-lint-gates.sh` (passes this repo's root) |
| `ci-image-ref.sh` | `ci-image-ref.sh` |
| `renovate-local.sh` | `renovate-local.sh` (passes this repo's root) |

Two upstream facts repeated here only because they bite before you reach a doc:

- Every ANTfrastructure PowerShell module declares `#requires -Version 7.0`, so
  `Build-Windows.ps1` does too — launch with `pwsh`, never `powershell`. Under
  5.1 it fails as an opaque `Import-Module` error.
- Composite actions resolve at `@main`, so a ANTfrastructure change a workflow
  depends on must be pushed **before** the consumer change.

**This repo's glue** (deliberately thin):

- `scripts/windows/Resolve-BuildModule.ps1` — the one file that cannot live
  upstream, because it is what *finds* the submodule. There are no local
  PowerShell modules; `Build-Windows.ps1` imports `WindowsScripts.Shared`,
  `WindowsBuild.Common` and `WindowsUv.Common` from ANTfrastructure.
- Nested imports inside a `.psm1` are **module-private**. `WindowsBuild.Common`
  importing `WindowsScripts.Shared` does not re-export it, so every module you
  call into must be named in the `Import-BuildModule` list explicitly.

## 3. Pitfalls specific to this project

Everything here is false or meaningless in another repo — that is why it is
written out rather than linked.

- **`PACKAGE_NAME` must be exported explicitly.** The upstream drivers default it
  from the distribution name, which here is `OrchestrANT` — not an
  importable module. `ci_tests.sh` and `ci_static_analysis.sh` therefore export
  `PACKAGE_NAME=orchestrant` before delegating. Remove that and coverage and
  the analysis target silently point at a directory that does not exist.
- **A static-analysis finding fails CI, on both lanes.** `ruff check --no-fix`,
  `ruff format --check`, `ty check`, `bandit`, `vulture` and `codespell` all
  decide the exit code — `scripts/linux/ci_static_analysis.sh` collects the
  failures through ANTfrastructure's `01-core/gates.sh` and `assert_gates` exits 1;
  `Build-Windows.ps1` does the same through the PowerShell twin,
  `Invoke-BuildGate` / `Assert-BuildGates`, whose throw puts the step in
  `Results.Failed` and reaches the script's `exit 1`. Both aggregators also
  fail when NO gate ran, so an empty batch cannot report green. Every tool
  still RUNS when an earlier one fails, so one push shows every finding. Keep
  the two tool lists identical: two lanes grading the same tree differently is
  what this replaced.
  `--no-fix` and `--check` are load-bearing — `ruff check --fix` reports only
  what it could not repair, and CI throws the checkout away.
- **`WORKSPACE_ROOT` is handled for you — do not remove it.** Upstream derives it
  relative to the driver, which for a *delegated* driver resolves inside
  `third_party/ANTfrastructure/` rather than this repo. `antfrastructure_exec`
  pins it to the repo root before handing off (it used to be repeated in every
  wrapper). That is upstream's concern now, listed here only because a wrapper
  that stops going through `antfrastructure_exec` loses it silently — which is
  exactly why `ci_static_analysis.sh`, the one wrapper that does not `exec`,
  exports `WORKSPACE_ROOT` itself before sourcing any ANTfrastructure library.
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
- **Generated C files sit next to the Python.** `orchestrant/` contains
  `__init__.c`, `dummy.c`, `logging_config.c` alongside their `.py` sources.
  Tooling that globs the package directory must not treat them as source.
- **GPU monitoring is two vendors with different mechanisms.**
  `orchestrant/monitoring/gpu.py` is a facade over NVML (NVIDIA, the
  `nvidia-ml-py` extra) and `gpu_amd.py` (ADL on Windows, amdgpu sysfs on
  Linux). On Windows the probe reads the ADL PMLog sensors
  (`ADL2_New_QueryPMLogData_Get`); the legacy Overdrive5 calls that `pyadl`
  wraps return `ADL_ERR` on RDNA-era cards (verified on an RX 9070 XT), which
  is why there is no `pyadl` dependency. AMD adapters are ordered by
  dedicated VRAM, largest first, so `gpu_index=0` is the discrete GPU on an
  APU+dGPU host.

## 4. Build, run, test

```bash
uv sync --extra pytorch-cpu          # or pytorch-cu130 / pytorch-rocm71 / pytorch-custom

bash scripts/linux/ci_tests.sh           # pytest + coverage
bash scripts/linux/ci_static_analysis.sh # lint + type check (GATING: exits 1 on any finding)
bash scripts/linux/ci_build_docs.sh      # Sphinx
bash scripts/linux/ci_packaging.sh       # wheel + sdist

# The lint gate. This is the SAME command .github/workflows/lint-gates.yml
# runs, so a green local run means a green lane.
bash scripts/linux/run-lint-gates.sh     # shellcheck + actionlint + gitleaks

# The family CI image, resolved from ANTfrastructure's versions.env, for
# reproducing a CI step by hand:
#   nerdctl run --rm -v "$PWD:/workspace" -w /workspace \
#     "$(scripts/linux/ci-image-ref.sh)" bash -lc 'scripts/linux/ci_tests.sh'
bash scripts/linux/ci-image-ref.sh       # [--windows] for the Windows tag

# Dependency upgrades go through this, NOT by hand. Renovate as a local CLI —
# the Renovate GitHub App is installed on no repo in this family, so this is the
# only reader of the tracked .github/renovate.json, and no workflow runs it.
# The report only reads, and runs from WSL (no node on the Windows side).
# --apply is the writing half: gitlinks only, for submodules declaring a branch
# (here just third_party/ANTfrastructure), and it needs the git that wrote the
# working tree — the script switches to git.exe from WSL itself, and refuses up
# front when it cannot. Rationale:
# third_party/ANTfrastructure/docs/dependency-updates.md
bash scripts/linux/renovate-local.sh                   # git-submodules (default)
bash scripts/linux/renovate-local.sh --managers pep621 # the pyproject.toml pins
```

Windows:

```powershell
pwsh -NoProfile -File .\scripts\windows\Build-Windows.ps1
```

CI lanes: `.github/workflows/ubuntu-26.04-amd64-arm64.yml` (native x86-64 and
arm64), `.github/workflows/windows-2025.yml`, and
`.github/workflows/lint-gates.yml` (shellcheck + actionlint + gitleaks). The
first two are configuration for ANTfrastructure reusable lanes; the third is a
one-line `run:` of the wrapper above, because ANTfrastructure has no reusable
lint lane yet. `.github/actionlint.yaml` only ADDS the `ubuntu-26.04` runner
labels that the pinned actionlint predates — it disables no rule.

## 5. Docs owned by this repo

- Sphinx sources in `docs/`, published to <https://orchestr-ant-ion.jonasheinle.de/>.
- `CHANGELOG.md` and `VERSION.txt` — `pyproject.toml` reads the version from
  `VERSION.txt`, so bump it there, not in the TOML.
- Update docs in the same PR as user-facing behaviour changes.
