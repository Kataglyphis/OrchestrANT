"""Where an installed OrchestrANT finds its data and writes its logs."""

from __future__ import annotations

import os
import sys
from pathlib import Path


MODEL_NAME = "yolov26m.onnx"


def bundle_data_dir() -> Path | None:
    """The data directory of a packaged install; None in a checkout or a plain venv."""
    env = os.getenv("ORCHESTRANT_DATA_DIR")
    if env:
        return Path(env)
    # A bundle keeps the interpreter in <root>/runtime and its data in <root>/share/orchestrant.
    candidate = Path(sys.prefix).parent / "share" / "orchestrant"
    return candidate if candidate.is_dir() else None


def model_search_paths(name: str = MODEL_NAME) -> list[Path]:
    """The places the default model is looked for, in order."""
    paths: list[Path] = []
    env = os.getenv("ORCHESTRANT_MODEL")
    if env:
        paths.append(Path(env))
    data = bundle_data_dir()
    if data is not None:
        paths.append(data / "models" / name)
    paths.append(Path("resources") / "models" / name)
    # An editable install started from outside its checkout.
    paths.append(Path(__file__).resolve().parents[1] / "resources" / "models" / name)
    return paths


def default_model_path(name: str = MODEL_NAME) -> Path:
    """The first default model that exists, else the first candidate, so an error names it."""
    paths = model_search_paths(name)
    return next((path for path in paths if path.is_file()), paths[0])


def default_log_dir() -> Path:
    """A packaged install logs to the user's state dir, because its install dir may be read-only."""
    env = os.getenv("ORCHESTRANT_LOG_DIR")
    if env:
        return Path(env)
    if bundle_data_dir() is None:
        return Path("logs")
    if sys.platform == "win32":
        local = os.getenv("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "OrchestrANT" / "logs"
    state = os.getenv("XDG_STATE_HOME")
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / "orchestrant" / "logs"
