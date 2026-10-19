<!--
Copyright (c) 2025 Kataglyphis
SPDX-License-Identifier: MIT
-->

# Model weights: fetched, never tracked

Only `models.lock` and this note are tracked here. The lock names every file
`yolo-monitor` needs by SHA-256 and URL, and `python scripts/fetch_models.py` puts
them beside it, checking each checksum (a mismatch fails and writes nothing). The
same script runs on Linux and Windows, and every CI lane that packages the app runs
it first, because the packages ship these files. Its flags and download cache:
[`docs/source/overview.md` § The model](../../docs/source/overview.md#the-model).

| File | What it is |
| --- | --- |
| `deim_dfine_n_coco.onnx` | DEIM-D-FINE-N on COCO, the default `--model` (`orchestrant/paths.py`). Input `images` [1, 3, 640, 640], RGB in [0, 1], plain resize; output `output0` [1, 300, 6] = x1, y1, x2, y2 in input pixels, score, COCO class |
| `deim_dfine_n_coco.LICENSE` | Apache-2.0 |
| `deim_dfine_n_coco.NOTICE` | Where the weights come from; Apache-2.0 asks for it to travel with them, so the packages ship it |

All three are built and released by
[ModelANT](https://github.com/Kataglyphis/ModelANT). A new model goes into
ModelANT, and only the lock moves here.

**Why not tracked.** Until 2026-10-17 this directory tracked Ultralytics YOLO26
(78 MiB), which is AGPL-3.0 and cannot ship with this MIT project, and 78 MiB of
weights do not belong in a code repository. The family's history rewrite of 2026-10-17 removed it from the history as well
([`data-repositories.md`](https://github.com/Kataglyphis/ANTfrastructure/blob/develop/docs/data-repositories.md#what-moved-and-why));
`git log --all -- '*.onnx'` is empty. `.gitignore` excludes
`*.onnx`, `*.gguf`, `*.pt`, `*.pth` and the fetched `LICENSE`/`NOTICE` files,
so no weights are added by accident.
