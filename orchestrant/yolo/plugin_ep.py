"""Execution providers that ONNX Runtime reaches only as a registered plugin module, such as AMD's MIGraphX.

``get_available_providers`` lists no plugin before its registration, so "is it here" means "its module is on disk".
The caller passes the ``onnxruntime`` it loaded; nothing here imports one.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class Plugin:
    """One plugin provider: its registration name, the variable naming its root, its module file."""

    name: str
    root_variable: str
    module: str


# One row per plugin. AMD's module reports the registration name as the provider name of its devices and sessions.
PLUGINS = {"migraphx": Plugin("MIGraphX", "ORT_AMDGPU_EP_ROOT", "libmigraphx-ep.so")}


class PluginUnavailableError(RuntimeError):
    """A plugin provider cannot be had here; the message names what was looked for and why."""


# ONNX Runtime keeps a registration for the life of the process, and so does this record of it.
_lock = threading.Lock()
_registered: set[str] = set()
_failures: dict[str, str] = {}


def by_name(name: str) -> Plugin | None:
    """The plugin that registers under ``name``, or ``None`` for a provider ONNX Runtime has built in."""
    return next((plugin for plugin in PLUGINS.values() if plugin.name == name), None)


def candidates(
    plugin: Plugin,
    ort: Any,  # noqa: ANN401 -- the loaded onnxruntime module
    environ: Mapping[str, str] = os.environ,
) -> list[Path]:
    """Where the module is looked for, in order: the image's root, then beside the loaded ONNX Runtime."""
    # Every image but the rocm one sets the variable to the empty string.
    root = environ.get(plugin.root_variable, "")
    places = [Path(root) / "lib" / plugin.module] if root else []
    return [*places, Path(ort.__file__).resolve().parent / "capi" / plugin.module]


def _holds(path: Path) -> bool:
    # Before Python 3.14 is_file raises on a place that may not be read; auto must go on there too.
    try:
        return path.is_file()
    except OSError:
        return False


def find_module(
    plugin: Plugin,
    ort: Any,  # noqa: ANN401 -- the loaded onnxruntime module
    environ: Mapping[str, str] = os.environ,
) -> Path | None:
    """The first place that holds the module, as an absolute path; ``None`` when no place does."""
    places = candidates(plugin, ort, environ)
    return next((path.resolve() for path in places if _holds(path)), None)


def require_module(
    plugin: Plugin,
    ort: Any,  # noqa: ANN401 -- the loaded onnxruntime module
    environ: Mapping[str, str] = os.environ,
) -> Path:
    """The module's path, or an error naming every place searched and the root variable's state."""
    module = find_module(plugin, ort, environ)
    if module is not None:
        return module
    places = " or ".join(str(path) for path in candidates(plugin, ort, environ))
    value = environ.get(plugin.root_variable)
    state = "is not set" if value is None else f"is {value!r}"
    message = f"{plugin.name} was not found: no {plugin.module} at {places} ({plugin.root_variable} {state})"
    raise PluginUnavailableError(message)


def device_options(gpu: int) -> dict[str, str]:
    """The only option passed on: AMD's module fails on a key it does not list, such as a memory limit."""
    return {"device_id": str(gpu)}


def _offered(plugin: Plugin, ort: Any) -> list[Any]:  # noqa: ANN401 -- the loaded onnxruntime module
    return [device for device in ort.get_ep_devices() if device.ep_name == plugin.name]


def _register(plugin: Plugin, ort: Any, environ: Mapping[str, str]) -> None:  # noqa: ANN401 -- the loaded onnxruntime module
    """Register the module unless the name is taken already; the caller holds the lock."""
    if plugin.name in _registered:
        return
    module = require_module(plugin, ort, environ)
    # A second registration of a name is an error, also when another library of this process made the first.
    if not _offered(plugin, ort):
        try:
            ort.register_execution_provider_library(plugin.name, str(module))
        except Exception as error:
            message = f"{plugin.name} failed to register {module}: {error}"
            raise PluginUnavailableError(message) from error
    _registered.add(plugin.name)


def _device(plugin: Plugin, ort: Any, gpu: int, environ: Mapping[str, str]) -> Any:  # noqa: ANN401 -- the loaded onnxruntime module and its OrtEpDevice
    """Register the module unless that is done and return the device ``gpu`` names; the caller holds the lock."""
    _register(plugin, ort, environ)
    devices = _offered(plugin, ort)
    if not 0 <= gpu < len(devices):
        message = f"{plugin.name} offers {len(devices)} device(s) on this machine, so it has no device {gpu}"
        raise PluginUnavailableError(message)
    return devices[gpu]


def append(
    plugin: Plugin,
    ort: Any,  # noqa: ANN401 -- the loaded onnxruntime module
    session_options: Any,  # noqa: ANN401 -- ort.SessionOptions
    gpu: int,
    environ: Mapping[str, str] = os.environ,
) -> None:
    """Register the module once per process and give the session the one device ``gpu`` names."""
    # One step: ONNX Runtime's device list grows with a registration, and a second thread must not register again.
    with _lock:
        try:
            device = _device(plugin, ort, gpu, environ)
        except PluginUnavailableError as error:
            _failures[plugin.name] = str(error)
            raise
        _failures.pop(plugin.name, None)
        # AMD's module refuses more than one device and takes the card from device_id, not from the device.
        session_options.add_provider_for_devices([device], device_options(gpu))


def report() -> dict[str, Any]:
    """What a self-test says about plugins: the names registered in this process, and why one was not."""
    with _lock:
        return {
            "registered_plugin_eps": sorted(_registered),
            "plugin_ep_errors": dict(_failures),
        }
