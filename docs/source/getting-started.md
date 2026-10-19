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

To use OrchestrANT as a Python library instead, there are three ways to install it:

1. **Install directly via pip:**
   ```bash
   pip install OrchestrANT@git+https://github.com/Kataglyphis/OrchestrANT
   ```
   or install a specific tagged version:
   ```bash
   pip install OrchestrANT@git+https://github.com/Kataglyphis/OrchestrANT@v0.0.1
   ```

2. **Install after cloning the repo:**
   ```bash
   git clone --recurse-submodules https://github.com/Kataglyphis/OrchestrANT
   pip install .
   ```

   or `pip install -e .`, an editable install: changes in the repo are reflected in your
   environment.

3. **Add as a submodule to your repository:**
   ```bash
   git submodule add https://github.com/Kataglyphis/OrchestrANT
   ```
   Make sure that all dependencies are installed during your repo's installation.
   (Not generally recommended, as it can be more complicated.)

The heavy dependencies are extras, chosen at install time: [overview.md § Extras](overview.md#extras).

### Picamera web browser live stream

`picamera2`, the Raspberry Pi camera library behind `orchestrant/streaming/capture.py`, is
installed via `apt`, not PyPI ([source](https://github.com/raspberrypi/picamera2)), so the venv
has to see the system packages:

```bash
sudo apt install python3-picamera2
uv venv --system-site-packages
```

## Demos

```bash
python scripts/fetch_models.py   # once: the model yolo-monitor loads
uv run --extra app python -m orchestrant.yolo.monitor
uv run python examples/monitoring.py
```

`yolo-monitor` imports OpenCV and ONNX Runtime, which only the `app` extra (or an `ml-ai*`
extra) installs. `uv run --extra app yolo-monitor --self-test` is the check without a camera.

## Development setup

Feel free to adjust for your own environment, for example a virtual environment with a specific
Python version (`requires-python` is `>=3.11`):

```bash
uv venv
python3.11 -m venv .venv
```

The pre-commit hook, the static-analysis gate, binary-only wheels and the tools are shared with
WebDavClient: [ANTfrastructure `python-ci.md` § Working on a Python consumer locally](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/python-ci.md#working-on-a-python-consumer-locally).

Dependency upgrades go through Renovate as a local CLI, never by hand: the commands are in
[`AGENTS.md` § 5](https://github.com/Kataglyphis/OrchestrANT/blob/develop/AGENTS.md#5-build-run-test).

## Tests

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

The CI gate: it exits `1` on any finding, which the redirect hides
([why](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/python-ci.md#working-on-a-python-consumer-locally)).

## Binary-only wheels

The shipped wheels compile every module with Cython; inspecting one:
[ANTfrastructure `python-ci.md`](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/python-ci.md#working-on-a-python-consumer-locally).

**Before an editable install over a Cython build**, delete the files the build generated. They
all sit under `orchestrant/` (`.gitignore`'s `orchestrant/**/*.c`); Cython writes no HTML here,
and `orchestrant/streaming/template-files/index.html` is a tracked template that must stay:

```bash
find orchestrant -type f \( -name '*.c' -o -name '*.so' -o -name '*.pyd' \) -delete
```

On Windows:

```powershell
Get-ChildItem -Path orchestrant -Recurse -File | Where-Object { $_.Extension -in '.c', '.so', '.pyd' } | Remove-Item
```
