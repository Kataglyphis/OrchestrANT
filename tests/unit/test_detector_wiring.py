"""Preprocess, run and decode are wired once, in core/detector.py; the loop, the self-test and the golden use it."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def call_sites(name: str) -> set[str]:
    """The package files that call `name(` directly or as an attribute."""
    found = set()
    for path in sorted((ROOT / "orchestrant").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = (
                func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            )
            if called == name:
                found.add(path.relative_to(ROOT).as_posix())
    return found


def test_preprocess_and_postprocess_are_called_in_the_detector_only() -> None:
    """A second wiring is where a stretch flag or a layout drifted before; session.run( would not do, a probe calls it."""
    assert call_sites("postprocess") == {"orchestrant/yolo/core/detector.py"}
    assert call_sites("preprocess") == {"orchestrant/yolo/core/detector.py"}
