"""Select and initialize ONNX Runtime inference providers."""

import os
from pathlib import Path
from typing import Any

import onnxruntime as ort
from loguru import logger

from orchestrant.yolo import plugin_ep
from orchestrant.yolo.cuda_probe import require_kernel
from orchestrant.yolo.hip_probe import require_answer


SHIPPED_MAX_THREADS = 8
MIGRAPHX = plugin_ep.PLUGINS["migraphx"]


def select_providers(gpu: int, device: str = "auto") -> list[Any]:
    """Select the requested provider or prefer available providers in auto mode."""
    available = set(ort.get_available_providers())
    names = {
        "cpu": "CPUExecutionProvider",
        "cuda": "CUDAExecutionProvider",
        "directml": "DmlExecutionProvider",
        **{name: plugin.name for name, plugin in plugin_ep.PLUGINS.items()},
    }
    if device != "auto":
        if device not in names:
            message = f"Unknown inference device: {device}"
            raise ValueError(message)
        if device in plugin_ep.PLUGINS:
            plugin_ep.require_module(plugin_ep.PLUGINS[device], ort)
            return [(names[device], {"device_id": gpu})]
        if names[device] not in available:
            message = f"{names[device]} is not available in this ONNX Runtime"
            raise RuntimeError(message)
        if device == "cpu":
            return [names[device]]
        options: dict[str, Any] = {"device_id": gpu}
        if device == "cuda":
            options["arena_extend_strategy"] = "kNextPowerOfTwo"
        return [(names[device], options)]
    providers: list[Any] = []
    if "CUDAExecutionProvider" in available:
        providers.append(
            (
                "CUDAExecutionProvider",
                {"device_id": gpu, "arena_extend_strategy": "kNextPowerOfTwo"},
            )
        )
    # The available list names no plugin before its registration: the module on disk makes it a candidate.
    providers += [
        (plugin.name, {"device_id": gpu})
        for plugin in plugin_ep.PLUGINS.values()
        if plugin_ep.find_module(plugin, ort) is not None
    ]
    if "DmlExecutionProvider" in available:
        providers.append(("DmlExecutionProvider", {"device_id": gpu}))
    providers.append("CPUExecutionProvider")
    return providers


def shipped_intra_op_threads() -> int:
    """All usable CPUs but one, at most ``SHIPPED_MAX_THREADS``: ORT's default ignores the affinity mask."""
    usable = (
        len(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else os.cpu_count() or 1
    )
    # A pool that fills the mask loses its tail latency to whatever else needs a CPU.
    return min(max(usable - 1, 1), SHIPPED_MAX_THREADS)


def _provider_session(
    model: str,
    providers: list[Any],
    profile_prefix: str | None = None,
    intra_op_threads: int = 0,
) -> ort.InferenceSession:
    """Create a session with the options required by its providers; 0 threads keeps ORT's default."""
    for provider in providers:
        # AMD's registration calls HIP, so the copy that shows a hung card runs before it.
        if isinstance(provider, tuple) and provider[0] == MIGRAPHX.name:
            require_answer(provider[1]["device_id"])
        if isinstance(provider, tuple) and provider[0] == "CUDAExecutionProvider":
            require_kernel(provider[1]["device_id"])
    session_options = None
    directml = any(
        (provider[0] if isinstance(provider, tuple) else provider)
        == "DmlExecutionProvider"
        for provider in providers
    )
    first = providers[0]
    plugin = plugin_ep.by_name(first[0]) if isinstance(first, tuple) else None
    if plugin or directml or profile_prefix is not None or intra_op_threads:
        session_options = ort.SessionOptions()
        session_options.intra_op_num_threads = intra_op_threads
        if directml:
            session_options.enable_mem_pattern = False
            session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        if profile_prefix is not None:
            session_options.enable_profiling = True
            session_options.profile_file_prefix = profile_prefix
    if plugin is None:
        return ort.InferenceSession(
            model,
            sess_options=session_options,
            providers=providers,
            enable_fallback=False,
        )
    plugin_ep.append(plugin, ort, session_options, first[1]["device_id"])
    # No providers=: InferenceSession would use those and drop the appended device.
    return ort.InferenceSession(
        model, sess_options=session_options, enable_fallback=False
    )


def create_session(
    model: str,
    gpu: int,
    device: str,
    *,
    profile_prefix: str | None = None,
    intra_op_threads: int | None = None,
) -> ort.InferenceSession:
    """Try usable GPUs in auto mode and reject fallback; ``None`` threads is the shipped default."""
    if intra_op_threads is None:
        intra_op_threads = shipped_intra_op_threads()
    if intra_op_threads < 0:
        message = (
            f"intra_op_threads must be 0 (ORT default) or more, got {intra_op_threads}"
        )
        raise ValueError(message)
    providers = select_providers(gpu, device)
    if device == "auto":
        for provider in providers[:-1]:
            name = provider[0] if isinstance(provider, tuple) else provider
            try:
                session = _provider_session(
                    model,
                    [provider, "CPUExecutionProvider"],
                    profile_prefix,
                    intra_op_threads,
                )
                if name in session.get_providers():
                    return session
                logger.warning(
                    "ONNX Runtime did not activate {}; trying the next provider", name
                )
            except Exception as exc:
                logger.warning(
                    "Could not initialize {}: {}; trying the next provider", name, exc
                )
        return _provider_session(
            model, ["CPUExecutionProvider"], profile_prefix, intra_op_threads
        )

    session = _provider_session(model, providers, profile_prefix, intra_op_threads)
    requested = providers[0][0] if isinstance(providers[0], tuple) else providers[0]
    if requested not in session.get_providers():
        message = f"ONNX Runtime did not activate {requested}"
        raise RuntimeError(message)
    session.disable_fallback()
    return session


def provider_report(session: ort.InferenceSession) -> dict[str, Any]:
    """What a self-test says about the loaded ONNX Runtime's providers, its plugins and this session."""
    return {
        "onnxruntime_module": str(Path(ort.__file__).parent),
        "available_providers": ort.get_available_providers(),
        **plugin_ep.report(),
        "providers": session.get_providers(),
    }
