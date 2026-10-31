"""Select and initialize ONNX Runtime inference providers."""

from typing import Any

import onnxruntime as ort
from loguru import logger


def select_providers(gpu: int, device: str = "auto") -> list[Any]:
    """Select the requested provider or prefer available providers in auto mode."""
    available = set(ort.get_available_providers())
    names = {
        "cpu": "CPUExecutionProvider",
        "cuda": "CUDAExecutionProvider",
        "migraphx": "MIGraphXExecutionProvider",
        "directml": "DmlExecutionProvider",
    }
    if device != "auto":
        if device not in names:
            message = f"Unknown inference device: {device}"
            raise ValueError(message)
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
    if "MIGraphXExecutionProvider" in available:
        providers.append(("MIGraphXExecutionProvider", {"device_id": gpu}))
    if "DmlExecutionProvider" in available:
        providers.append(("DmlExecutionProvider", {"device_id": gpu}))
    providers.append("CPUExecutionProvider")
    return providers


def _provider_session(model: str, providers: list[Any]) -> ort.InferenceSession:
    """Create a session with the options required by its providers."""
    session_options = None
    if any(
        (provider[0] if isinstance(provider, tuple) else provider)
        == "DmlExecutionProvider"
        for provider in providers
    ):
        session_options = ort.SessionOptions()
        session_options.enable_mem_pattern = False
        session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(
        model, sess_options=session_options, providers=providers, enable_fallback=False
    )


def create_session(model: str, gpu: int, device: str) -> ort.InferenceSession:
    """Try usable GPUs in auto mode and reject fallback for explicit requests."""
    providers = select_providers(gpu, device)
    if device == "auto":
        for provider in providers[:-1]:
            name = provider[0] if isinstance(provider, tuple) else provider
            try:
                session = _provider_session(model, [provider, "CPUExecutionProvider"])
                if name in session.get_providers():
                    return session
                logger.warning(
                    "ONNX Runtime did not activate {}; trying the next provider", name
                )
            except Exception as exc:
                logger.warning(
                    "Could not initialize {}: {}; trying the next provider", name, exc
                )
        return _provider_session(model, ["CPUExecutionProvider"])

    session = _provider_session(model, providers)
    requested = providers[0][0] if isinstance(providers[0], tuple) else providers[0]
    if requested not in session.get_providers():
        message = f"ONNX Runtime did not activate {requested}"
        raise RuntimeError(message)
    session.disable_fallback()
    return session
