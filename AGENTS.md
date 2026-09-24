# AGENTS.md

Guidance for coding agents (and new contributors) working in OrchestrANT.

Laid out per ANTfrastructure's
[`shared/templates/AGENTS.md.template`](third_party/ANTfrastructure/shared/templates/README.md).
The rule that shapes it: *would this still be true in a different project?* If
yes, ANTfrastructure owns it and § 2 links to it. If no, it is written out in § 4.

## 1. What this project is

Four things at once, by owner decision (2026-09-15): the **package** for AI
workload orchestration — camera pipelines, YOLO monitoring, streaming and
system/GPU metrics; the **family's LLM benchmark lab**; the **template** a new
Python AI project starts from (what the repository description advertises); and
the **Reflex viewer** for the lab's results. They are not split apart and no
code moves between them — a change that only makes sense for one of the four
still belongs here. Python ≥ 3.11, managed with `uv`.

| Path | What lives there |
| --- | --- |
| `orchestrant/` | The package: `pipeline/`, `yolo/`, `streaming/`, `monitoring/`, `smoke/` |
| `orchestrant/benchmark/` | The LLM endpoint runner behind the `orchestrant-bench` console script — the measuring half of the lab |
| `tests/` | `unit/`, `integration/` |
| `benchmarks/` | The LLM benchmark lab (`bench_*.py`, `nas_census.py`, prompts, tracked results, `docs/`) — see [`benchmarks/README.md`](benchmarks/README.md) |
| `frontend/` | The Reflex benchmark viewer (the `frontend` extra) |
| `bench/` | The profiling demo set the hub's `ci_tests.sh` runs (cProfile, line_profiler, memory_profiler, py-spy, pytest-benchmark) — not the lab |
| `examples/` | Runnable example scripts (`monitoring.py`), run by path from the README's Demos section |
| `scripts/linux/` | Seven thin wrappers over ANTfrastructure drivers: the four Python CI lanes, plus `run-lint-gates.sh`, `ci-image-ref.sh` and `renovate-local.sh` |
| `scripts/windows/` | `Build-Windows.ps1`, the `Resolve-BuildModule.ps1` bootstrap and `Invoke-Lint.ps1` (the PowerShell lint wrapper) |
| `docs/` | Sphinx documentation |
| `resources/models/` | The one large tracked binary, `yolov26m.onnx` (78 MiB), and the note saying why it is tracked and why history is not rewritten — see [`resources/models/README.md`](resources/models/README.md) |
| `*.allow` at the root | The lint ratchets' freeze files (`function-size`, `file-size`, `code-complexity`, `comment-size`, `dead-functions`). Seeded 2026-09-15 from the first `--ratchets` run; each file's header states its format and what makes a row go stale |
| `third_party/ANTfrastructure` | The submodule owning every reusable script, module and doc |

**The distribution name is not the module name.** `pyproject.toml` declares
`name = "OrchestrANT"` while the importable package is `orchestrant`.
Anything deriving one from the other is wrong — see § 4.

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
| Python CI lanes and the uv traps | [`docs/python-ci.md`](third_party/ANTfrastructure/docs/python-ci.md) |
| The five shell-safety bug classes | `third_party/ANTfrastructure/AGENTS.md` § *Shell safety conventions* |

On the Windows-on-ARM lab host (Snapdragon X) Rancher Desktop is not used:
rootless `nerdctl` runs directly inside WSL (`Ubuntu-26.04`), so the § 5
`nerdctl run` recipe is run from a WSL shell.

**Every `scripts/linux/*.sh` here is a wrapper, not an implementation.** Each
sources `scripts/linux/lib/antfrastructure.sh` and calls `antfrastructure_exec` into
the submodule. When behaviour needs to change, change it **upstream** — a fix
made in the wrapper is a fix the other consumers never get.

`ci_static_analysis.sh` was a local fork until upstream gated; why, and what
moved, is in [`CHANGELOG.md`](CHANGELOG.md) *[Unreleased] > Fixed* and
[`docs/python-ci.md`](third_party/ANTfrastructure/docs/python-ci.md). The wrapper
keeps exactly one local thing: the `PACKAGE_NAME` export.

`run-lint-gates.sh`, `ci-image-ref.sh` and `renovate-local.sh` are the same shape
over three other ANTfrastructure entry points — see § 5.

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

## 3. Critical invariant: submodule pins

Builds are only supported against the **recorded submodule gitlink** — the
commit CI builds green. `git submodule update --checkout --recursive` restores
it. If a drifted submodule is what you actually want, update the gitlink **and**
fix the fallout in the same change. No version couplings yet — checked: the
only number repeated from the hub is the `ruff` rev (`.pre-commit-config.yaml`,
`pyproject.toml`), and the lint aggregator's consumer-pins gate compares it
against `versions.env`. Drift is guarded by ANTfrastructure's shared Pester suite, run from
[`.github/workflows/submodule-pins.yml`](.github/workflows/submodule-pins.yml)
after any pin bump.

## 4. Pitfalls specific to this project

Everything here is false or meaningless in another repo — that is why it is
written out rather than linked.

- **`PACKAGE_NAME` must be exported explicitly.** The upstream drivers default it
  from the distribution name, which here is `OrchestrANT` — not an
  importable module. `ci_tests.sh` and `ci_static_analysis.sh` therefore export
  `PACKAGE_NAME=orchestrant` before delegating. Remove that and coverage and
  the analysis target silently point at a directory that does not exist.
- **A static-analysis finding fails CI, on both lanes**, and the aggregation is
  upstream's — see
  [`docs/python-ci.md`](third_party/ANTfrastructure/docs/python-ci.md) and the
  drivers it names. Only two things about it are local, and both live in
  `scripts/linux/ci_static_analysis.sh`: it exports `PACKAGE_NAME=orchestrant`
  (the bullet above) and it clears `VIRTUAL_ENV`, because the family image
  exports `VIRTUAL_ENV=/opt/venv`, whose `bin/` is root-owned — the driver
  would pin `uv sync` to it and die removing a stale console script.
- **`WORKSPACE_ROOT` is pinned by `antfrastructure_exec` — a wrapper that stops
  going through it loses the export silently**, which is why
  `ci_static_analysis.sh` exports it itself; see
  [`shared/linux/templates/README.md`](third_party/ANTfrastructure/shared/linux/templates/README.md).
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

## 5. Build, run, test

```bash
uv sync --extra pytorch-cpu          # or pytorch-cu130 / pytorch-rocm71 / pytorch-custom

bash scripts/linux/ci_tests.sh           # pytest + coverage
bash scripts/linux/ci_static_analysis.sh # lint + type check (GATING: exits 1 on any finding)
bash scripts/linux/ci_build_docs.sh      # Sphinx
bash scripts/linux/ci_packaging.sh       # wheel + sdist

# The lint gate. The wrapper and .github/workflows/lint-gates.yml are two
# callers of ONE aggregator and must keep saying the same thing: the wrapper
# passes --ratchets unconditionally, the lane passes ratchets: true.
bash scripts/linux/run-lint-gates.sh     # the hub lint aggregator (seven gates; third_party/ANTfrastructure/linux/scripts/run-lint-gates.sh header)

# The family CI image, resolved from ANTfrastructure's versions.env, for
# reproducing a CI step by hand:
#   nerdctl run --rm -v "$PWD:/workspace" -w /workspace \
#     "$(scripts/linux/ci-image-ref.sh)" bash -lc 'scripts/linux/ci_tests.sh'
bash scripts/linux/ci-image-ref.sh       # [--windows] for the Windows tag

# Dependency upgrades, NOT by hand. Rationale:
# third_party/ANTfrastructure/docs/dependency-updates.md
# --apply moves gitlinks only for submodules that declare a `branch =` in
# .gitmodules; here that is third_party/ANTfrastructure (branch = main), the one
# submodule this repo has, so nothing else can come back REFUSED.
bash scripts/linux/renovate-local.sh                   # git-submodules (default)
bash scripts/linux/renovate-local.sh --managers pep621 # the pyproject.toml pins
```

Windows:

```powershell
pwsh -NoProfile -File .\scripts\windows\Build-Windows.ps1

# The PowerShell lint gate: the hub's parse + AST-trap + PSScriptAnalyzer
# passes over scripts/windows, with its ruleset consumed by reference. CI runs
# the same gate over the same tree as the `lint-powershell` job of
# .github/workflows/windows-x64.yml; this wrapper is its dev-box twin.
pwsh -NoProfile -File .\scripts\windows\Invoke-Lint.ps1
```

CI lanes: `.github/workflows/ubuntu-26.04-amd64-arm64.yml` (native x86-64 and
arm64), `.github/workflows/windows-x64.yml` (the container build AND, since the
`lint-powershell: true` input, the PowerShell gate — parse + AST traps +
PSScriptAnalyzer over `scripts/windows` — as a second job that runs even when
the build fails; the standalone `powershell-lint.yml` it replaced is gone),
`.github/workflows/lint-gates.yml` (the hub lint aggregator: seven gates with
`ratchets: true`, see the
`third_party/ANTfrastructure/linux/scripts/run-lint-gates.sh` header),
`.github/workflows/benchmarks.yml` (the lab, the runner and the viewer: offline
suites plus a live-ollama contract job) and
`.github/workflows/submodule-pins.yml` (§ 3). All but the benchmarks lane are
pure configuration for ANTfrastructure reusable workflows — as of 2026-09-15
that includes the lint, pin and PowerShell-lint lanes, whose jobs used to be
inline copies here.
File and display names follow the fleet convention (owner decision
2026-09-24): kebab-case, one file per platform + arch, display names
`<Platform> <Arch> · <what>` or `<Area> · <what>`, and the shared lanes named
the same in every repo (`Lint gates`, `Submodule pins`). The one file still
named the old way is `ubuntu-26.04-amd64-arm64.yml`: its split into
`linux-x64.yml` and `linux-arm64.yml` waits for a hub reusable-lane input that
is not on hub `main` yet.
`.github/actionlint.yaml` only ADDS the `ubuntu-26.04` runner labels that the
pinned actionlint predates — it disables no rule.

## 6. Docs owned by this repo

- Sphinx sources in `docs/`, published to <https://orchestr-ant-ion.jonasheinle.de/>.
- `CHANGELOG.md` and `VERSION.txt` — `pyproject.toml` reads the version from
  `VERSION.txt`, so bump it there, not in the TOML.
- Update docs in the same PR as user-facing behaviour changes.
