<div align="center">
  <a href="https://jonasheinle.de">
    <img src="images/logo.png" alt="logo" width="200" />
  </a>

  <h1>OrchestrANT</h1>

  <h4>👨🏻‍💻 Fast python AI prototyping 🐍 </h4>
</div>

Docs can be found [here](https://orchestr-ant-ion.jonasheinle.de/).

[![Build + test + run on Linux natively - x86-64/arm64](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/ubuntu-26.04-amd64-arm64.yml/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/ubuntu-26.04-amd64-arm64.yml)
[![Windows x64 · build + test](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/windows-x64.yml/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/windows-x64.yml)
[![Lint gates](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/lint-gates.yml/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/lint-gates.yml)
[![Submodule pins](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/submodule-pins.yml/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/submodule-pins.yml)
[![Benchmarks](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/benchmarks.yml/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/benchmarks.yml)
[![CodeQL](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/Kataglyphis/OrchestrANT/actions/workflows/github-code-scanning/codeql)
[![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg)](https://www.paypal.com/paypalme/JonasHeinle)
[![Twitter](https://img.shields.io/twitter/follow/Cataglyphis_?style=social)](https://twitter.com/Cataglyphis_)

# OrchestrANT

## Table of Contents

- [About The Project](#about-the-project)
  - [Key Features](#key-features)
  - [LLM benchmark lab](#llm-benchmark-lab)
  - [Useful Tools](#useful-tools)
- [Overview](#overview)
- [Getting Started](#getting-started)
  - [Setup](#setup)
  - [Pre commit hook](#pre-commit-hook)
  - [Dependency updates](#dependency-updates)
  - [Installation](#installation)
  - [Deployment Recommendations (Hardware/Software)](#deployment-recommendations-hardwaresoftware)
- [Tests](#tests)
- [Demos](#demos)
- [Contributing](#contributing)
- [License](#license)
- [Contact and Maintainers](#contact-and-maintainers)
- [References](#references)

---

## About The Project

Four things live in this repository, and that is deliberate (owner decision,
2026-09-15 — no split, no code moves):

- **The package.** AI workload orchestration: camera pipelines, YOLO
  monitoring, streaming, and system/GPU metrics.
- **The family's LLM benchmark lab.** [`benchmarks/`](benchmarks/README.md) plus
  the `orchestrant.benchmark` runner behind `orchestrant-bench` — the suite the
  rest of the family points at when it asks which model to run.
- **The template.** What the repository description advertises — *"Lets
  bootstrap your Python AI project"*: the `uv`/`ruff`/`ty` setup, the Cython
  wheel build, the Sphinx docs and the ANTfrastructure CI wiring are meant to be
  copied into a new project.
- **The Reflex viewer.** `frontend/`, which reads the lab's result manifest
  directly.

The distribution is `OrchestrANT`; the importable package is `orchestrant`:

| Subpackage | What lives there |
| --- | --- |
| `orchestrant.pipeline` | The camera pipeline building blocks: capture (OpenCV, GStreamer), tracking, metrics, monitoring, UI |
| `orchestrant.yolo` | The YOLO monitor — pre/post-processing, drawing, the CLI behind `yolo-monitor` |
| `orchestrant.streaming` | Flask MJPEG live stream of a capture source |
| `orchestrant.monitoring` | CPU, memory and GPU snapshots (NVIDIA via NVML, AMD via ADL on Windows / amdgpu sysfs on Linux) and plotting — see [docs/source/monitoring.md](docs/source/monitoring.md) |
| `orchestrant.smoke` | Wheel smoke test: every compiled dependency does real work, not a bare import |
| `orchestrant.benchmark` | The LLM endpoint runner — see [docs/source/benchmark.rst](docs/source/benchmark.rst) |

Console scripts (`pyproject.toml` `[project.scripts]`): `yolo-monitor`,
`orchestrant-smoke`, `orchestrant-bench`.

The heavy dependencies are extras, chosen at install time:

- `pytorch-cpu` / `pytorch-cu130` / `pytorch-rocm71` / `pytorch-custom` — one
  torch backend, mutually exclusive (`pytorch-custom` takes your own wheelhouse
  via `--find-links`)
- `ml-ai` / `ml-ai-webgpu` / `ml-ai-nvidia` / `ml-ai-rocm` — ONNX Runtime for
  that backend plus OpenCV, scikit-learn, mlflow, optuna, IREE and LiteRT
- `gpu` / `gpu-nvidia` / `gpu-directml` / `gpu-rocm` — the GPU execution
  provider and the vendor's monitoring library
- `frontend` — wxPython for the YOLO viewer and Reflex for the benchmark viewer
- `test`, `docs`, `packaging`

### Key Features

- GPU monitoring for two vendors through one `GPUProbe`: NVML for NVIDIA, ADL
  (Windows) or amdgpu sysfs (Linux) for AMD, no `pyadl` needed.
- `orchestrant-bench`: throughput, time-to-first-token, decode rate and a
  verifiable-answer correctness probe against any OpenAI-compatible endpoint,
  with the host GPU recorded in every result.
- A Reflex viewer for the benchmark results in `frontend/`.
- `orchestrant-smoke`: a shipped wheel smoke test that exercises torch,
  torchvision, ONNX Runtime, OpenCV, IREE and LiteRT.
- Static analysis is a gate on both CI lanes: `ruff check --no-fix`,
  `ruff format --check`, `ty`, `bandit`, `vulture` and `codespell`.

See [CHANGELOG.md](CHANGELOG.md) for the history behind each of these.

### LLM benchmark lab

The measurement suite — the sweep, the coding / tool-calling / agent-loop
benchmarks, the prompts and the tracked results — lives in
[`benchmarks/`](benchmarks/README.md); the Reflex viewer lives in `frontend/`.
The serving stack it measures is owned by ANTfrastructure's
`linux/llm-stack/`.

### Useful Tools

| Tool                                                            | Description            |
| --------------------------------------------------------------- | ---------------------- |
| [ty](https://github.com/astral-sh/ty)                           | Type checker           |
| [ruff](https://github.com/astral-sh/ruff)                       | Linter and formatter   |
| [uv](https://github.com/astral-sh/uv)                           | Environments and locks |
| [py-spy](https://github.com/benfred/py-spy)                     | Sampling profiler      |
| [line_profiler](https://github.com/pyutils/line_profiler)       | Line-by-line profiling |
| [pytest-benchmark](https://github.com/ionelmc/pytest-benchmark) | Benchmark tests        |

---

## Overview

The versioning of the package can be viewed in [CHANGELOG.md](CHANGELOG.md).

---

## Getting Started

### Setup

Feel free to adjust for your own environment.
F.e. create a virtual venv with a specific python version.

```bash
uv venv
python3.11 -m venv .venv
```

### Pre commit hook
```bash
uv venv
source .venv/bin/activate # .venv/Scripts/activate on pwsh
uv pip install pre-commit
pre-commit install
# run on all files once (optional)
pre-commit run --all-files
```

### Dependency updates

```bash
bash scripts/linux/renovate-local.sh                   # what is behind (report)
bash scripts/linux/renovate-local.sh --managers pep621 # the pyproject.toml pins
bash scripts/linux/renovate-local.sh --apply --dry-run # the gitlink plan
```

`--apply` moves gitlinks only for submodules that declare a `branch =` in
`.gitmodules` — here only `third_party/ANTfrastructure` (`branch = main`) does,
so nothing else can come back REFUSED. Rationale and what `--apply` moves:
[dependency-updates.md](third_party/ANTfrastructure/docs/dependency-updates.md).

### Installation

There are three major ways to install this package in your environment:

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

   or

   ```bash
   pip install -e .
   ```

   (an editable install: changes in the repo will be reflected in your environment)

3. **Add as a submodule to your repository:**
   ```bash
   git submodule add https://github.com/Kataglyphis/OrchestrANT
   ```
   Make sure that all dependencies are installed during your repo’s installation.  
   (Not generally recommended, as it can be more complicated.)

#### Picamera web browser live stream

You need to shared system packages for the 
`picamera2` should be installed via `apt`
([source](https://github.com/raspberrypi/picamera2))

```bash
sudo apt install python3-picamera2
uv venv --system-site-packages
```

### Deployment Recommendations (Hardware/Software)

#### Python package deployment in pure C

For insights into deploying Python packages into production as “binary only” wheels
have a look into the corresponding workflows
(background: [Protect source code](https://art-vasilyev.github.io/posts/protecting-source-code/)).

After creating the wheel you can check content with the command:
```bash
# Unzip a .whl
python -m zipfile --extract <ZIP_DATEI> <ZIEL_ORDNER>
```

This will print all available compatible tags for deployment

```bash
pip debug --verbose
```

```bash
./scripts/linux/ci_static_analysis.sh > "ci_analysis_$(date +%Y%m%d_%H%M%S).log" 2>&1
```

This is the same gate CI runs, and it is a **gate**: it exits `1` if codespell,
bandit, vulture, `ruff check --no-fix`, `ruff format --check` or `ty check`
reports anything. Redirecting both streams into a log as above hides that from
the terminal — check `$?`, or drop the redirect.

**__NOTE:__** If you want to install your package editable and you previously deployed  
it you will need to delete all Cython generated files first. You can use the following  
command for it:  
```bash  
find . -type f \( -name '\*.c' -o -name '\*.cpp' -o -name '\*.so' -o -name '\*.pyd' -o -name '\*.html' \) -delete  
```

Or on windows ... do this  
```powershell  
Get-ChildItem -Path . -Recurse -File | Where-Object { $\_.Extension -in '.c', '.cpp', '.so', '.pyd', '.html' } | Remove-Item  
```

## Tests

```bash
uv sync --extra pytorch-cpu --extra test
uv run pytest tests/unit
bash scripts/linux/ci_tests.sh   # what the Linux lane runs: pytest + coverage
```

The LLM lab's own suite is `benchmarks/tests`, run by
`.github/workflows/benchmarks.yml` together with `tests/unit/benchmark`.

---

## Demos

```bash
uv run python -m orchestrant.yolo.monitor
uv run python examples/monitoring.py
```

---

## Contributing

Contributions make open source software better! To contribute:

1. Fork the project
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## License

MIT — see [LICENSE](LICENSE).

---

## Contact and Maintainers

- Primary contact: [@Cataglyphis_](https://twitter.com/Cataglyphis_)
- Repository: [GitHub](https://github.com/Kataglyphis/OrchestrANT)

**Maintainers:**  
Jonas Heinle

---

## References

* [yolov12](https://github.com/sunsmarterjie/yolov12)
