# Getting started

Package CI checks the ModelANT licence and notice in each built DEB, tarball, AppImage, ZIP, MSI and MSIX against the original texts pinned in `resources/models/models.lock`.
Every package also carries `THIRD-PARTY-NOTICES.txt` at its root: each bundled open-source component, its license and where its original texts are inside the package. CI fails a package whose contents the notice does not match.

For a package round, generate the notice from its four CPU archives and one archive for
each GPU variant being shipped. CPU and GPU inventories have separate platform labels,
and generation rejects duplicate labels. The Windows DirectML AMD and NVIDIA builds
have distinct package names and inventory labels, so include both when generating the
notice and check each finished ZIP independently.
All variants carry the original NVIDIA CUDA 13.4 and cuDNN 9.27.0.42 license texts;
the component inventory names them when an image's OpenCV closure includes those
runtime libraries. The NVIDIA candidate checks the DLLs and texts against the image's
matching SDKs before packaging.

Two ways in: a ready-made package that carries its own Python, or the `orchestrant` library in
an environment of your own. What the package, `yolo-monitor` and the extras are is
[overview.md](overview.md); how to contribute to any of the family's repositories is
ANTfrastructure's [`repository-family.md`](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/repository-family.md).

## Install a package

**Ready-made packages: no Python needed.** Every lane run builds them as CI artifacts, and every
push to `main` publishes them as the `v<version>` GitHub release once every lane is green
(`release.yml`, since 2026-10-16). Each one carries its own CPython, the locked dependencies,
ONNX Runtime and the detection model with its LICENSE and NOTICE, and CI starts each one once
(`yolo-monitor --self-test`) before it ships. On a bare Ubuntu runner the Linux tarball also runs
`--self-test --self-test-image two_cats.jpg`, whose report then carries what it found (`detected`, the
shape of `tests/unit/detector/two_cats_golden.json`), and the hub's `compare_detector_parity.py` holds it to
that golden:

| Platform | Package | After installing |
|---|---|---|
| Windows x64 / arm64 | `orchestrant-<version>-windows-<x64\|arm64>.msi` | Start menu → OrchestrANT; `yolo-monitor`, `orchestrant-bench` and `orchestrant-smoke` on `PATH` |
| Windows x64 / arm64 | `orchestrant-<version>-windows-<x64\|arm64>.msix` | trust the `…-test-signing.cer` first (below); then Start menu → OrchestrANT, and the three commands as execution aliases |
| Windows x64 / arm64 | `orchestrant-<version>-windows-<x64\|arm64>.zip` | unzip anywhere, run `yolo-monitor.exe` |
| Linux x86_64 / arm64 | `orchestrant_<version>_<amd64\|arm64>.deb` | `sudo apt install ./orchestrant_<version>_<arch>.deb`, then `yolo-monitor` |
| Linux x86_64 / arm64 | `orchestrant-<version>-<x86_64\|aarch64>.AppImage` | `chmod +x`, then run it |
| Linux x86_64 / arm64 | `orchestrant-<version>-linux-<x86_64\|aarch64>.tar.gz` | unpack anywhere, run `bin/yolo-monitor` |

### Windows DirectML candidate

The on-request **Windows x64 DirectML candidate** workflow builds ZIP, MSI and MSIX packages
in the NVIDIA and AMD Windows x64 registry images and uploads distinct candidates. Both use
the images' DirectML-capable ONNX Runtime. The build checks that the provider is present
and runs the bundled model explicitly on CPU through the launcher, including when testing
the packaged installers. The bundle report must name only the CPU provider and locate its
runtime and model inside the bundle.
The vendor packages share the same MSI upgrade family and MSIX identity; choose the
candidate for your GPU instead of treating them as side-by-side installations.
Each DirectML package selects its named vendor's physical adapter by default, preferring
the adapter with the most dedicated video memory. If none is found, launch from a terminal
to see the error, or run `yolo-monitor --device cpu` to use CPU. Pass `--gpu N` to choose
a specific adapter, or `--device auto` to use the general automatic fallback behavior.
The finished candidate's ONNX Runtime core, Python extension, DirectML DLL and every
bundled provider DLL must match the same chain wheel and survive packaging byte for byte.
The recorded infrastructure pin lacks that verifier, so this workflow stops before
pulling images until the shared changes are published and pinned.
The tracked notice does not yet contain the DirectML inventory labels, so its licence gate
will fail until the new-round notice is generated and packaged; see the
[backlog](https://github.com/Kataglyphis/OrchestrANT/blob/develop/BACKLOG.md).
For the inventory round, dispatch the workflow with `inventory-only` selected. It uploads
separately named ZIPs and an `INVENTORY-ONLY.txt` warning, with no MSI or MSIX and no
claim that the notice is complete. Generate the new notice from the matching CPU and GPU
archives, then rerun the normal candidate and check each vendor's ZIP independently.
Candidate and inventory uploads include the run-attempt number and cannot be overwritten;
the licence and GPU jobs select the candidate from their own attempt. Choose **Re-run all jobs**
to rebuild the candidates for a new attempt; rerunning only failed or downstream jobs can
leave no candidate for that attempt and fails instead of using an older upload.
Attempt names alone do not prove that every successful check used the same package bytes.
A hosted build does not prove GPU execution. Select `verify-gpu` to run additional profiles on self-hosted
Windows x64 runners labelled `directml-gpu-nvidia` and `directml-gpu-amd`; each requires a
matching physical adapter and profiles the production session with explicit `directml`,
the packaged defaults and explicit `auto`. All three profiles must record operators on DirectML. The profile
also checks that the package's default selects that vendor and adapter on a multi-GPU machine.
On your own GPU, unpack the ZIP and run `scripts/diagnostics/detector_parity.py --device directml`
with the bundle's `runtime\python.exe`; its [README](https://github.com/Kataglyphis/OrchestrANT/blob/develop/scripts/diagnostics/README.md)
has the capture and comparison steps.
The candidate is not added to the
release workflow. The Windows candidate and Linux inventory workflows also accept reusable
workflow calls with the same default-off `verify-gpu` input; Windows retains the separate
`inventory-only` input. Calling either workflow does not promote its outputs to a release.
Set Windows `gpu-vendor` to `amd` or `nvidia`, or keep `all` for both. Linux `gpu-tuple`
accepts `nvidia-x64`, `nvidia-arm64`, `amd-x64`, or `all`. For overlapping calls in one
parent workflow, pass a distinct `invocation-scope` to each call; use 1–32 lowercase ASCII
letters, digits or hyphens, starting with a letter. `default` keeps the existing artifact
names; another scope appends a validated suffix. The jobs validate before using it and never
replace an upload. Invalid scopes or unsupported target choices fail before pulling an image.
Successful candidates still receive their clean-host or licence checks if a different selected
matrix row fails; missing uploads fail their own checks. Hardware profiling remains gated on
the overall prerequisite jobs, so select a single tuple to profile it independently after a
different tuple fails. Duplicate calls with the same scope are not isolated.

### Linux GPU inventory candidates

The **Linux GPU inventory candidates** workflow uses the registry's
NVIDIA image for x64 and arm64 and its ROCm image for x64. Its separately named
`unverified-gpu-*` artifacts are for collecting package inventories; they are not
release packages and have not been proven on GPU hardware. The packages need the
compatible CUDA/cuDNN or ROCm/MIGraphX runtime and driver on the host.
Package and test artifacts include the run-attempt number and are not overwritten.
The clean-host and optional GPU jobs require the package from their own attempt;
choose **Re-run all jobs** so each candidate is rebuilt for that attempt. Rerunning only
failed or downstream jobs can leave no package for that attempt and fails.
This prevents a later attempt from replacing the package under the same name, but
does not itself establish that every successful check used the same artifact.
The inventory builds test the pinned GIL interpreter and skip free-threaded wheels;
the separate CPU lanes retain their free-threaded checks.
Each candidate's DEB, tarball and AppImage also run the packaged model with
`--device cpu` on a clean Ubuntu runner before the optional GPU profile. This checks
CPU fallback from the finished packages; it does not establish GPU execution.
Select `verify-gpu` to profile each unverified TAR, DEB payload and AppImage on self-hosted runners
labelled `gpu-nvidia-linux-x64`, `gpu-nvidia-linux-arm64` and `gpu-amd-linux-x64`.
The profile uses the candidate's own launcher, Python runtime, ONNX Runtime and model.
It checks both the explicit provider and the normal `yolo-monitor` default selection,
requiring a model operator on the requested provider in each run. The check invokes the
AppImage through its `orchestrant-smoke` alias with extract-and-run, without FUSE. Runners
need `dpkg-deb` and temporary space for extracted packages as well as the host GPU SDK.
The default check uses the monitor's default GPU index 0; on a mixed-GPU host this may not
select the vendor being tested, and the check fails rather than claiming the wrong device
works. A passing hardware profile does not publish or approve the candidate for release.

Before an image is pulled, preflight checks the recorded ANTfrastructure pin for the packaging
API the build needs, including the plugin provider marker `PYTHON_APP_PLUGIN_EP_API=1`.
A complete new-round third-party notice and finished-package licence checks are still
required before these candidates can ship.

Use `yolo-monitor --self-test --device cpu` to test CPU inference, or choose `cuda`,
`migraphx`, or `directml` to require the corresponding ONNX Runtime provider. `--gpu 1`
selects a different device index. CUDA needs a compatible NVIDIA driver and CUDA/cuDNN
runtime; MIGraphX needs a compatible AMD driver and ROCm/MIGraphX runtime; DirectML needs
a compatible Windows GPU and driver. MIGraphX is AMD's plugin module
([AMD's MIGraphX plugin](#amds-migraphx-plugin)); it first copies a few bytes to the selected GPU, and
after 30 seconds without an answer `migraphx` fails and `auto` moves on to the next provider.
CUDA first runs one kernel on the selected card. On a card this ONNX Runtime has no kernel for
(an RTX 20xx today), `cuda` fails naming the card and `auto` moves on, to the CPU in the end.
Provider activation does not prove every model
operator ran on the GPU; use an inference profile to check placement.
For a Linux bundle, run `bin/orchestrant-smoke --ort-profile cuda` or
`bin/orchestrant-smoke --ort-profile migraphx` to require actual GPU node placement;
`--ort-profile cpu` verifies the CPU path without GPU hardware. Add `--gpu 1` to select
another device. The JSON report gives the model, runtime and counts by provider; for
`migraphx` the provider is `MIGraphX` and `plugin_module` is the bundle's module.

**The Linux packages carry the image's glibc and the family's own GStreamer** (since
2026-10-18), so they start on Debian 13 and Ubuntu 24.04 too, and CI installs and starts them on
a bare `ubuntu-26.04` runner. Releases up to v0.0.32 still need glibc 2.43 (Ubuntu 26.04 or
newer), which their bundled ONNX Runtime asks for. On Windows, OpenCV is the build without Media
Foundation, so it also runs on Windows N and Server Core.

The MSIX is signed with a test certificate, not a trusted publisher, so Windows
installs it only after you trust that certificate. From an elevated PowerShell:

```powershell
Import-Certificate -FilePath .\orchestrant-<version>-windows-x64-test-signing.cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople
Add-AppxPackage .\orchestrant-<version>-windows-x64.msix
```

The MSI needs no such step. How the packages are built, and what each CI check proves, is
ANTfrastructure's
[`python-app-bundles.md`](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/python-app-bundles.md).

### AMD's MIGraphX plugin

**Not proven on a card.** No line of this path has run with AMD's module: the build host is
arm64 without an AMD GPU, and the ROCm image is x64 only. The tests run the real ONNX Runtime
with no module, with a file that is no library and, in the arm64 image, with Qualcomm's plugin
module under the name `MIGraphX`. The card proof is an open `BACKLOG.md` entry.

The ROCm image has no MIGraphX provider built into ONNX Runtime since hub commit `ffdbcf1c`.
`--device migraphx` loads AMD's plugin module `libmigraphx-ep.so` instead, on Linux only:

- **Where the module is looked for.** `$ORT_AMDGPU_EP_ROOT/lib/libmigraphx-ep.so` when the
  variable is not empty and the file exists, then `onnxruntime/capi/libmigraphx-ep.so` beside
  the loaded ONNX Runtime, where the hub's builder puts it in an AMD package. Every image but
  the ROCm one sets the variable to the empty string, and that names no place.
- **Registration.** The module is registered once per process under the name `MIGraphX`
  (`orchestrant/yolo/plugin_ep.py`). AMD's module takes that name as its provider name, so the
  session's providers, the self-test report and ONNX Runtime profiles say `MIGraphX`, no longer
  `MIGraphXExecutionProvider`. The device name on the command line stays `migraphx`.
- **Device.** The session gets one device, the one `--gpu` counts to among those the module
  offers, with the option `device_id` and no other: AMD's module fails on an option it does not
  list. The copy to the GPU described above runs before the registration, which calls HIP.
- **When it cannot load.** `--device migraphx` fails and says why: the places searched and the
  variable's value when no module is found, the module and ONNX Runtime's reason when the
  registration fails (AMD's module asks HIP for its devices and fails without a card), or the
  number of devices offered when `--gpu` names none of them. `--device auto` skips MIGraphX
  where no module is on disk, and goes on to the next provider with a warning in the other
  cases. A registered plugin may still claim no operator; only a profile shows placement.
- **The self-test report** names what was registered in `registered_plugin_eps`, which the
  hub's AMD bundle gate requires to hold `MIGraphX`, and the reason for a failed attempt in
  `plugin_ep_errors`. `--device cpu` registers nothing. The hub says both that its gate permits
  a build without a GPU and that the registration fails without one (HIP error 100). If the
  second holds, the hosted AMD build stops at that gate with
  `the self-test did not register the plugin EP MIGraphX: []`, which is the gate's answer to a
  report without the name. AMD's registration without a card was not run here.
- **Package proofs use the package's module.** `orchestrant-smoke --ort-profile migraphx`
  fails when the module found is outside the bundle, and `scripts/linux/profile-gpu-candidate.sh`
  unsets `ORT_AMDGPU_EP_ROOT`: inside the ROCm image the variable names the image's copy.
- **Licence texts.** Every package carries the texts of the module and of what is linked into
  it as `licenses/onnxruntime-ep-amdgpu-*`; `packaging/native-license-provenance.md` names the
  source of each. The tracked `THIRD-PARTY-NOTICES.txt` still describes the AMD package of the
  old form and is generated again from the first package that carries the module.
- **The hub's profile checker.** `shared/python/verify_ort_profile.py` expects the provider
  `MIGraphX` for `--device migraphx`; no trace from a card has been checked with it.

## Install the Python library

Three ways to use OrchestrANT as a library:

1. **pip, from GitHub:**
   ```bash
   pip install OrchestrANT@git+https://github.com/Kataglyphis/OrchestrANT
   ```
   or a tagged version:
   ```bash
   pip install OrchestrANT@git+https://github.com/Kataglyphis/OrchestrANT@v0.0.1
   ```

2. **From a clone:**
   ```bash
   git clone --recurse-submodules https://github.com/Kataglyphis/OrchestrANT
   pip install .
   ```

   or `pip install -e .` for an editable install, which reflects changes in the clone.

3. **As a submodule of your repository** (not generally recommended: your repo's
   installation then has to install all of OrchestrANT's dependencies itself):
   ```bash
   git submodule add https://github.com/Kataglyphis/OrchestrANT
   ```

The heavy dependencies are extras, chosen at install time: [overview.md § Extras](overview.md#extras).

### Picamera web browser live stream

`picamera2`, the Raspberry Pi camera library behind `orchestrant/streaming/capture.py`, is
installed via `apt`, not PyPI ([source](https://github.com/raspberrypi/picamera2)), so the venv
has to see the system packages:

```bash
sudo apt install python3-picamera2
uv venv --system-site-packages
uv run --extra frontend orchestrant-stream   # http://127.0.0.1:5000
```

`KATAGLYPHIS_STREAM_HOST=0.0.0.0` lets other machines on the network open it. Each browser tab
draws the camera's frames as JPEG through Flet; it replaced the Flask MJPEG page.

## Demos

```bash
python third_party/ANTfrastructure/linux/scripts/lib/fetch-data-lock.py --lock resources/models/models.lock   # once: the model
uv run --extra app python -m orchestrant.yolo.monitor
uv run --extra app --extra frontend python -m orchestrant.yolo.monitor --web   # in a browser
uv run python examples/monitoring.py
```

`yolo-monitor` imports OpenCV and ONNX Runtime, which only the `app` extra (or an `ml-ai*`
extra) installs. `uv run --extra app yolo-monitor --self-test` is the check without a camera.

## Development setup

A virtual environment on the pinned CPython, `PYTHON_VERSION` in
`third_party/ANTfrastructure/linux/scripts/01-core/versions.env`; `requires-python` allows `>=3.11`,
but the lanes test the pin alone:

```bash
uv venv --python 3.15    # 3.15t for the free-threaded build
```

`pyproject.toml` asks for uv 0.11.3 or newer (`[tool.uv] required-version`). An older uv stops with
`Required uv version ... does not match the running version` before it reads `uv.lock`. 0.11.3 is the oldest
release seen reading the lock, not a proven first one; the lanes run `UV_VERSION` from the same `versions.env`.

The ruff configuration behind `pre-commit run`, the static-analysis gate, binary-only wheels and the tools are shared with
WebDavClient: [ANTfrastructure `python-ci.md` § Working on a Python consumer locally](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/python-ci.md#working-on-a-python-consumer-locally).

Compiled wheels use `build_support/kataglyphis_wheel.py`, the manifest-checked copy of
ANTfrastructure's shared wheel rewriter. `setup.py` keeps this project's metadata and compiler
options; the helper owns source/signature removal, compression and `RECORD` regeneration.
`MANIFEST.in` includes the helper, so an isolated sdist builds without submodules. Change the
canonical helper in ANTfrastructure and refresh the copy rather than editing it locally.

Dependency upgrades go through Renovate as a local CLI, never by hand: the commands are in
[`AGENTS.md` § 5](https://github.com/Kataglyphis/OrchestrANT/blob/develop/AGENTS.md#5-build-run-test).
The copied wrapper initializes an absent pinned hub only for a real `--apply`; a report or
`--apply --dry-run` needs that checkout initialized or an existing hub supplied explicitly.
The driver updates the direct hub gitlink to its remote default branch tip regardless of its
`.gitmodules branch` setting.
For Torch/vision, publish and commit the ANTfrastructure submodule pin first. The shared
Renovate driver then checks all seven riscv64 Git tags against GitHub and moves those URLs,
the wheel pins and `uv.lock` only for the pair accepted by that pinned hub version.

## Tests

The requested Windows x64 lane also runs `scripts/windows/tests/StageDirectoryReuse.Tests.ps1`
through ANTfrastructure's Pester action. It checks that the arm64 test stage starts empty and
fails when its output directory cannot be created. The shared
`WindowsStageDirectory.Fixture.psm1` owns the AST and filesystem checks; this repo supplies
its script, reset command and callback. Its job has a 20-minute timeout. Both Windows
build checkouts initialize direct submodules only, so they do not descend through the
ANTfrastructure/DocumANTation cycle.

```bash
uv sync --extra pytorch-cpu --extra test
uv run pytest tests/unit
bash scripts/linux/ci_tests.sh   # what the Linux lane runs: pytest + coverage
```

Each stable Linux test leg (x64 and arm64, GIL and free-threaded) fails below 60 % of package
lines: both workflows pass `coverage-min: '60'` to the hub's lane, and the leg's
`test report check:` line ends in `(minimum 60%)`.

The LLM lab's own suite, `benchmarks/tests`, is in `testpaths` beside `tests`, so
every lane runs it. Its ten harness modules skip on Windows: the candidate sandbox
needs POSIX rlimits, process groups and shell fixtures.

### Hot-path timings

`bench/demo_pytest_benchmark.py` times three hot paths with plain pytest, no plugin:
the detector's letterbox preprocess of a 1280x720 frame, the decode of a YOLOv8-shaped
`(1, 84, 8400)` output, and `orchestrant-serve`'s chat-completions body check
(`orchestrant.genai.server.parse_request`). Each runs 3 warm-up and 30 timed calls on
synthetic inputs and asserts its output, so a wrong result fails before a slow one is recorded.
`postprocess` has no suppression (NMS) step, so none is timed.

```bash
uv run pytest bench/
ORCHESTRANT_BENCH_JSON=/tmp/hot-paths.json uv run pytest bench/
```

The report goes to `ORCHESTRANT_BENCH_JSON`, default
`logs/test-results/bench/orchestrant-hot-paths-gil.json` (`-free-threaded.json` on a free-threaded
interpreter), so it rides in each lane's `test-reports-<arch>` artifact,
in the envelope `orchestrant-bench` writes (`benchmark`, `provenance`, `config`, `reports`).
Each `reports` entry has a `label` (`detector_preprocess`, `detector_decode`,
`serve_request_parse`), `p50_ms`, `p95_ms`, `min_ms`, `mean_ms`, `better: "lower"` and the raw
`results`; these are the keys a bench-history caller declares. Wall time is recorded, never
gated: `linux-x64.yml`'s `bench-history` job compares each flavour's `p50_ms` and `p95_ms` with the
median of up to 20 earlier develop runs (keys `hot-paths-x64-gil`, `hot-paths-x64-free-threaded`)
and only warns. On a free-threaded interpreter without OpenCV the preprocess timing skips, visibly:
OpenCV publishes no free-threaded wheel.

## Static analysis

```bash
./scripts/linux/ci_static_analysis.sh > "ci_analysis_$(date +%Y%m%d_%H%M%S).log" 2>&1
```

The CI gate on both lanes: `ruff check --no-fix`, `ruff format --check`, `ty`, `bandit`,
`vulture` and `codespell`. It exits `1` on any finding, which the redirect hides
([why](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/python-ci.md#working-on-a-python-consumer-locally)).

## Binary-only wheels

The shipped wheels compile every module with Cython; inspecting one:
[ANTfrastructure `python-ci.md`](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/python-ci.md#working-on-a-python-consumer-locally).

**Before an editable install over a Cython build**, delete the files the build generated. They
all sit under `orchestrant/` (`.gitignore`'s `orchestrant/**/*.c`):

```bash
find orchestrant -type f \( -name '*.c' -o -name '*.so' -o -name '*.pyd' \) -delete
```

On Windows:

```powershell
Get-ChildItem -Path orchestrant -Recurse -File | Where-Object { $_.Extension -in '.c', '.so', '.pyd' } | Remove-Item
```
