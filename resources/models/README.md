<!--
Copyright (c) 2025 Kataglyphis
SPDX-License-Identifier: MIT
-->

# Tracked model weights

One file here is large and tracked on purpose:

| File | Size | Why it is in git |
| --- | --- | --- |
| `yolov26m.onnx` | 81,914,490 bytes (78 MiB) | The default `--model` of the YOLO monitor (`orchestrant/yolo/cli.py:35`). A clone can run `yolo-monitor` with no download step, which is the point of a repository that also serves as the template for a new Python AI project. |

**Owner decision, 2026-09-15: documented, not rewritten.** The file stays
tracked and its history stays as it is — no `filter-repo`, no `filter-branch`,
no BFG, no Git LFS migration. Any of those rewrite every commit id in the
repository and force every clone and every submodule pin that names one to be
redone; that cost is not worth 78 MiB, and the decision is recorded here so it
is not re-opened by the next person who runs a repository-size report.

**Nothing new joins it.** `.gitignore` excludes `*.onnx`, `*.gguf`, `*.pt` and
`*.pth` with a single explicit exception for `resources/models/yolov26m.onnx`,
so a second set of weights cannot be added by accident. If a new model really
must ship, add it as a GitHub release asset and fetch it at run time instead of
negating the ignore rule.
