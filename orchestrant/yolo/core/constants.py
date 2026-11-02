"""Model constants for class names and colors."""

from __future__ import annotations

from importlib.resources import files

import numpy as np


CLASS_NAMES = (
    files("orchestrant.yolo.core")
    .joinpath("coco-classes.txt")
    .read_text(encoding="utf-8")
    .splitlines()
)

_rng = np.random.default_rng(42)
COLORS = _rng.integers(0, 255, size=(len(CLASS_NAMES), 3), dtype=np.uint8)
