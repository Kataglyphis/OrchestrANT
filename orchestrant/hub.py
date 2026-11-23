"""Hub modules the bundle runs (ANTfrastructure CON289, owner 2026-11-23).

setup.py copies each into orchestrant/_hub, where Cython compiles it with the rest of the
wheel, which keeps no .py file; a checkout reads the hub's own file from third_party, so a
stale copy there never stands in for it.
"""

from __future__ import annotations

import functools
import importlib
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from types import ModuleType


# The hub's shared Python, as a checkout has it; an installed wheel has no such directory.
CHECKOUT_DIR = (
    Path(__file__).resolve().parents[1] / "third_party/ANTfrastructure/shared/python"
)


@functools.cache
def hub_module(name: str) -> ModuleType:
    """The hub module `name`: the checkout's own file, else the copy compiled into the wheel."""
    path = CHECKOUT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"orchestrant._hub.{name}", path)
    if not path.is_file() or spec is None or spec.loader is None:
        return importlib.import_module(f"orchestrant._hub.{name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
