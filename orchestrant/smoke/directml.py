"""Prove the packaged model executes operators on a physical AMD or NVIDIA GPU via DirectML."""

from __future__ import annotations

import ctypes
import json
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrant.smoke.ort_profile import operator_counts


if TYPE_CHECKING:
    from collections.abc import Callable


class Guid(ctypes.Structure):
    """Windows GUID for the DXGI factory interface."""

    _fields_ = [
        ("data1", wintypes.DWORD),
        ("data2", wintypes.WORD),
        ("data3", wintypes.WORD),
        ("data4", ctypes.c_ubyte * 8),
    ]


class Luid(ctypes.Structure):
    """Windows adapter identifier."""

    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.LONG)]


class AdapterDescription(ctypes.Structure):
    """The DXGI adapter identity and software flag."""

    _fields_ = [
        ("description", wintypes.WCHAR * 128),
        ("vendor", wintypes.UINT),
        ("device", wintypes.UINT),
        ("subsystem", wintypes.UINT),
        ("revision", wintypes.UINT),
        ("vram", ctypes.c_size_t),
        ("system_memory", ctypes.c_size_t),
        ("shared_memory", ctypes.c_size_t),
        ("luid", Luid),
        ("flags", wintypes.UINT),
    ]


def _method(
    obj: ctypes.c_void_p, index: int, restype: object, *types: object
) -> Callable[..., int]:
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *types)(table[index])  # ty: ignore[unresolved-attribute]


def matches_adapter(vendor_id: int, flags: int, vendor: str | None) -> bool:
    """Match a physical adapter against the requested GPU vendor."""
    expected = {"amd": 0x1002, "nvidia": 0x10DE}
    return (
        not flags & 2
        and vendor_id in expected.values()
        and (vendor is None or vendor_id == expected[vendor])
    )


def physical_adapter(vendor: str | None = None) -> tuple[int, str]:
    """Find a physical AMD or NVIDIA adapter and return its DXGI index."""
    iid = Guid(
        0x770AAE78,
        0xF26F,
        0x4DBA,
        (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87),
    )
    factory = ctypes.c_void_p()
    status = ctypes.WinDLL("dxgi").CreateDXGIFactory1(  # ty: ignore[unresolved-attribute]
        ctypes.byref(iid), ctypes.byref(factory)
    )
    if status:
        msg = f"CreateDXGIFactory1 failed: {status:#x}"
        raise OSError(msg)
    try:
        enum = _method(
            factory, 12, ctypes.c_long, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)
        )
        for index in range(32):
            adapter = ctypes.c_void_p()
            if enum(factory, index, ctypes.byref(adapter)):
                break
            try:
                desc = AdapterDescription()
                status = _method(
                    adapter, 10, ctypes.c_long, ctypes.POINTER(AdapterDescription)
                )(adapter, ctypes.byref(desc))
                if status:
                    msg = f"GetDesc1 failed: {status:#x}"
                    raise OSError(msg)
                if matches_adapter(desc.vendor, desc.flags, vendor):
                    return index, desc.description
            finally:
                _method(adapter, 2, wintypes.ULONG)(adapter)
    finally:
        _method(factory, 2, wintypes.ULONG)(factory)
    msg = f"No matching physical {vendor or 'AMD or NVIDIA'} DXGI adapter was found"
    raise RuntimeError(msg)


def main() -> int:
    """Run the bundled model and require profiled DirectML operator placement."""
    import numpy as np
    import onnxruntime as ort

    if len(sys.argv) not in (2, 3) or (
        len(sys.argv) == 3 and sys.argv[2] not in {"amd", "nvidia"}
    ):
        msg = "usage: python -m orchestrant.smoke.directml MODEL.onnx [amd|nvidia]"
        raise SystemExit(msg)
    index, adapter = physical_adapter(sys.argv[2] if len(sys.argv) == 3 else None)
    if "DmlExecutionProvider" not in ort.get_available_providers():
        msg = "The installed ONNX Runtime has no DirectML provider"
        raise RuntimeError(msg)
    with tempfile.TemporaryDirectory(prefix="orchestrant-directml-") as directory:
        options = ort.SessionOptions()
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_profiling = True
        options.profile_file_prefix = str(Path(directory) / "profile")
        session = ort.InferenceSession(
            sys.argv[1],
            sess_options=options,
            providers=[
                ("DmlExecutionProvider", {"device_id": index}),
                "CPUExecutionProvider",
            ],
        )
        session.disable_fallback()
        if "DmlExecutionProvider" not in session.get_providers():
            msg = "DirectML was not activated in the session"
            raise RuntimeError(msg)
        inputs = {
            entry.name: np.zeros(
                [dim if isinstance(dim, int) and dim > 0 else 1 for dim in entry.shape],
                dtype=np.float32,
            )
            for entry in session.get_inputs()
        }
        session.run(None, inputs)
        events = json.loads(Path(session.end_profiling()).read_text(encoding="utf-8"))
    counts = operator_counts(events)
    if not counts["DmlExecutionProvider"]:
        msg = "No profiled model operator ran on DirectML"
        raise RuntimeError(msg)
    sys.stdout.write(
        json.dumps({"adapter": adapter, "device_id": index, "providers": dict(counts)})
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
