from __future__ import annotations

import os
import platform
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import psutil


class PowerMonitor:
    """Best-effort power reader with OS-specific backends and fallback estimation."""

    def __init__(self) -> None:
        self._platform = platform.system()
        self._last_energy_uj: Optional[int] = None
        self._last_energy_ts: Optional[float] = None
        self._energy_wh = 0.0
        self._last_os_power_ts = 0.0
        self._os_power_enabled = self._resolve_os_power_enabled()
        self._os_power_cache: Optional[float] = None
        self._stop_event = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._rapl_energy_paths = self._find_rapl_energy_paths()
        self._start_polling()

    def _start_polling(self) -> None:
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
        if self._poll_thread and self._poll_thread.is_alive():
            self._stop_event.set()
            self._poll_thread.join(timeout=1.0)

    def _poll_os_power(self) -> None:
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
        flag = os.getenv("KATAGLYPHIS_ENABLE_OS_POWER", "")
        if flag:
            return flag.strip() in {"1", "true", "TRUE", "yes", "YES"}
        if self._platform == "Linux":
            return True
        return False

    def update(
        self,
        sys_gpu_power: float,
        cpu_util_percent: float,
        cpu_tdp_watts: float,
        freq_ratio: float,
        dt_seconds: float,
    ) -> Dict[str, float]:
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
        power = 0.0
        if self._platform == "Linux":
            power = self._read_linux_rapl_power(dt_seconds)
        elif self._platform == "Darwin":
            power = float(self._os_power_cache or 0.0)
        elif self._platform == "Windows":
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

    def _find_rapl_energy_paths(self) -> Tuple[Path, ...]:
        root = Path("/sys/class/powercap")
        if not root.exists():
            return tuple()
        paths = []
        for entry in root.glob("intel-rapl:*"):
            name_file = entry / "name"
            energy_file = entry / "energy_uj"
            if not energy_file.exists() or not name_file.exists():
                continue
            try:
                name = name_file.read_text(encoding="utf-8").strip().lower()
            except Exception:
                continue
            if "package" in name or "cpu" in name:
                paths.append(energy_file)
        return tuple(paths)

    def _read_linux_rapl_power(self, dt_seconds: float) -> float:
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
        except Exception:
            return 0.0

    def _read_macos_powermetrics_power(self, blocking: bool = True) -> float:
        if blocking and time.perf_counter() - self._last_os_power_ts < 2.0:
            return 0.0
        self._last_os_power_ts = time.perf_counter()
        try:
            result = subprocess.run(
                ["/usr/bin/powermetrics", "--samplers", "smc", "-n", "1"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
            )
            output = result.stdout + result.stderr
            match = re.search(r"CPU Power:\s*([0-9.]+)\s*W", output)
            if match:
                return float(match.group(1))
        except Exception:
            return 0.0
        return 0.0

    def _read_windows_ohm_power(self, blocking: bool = True) -> float:
        if blocking and time.perf_counter() - self._last_os_power_ts < 2.0:
            return 0.0
        self._last_os_power_ts = time.perf_counter()
        try:
            cmd = [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance -Namespace root\\OpenHardwareMonitor -Class Sensor | "
                "Where-Object { $_.SensorType -eq 'Power' -and $_.Name -match 'CPU' } | "
                "Select-Object -First 1 -ExpandProperty Value",
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
            )
            value = result.stdout.strip()
            if value:
                return float(value)
        except Exception:
            return 0.0
        return 0.0


def get_cpu_freq_ratio() -> float:
    try:
        freq = psutil.cpu_freq()
        if freq and freq.max:
            return max(0.0, min(1.0, (freq.current or 0.0) / freq.max))
    except Exception:
        return 1.0
    return 1.0
