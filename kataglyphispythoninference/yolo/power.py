"""Power monitoring utilities."""

from __future__ import annotations

import os
import platform
import threading
import time
from contextlib import suppress
from pathlib import Path

import psutil
from loguru import logger


class PowerMonitor:
    """Best-effort power reader with OS-specific backends and fallback estimation."""

    def __init__(self) -> None:
        """Initialize power monitoring state and background polling."""
        self._platform = platform.system()
        self._last_energy_uj: int | None = None
        self._last_energy_ts: float | None = None
        self._energy_wh = 0.0
        self._last_os_power_ts = 0.0
        self._os_power_enabled = self._resolve_os_power_enabled()
        self._os_power_cache: float | None = None
        self._os_power_warned: set[str] = set()
        self._stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._rapl_energy_paths = self._find_rapl_energy_paths()
        self._start_polling()

    def _start_polling(self) -> None:
        """Start background polling if OS power is enabled."""
        if not self._os_power_enabled:
            return
        if self._platform not in {"Windows", "Darwin"}:
            return
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_thread = threading.Thread(
            target=self._poll_os_power,
            name="power-poll",
            daemon=True,
        )
        self._poll_thread.start()

    def shutdown(self) -> None:
        """Stop background polling."""
        if self._poll_thread and self._poll_thread.is_alive():
            self._stop_event.set()
            self._poll_thread.join(timeout=1.0)

    def _poll_os_power(self) -> None:
        """Poll OS power metrics on supported platforms."""
        interval = float(os.getenv("KATAGLYPHIS_OS_POWER_INTERVAL", "2.0") or 2.0)
        while not self._stop_event.is_set():
            power = 0.0
            if self._platform == "Windows":
                power = self._read_windows_ohm_power(blocking=False)
            elif self._platform == "Darwin":
                power = self._read_macos_powermetrics_power(blocking=False)
            if power > 0.0:
                self._os_power_cache = power
            self._stop_event.wait(interval)

    def _resolve_os_power_enabled(self) -> bool:
        """Determine whether OS power sampling should be enabled."""
        flag = os.getenv("KATAGLYPHIS_ENABLE_OS_POWER", "")
        if flag:
            return flag.strip() in {"1", "true", "TRUE", "yes", "YES"}
        return self._platform == "Linux"

    def update(
        self,
        sys_gpu_power: float,
        cpu_util_percent: float,
        cpu_tdp_watts: float,
        freq_ratio: float,
        dt_seconds: float,
    ) -> dict[str, float]:
        """Update power metrics and return aggregated values."""
        cpu_power = self._read_cpu_power(
            cpu_util_percent=cpu_util_percent,
            cpu_tdp_watts=cpu_tdp_watts,
            freq_ratio=freq_ratio,
            dt_seconds=dt_seconds,
        )
        gpu_power = float(sys_gpu_power or 0.0)
        system_power = 0.0
        if cpu_power or gpu_power:
            system_power = cpu_power + gpu_power
        if system_power > 0.0 and dt_seconds > 0.0:
            self._energy_wh += (system_power * dt_seconds) / 3600.0

        return {
            "system_power_watts": system_power,
            "cpu_power_watts": cpu_power,
            "gpu_power_watts": gpu_power,
            "energy_wh": self._energy_wh,
        }

    def _read_cpu_power(
        self,
        cpu_util_percent: float,
        cpu_tdp_watts: float,
        freq_ratio: float,
        dt_seconds: float,
    ) -> float:
        """Estimate CPU power, preferring OS and RAPL sources."""
        power = 0.0
        if self._platform == "Linux":
            power = self._read_linux_rapl_power(dt_seconds)
        elif self._platform in {"Darwin", "Windows"}:
            power = float(self._os_power_cache or 0.0)

        if power <= 0.0:
            power = (
                cpu_tdp_watts * (cpu_util_percent / 100.0) * max(0.2, freq_ratio)
                if cpu_tdp_watts > 0.0
                else 0.0
            )
            if cpu_util_percent > 0.5 and power < 1.0:
                power = 1.0
        return power

    def _find_rapl_energy_paths(self) -> tuple[Path, ...]:
        """Find available Linux RAPL energy counters."""
        root = Path("/sys/class/powercap")
        if not root.exists():
            return ()
        paths = []
        for entry in root.glob("intel-rapl:*"):
            name_file = entry / "name"
            energy_file = entry / "energy_uj"
            if not energy_file.exists() or not name_file.exists():
                continue
            try:
                name = name_file.read_text(encoding="utf-8").strip().lower()
            except OSError as exc:
                logger.debug("Skipping RAPL path {}: {}", name_file, exc)
                continue
            if "package" in name or "cpu" in name:
                paths.append(energy_file)
        return tuple(paths)

    def _read_linux_rapl_power(self, dt_seconds: float) -> float:
        """Read power from Linux RAPL counters."""
        if not self._rapl_energy_paths or dt_seconds <= 0.0:
            return 0.0
        try:
            total_uj = 0
            for energy_path in self._rapl_energy_paths:
                total_uj += int(energy_path.read_text(encoding="utf-8").strip())
            now_ts = time.perf_counter()
            if self._last_energy_uj is None or self._last_energy_ts is None:
                self._last_energy_uj = total_uj
                self._last_energy_ts = now_ts
                return 0.0
            delta_uj = total_uj - self._last_energy_uj
            delta_ts = now_ts - self._last_energy_ts
            self._last_energy_uj = total_uj
            self._last_energy_ts = now_ts
            if delta_uj <= 0 or delta_ts <= 0:
                return 0.0
            return (delta_uj * 1e-6) / delta_ts
        except (OSError, ValueError):
            return 0.0

    def _read_macos_powermetrics_power(self, *, blocking: bool = True) -> float:
        """Read CPU power using powermetrics on macOS."""
        if blocking and time.perf_counter() - self._last_os_power_ts < 2.0:
            return 0.0
        self._last_os_power_ts = time.perf_counter()
        if "macos" not in self._os_power_warned:
            logger.debug(
                "powermetrics subprocess sampling disabled; returning 0.0 watts"
            )
            self._os_power_warned.add("macos")
        return 0.0

    def _read_windows_ohm_power(self, *, blocking: bool = True) -> float:
        """Read CPU power via OpenHardwareMonitor on Windows."""
        if blocking and time.perf_counter() - self._last_os_power_ts < 2.0:
            return 0.0
        self._last_os_power_ts = time.perf_counter()
        if "windows" not in self._os_power_warned:
            logger.debug(
                "OpenHardwareMonitor subprocess sampling disabled; returning 0.0 watts"
            )
            self._os_power_warned.add("windows")
        return 0.0


def get_cpu_freq_ratio() -> float:
    """Return normalized CPU frequency ratio between 0 and 1."""
    with suppress(OSError, RuntimeError):
        freq = psutil.cpu_freq()
        if freq and freq.max:
            return max(0.0, min(1.0, (freq.current or 0.0) / freq.max))
    return 1.0
