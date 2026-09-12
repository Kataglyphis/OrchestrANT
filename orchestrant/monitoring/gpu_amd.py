"""AMD GPU probing: ADL on Windows, amdgpu sysfs on Linux.

The two platforms expose AMD telemetry in completely different ways:

* Linux's ``amdgpu`` kernel driver publishes per-card counters as sysfs
  files -- ``gpu_busy_percent``, ``mem_info_vram_*`` and hwmon
  temperature/power. No vendor library, no special privileges.
* Windows has no sysfs. The supported interface is the AMD Display Library
  (ADL) that ships with the graphics driver. Its modern PMLog block returns
  utilization, temperature and power in one call. The legacy Overdrive
  functions that ``pyadl`` wraps return ``ADL_ERR`` on RDNA-era cards
  (verified against an RX 9070 XT / driver 32.0.31041.1004), so this module
  talks to PMLog instead of taking a dependency on the unmaintained wrapper.

The public entry point is :class:`AmdGpuProbe`, which picks the platform
backend and presents the same tiny surface as the NVML side of
:mod:`orchestrant.monitoring.gpu`: ``available``, ``name``, ``read()`` and
``shutdown()``.
"""

from __future__ import annotations

import ctypes
import importlib
import sys
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Self

from loguru import logger

from orchestrant.monitoring.gpu_types import GPUSnapshot


if TYPE_CHECKING:
    from types import ModuleType, TracebackType


# True when this platform has an AMD backend worth constructing. On Windows
# that means "the driver may ship ADL" -- the DLL load itself is deferred to
# AmdGpuProbe so importing this module never touches the driver.
AMD_AVAILABLE = bool(
    sys.platform == "win32"
    or (sys.platform.startswith("linux") and Path("/sys/class/drm").is_dir())
)


@lru_cache(maxsize=1)
def _load_amdsmi() -> ModuleType | None:
    """Import AMD SMI on first use, or None without package/library.

    Deliberately lazy: with the package installed but ``libamd_smi.so``
    missing -- exactly the ``uv sync --all-extras`` dev machine -- its import
    both raises and prints. Doing that at module import would pollute every
    process that only wants NVML or the sysfs metrics; everything except the
    Linux marketing-name lookup works without AMD SMI.
    """
    try:
        module = importlib.import_module("amdsmi")
    except Exception:  # pragma: no cover - environment-dependent
        return None
    return module


def _read_text(path: Path) -> str | None:
    """Read a small sysfs file, returning None when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as exc:
        logger.debug("Cannot read {}: {}", path, exc)
        return None


def _read_int(path: Path) -> int | None:
    """Read an integer from a sysfs file, returning None on any failure."""
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.debug("Not an integer in {}: {!r}", path, raw)
        return None


def _read_float(path: Path) -> float | None:
    """Read a float from a sysfs file, returning None on any failure."""
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.debug("Not a number in {}: {!r}", path, raw)
        return None


def _first_sensor(sensors: dict[int, int], ids: tuple[int, ...]) -> int | None:
    """First present PMLog sensor among ``ids`` (priority order)."""
    for sensor_id in ids:
        if sensor_id in sensors:
            return sensors[sensor_id]
    return None


def _amdsmi_drm_card(smi: ModuleType, handle: object) -> int | None:
    """DRM card index AMD SMI reports for a processor handle, if any."""
    try:
        info = smi.amdsmi_get_gpu_enumeration_info(handle) or {}
    except Exception as exc:
        logger.debug("amdsmi enumeration info failed: {}", exc)
        return None
    value = info.get("drm_card")
    return value if isinstance(value, int) else None


def _amdsmi_product_name(card_index: int) -> str | None:
    """Marketing name of an amdgpu card through AMD SMI, when installed.

    AMD SMI is the only Linux interface carrying the product name; sysfs has
    just the PCI IDs. It is strictly optional -- the sysfs backend reads all
    metrics without it and falls back to a generic name.
    """
    smi = _load_amdsmi()
    if smi is None:
        return None
    try:
        smi.amdsmi_init()
    except Exception as exc:
        logger.debug("amdsmi init failed: {}", exc)
        return None
    try:
        handles = smi.amdsmi_get_processor_handles()
        if not handles:
            return None
        handle = None
        for candidate in handles:
            if _amdsmi_drm_card(smi, candidate) == card_index:
                handle = candidate
                break
        if handle is None and 0 <= card_index < len(handles):
            handle = handles[card_index]
        if handle is None:
            return None
        board = smi.amdsmi_get_gpu_board_info(handle) or {}
        name = board.get("product_name") or board.get("market_name")
        return str(name).strip() or None
    except Exception as exc:
        logger.debug("amdsmi name lookup failed: {}", exc)
        return None
    finally:
        with suppress(Exception):
            smi.amdsmi_shut_down()


class _SysfsBackend:
    """Linux amdgpu backend over ``/sys/class/drm/card*/device``."""

    backend = "sysfs"

    def __init__(self, gpu_index: int = 0, root: Path | None = None) -> None:
        """Select the ``gpu_index``-th AMD card below ``root``.

        Cards are ordered by dedicated VRAM (largest first) so index 0 is the
        accelerator an LLM workload actually uses on an APU+dGPU machine.
        """
        self.available = False
        self.name = "N/A"
        self.memory_type = ""
        self.memory_bandwidth_mbps = 0
        self.memory_total_bytes = 0
        self.handle: object | None = None
        self._device: Path | None = None
        self._hwmon: Path | None = None

        cards = self._discover(root or Path("/sys/class/drm"))
        if not 0 <= gpu_index < len(cards):
            logger.debug(
                "AMD GPU index {} out of range ({} card(s) found)",
                gpu_index,
                len(cards),
            )
            return

        device = cards[gpu_index]
        self._device = device
        self._hwmon = self._find_hwmon(device)
        self.memory_total_bytes = _read_int(device / "mem_info_vram_total") or 0
        card_name = device.parent.name
        card_number = card_name.removeprefix("card")
        self.name = (
            (_amdsmi_product_name(int(card_number)) if card_number.isdigit() else None)
            or _read_text(device / "product_name")
            or f"AMD GPU ({card_name})"
        )
        self.handle = device
        self.available = True
        logger.success("AMD GPU monitoring initialized: {} (sysfs)", self.name)

    @staticmethod
    def _discover(root: Path) -> list[Path]:
        """AMD device directories under drm, largest VRAM first."""
        cards: list[tuple[int, int, Path]] = []
        for device in root.glob("card[0-9]*/device"):
            vendor = (_read_text(device / "vendor") or "").lower()
            if vendor != "0x1002":
                continue
            card_name = device.parent.name
            card_number = card_name.removeprefix("card")
            if not card_number.isdigit():
                continue
            vram = _read_int(device / "mem_info_vram_total") or 0
            cards.append((-vram, int(card_number), device))
        cards.sort()
        return [device for _vram, _number, device in cards]

    @staticmethod
    def _find_hwmon(device: Path) -> Path | None:
        """The ``amdgpu`` hwmon directory, or the first hwmon that has temp."""
        fallback = None
        for candidate in sorted((device / "hwmon").glob("hwmon*")):
            if (_read_text(candidate / "name") or "") == "amdgpu":
                return candidate
            if fallback is None and (candidate / "temp1_input").exists():
                fallback = candidate
        return fallback

    def read(self) -> GPUSnapshot | None:
        """One sysfs read of utilization, VRAM, temperature and power."""
        if not self.available or self._device is None:
            return None
        device = self._device
        temperature = 0.0
        power = 0.0
        if self._hwmon is not None:
            temp_raw = _read_float(self._hwmon / "temp1_input")
            temperature = (temp_raw or 0.0) / 1000.0
            power_raw = _read_float(self._hwmon / "power1_average")
            if power_raw is None:
                power_raw = _read_float(self._hwmon / "power1_input")
            power = (power_raw or 0.0) / 1_000_000.0
        return GPUSnapshot(
            utilization=float(_read_int(device / "gpu_busy_percent") or 0),
            memory_used_bytes=_read_int(device / "mem_info_vram_used") or 0,
            memory_total_bytes=self.memory_total_bytes,
            temperature_celsius=temperature,
            power_watts=power,
            vendor="amd",
        )

    def shutdown(self) -> None:
        """Nothing to release: sysfs reads hold no resources."""
        self.available = False


# --------------------------------------------------------------------------
# Windows: AMD Display Library (ADL)
# --------------------------------------------------------------------------

_ADL_MAX_PATH = 256
_ADL_OK = 0
# ADL reports the PCI vendor ID in decimal (1002), not as 0x1002.
_ADL_VENDOR_AMD = 1002
_PMLOG_MAX_SENSORS = 256

# Sensor IDs from the ADL SDK's ADL_PMLOG_SENSORS enum. Ordering below is
# preference, not enumeration: the first supported sensor wins.
_PMLOG_TEMP_IDS = (8, 27, 28, 29, 9)  # EDGE, HOTSPOT, GFX, SOC, MEM
_PMLOG_POWER_IDS = (23, 73, 30, 17)  # ASIC, BOARD, GFX, SOC
_PMLOG_ACTIVITY_GFX = 19


class _AdlUnavailableError(RuntimeError):
    """Raised when the ADL library or its context cannot be brought up."""


class _AdlAdapterInfo(ctypes.Structure):
    """Modern ``AdapterInfo`` layout (1572 bytes on x64).

    It grew beyond the ADL SDK 10 layout that pyadl still carries:
    ``iPresent``/``iExist`` replaced ``strPresent``, and the driver path,
    extension, PNP string and OS display index were appended. Passing the
    old, smaller layout makes ``ADL2_Adapter_AdapterInfo_Get`` return
    ``ADL_ERR_INVALID_PARAM`` (-3) and leaves the buffer zeroed.
    """

    _fields_ = [
        ("iSize", ctypes.c_int),
        ("iAdapterIndex", ctypes.c_int),
        ("strUDID", ctypes.c_char * _ADL_MAX_PATH),
        ("iBusNumber", ctypes.c_int),
        ("iDeviceNumber", ctypes.c_int),
        ("iFunctionNumber", ctypes.c_int),
        ("iVendorID", ctypes.c_int),
        ("strAdapterName", ctypes.c_char * _ADL_MAX_PATH),
        ("strDisplayName", ctypes.c_char * _ADL_MAX_PATH),
        ("iPresent", ctypes.c_int),
        ("iExist", ctypes.c_int),
        ("strDriverPath", ctypes.c_char * _ADL_MAX_PATH),
        ("strDriverPathExt", ctypes.c_char * _ADL_MAX_PATH),
        ("strPNPString", ctypes.c_char * _ADL_MAX_PATH),
        ("iOSDisplayIndex", ctypes.c_int),
    ]


class _AdlMemoryInfo(ctypes.Structure):
    """``ADL2_Adapter_MemoryInfo_Get`` payload; padding guards newer fields."""

    _fields_ = [
        ("iMemorySize", ctypes.c_longlong),
        ("strMemoryType", ctypes.c_char * _ADL_MAX_PATH),
        ("iMemoryBandwidth", ctypes.c_longlong),
        ("reserved", ctypes.c_char * 512),
    ]


class _AdlSingleSensor(ctypes.Structure):
    """One PMLog sensor: a supported flag plus its current value."""

    _fields_ = [("supported", ctypes.c_int), ("value", ctypes.c_int)]


class _AdlPMLogData(ctypes.Structure):
    """Output of ``ADL2_New_QueryPMLogData_Get`` (256 sensor slots)."""

    _fields_ = [
        ("iSize", ctypes.c_int),
        ("sensors", _AdlSingleSensor * _PMLOG_MAX_SENSORS),
    ]


@dataclass(frozen=True)
class _AdlAdapter:
    """One deduplicated AMD display adapter as ADL sees it."""

    index: int
    name: str
    pnp_id: str
    memory_total_bytes: int
    memory_type: str
    memory_bandwidth_mbps: int


def _decode(raw: bytes) -> str:
    """Decode a fixed-size ADL C string, trimming at the first NUL."""
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


class _AdlApi:
    """ctypes binding over the ADL DLL, narrowed to what the probe uses.

    Binding is split from reading so tests can substitute an object with the
    same three methods (``list_adapters``, ``read_sensors``, ``vram_used_mb``).
    """

    _MALLOC_CB = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_int)

    def __init__(self) -> None:
        """Load ``atiadlxx.dll`` and open an ADL2 context."""
        if sys.platform != "win32":
            msg = "ADL is only available on Windows"
            raise _AdlUnavailableError(msg)
        self._lib = self._load_dll()
        self._msvcrt = ctypes.CDLL("msvcrt.dll")
        self._malloc = self._MALLOC_CB(self._alloc)
        self._ctx: ctypes.c_void_p | None = ctypes.c_void_p()
        self._bind_required()
        self._bind_optional()
        if self._create(self._malloc, 1, ctypes.byref(self._ctx)) != _ADL_OK:
            msg = "ADL2_Main_Control_Create failed"
            raise _AdlUnavailableError(msg)

    def _alloc(self, size: int) -> int | None:
        """Allocator callback ADL calls for its internal buffers."""
        return self._msvcrt.malloc(size)

    @staticmethod
    def _load_dll() -> ctypes.CDLL:
        """Load the 64-bit ADL DLL, falling back to the 32-bit one."""
        last_error: OSError | None = None
        for dll in ("atiadlxx.dll", "atiadlxy.dll"):
            try:
                return ctypes.CDLL(dll)
            except OSError as exc:
                last_error = exc
        msg = f"ADL library not loadable: {last_error}"
        raise _AdlUnavailableError(msg)

    def _bind_required(self) -> None:
        """Bind the functions every ADL version exposes."""
        lib = self._lib
        self._create = lib.ADL2_Main_Control_Create
        self._create.restype = ctypes.c_int
        self._create.argtypes = [
            self._MALLOC_CB,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._destroy = lib.ADL2_Main_Control_Destroy
        self._destroy.restype = ctypes.c_int
        self._destroy.argtypes = [ctypes.c_void_p]
        self._number = lib.ADL2_Adapter_NumberOfAdapters_Get
        self._number.restype = ctypes.c_int
        self._number.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        self._adapter_info = lib.ADL2_Adapter_AdapterInfo_Get
        self._adapter_info.restype = ctypes.c_int
        self._adapter_info.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_AdlAdapterInfo),
            ctypes.c_int,
        ]
        self._adapter_id = lib.ADL2_Adapter_ID_Get
        self._adapter_id.restype = ctypes.c_int
        self._adapter_id.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        self._query_pmlog = lib.ADL2_New_QueryPMLogData_Get
        self._query_pmlog.restype = ctypes.c_int
        self._query_pmlog.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(_AdlPMLogData),
        ]

    def _bind_optional(self) -> None:
        """Bind functions older drivers may not export."""
        try:
            self._memory_info = self._lib.ADL2_Adapter_MemoryInfo_Get
            self._memory_info.restype = ctypes.c_int
            self._memory_info.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.POINTER(_AdlMemoryInfo),
            ]
        except AttributeError:
            self._memory_info = None
        try:
            self._vram_usage = self._lib.ADL2_Adapter_DedicatedVRAMUsage_Get
            self._vram_usage.restype = ctypes.c_int
            self._vram_usage.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
            ]
        except AttributeError:
            self._vram_usage = None

    def list_adapters(self) -> list[_AdlAdapter]:
        """AMD adapters, deduplicated by adapter ID and with VRAM size."""
        context = self._ctx
        if context is None:
            return []
        number = ctypes.c_int(0)
        if self._number(context, ctypes.byref(number)) != _ADL_OK or number.value <= 0:
            return []
        entries = (_AdlAdapterInfo * number.value)()
        if (
            self._adapter_info(
                context,
                ctypes.cast(entries, ctypes.POINTER(_AdlAdapterInfo)),
                ctypes.sizeof(entries),
            )
            != _ADL_OK
        ):
            return []
        adapters: list[_AdlAdapter] = []
        seen: set[int] = set()
        for entry in entries:
            if entry.iVendorID != _ADL_VENDOR_AMD:
                continue
            adapter_id = ctypes.c_int(0)
            if (
                self._adapter_id(context, entry.iAdapterIndex, ctypes.byref(adapter_id))
                != _ADL_OK
            ):
                continue
            if adapter_id.value in seen:
                continue
            seen.add(adapter_id.value)
            memory_total = 0
            memory_type = ""
            bandwidth = 0
            memory = _AdlMemoryInfo()
            if (
                self._memory_info is not None
                and self._memory_info(
                    context, entry.iAdapterIndex, ctypes.byref(memory)
                )
                == _ADL_OK
            ):
                memory_total = int(memory.iMemorySize)
                memory_type = _decode(memory.strMemoryType)
                bandwidth = int(memory.iMemoryBandwidth)
            adapters.append(
                _AdlAdapter(
                    index=entry.iAdapterIndex,
                    name=_decode(entry.strAdapterName)
                    or f"AMD GPU {entry.iAdapterIndex}",
                    pnp_id=_decode(entry.strPNPString),
                    memory_total_bytes=memory_total,
                    memory_type=memory_type,
                    memory_bandwidth_mbps=bandwidth,
                )
            )
        return adapters

    def read_sensors(self, adapter_index: int) -> dict[int, int]:
        """Current PMLog sensor values, keyed by ``ADL_PMLOG_*`` ID."""
        context = self._ctx
        if context is None:
            return {}
        data = _AdlPMLogData()
        data.iSize = ctypes.sizeof(data)
        if self._query_pmlog(context, adapter_index, ctypes.byref(data)) != _ADL_OK:
            return {}
        return {i: s.value for i, s in enumerate(data.sensors) if s.supported}

    def vram_used_mb(self, adapter_index: int) -> int | None:
        """Dedicated VRAM in use, in MiB, when the driver reports it."""
        context = self._ctx
        if context is None or self._vram_usage is None:
            return None
        usage = ctypes.c_int(-1)
        if (
            self._vram_usage(context, adapter_index, ctypes.byref(usage)) != _ADL_OK
            or usage.value < 0
        ):
            return None
        return int(usage.value)

    def close(self) -> None:
        """Destroy the ADL context. Safe to call more than once."""
        if self._ctx is None:
            return
        with suppress(Exception):
            self._destroy(self._ctx)
        self._ctx = None


class _AdlBackend:
    """Windows AMD backend over the ADL PMLog sensors."""

    backend = "adl"

    def __init__(self, gpu_index: int = 0, api: _AdlApi | None = None) -> None:
        """Open ADL and select the AMD adapter with the most VRAM."""
        self.available = False
        self.name = "N/A"
        self.memory_type = ""
        self.memory_bandwidth_mbps = 0
        self.memory_total_bytes = 0
        self.handle: object | None = None
        self._api: _AdlApi | None = None
        self._index = -1

        try:
            adl = api if api is not None else _AdlApi()
        except Exception as exc:
            logger.debug("AMD ADL backend unavailable: {}", exc)
            return
        try:
            adapters = sorted(
                adl.list_adapters(),
                key=lambda adapter: (-adapter.memory_total_bytes, adapter.index),
            )
        except Exception as exc:
            logger.debug("AMD adapter enumeration failed: {}", exc)
            adl.close()
            return
        if not adapters:
            adl.close()
            logger.debug("No AMD display adapter reported by ADL")
            return
        if not 0 <= gpu_index < len(adapters):
            adl.close()
            logger.debug(
                "AMD GPU index {} out of range ({} adapter(s) found)",
                gpu_index,
                len(adapters),
            )
            return
        chosen = adapters[gpu_index]
        self._api = adl
        self._index = chosen.index
        self.name = chosen.name
        self.memory_type = chosen.memory_type
        self.memory_bandwidth_mbps = chosen.memory_bandwidth_mbps
        self.memory_total_bytes = chosen.memory_total_bytes
        self.handle = chosen.index
        self.available = True
        logger.success("AMD GPU monitoring initialized: {} (ADL)", self.name)

    def read(self) -> GPUSnapshot | None:
        """One ADL read of utilization, VRAM, temperature and power."""
        if not self.available or self._api is None:
            return None
        sensors = self._api.read_sensors(self._index)
        if not sensors:
            return None
        used_mb = self._api.vram_used_mb(self._index)
        return GPUSnapshot(
            utilization=float(sensors.get(_PMLOG_ACTIVITY_GFX, 0)),
            memory_used_bytes=(used_mb or 0) * 1024**2,
            memory_total_bytes=self.memory_total_bytes,
            temperature_celsius=float(_first_sensor(sensors, _PMLOG_TEMP_IDS) or 0),
            power_watts=float(_first_sensor(sensors, _PMLOG_POWER_IDS) or 0),
            vendor="amd",
        )

    def shutdown(self) -> None:
        """Destroy the ADL context and mark the backend unavailable."""
        if self._api is not None:
            self._api.close()
            self._api = None
        self.available = False


class AmdGpuProbe:
    """Vendor-side probe picking the AMD backend for this platform.

    Example:
        >>> with AmdGpuProbe() as gpu:
        ...     snapshot = gpu.read()
        ...     print(f"GPU: {gpu.name}, Temp: {snapshot.temperature_celsius}C")
    """

    def __init__(self, gpu_index: int = 0) -> None:
        """Construct the platform backend and adopt it when it answers."""
        self.available = False
        self.name = "N/A"
        self.backend = "none"
        self.memory_type = ""
        self.memory_bandwidth_mbps = 0
        self.handle: object | None = None
        self._backend: _AdlBackend | _SysfsBackend | None = None

        backend: _AdlBackend | _SysfsBackend | None = None
        if sys.platform == "win32":
            backend = _AdlBackend(gpu_index)
        elif sys.platform.startswith("linux"):
            backend = _SysfsBackend(gpu_index)
        if backend is None or not backend.available:
            return
        self._backend = backend
        self.available = True
        self.name = backend.name
        self.backend = backend.backend
        self.memory_type = backend.memory_type
        self.memory_bandwidth_mbps = backend.memory_bandwidth_mbps
        self.handle = backend.handle

    def read(self) -> GPUSnapshot | None:
        """Read the current GPU metrics, or None when unavailable."""
        if self._backend is None:
            return None
        return self._backend.read()

    def shutdown(self) -> None:
        """Release backend resources. Safe to call multiple times."""
        if self._backend is not None:
            self._backend.shutdown()
            self._backend = None
        self.available = False

    def __enter__(self) -> Self:
        """Enter context manager."""
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        """Exit context manager and ensure cleanup."""
        self.shutdown()
