# Getting started

Two ways in: a ready-made package that carries its own Python, or the `orchestrant` library in
an environment of your own. What the package, `yolo-monitor` and the extras are is
[overview.md](overview.md); how to contribute to any of the family's repositories is
ANTfrastructure's [`repository-family.md`](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/repository-family.md).

## Install a package

**Ready-made packages: no Python needed.** Every lane run builds them as CI artifacts, and every
push to `main` publishes them as the `v<version>` GitHub release once every lane is green
(`release.yml`, since 2026-10-16). Each one carries its own CPython, the locked dependencies,
ONNX Runtime and the detection model with its LICENSE and NOTICE, and CI starts each one once
(`yolo-monitor --self-test`) before it ships:

| Platform | Package | After installing |
|---|---|---|
| Windows x64 / arm64 | `orchestrant-<version>-windows-<x64\|arm64>.msi` | Start menu → OrchestrANT; `yolo-monitor`, `orchestrant-bench` and `orchestrant-smoke` on `PATH` |
| Windows x64 / arm64 | `orchestrant-<version>-windows-<x64\|arm64>.msix` | trust the `…-test-signing.cer` first (below); then Start menu → OrchestrANT, and the three commands as execution aliases |
| Windows x64 / arm64 | `orchestrant-<version>-windows-<x64\|arm64>.zip` | unzip anywhere, run `yolo-monitor.exe` |
| Linux x86_64 / arm64 | `orchestrant_<version>_<amd64\|arm64>.deb` | `sudo apt install ./orchestrant_<version>_<arch>.deb`, then `yolo-monitor` |
| Linux x86_64 / arm64 | `orchestrant-<version>-<x86_64\|aarch64>.AppImage` | `chmod +x`, then run it |
| Linux x86_64 / arm64 | `orchestrant-<version>-linux-<x86_64\|aarch64>.tar.gz` | unpack anywhere, run `bin/yolo-monitor` |

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

The pre-commit hook, the static-analysis gate, binary-only wheels and the tools are shared with
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

The LLM lab's own suite, `benchmarks/tests`, is in `testpaths` beside `tests`, so
every lane runs it. Its ten harness modules skip on Windows: the candidate sandbox
needs POSIX rlimits, process groups and shell fixtures.

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
