# Overview

## Four things in one repository

Four things live in this repository, and that is deliberate (owner decision,
2026-09-15 — no split, no code moves):

- **The package.** AI workload orchestration: camera pipelines, YOLO
  monitoring, streaming, and system/GPU metrics.
- **The family's LLM benchmark lab.** [`benchmarks/`](https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md) plus
  the `orchestrant.benchmark` runner behind `orchestrant-bench` — the suite the
  rest of the family points at when it asks which model to run.
- **The template.** What the repository description advertises — *"Lets
  bootstrap your Python AI project"*: the `uv`/`ruff`/`ty` setup, the Cython
  wheel build, the Sphinx docs and the ANTfrastructure CI wiring are meant to be
  copied into a new project.
- **The Reflex viewer.** `frontend/`, which reads the lab's result manifest
  directly.

## The package

The distribution is `OrchestrANT`; the importable package is `orchestrant`:

| Subpackage | What lives there |
| --- | --- |
| `orchestrant.pipeline` | The camera pipeline building blocks: capture (OpenCV, GStreamer), tracking, metrics, monitoring, UI |
| `orchestrant.yolo` | The YOLO monitor — pre/post-processing, drawing, the CLI behind `yolo-monitor` |
| `orchestrant.streaming` | Flask MJPEG live stream of a capture source |
| `orchestrant.monitoring` | CPU, memory and GPU snapshots (NVIDIA via NVML, AMD via ADL on Windows / amdgpu sysfs on Linux) and plotting — see [monitoring.md](monitoring.md) |
| `orchestrant.smoke` | Wheel smoke test: every compiled dependency does real work, not a bare import |
| `orchestrant.benchmark` | The LLM endpoint runner — see [benchmark.rst](benchmark.rst) |

Console scripts (`pyproject.toml` `[project.scripts]`): `yolo-monitor`,
`orchestrant-smoke`, `orchestrant-bench`.

## yolo-monitor

`yolo-monitor` needs no working directory of its own:

- **Model.** Without `--model`, it takes the first of these that exists:
  1. `$ORCHESTRANT_MODEL`
  2. the install's `share/orchestrant/models/` (`$ORCHESTRANT_DATA_DIR`, or
     beside the interpreter of a packaged install)
  3. `resources/models/` in the working directory
  4. `resources/models/` in the checkout of an editable install

  A checkout has none until `python scripts/fetch_models.py` runs ([The model](#the-model)).
- **Logs.** A packaged install writes them to `%LOCALAPPDATA%\OrchestrANT\logs`
  or `$XDG_STATE_HOME/orchestrant/logs`; `$ORCHESTRANT_LOG_DIR` overrides.
  Anything else still writes to `logs/`.
- **Execution provider.** CUDA, then DirectML, then CPU, whichever the
  installed ONNX Runtime offers.
- **`yolo-monitor --self-test`** loads the model, runs one inference on a blank
  frame and prints a JSON report. It needs no camera or display, and it is the
  install check for packaged builds.

## The model

`yolo-monitor`'s default model is **DEIM-D-FINE-N**, a COCO detector (80
classes) under **Apache-2.0**, exported to ONNX and released by
[ModelANT](https://github.com/Kataglyphis/ModelANT). It is fetched, never
tracked: `resources/models/models.lock` names the `.onnx`, its `LICENSE` and its
`NOTICE` with their URLs and SHA-256, and

```bash
python scripts/fetch_models.py   # [--dest DIR] [--lock FILE]
```

puts them beside the lock, checking each checksum. Downloads are kept by
checksum in `$KATAGLYPHIS_MODEL_CACHE`, else `~/.cache/kataglyphis-models`
(shared with OxidANT's `fetch-models.sh`) or, on Windows,
`%LOCALAPPDATA%\kataglyphis-models`. The packages ship all three files in
`share/orchestrant/models/`, because Apache-2.0 asks for the NOTICE to travel
with the model. The files and their shapes: [`resources/models/README.md`](https://github.com/Kataglyphis/OrchestrANT/blob/develop/resources/models/README.md).

It replaced Ultralytics YOLO26, which was tracked here until 2026-10-17: YOLO26
is AGPL-3.0, which this MIT project cannot ship, and 78 MiB of weights did not
belong in a code repository. The model's metadata asks for a plain resize to
640x640 (`kataglyphis.preprocess=stretch`), which `yolo-monitor` follows; any
other model passed with `--model`, a YOLO export of your own say, keeps the
letterbox.

## Extras

The heavy dependencies are extras, chosen at install time:

- `app` — exactly what `yolo-monitor` imports beyond the core (numpy, OpenCV,
  ONNX Runtime); the packaged app's lean set, so it excludes every `ml-ai*`
  extra, which brings its own OpenCV and ONNX Runtime
- `pytorch-cpu` / `pytorch-cu130` / `pytorch-rocm10` / `pytorch-custom` — one
  torch backend, mutually exclusive (`pytorch-custom` takes your own wheelhouse
  via `--find-links`; `pytorch-rocm10` was `pytorch-rocm71` up to v0.0.28)
- `ml-ai` / `ml-ai-webgpu` / `ml-ai-nvidia` / `ml-ai-rocm` — ONNX Runtime for
  that backend plus OpenCV, scikit-learn, mlflow, optuna, IREE and LiteRT
- `gpu` / `gpu-nvidia` / `gpu-directml` / `gpu-rocm` — the GPU execution
  provider and the vendor's monitoring library
- `frontend` — wxPython for the YOLO viewer and Reflex for the benchmark viewer
- `test`, `docs`, `packaging`

## Key features

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

See [CHANGELOG.md](https://github.com/Kataglyphis/OrchestrANT/blob/develop/CHANGELOG.md) for the history behind each of these.

## The LLM benchmark lab

The measurement suite — the sweep, the coding / tool-calling / agent-loop
benchmarks, the prompts and the tracked results — lives in
[`benchmarks/`](https://github.com/Kataglyphis/OrchestrANT/blob/develop/benchmarks/README.md); the Reflex viewer lives in `frontend/`.
The serving stack it measures is owned by ANTfrastructure's
[`linux/llm-stack/`](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/linux/llm-stack/README.md).

## References

* [yolov12](https://github.com/sunsmarterjie/yolov12)
* [DEIM](https://github.com/ShihuaHuang95/DEIM) and [D-FINE](https://github.com/Peterande/D-FINE), the default model's upstream
