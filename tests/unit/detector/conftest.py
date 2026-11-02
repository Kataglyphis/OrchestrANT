"""The detector suites' inputs: the default model and ModelANT's pinned contracts, each fetched by SHA-256."""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import os
import platform
import sysconfig
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from orchestrant.paths import default_model_name


if TYPE_CHECKING:
    from types import ModuleType


REPO = Path(__file__).resolve().parents[3]
HUB = REPO / "third_party/ANTfrastructure"
FETCHER = HUB / "linux/scripts/lib/fetch-data-lock.py"
COMPARATOR = HUB / "shared/python/compare_detector_parity.py"
MODELS_LOCK = REPO / "resources/models/models.lock"
CONTRACTS_LOCK = REPO / "scripts/linux/ai-regression.lock"
DIAGNOSTIC = REPO / "scripts/diagnostics/detector_parity.py"


def model_required() -> bool:
    """Return whether this leg must run the model cases: CI asks, and only GIL legs off riscv64 install ORT and OpenCV."""
    free_threaded = sysconfig.get_config_var("Py_GIL_DISABLED") == 1
    asked = os.environ.get("ORCHESTRANT_REQUIRE_MODEL") == "1"
    return asked and not free_threaded and platform.machine() != "riscv64"


def unavailable(reason: str) -> None:
    """Fail when this leg must run the model cases, else skip with the reason."""
    if model_required():
        pytest.fail(f"ORCHESTRANT_REQUIRE_MODEL=1 but {reason}")
    pytest.skip(f"{reason}; the Linux test legs set ORCHESTRANT_REQUIRE_MODEL=1")


def load_script(path: Path) -> ModuleType:
    """Import a script by path, the hub's included, so the tests call it instead of a copy."""
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    if spec is None or spec.loader is None:
        msg = f"cannot load {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch(lock: Path, dest: Path, *names: str) -> Path:
    """Fetch the named rows of a lock with the hub's checksum-checking fetcher."""
    only = [arg for name in names for arg in ("--only", name)]
    errors = io.StringIO()
    with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
        status = load_script(FETCHER).main(
            ["--lock", str(lock), "--dest", str(dest), *only]
        )
    if status != 0:
        unavailable(f"fetching {', '.join(names)} failed: {errors.getvalue().strip()}")
    return dest


@pytest.fixture(scope="session")
def runtime() -> SimpleNamespace:
    """OpenCV and ONNX Runtime, which the free-threaded and riscv64 legs do not install."""
    for name in ("cv2", "onnxruntime"):
        if importlib.util.find_spec(name) is None:
            unavailable(f"{name} is not importable")
    return SimpleNamespace(
        cv2=importlib.import_module("cv2"), ort=importlib.import_module("onnxruntime")
    )


@pytest.fixture(scope="session")
def contracts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """ModelANT's inference-v1.json and two_cats.jpg at the commit the lock pins."""
    return fetch(
        CONTRACTS_LOCK,
        tmp_path_factory.mktemp("modelant-contracts"),
        "inference-v1.json",
        "two_cats.jpg",
    )


@pytest.fixture(scope="session")
def model(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The default detector from models.lock."""
    return (
        fetch(MODELS_LOCK, tmp_path_factory.mktemp("models"), default_model_name())
        / default_model_name()
    )


@pytest.fixture(scope="session")
def diagnostic() -> ModuleType:
    """The packaged diagnostic, whose capture() is the production pre/postprocess path."""
    return load_script(DIAGNOSTIC)


@pytest.fixture(scope="session")
def comparator() -> ModuleType:
    """The hub's detector comparator: both cats, equal counts, IoU >= 0.90, score delta <= 0.05."""
    return load_script(COMPARATOR)
