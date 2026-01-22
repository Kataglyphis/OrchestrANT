from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import cv2
import numpy as np

from kataglyphispythoninference.yolo.types import PerformanceMetrics, SystemStats


if TYPE_CHECKING:
    import wx


class WxPythonViewer:
    """wxPython viewer for rendering frames with a side info panel."""

    def __init__(self, width: int, height: int, title: str = "YOLO Monitor") -> None:
        self.width = int(width)
        self.height = int(height)
        self._frame_size = (self.width, self.height)
        self._open = True
        self._closing = False
        self._ready = threading.Event()
        self._run_ui(title)

        self._last_labels: dict[str, str] = {}
        self._last_log_text = ""
        self._needs_layout = True

    def _run_ui(self, title: str) -> None:
        import wx

        self.wx = wx
        self.app = wx.GetApp() or wx.App(False)
        self.frame = wx.Frame(
            None,
            title=title,
            size=wx.Size(self.width + 360, self.height + 120),
        )
        self.panel = wx.Panel(self.frame)
        self.frame.SetDoubleBuffered(True)
        self.panel.SetDoubleBuffered(True)

        class _FramePanel(wx.Panel):
            def __init__(self, parent: wx.Panel) -> None:
                super().__init__(parent)
                self._bitmap: wx.Bitmap | None = None
                self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
                self.SetBackgroundColour(parent.GetBackgroundColour())
                self.Bind(wx.EVT_PAINT, self._on_paint)

            def set_bitmap(self, bmp: wx.Bitmap) -> None:
                self._bitmap = bmp
                self.Refresh(False)

            def _on_paint(self, _event: wx.PaintEvent) -> None:
                dc = wx.BufferedPaintDC(self)
                dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
                dc.Clear()
                if self._bitmap is not None:
                    dc.DrawBitmap(self._bitmap, 0, 0, False)

        self.frame_panel = _FramePanel(self.panel)
        self.frame_panel.SetMinSize((self.width, self.height))
        self.frame_panel.SetSize((self.width, self.height))

        self._labels = {
            "resolution": wx.StaticText(self.panel, label=""),
            "capture": wx.StaticText(self.panel, label=""),
            "backend": wx.StaticText(self.panel, label=""),
            "pipeline": wx.StaticText(self.panel, label=""),
            "cpu_model": wx.StaticText(self.panel, label=""),
            "ram_total": wx.StaticText(self.panel, label=""),
            "gpu_model": wx.StaticText(self.panel, label=""),
            "vram_total": wx.StaticText(self.panel, label=""),
            "detections": wx.StaticText(self.panel, label=""),
            "camera_fps": wx.StaticText(self.panel, label=""),
            "inference_ms": wx.StaticText(self.panel, label=""),
            "budget": wx.StaticText(self.panel, label=""),
            "headroom": wx.StaticText(self.panel, label=""),
            "sys_cpu": wx.StaticText(self.panel, label=""),
            "sys_ram": wx.StaticText(self.panel, label=""),
            "gpu": wx.StaticText(self.panel, label=""),
            "vram": wx.StaticText(self.panel, label=""),
            "power": wx.StaticText(self.panel, label=""),
            "energy": wx.StaticText(self.panel, label=""),
            "proc": wx.StaticText(self.panel, label=""),
            "class": wx.StaticText(self.panel, label=""),
        }

        self.log_ctrl = wx.TextCtrl(
            self.panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY,
            size=wx.Size(320, 200),
        )

        right_sizer = wx.BoxSizer(wx.VERTICAL)
        right_sizer.Add(self._labels["resolution"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["capture"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["backend"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["pipeline"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["cpu_model"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["ram_total"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["gpu_model"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["vram_total"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["detections"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["camera_fps"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["inference_ms"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["budget"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["headroom"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["sys_cpu"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["sys_ram"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["gpu"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["vram"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["power"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["energy"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["proc"], 0, wx.ALL, 2)
        right_sizer.Add(self._labels["class"], 0, wx.ALL, 2)
        right_sizer.Add(self.log_ctrl, 0, wx.ALL, 4)

        main_sizer = wx.BoxSizer(wx.HORIZONTAL)
        main_sizer.Add(self.frame_panel, 0, wx.ALL, 6)
        main_sizer.Add(right_sizer, 0, wx.ALL, 6)

        self.panel.SetSizer(main_sizer)
        self.frame.Bind(wx.EVT_CLOSE, self._on_close)
        self.frame.Show()

        self._ready.set()

    def run(self) -> None:
        if not self._ready.is_set():
            return
        try:
            if not self.app.IsMainLoopRunning():
                self.app.MainLoop()
        except Exception:
            pass

    def _on_close(self, event: wx.CloseEvent | None) -> None:
        if self._closing:
            return
        self._closing = True
        self._open = False
        try:
            if hasattr(self, "frame") and self.frame:
                self.frame.Destroy()
        except Exception:
            pass
        try:
            if hasattr(self, "app") and self.app:
                self.app.ExitMainLoop()
        except Exception:
            pass
        if event is not None:
            try:
                event.Skip()
            except Exception:
                pass

    def is_open(self) -> bool:
        if not self._ready.is_set():
            return False
        return self._open and not self._closing

    def render(
        self,
        frame: np.ndarray,
        perf_metrics: PerformanceMetrics | None = None,
        sys_stats: SystemStats | None = None,
        proc_stats: dict | None = None,
        camera_info: dict | None = None,
        detections_count: int | None = None,
        classification: dict | None = None,
        log_lines: list[str] | None = None,
        hardware_info: dict | None = None,
        power_info: dict | None = None,
    ) -> None:
        if not self.is_open() or not self._ready.is_set():
            return
        try:
            self.wx.CallAfter(
                self._update_ui,
                frame,
                perf_metrics,
                sys_stats,
                proc_stats,
                camera_info,
                detections_count,
                classification,
                log_lines,
                hardware_info,
                power_info,
            )
        except Exception:
            pass

    def _update_ui(
        self,
        frame: np.ndarray,
        perf_metrics: PerformanceMetrics | None,
        sys_stats: SystemStats | None,
        proc_stats: dict | None,
        camera_info: dict | None,
        detections_count: int | None,
        classification: dict | None,
        log_lines: list[str] | None,
        hardware_info: dict | None,
        power_info: dict | None,
    ) -> None:
        if not self.is_open():
            return

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = frame_rgb.shape[:2]
        if (w, h) != self._frame_size:
            self._frame_size = (w, h)
            self._needs_layout = True
            self.frame_panel.SetMinSize((w, h))
            self.frame_panel.SetSize((w, h))

        bitmap = self.wx.Bitmap.FromBuffer(w, h, frame_rgb)
        self.frame_panel.set_bitmap(bitmap)

        if (
            perf_metrics is not None
            and sys_stats is not None
            and proc_stats is not None
        ):
            self._set_label("resolution", f"Resolution: {w}x{h}")
            if camera_info is not None:
                backend = camera_info.get("backend", "unknown")
                pipeline = camera_info.get("pipeline", "")
                if len(pipeline) > 70:
                    pipeline = pipeline[:67] + "..."
                self._set_label("capture", f"Capture: {backend}")
                self._set_label("backend", f"Backend: {backend}")
                self._set_label("pipeline", f"Pipeline: {pipeline}")

            if hardware_info is not None:
                self._set_label(
                    "cpu_model",
                    f"CPU: {hardware_info.get('cpu_model', 'N/A')}",
                )
                self._set_label(
                    "ram_total",
                    f"RAM: {hardware_info.get('ram_total_gb', 0.0):.1f} GB",
                )
                gpu_model = hardware_info.get("gpu_model", "N/A")
                vram_total = hardware_info.get("vram_total_gb", 0.0)
                if gpu_model and gpu_model != "N/A":
                    self._set_label("gpu_model", f"GPU: {gpu_model}")
                    self._set_label("vram_total", f"VRAM: {vram_total:.1f} GB")
                else:
                    self._set_label("gpu_model", "")
                    self._set_label("vram_total", "")

            if detections_count is not None:
                self._set_label("detections", f"Detections: {detections_count}")

            self._set_label(
                "camera_fps",
                f"Camera Input: {perf_metrics.camera_fps:.1f} FPS",
            )
            self._set_label(
                "inference_ms",
                f"Inference Latency: {perf_metrics.inference_ms:.1f} ms/frame",
            )
            self._set_label(
                "budget",
                f"Frame Budget Used: {perf_metrics.frame_budget_percent:.1f}%",
            )
            headroom = 100 - perf_metrics.frame_budget_percent
            self._set_label(
                "headroom",
                f"GPU Headroom: {headroom:.0f}% (cap: {perf_metrics.inference_capacity_fps:.0f} FPS)",
            )
            self._set_label(
                "sys_cpu",
                f"System CPU: {sys_stats.cpu_percent:.1f}%",
            )
            self._set_label(
                "sys_ram",
                f"System RAM: {sys_stats.ram_used_gb:.1f}/{sys_stats.ram_total_gb:.1f} GB ({sys_stats.ram_percent:.1f}%)",
            )
            if sys_stats.gpu_name != "N/A":
                self._set_label(
                    "gpu",
                    f"GPU Load: {sys_stats.gpu_percent:.0f}% | Temp: {sys_stats.gpu_temp_celsius:.0f}C | {sys_stats.gpu_power_watts:.0f}W",
                )
                self._set_label(
                    "vram",
                    f"VRAM: {sys_stats.gpu_memory_used_gb:.1f}/{sys_stats.gpu_memory_total_gb:.1f} GB ({sys_stats.gpu_memory_percent:.0f}%)",
                )
            else:
                self._set_label("gpu", "")
                self._set_label("vram", "")

            if power_info is not None:
                system_power = power_info.get("system_power_watts", 0.0)
                cpu_power = power_info.get("cpu_power_watts", 0.0)
                gpu_power = power_info.get("gpu_power_watts", 0.0)
                energy_wh = power_info.get("energy_wh", 0.0)
                if system_power > 0.0:
                    self._set_label(
                        "power",
                        f"Power: {system_power:.0f}W (CPU {cpu_power:.0f}W, GPU {gpu_power:.0f}W)",
                    )
                elif gpu_power > 0.0:
                    self._set_label("power", f"Power: GPU {gpu_power:.0f}W")
                else:
                    self._set_label("power", "")
                if energy_wh > 0.0:
                    self._set_label("energy", f"Energy: {energy_wh:.3f} Wh")
                else:
                    self._set_label("energy", "")

            self._set_label(
                "proc",
                f"Process: CPU {proc_stats['cpu_percent']:.1f}% | RAM {proc_stats['memory_mb']:.0f}MB | Threads {proc_stats['threads']}",
            )

        if classification is not None:
            class_label = classification.get("label", "unknown")
            class_score = classification.get("score", 0.0)
            self._set_label("class", f"Class: {class_label} ({class_score:.2f})")

        if log_lines is not None:
            new_log = "\n".join(log_lines)
            if new_log != self._last_log_text:
                self.log_ctrl.Freeze()
                self.log_ctrl.SetValue(new_log)
                self.log_ctrl.Thaw()
                self._last_log_text = new_log

        if self._needs_layout:
            self.panel.Layout()
            self._needs_layout = False
        self.wx.YieldIfNeeded()

    def close(self) -> None:
        if self._open:
            self._open = False
            self._closing = True
            try:
                if self._ready.is_set() and hasattr(self, "wx"):
                    self.wx.CallAfter(self._on_close, None)
            except Exception:
                pass

    def _set_label(self, key: str, text: str) -> None:
        if self._last_labels.get(key) == text:
            return
        self._labels[key].SetLabel(text)
        self._last_labels[key] = text
