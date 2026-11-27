"""Execution providers that ONNX Runtime reaches only as a registered plugin module, such as AMD's MIGraphX.

``get_available_providers`` lists no plugin before its registration, so "is it here" means "its module is on disk".
The caller passes the ``onnxruntime`` it loaded; nothing here imports one. Where the module is and how it registers
is the hub's exact ``orchestrant/ort_plugin_eps.py``; this module only adapts it to the detector's calls.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrant.ort_plugin_eps import (
    MIGRAPHX,
    PluginLibrary,
    plugin_library_path,
    register_plugin_library,
    select_plugin_device,
)


if TYPE_CHECKING:
    from collections.abc import Mapping


Plugin = PluginLibrary

# One row per plugin. AMD's module reports the registration name as the provider name of its devices and sessions.
PLUGINS = {"migraphx": MIGRAPHX}


class PluginUnavailableError(RuntimeError):
    """A plugin provider cannot be had here; the message names what was looked for and why."""


# What this process got and why a request failed; the hub keeps the registrations and their lock.
_lock = threading.Lock()
_registered: set[str] = set()
_failures: dict[str, str] = {}


def by_name(name: str) -> Plugin | None:
    """The plugin that registers under ``name``, or ``None`` for a provider ONNX Runtime has built in."""
    return next((plugin for plugin in PLUGINS.values() if plugin.name == name), None)


def module_filename(plugin: Plugin) -> str:
    """The module's file name on this platform, the one ``plugin_library_path`` looks for."""
    return plugin.windows_filename if sys.platform == "win32" else plugin.linux_filename


def require_module(
    plugin: Plugin,
    ort: Any,  # noqa: ANN401 -- the loaded onnxruntime module
    environ: Mapping[str, str] = os.environ,
) -> Path:
    """The module's path, or an error naming the rule and what it found."""
    try:
        module = plugin_library_path(ort, plugin, environ=environ)
    except (OSError, ValueError) as error:
        message = f"{plugin.name} was not found: {error}"
        raise PluginUnavailableError(message) from error
    if module is None:
        filename = module_filename(plugin)
        beside = Path(ort.__file__).resolve().parent / "capi" / filename
        value = environ.get(plugin.root_variable)
        state = "is not set" if value is None else f"is {value!r}"
        message = f"{plugin.name} was not found: no {filename} at {beside} ({plugin.root_variable} {state})"
        raise PluginUnavailableError(message)
    return module


def find_module(
    plugin: Plugin,
    ort: Any,  # noqa: ANN401 -- the loaded onnxruntime module
    environ: Mapping[str, str] = os.environ,
) -> Path | None:
    """The module as an absolute path; ``None`` where it cannot be had, so ``auto`` goes on."""
    try:
        return require_module(plugin, ort, environ)
    except PluginUnavailableError:
        return None


def device_options(gpu: int) -> dict[str, str]:
    """The only option passed on: AMD's module fails on a key it does not list, such as a memory limit."""
    return {"device_id": str(gpu)}


def _device(plugin: Plugin, ort: Any, gpu: int, environ: Mapping[str, str]) -> Any:  # noqa: ANN401 -- the loaded onnxruntime module and its OrtEpDevice
    """The registered plugin's device ``gpu``; a registration another library made is used as it is."""
    module = require_module(plugin, ort, environ)
    # A second registration of a name is an error, also when another library of this process made the first.
    try:
        register_plugin_library(ort, plugin, environ=environ, reuse_existing=True)
    except Exception as error:
        message = f"{plugin.name} failed to register {module}: {error}"
        raise PluginUnavailableError(message) from error
    with _lock:
        _registered.add(plugin.name)
    offered = [
        device for device in ort.get_ep_devices() if device.ep_name == plugin.name
    ]
    if not 0 <= gpu < len(offered):
        message = f"{plugin.name} offers {len(offered)} device(s) on this machine, so it has no device {gpu}"
        raise PluginUnavailableError(message)
    device = select_plugin_device(
        ort, gpu, plugin, environ=environ, reuse_existing=True
    )
    if device is None:
        message = f"{plugin.name} was not found: {module} disappeared"
        raise PluginUnavailableError(message)
    return device


def append(
    plugin: Plugin,
    ort: Any,  # noqa: ANN401 -- the loaded onnxruntime module
    session_options: Any,  # noqa: ANN401 -- ort.SessionOptions
    gpu: int,
    environ: Mapping[str, str] = os.environ,
) -> None:
    """Register the module once per process and give the session the one device ``gpu`` names."""
    # The hub's registry makes concurrent first sessions register once; this lock only keeps the report whole.
    try:
        device = _device(plugin, ort, gpu, environ)
    except PluginUnavailableError as error:
        with _lock:
            _failures[plugin.name] = str(error)
        raise
    with _lock:
        _failures.pop(plugin.name, None)
    # AMD's module refuses more than one device and takes the card from device_id, not from the device.
    session_options.add_provider_for_devices([device], device_options(gpu))


def report() -> dict[str, Any]:
    """What a self-test says about plugins: the names this process uses, and why one was not."""
    with _lock:
        return {
            "registered_plugin_eps": sorted(_registered),
            "plugin_ep_errors": dict(_failures),
        }
