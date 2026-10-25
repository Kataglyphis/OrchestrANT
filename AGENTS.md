# AGENTS.md

Guidance for coding agents (and new contributors) working in OrchestrANT.

Laid out per ANTfrastructure's
[`shared/templates/AGENTS.md.template`](third_party/ANTfrastructure/shared/templates/README.md).
The rule that shapes it: *would this still be true in a different project?* If
yes, ANTfrastructure owns it and § 2 links to it. If no, it is written out in § 4.

## 1. What this project is

Four things in one repository, by owner decision (2026-09-15): the **package** for AI
workload orchestration (camera pipelines, YOLO monitoring, streaming, system/GPU metrics),
the **family's LLM benchmark lab**, the **template** a new Python AI project starts from
(what the repository description advertises), and the **Flet viewer** for the lab's
results. They are not split and no code moves between them: a change that only makes
sense for one of the four still belongs here. Python ≥ 3.11, managed with `uv`.

| Path | What lives there |
| --- | --- |
| `orchestrant/` | The package: `pipeline/`, `yolo/`, `streaming/`, `monitoring/`, `smoke/` |
| `orchestrant/benchmark/` | The LLM endpoint runner behind the `orchestrant-bench` console script — the measuring half of the lab |
| `tests/` | `unit/`, `integration/` |
| `benchmarks/` | The LLM benchmark lab (`bench_*.py`, `nas_census.py`, prompts, tracked results, `docs/`) — see [`benchmarks/README.md`](benchmarks/README.md) |
| `frontend/` | The Flet benchmark viewer (the `frontend` extra), `python -m frontend.frontend [--web]` from the root |
| `bench/` | The profiling demo set the hub's `ci_tests.sh` runs (cProfile, line_profiler, memory_profiler, py-spy, pytest-benchmark) — not the lab |
| `examples/` | Runnable example scripts (`monitoring.py`), run by path from [`docs/source/getting-started.md`](docs/source/getting-started.md#demos) § *Demos* |
| `scripts/linux/` | Eight thin wrappers over ANTfrastructure drivers: the four Python CI lanes, plus `run-lint-gates.sh`, `ci-image-ref.sh`, `renovate-local.sh` and `serve-stack.sh` (the lab's gateway) |
| `scripts/windows/` | `python-ci.psd1` (the hub's Windows build driver reads it, § 2), `Build-WindowsArm64.ps1` and `Stage-Arm64Tests.ps1` (§ 5), the `Resolve-BuildModule.ps1` bootstrap and `Invoke-Lint.ps1` (the PowerShell lint wrapper) |
| `docs/` | Sphinx documentation |
| `resources/models/` | `models.lock` only: the default model, ModelANT's DEIM-D-FINE-N (Apache-2.0), with its LICENSE and NOTICE, by SHA-256 and URL. The hub's `linux/scripts/lib/fetch-data-lock.py` puts the files beside it; see [`resources/models/README.md`](resources/models/README.md) and § 4 |
| `*.allow` at the root | The lint ratchets' freeze files (`function-size`, `file-size`, `code-complexity`, `comment-size`, `dead-functions`). Seeded 2026-09-15 from the first `--ratchets` run; each file's header states its format and what makes a row go stale |
| `third_party/ANTfrastructure` | The submodule owning every reusable script, module and doc |
| `build_support/kataglyphis_wheel.py` | The hub-owned, manifest-checked wheel rewriter; `MANIFEST.in` includes it in the sdist |

**The distribution name is not the module name.** `pyproject.toml` declares
`name = "OrchestrANT"`; the importable package is `orchestrant`. Anything deriving one
from the other is wrong (§ 4).

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
| Which CI lanes run when, `[ci <lane>]` in a commit message, and the release on a push to `main` (here since 2026-10-16; this repo's lanes: § 5) | [`docs/branch-and-release-strategy.md`](third_party/ANTfrastructure/docs/branch-and-release-strategy.md), `docs/ci-build-triggers.md` |
| Dependency upgrades — Renovate as a local CLI, and what `--apply` moves | `docs/dependency-updates.md` |
| Python CI lanes and the uv traps | [`docs/python-ci.md`](third_party/ANTfrastructure/docs/python-ci.md) |
| The installable packages: the relocatable bundle, its MSI/zip/deb/AppImage/tar.gz, `packaging/app.json`'s keys, how each is started in CI | [`docs/python-app-bundles.md`](third_party/ANTfrastructure/docs/python-app-bundles.md) |
| The five shell-safety bug classes | `third_party/ANTfrastructure/AGENTS.md` § *Shell safety conventions* |
| Code comments: one line, only the why; API docs short; gated | `third_party/ANTfrastructure/AGENTS.md` § *Comments: one line, only the why* |
| Searching the tree: `rg`, not `grep -r` | `third_party/ANTfrastructure/AGENTS.md` § *Searching the tree: ripgrep (`rg`)* |

The Windows-on-ARM lab host (Snapdragon X) has no Rancher Desktop: rootless `nerdctl`
runs directly inside WSL (`Ubuntu-26.04`), so § 5's `nerdctl run` recipe runs from a WSL
shell.

**Every `scripts/linux/*.sh` here is a wrapper, not an implementation.** Each sources
`scripts/linux/lib/antfrastructure.sh` and calls `antfrastructure_exec` into the
submodule. Change behaviour **upstream**: a fix made in the wrapper is a fix the other
consumers never get.

| Wrapper | Upstream driver (under `third_party/ANTfrastructure/linux/scripts/`) |
| --- | --- |
| `ci_tests.sh` | `02-toolchain/python/ci_tests.sh` |
| `ci_static_analysis.sh` | `02-toolchain/python/ci_static_analysis.sh` |
| `ci_build_docs.sh` | `02-toolchain/python/ci_build_docs.sh` |
| `ci_packaging.sh` | `lib/fetch-data-lock.py` over `resources/models/models.lock`, then `02-toolchain/python/ci_packaging.sh`, then `02-toolchain/python/test-free-threaded-wheel.sh` |
| `run-lint-gates.sh` | `run-lint-gates.sh` (passes this repo's root) |
| `ci-image-ref.sh` | `ci-image-ref.sh` |
| `renovate-local.sh` | `renovate-local.sh` (passes this repo's root) |
| `serve-stack.sh` | `../llm-stack/scripts/serve-stack.sh` (exports the prompts dir; renders from `LLM_BACKENDS` when set) |

What the wrappers keep locally:

- **`ci_packaging.sh`** runs the hub's `fetch-data-lock.py` first, since the packages ship
  the model (§ 4), then the hub driver in a subshell, then the hub's
  `test-free-threaded-wheel.sh` (since 2026-10-12; a local script until then). The
  driver's free-threaded proof creates each cp314t module but never runs its body. The
  second step installs the proved `cp314-cp314t` wheel into a fresh 3.14t venv, with the
  lock's set and the `test` extra, and runs `tests/unit/test_thread_safety.py` against it
  from a directory outside the checkout, under `python -I`. With
  `ORCHESTRANT_REQUIRE_COMPILED=1` the test fails when a module under test came from
  source, or when the GIL is on once every module is imported. The step skips where the
  driver builds no twin. Its knobs are `pyproject.toml`'s
  `[tool.antfrastructure.free-threaded-wheel-tests]`, which the Windows x64 build and the
  `windows-11-arm` stage (`wheel-tests-ft.cmd`) read too; the step itself is the hub's
  (`docs/python-ci.md` § The suite on the installed wheel).
- **`ci_static_analysis.sh`** was a local fork until upstream gated; why, and what moved:
  [`CHANGELOG.md`](CHANGELOG.md) *[Unreleased] > Fixed* and
  [`docs/python-ci.md`](third_party/ANTfrastructure/docs/python-ci.md). What it keeps is
  in § 4.
- **`serve-stack.sh`** keeps the directory of the pinned tools prompt
  (`benchmarks/prompts/`), which the hub's gateway renderer cannot know, and
  `LLM_BACKENDS`, passed on as the registry to render, so the gateway and the lab read one
  file.
- `run-lint-gates.sh`, `ci-image-ref.sh` and `renovate-local.sh` are the same shape over
  three more ANTfrastructure entry points (§ 5). `renovate-entrypoint.yml` checks the
  Renovate wrapper and config in CI without applying upgrades.

Two upstream facts repeated here only because they bite before you reach a doc:

- Every ANTfrastructure PowerShell module declares `#requires -Version 7.0`, so the
  hub's build driver and `Build-WindowsArm64.ps1` do too — launch with `pwsh`, never
  `powershell`. Under 5.1 it fails as an opaque `Import-Module` error.
- Composite actions resolve at `@develop`, so a ANTfrastructure change a workflow
  depends on must be pushed **before** the consumer change.

**This repo's glue** (deliberately thin):

- `setup.py` delegates wheel rewriting to `build_support/kataglyphis_wheel.py`, the
  `python-wheel-rewrite` shared asset. Change its canonical copy under the hub's
  `shared/python/`, then refresh this copy. Keep it in the sdist: isolated source builds
  must not import the hub or require a populated submodule.

- `scripts/windows/Resolve-BuildModule.ps1` and `scripts/linux/lib/antfrastructure.sh`
  (its bash twin) cannot live upstream, because they are what *find* the submodule. Both
  are copies of the hub's templates, compared byte for byte below their header: re-copy
  them after an upstream change, never edit them here
  ([the hub's `shared/config/README.md`](third_party/ANTfrastructure/shared/config/README.md#the-template-trees-are-owners-too)).
- There are no local PowerShell modules and, since 2026-10-19, no local x64 build script:
  `python-ci-windows.yml` runs the hub's `windows/scripts/python/Invoke-PythonCiBuild.ps1`,
  which takes this repo's values from `scripts/windows/python-ci.psd1`
  ([`python-ci.md` § *The Windows build driver*](third_party/ANTfrastructure/docs/python-ci.md#the-windows-build-driver)).
  `Build-WindowsArm64.ps1` imports the one hub module it calls, `WindowsPythonWheel.Common`;
  `Stage-Arm64Tests.ps1` adds `WindowsPythonApp.Common` for the free-threaded site.

## 3. Critical invariant: submodule pins

Builds are only supported against the **recorded submodule gitlink**, the commit CI builds
green; `git submodule update --checkout --recursive` restores it. If a drifted submodule
is what you actually want, update the gitlink **and** fix the fallout in the same change.
Drift is guarded by ANTfrastructure's shared Pester suite, run from
[`.github/workflows/submodule-pins.yml`](.github/workflows/submodule-pins.yml)
after any pin bump.

What crosses the pin, each with its gate:

- **The `ruff` version** (`.pre-commit-config.yaml`, `pyproject.toml` and `uv.lock` with
  it) and, since 2026-09-29, torch, torchvision and onnxruntime-genai (§ 4): the lint
  aggregator's consumer-pins gate compares them against `versions.env`.
- **The tools prompt** (`benchmarks/prompts/tool-disambiguation.md`, CRLF, `-text` in
  `.gitattributes`): the hub registry's `serving.gateway.prompts` pins its raw-byte
  sha256, held equal to the P8.1 reports by `tests/unit/benchmark/test_serving_evidence.py`.
- **What `orchestrant/benchmark/gateway.py` reads from the gateway** (the lane behind
  each alias, the `X-Gw-Lane`/`X-Gw-Rerouted` headers, the `/gateway/info` keys and
  registry sha): held to the hub's renderer and plugin by
  `tests/unit/benchmark/test_bench_gateway.py` (`TestAgreesWithTheHub`).
- **The CI image tag**: the lint aggregator's CI image ref gate holds every workflow
  literal to it.
- **The hub's `backends.json`**, which the lab reads: a bump can move what a `--backend`
  name measures (§ 4).

The opposite move, for local work on the newest code, is
`bash third_party/ANTfrastructure/linux/scripts/git-sync-branches.sh` (`--dry-run`
first): [`adopting-in-a-new-project.md` § *Putting every checkout on its branch*](third_party/ANTfrastructure/docs/adopting-in-a-new-project.md#putting-every-checkout-on-its-branch).

## 4. Pitfalls specific to this project

Everything here is false or meaningless in another repo — that is why it is
written out rather than linked.

- **The packages are proven only through `yolo-monitor --self-test`.** CI
  starts every MSI, zip, deb, AppImage and tar.gz with it (`packaging/app.json`'s
  `self_test`), so three things must hold:
  - It must keep printing its JSON report last, between a bare `{` line and a
    bare `}` line, with `"ok": true`. ONNX Runtime prints a notice with braces
    before it.
  - It must keep naming `onnxruntime_module`; that must lie inside the
    package.
  - The model and every other runtime file must be found through
    `ORCHESTRANT_DATA_DIR` / `orchestrant/paths.py`, never relative to a
    checkout.

  The `app` extra's conflicts with every `ml-ai*` extra are load-bearing:
  without them uv reuses the riscv64 git `opencv-python` (4.13) on win32.
- **The model is fetched, never tracked** (2026-10-17). The default is ModelANT's
  DEIM-D-FINE-N (Apache-2.0); why it replaced Ultralytics YOLO26:
  [`resources/models/README.md`](resources/models/README.md). `resources/models/models.lock` pins the `.onnx`, its LICENSE and its NOTICE in
  OxidANT's format and entries, and the hub's `linux/scripts/lib/fetch-data-lock.py` is the
  only way in (2026-10-19; `scripts/fetch_models.py` and its unit test until then, which the
  hub's `tests/test-fetch-data-lock.sh` replaces). A checkout has no model until it runs, so
  every packaging entry point runs it first: `ci_packaging.sh`, the hub's Windows driver
  (`DataLocks` in `scripts/windows/python-ci.psd1`) and `Build-WindowsArm64.ps1`, the
  Windows two with the interpreter `Find-UvPython` resolves.
  `packaging/app.json` ships all three files, since Apache-2.0 asks for the NOTICE to
  travel with the model. A new model goes into ModelANT, and only the lock moves here.
- **The model's metadata picks the preprocessing.** `kataglyphis.preprocess=stretch`
  (ModelANT's models) is a plain resize with one scale per axis
  (`wants_stretch`, `DecodeConfig.scale` as an (x, y) pair); every other model
  keeps the letterbox, so a YOLO export passed with `--model` still decodes.
- **`PACKAGE_NAME` must be exported explicitly.** The upstream drivers default it from
  the distribution name, `OrchestrANT`, which is not an importable module, so
  `ci_tests.sh` and `ci_static_analysis.sh` export `PACKAGE_NAME=orchestrant` before
  delegating. Without it, coverage and the analysis target silently point at a directory
  that does not exist.
- **A static-analysis finding fails CI, on both lanes**; the aggregation is upstream's
  ([`docs/python-ci.md`](third_party/ANTfrastructure/docs/python-ci.md) and the drivers
  it names). What is local lives in `scripts/linux/ci_static_analysis.sh`: the
  `PACKAGE_NAME` export (above), `UV_PROJECT_ENVIRONMENT` pointed at the venv the driver
  creates but never activates (with `UV_NO_SYNC=1`), `STATIC_ANALYSIS_EXTRA_PATHS` and
  `BANDIT_EXCLUDES`, kept equal to `ExtraPaths` and `BanditExcludes` in
  `scripts/windows/python-ci.psd1`.
  It no longer clears `VIRTUAL_ENV`: the family image stopped exporting
  `VIRTUAL_ENV=/opt/venv` and `UV_PYTHON` with hub CON18 (`:latest` of 2026-09-29).
- **`WORKSPACE_ROOT` is pinned by `antfrastructure_exec`; a wrapper that stops going
  through it loses the export silently.** `ci_static_analysis.sh` needs the root before
  that call, so it falls back to `KATAGLYPHIS_REPO_ROOT`; see
  [`shared/linux/templates/README.md`](third_party/ANTfrastructure/shared/linux/templates/README.md).
- **The torch backend is an extra, and the choice is yours.**
  `uv sync --extra pytorch-cpu` (default), `--extra pytorch-cu130` (CUDA 13.0,
  Linux/Windows wheels only, hence the darwin exclusion), `--extra pytorch-rocm10`, or
  `--extra pytorch-custom` with `--find-links` pointing at your own wheelhouse.
  - Pinned at `torch==2.14.1` / `torchvision==0.29.1` across all of them, equal to the
    hub's `PYTORCH_VERSION` / `TORCHVISION_VERSION`: its image force-installs those over
    this lock, and the lint aggregator's consumer-pins gate fails when they differ, so a
    torch bump starts in `versions.env`.
  - `pytorch-rocm10` is for ROCm 10 systems but takes pytorch.org's `rocm7.14` wheel
    line, as the hub's Linux ROCm image does, because no published torch 2.14 wheel is
    built against ROCm 10 (the hub's Windows rocm image compiles its own since
    2026-09-29). That wheel pulls `rocm[device-all,libraries]`, whose packages the extra
    lists only so `[tool.uv.sources]` can route them (uv ignores sources on transitive
    dependencies). It was `pytorch-rocm71` up to v0.0.28.
- **The hub's images build this repo's `develop`.** Since 2026-09-29 the hub's `APP_REF`
  is `develop`, resolved to its current commit when an image build starts. A push here is
  what the next image contains, with no release tag in between, so a fix the image build
  needs goes to `develop` directly. The image's `org.opencontainers.image.version` label
  names the commit it was built from.
- **riscv64 is in the lock since 2026-10-02.** `[tool.uv] environments` names it as a
  real environment, and every non-riscv64 row also says `platform_machine != 'riscv64'`:
  without that the riscv64 row forks win32/darwin into impossible riscv64 cross-products
  and `uv lock` fails on `onnxruntime-directml` (no cp314 wheel). torch/torchvision
  resolve through the `pytorch-cpu`/`pytorch-cu130`/`pytorch-rocm10` git sources;
  `pytorch-custom` stays **empty** on riscv64 on purpose (a git source would shadow a
  local wheel, and no PyPI wheel exists). `uv sync` does not resolve fresh for an
  uncovered platform, as was once claimed: with uv 0.12.17 every mode (`--locked`,
  `--frozen`, plain) errors on the lock's environment check.
- **The registry's lane models are the served models.** The `model` of `geniex-npu`,
  `geniex-gpu` and `geniex-cpu-9b` in the hub's `backends.json` is what `--backend`
  measures and what the gateway pins for its lanes, and a gitlink bump can move one.
  Compare by the `model` a report recorded, not by the backend name.
  - `Start-GeniexServers.ps1` warms each lane by compute name: `geniex-npu`'s and
    `geniex-gpu`'s models, but `geniex-cpu`'s 4B on the CPU lane, so the gateway's
    `agent` lane (`geniex-cpu-9b`) needs
    `-Models @{cpu='empero-ai/Qwen3.8-9B-Distill-GGUF:Q4_K_M'}`, as
    `benchmarks/README.md` § *Through the gateway* passes.
  - Since 2026-09-25 `geniex-gpu` serves `unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_0`
    (the gateway's `chat-long` lane); earlier reports under that name measured the base
    `Qwen3-4B-GGUF`. `geniex-cpu` keeps the 4B; the 9B distill is `geniex-cpu-9b`.
- **Hold serving during a lab campaign.** The gateway forwards any keyed client (`webui`,
  `agent`, `lab`) to the very lanes a direct-lane campaign measures: one request from it
  is load no report names, and it breaks the repeat spacer (GenieX answers an identical
  follow-up along a cache path). P1 has no `hold`/`release` yet (P2/P3), so
  `bash scripts/linux/serve-stack.sh down` before a direct campaign and `up` after it.
- **The gateway re-encodes bodies: `lab-raw-*` is not byte-transparent.** What APISIX
  changes on the way through, and why that can change the tokens:
  [`benchmarks/README.md`](benchmarks/README.md#through-the-gateway-lab--backends). A
  transparency comparison sends its direct baseline with sorted keys, or first shows key
  order does not matter.
- **Every module is compiled, and the cp314t build runs it with the GIL off.**
  `setup.py` cythonizes each `.py` under `orchestrant/` with
  `freethreading_compatible=True`, so module-level mutable state is shared across threads
  without a GIL. A process-wide tally, a lazy singleton or a check-then-act on a queue
  needs a lock or an atomic operation. The ones the 2026-10-11 audit found are fixed and
  driven from eight threads by `tests/unit/test_thread_safety.py`: `gateway._SERVED`,
  `openai_api.gpu_probe()`, the GStreamer reader's frame buffer, queue and `process`,
  `FrameCapture.get_frame`, `log_buffer_lines` and the Flet viewer's frame slot and trend
  history. Extend that file when you add such state. The 3.14t test leg runs it against the source, and `ci_packaging.sh`'s wheel
  step against the wheel (§ 2).
- **Every test must also pass on the compiled wheel**: `windows-11-arm`'s free-threaded leg
  runs the whole suite on it (§ 5). There a module's `__file__` is its `.pyd` (read the `.py`
  beside it), `inspect` finds no source, and a `None` in `sys.modules` imports as `None`,
  because Cython's `import` returns the entry; make a wheel absent with a finder that raises,
  as `tests/unit/test_smoke_checks.py` does.
- **Flet code under `orchestrant/` is compiled with `binding: False`.** Flet cannot read a
  compiled handler's signature, so it passes the event: every handler takes one argument.
  Flet's `run_task` asks for a coroutine function, which a compiled `async def` may not
  pass, so the viewer and the stream page draw from `page.run_thread` pumps that hand
  Flet's own coroutines to `page.loop` (`asyncio.run_coroutine_threadsafe`).
- **The packages have no Flet.** They ship the `app` extra, so `yolo-monitor --ui auto`
  falls back to OpenCV's window there until the hub bundles a Flet desktop client
  offline; `flet_desktop` would otherwise fetch one from GitHub on first start.
- **Generated C files sit next to the Python.** `orchestrant/` contains
  `__init__.c`, `dummy.c`, `logging_config.c` alongside their `.py` sources.
  Tooling that globs the package directory must not treat them as source.
- **`pythonpath = ["."]` in `pyproject.toml` is load-bearing.** The `pytest`
  console script does not put the repo root on `sys.path`, and a
  `tests/__init__.py` would clash with `benchmarks/tests` as the `tests` package.
- **A comment edit in a lab tool changes `tool_sha256`.** `tool_fingerprint`
  hashes the raw source, so reports taken before and after the edit compare as
  different tool versions.
- **GPU monitoring is two vendors with different mechanisms.**
  `orchestrant/monitoring/gpu.py` is a facade over NVML (NVIDIA, the `nvidia-ml-py`
  extra) and `gpu_amd.py` (ADL on Windows, amdgpu sysfs on Linux). On Windows the probe
  reads the ADL PMLog sensors (`ADL2_New_QueryPMLogData_Get`); the legacy Overdrive5 calls
  that `pyadl` wraps return `ADL_ERR` on RDNA-era cards (verified on an RX 9070 XT), so
  there is no `pyadl` dependency. AMD adapters are ordered by dedicated VRAM, largest
  first, so `gpu_index=0` is the discrete GPU on an APU+dGPU host.
- **The lab's candidate sandbox shares uid 1001 with the GitHub runner.** RLIMIT_NPROC is
  checked against every task of the UID, and in CI the runner agent is uid 1001 too,
  outside the container's `/proc`. So `bench_coding._nproc_ceiling` takes the kernel's
  count (a thread-start probe), never a `/proc` census alone: under qemu-user every
  candidate's start creates a QEMU thread, and a census ceiling killed them all with
  `qemu_thread_create: Resource temporarily unavailable` (riscv64 run 37520541295). How
  the address-space ceiling survives qemu-user: the sandbox note in
  [`benchmarks/README.md`](benchmarks/README.md#which-model-writes-code-that-actually-runs-bench_codingpy).
- **A nested `python -m pytest` in `benchmarks/tests` loads no plugins.** The suite's
  conftest sets `PYTEST_DISABLE_PLUGIN_AUTOLOAD` for its subprocesses: this venv's
  plugins cost 49 of each nested run's 60 s under QEMU. A nested run that needs one names
  it with `-p`.
- **The viewer's Flet is pinned exactly, in three places.** The `frontend` extra,
  `uv.lock` and `benchmarks.yml`'s viewer job; `tests/unit/frontend/test_viewer_job.py`
  fails when the job's pin is not the lock's. The desktop client Flet downloads is the
  pinned release's, so a bump moves all three together. FastAPI stays below 0.145 until
  `flet-web` stops passing `openapi_prefix` (flet-dev/flet#6984); `test_view.py` builds
  `flet-web`'s app, so a lock refresh past it fails there before `--web` does.
- **`flet-web` is left out on Windows, on purpose.** Its `platform_machine != "ARM64"`
  marker makes uv resolve Windows ARM64 on its own, and there the CUDA and DirectML extras
  have no wheels: their markers exclude `arm64` and `aarch64`, not Windows' `ARM64`. Taking
  it in means giving those extras that exclusion first.

## 5. Build, run, test

```bash
uv sync --extra pytorch-cpu          # or pytorch-cu130 / pytorch-rocm10 / pytorch-custom

bash scripts/linux/ci_tests.sh           # pytest + coverage
bash scripts/linux/ci_static_analysis.sh # lint + type check (GATING: exits 1 on any finding)
bash scripts/linux/ci_build_docs.sh      # Sphinx
bash scripts/linux/ci_packaging.sh       # wheel + sdist

# The lint gate. The wrapper and .github/workflows/lint-gates.yml are two
# callers of ONE aggregator and must keep saying the same thing: the wrapper
# passes --ratchets unconditionally, the lane passes ratchets: true.
bash scripts/linux/run-lint-gates.sh     # the hub lint aggregator (eight gates; third_party/ANTfrastructure/linux/scripts/run-lint-gates.sh header)

# The family CI image, resolved from ANTfrastructure's versions.env, for
# reproducing a CI step by hand:
#   nerdctl run --rm -v "$PWD:/workspace" -w /workspace \
#     "$(scripts/linux/ci-image-ref.sh)" bash -lc 'scripts/linux/ci_tests.sh'
bash scripts/linux/ci-image-ref.sh       # [--windows] for the Windows tag

# The lab's gateway: APISIX in front of the GenieX lanes, from WSL (rootless
# nerdctl). Backends and acceptance: benchmarks/README.md § Through the gateway.
bash scripts/linux/serve-stack.sh up     # keys | up | status | reload | down

# Dependency upgrades, never by hand: third_party/ANTfrastructure/docs/dependency-updates.md.
# The hub is the only submodule and declares `branch = develop`, so --apply moves its gitlink.
bash scripts/linux/renovate-local.sh                   # what is behind, every manager the tree has
bash scripts/linux/renovate-local.sh --managers pep621 # only the pyproject.toml pins
bash scripts/linux/renovate-local.sh --apply --dry-run # the plan; --apply writes it

# The pre-commit hook (ruff + ruff-format), once per clone:
uv pip install pre-commit && pre-commit install
```

Windows:

```powershell
# Inside :winamd64, from the repo root: the hub's driver, as windows-x64.yml runs it.
pwsh -NoProfile -File .\third_party\ANTfrastructure\windows\scripts\python\Invoke-PythonCiBuild.ps1

# The PowerShell lint gate: the hub's parse + AST-trap + PSScriptAnalyzer
# passes over scripts/windows, with its ruleset consumed by reference. CI runs
# the same gate over the same tree as the `lint-powershell` job of
# .github/workflows/windows-x64.yml; this wrapper is its dev-box twin.
pwsh -NoProfile -File .\scripts\windows\Invoke-Lint.ps1
```

**CI rounds and releases follow the family rule**, ANTfrastructure's
[`branch-and-release-strategy.md`](third_party/ANTfrastructure/docs/branch-and-release-strategy.md)
(here since 2026-10-16). A push or PR runs Linux x64 and the gates; Linux arm64, Linux riscv64,
Windows x64 and Windows arm64 run on request (`[ci arm64]`, `[ci riscv64]`, `[ci windows]`,
`[ci all]`). A push to `main` releases the `VERSION.txt` version with every lane's `packages/`:
`release.yml` calls the hub's `release-publish.yml`, which collects them with its `collect`
options and tags `v<version>` (since 2026-10-19; `gh workflow run release.yml --ref develop -f
dry-run=true` collects and lists them, tags nothing). It replaced the pushed `v*` tags, whose
four lanes each attached their own files, so a red lane still left a release without its packages.

| CI lane | What it runs |
| --- | --- |
| `.github/workflows/linux-x64.yml`, `.github/workflows/linux-arm64.yml` | native x86-64 and arm64, one `arches` row each |
| `.github/workflows/linux-riscv64.yml` | the riscv64 image under QEMU on an amd64 runner, `arches: riscv64`, tests only |
| `.github/workflows/windows-x64.yml` | the container build and, through the `lint-powershell: true` input, the PowerShell gate (parse + AST traps + PSScriptAnalyzer over `scripts/windows`) as a second job that runs even when the build fails; the standalone `powershell-lint.yml` it replaced is gone |
| `.github/workflows/windows-arm64-cross.yml` | "Windows arm64 · cross build + test" (2026-10-01; the badge name moved from "… + run" on 2026-10-02, the day the device pytest came in), below |
| `.github/workflows/lint-gates.yml` | the hub lint aggregator: eight gates with `ratchets: true`, see the `third_party/ANTfrastructure/linux/scripts/run-lint-gates.sh` header |
| `.github/workflows/benchmarks.yml` | the lab's live-ollama contract job (on the runner until the Linux image carries pwsh, hub CON131) and the viewer, in the family image on its pinned X.Y and X.Yt (`benchmarks/ci-image-leg.sh`, owner 2026-10-20); the lab's offline suite, `benchmarks/tests`, is in `testpaths` since 2026-10-06, so every lane runs it |
| `.github/workflows/submodule-pins.yml` | § 3 |

All but the benchmarks lane are pure configuration for ANTfrastructure reusable
workflows; as of 2026-09-15 that includes the lint, pin and PowerShell-lint lanes, whose
jobs used to be inline copies here.

**Windows arm64.** `scripts/windows/Build-WindowsArm64.ps1` cross-builds the arm64 bundle
and its zip, MSI and MSIX in the hub's `:winarm64` image. The `windows-11-arm` job starts
the bundle and the unpacked zip, and runs the whole pytest suite twice: under the bundle's own
arm64 `python.exe`, so the shipped package is what it tests, and under the free-threaded kit's
`python3.Yt.exe` with the cp3XYt wheel:

- `scripts/windows/Stage-Arm64Tests.ps1` stages the tests, `pytest`, `requests`,
  coverage and the benchmark/report plugins, offline from the image's wheel store
  `PYTHON_WHEELS` (hub CON67, `:winarm64` since 2026-10-10; PyPI is never asked). They are
  resolved against a `+gil` interpreter: a bare `3.14` can pick a free-threaded build,
  whose cp314t `.pyd` wheels refuse to load in the bundle's plain-3.14 runtime (the hub's
  `uv_python_request` rule).
- The free-threaded leg (`run-pytest-ft.cmd`, CON111, 2026-10-20) gets `site-ft`: the
  cross lane's cp3XYt wheel and the lock's core and `test` set, as x64's free-threaded leg
  syncs them, installed for the target with the image's `python3.Yt` by the hub's
  `Install-PythonCrossSite`. psutil's cp315t wheel, which PyPI lacks, comes from the image's
  `wheels-free-threaded\deps`; pyyaml builds pure. line-profiler (no free-threading declared)
  and py-spy (a Rust binary, sdist only) stay out: no test imports them. The wheel ships no
  `.py`, so the stage copies each compiled module's source beside its `.pyd` for the tool
  fingerprints; the import still takes the `.pyd`, which `ORCHESTRANT_REQUIRE_COMPILED=1` proves.
- It adds a manifest entry for x64's `bench/demo_pytest_benchmark.py`, but no
  `orchestrant` package beside the tests. Static analysis and the Cython wheel stay on x64, and no package
  is installed on the device (hub `docs/python-app-bundles.md` § Packages).
- `test-results-path: results/*` (hub 305a1c92) uploads the device's coverage/junit/html/md
  reports as the `-test-results` artifact. Without it they exist only in the job log,
  since the `-tests` artifact is uploaded before the device runs.

**Workflow names follow the fleet convention**
([`adopting-in-a-new-project.md` § *Workflow file names and display names*](third_party/ANTfrastructure/docs/adopting-in-a-new-project.md#workflow-file-names-and-display-names));
the last file named the old way, `ubuntu-26.04-amd64-arm64.yml`, became `linux-x64.yml` +
`linux-arm64.yml` on 2026-10-01. Every lane file sets a top-level `permissions: contents: read`;
`on-request.yml` and `release.yml` start from `{}` and grant per job; only `release.yml`'s two
calls of `release-publish.yml` get `contents: write`, which GitHub checks against every job of the
called file, its read-only check job included. `.github/actionlint.yaml` only adds the
`ubuntu-26.04` runner labels the pinned actionlint predates; it disables no rule.

## 6. Docs owned by this repo

**Write docs like a developer: short and precise** (owner, 2026-10-19). Say what a thing does,
how to run it and what breaks; no filler, no marketing, no repetition.

- Sphinx sources in `docs/`, published to <https://orchestr-ant-ion.jonasheinle.de/>.
  A new page goes into `docs/source/index.rst`'s toctree. `conf.py` calls the family
  theme's `setup_theme()`, which `docs/Makefile` installs editable from the hub's vendored
  DocumANTation before every build. It is not a uv path source: a checkout without that
  submodule, such as the hub image's clone of this repo, would then fail every
  `uv sync --locked`. The docs build copies
  `README.md` and `CHANGELOG.md` into `docs/source/` first, and `pyproject.toml`'s
  `readme` makes the README the package's long description, so the README links
  with absolute GitHub URLs.
- `README.md` is the short overview the family's README rule asks for
  ([`repository-family.md`](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/repository-family.md#the-readme-rule)).
  Installing, demos, tests and the development setup are
  [`docs/source/getting-started.md`](docs/source/getting-started.md); the package,
  `yolo-monitor`, the model and the extras are
  [`docs/source/overview.md`](docs/source/overview.md).
- `CHANGELOG.md` and `VERSION.txt` — `pyproject.toml` reads the version from
  `VERSION.txt`, so bump it there, not in the TOML.
- Update docs in the same PR as user-facing behaviour changes.
