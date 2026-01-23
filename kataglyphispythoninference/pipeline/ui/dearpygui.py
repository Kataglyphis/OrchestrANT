"""DearPyGui viewer for monitoring pipelines."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

import cv2
import numpy as np


try:
    import dearpygui.dearpygui as dpg
except ImportError as exc:  # pragma: no cover - optional dependency
    dpg = None
    _DPG_IMPORT_ERROR = exc
else:
    _DPG_IMPORT_ERROR = None

if TYPE_CHECKING:
    from kataglyphispythoninference.pipeline.types import (
        PerformanceMetrics,
        SystemStats,
    )


class DearPyGuiViewer:
    """Minimal DearPyGui viewer for rendering frames as a texture."""

    def __init__(self, width: int, height: int, title: str = "YOLO Monitor") -> None:
        """Initialize the DearPyGui viewer UI."""
        if dpg is None:
            message = "dearpygui is required for DearPyGuiViewer"
            raise ImportError(message) from _DPG_IMPORT_ERROR

        self.dpg = dpg
        self.width = int(width)
        self.height = int(height)
        self._frame_size = (self.width, self.height)
        self._texture_registry_tag = "frame_texture_registry"
        self._texture_tag = "frame_texture"
        self._image_tag = "frame_image"

        self._perf_tags = {
            "title": "perf_title",
            "resolution": "perf_resolution",
            "capture": "perf_capture",
            "backend": "perf_backend",
            "pipeline": "perf_pipeline",
            "cpu_model": "perf_cpu_model",
            "ram_total": "perf_ram_total",
            "gpu_model": "perf_gpu_model",
            "vram_total": "perf_vram_total",
            "detections": "perf_detections",
            "camera_fps": "perf_camera_fps",
            "inference_ms": "perf_inference_ms",
            "budget": "perf_budget",
            "headroom": "perf_headroom",
            "sys_cpu": "perf_sys_cpu",
            "sys_ram": "perf_sys_ram",
            "gpu": "perf_gpu",
            "vram": "perf_vram",
            "power": "perf_power",
            "energy": "perf_energy",
            "proc": "perf_proc",
        }

        self._det_tags = {
            "detections": "det_count",
            "class": "det_class",
        }

        self._log_tag = "log_output"
        self._plot_tags = {
            "perf_plot": "perf_plot",
            "perf_camera_fps": "series_camera_fps",
            "perf_inference_ms": "series_inference_ms",
            "perf_x": "perf_x_axis",
            "perf_y": "perf_y_axis",
            "sys_plot": "sys_plot",
            "sys_cpu": "series_sys_cpu",
            "sys_ram": "series_sys_ram",
            "sys_gpu": "series_sys_gpu",
            "sys_x": "sys_x_axis",
            "sys_y": "sys_y_axis",
        }
        self._plot_history_len = 120
        self._plot_index = 0
        self._plot_data = {
            "x": deque(maxlen=self._plot_history_len),
            "camera_fps": deque(maxlen=self._plot_history_len),
            "inference_ms": deque(maxlen=self._plot_history_len),
            "sys_cpu": deque(maxlen=self._plot_history_len),
            "sys_ram": deque(maxlen=self._plot_history_len),
            "sys_gpu": deque(maxlen=self._plot_history_len),
        }

        self.dpg.create_context()
        self._colors = {
            "bg": (17, 24, 39, 255),
            "panel": (31, 41, 55, 255),
            "panel_alt": (24, 32, 45, 255),
            "border": (55, 65, 81, 255),
            "text": (229, 231, 235, 255),
            "muted": (156, 163, 175, 255),
            "accent": (34, 211, 238, 255),
            "accent_soft": (14, 116, 144, 255),
        }
        self._theme = self._create_theme()
        self.dpg.bind_theme(self._theme)
        self.dpg.create_viewport(
            title=title,
            width=self.width + 360,
            height=self.height + 120,
        )

        with self.dpg.texture_registry(show=False, tag=self._texture_registry_tag):
            self._create_texture(self.width, self.height)

        with self.dpg.window(
            label="Video",
            tag="video_window",
            pos=(10, 10),
            width=self.width + 20,
            height=self.height + 20,
            no_move=True,
            no_resize=True,
        ):
            self.dpg.add_image(
                self._texture_tag,
                tag=self._image_tag,
                width=self.width,
                height=self.height,
            )

        with self.dpg.window(
            label="System & Performance",
            tag="perf_window",
            pos=(self.width + 30, 10),
            width=320,
            height=400,
        ):
            self.dpg.add_text(
                "YOLO Monitor",
                tag=self._perf_tags["title"],
                color=self._colors["accent"],
            )
            with self.dpg.child_window(border=True, autosize_x=True):
                self.dpg.add_text("Overview", color=self._colors["muted"])
                self.dpg.add_separator()
                self.dpg.add_text("", tag=self._perf_tags["resolution"])
                self.dpg.add_text("", tag=self._perf_tags["capture"])
                self.dpg.add_text("", tag=self._perf_tags["backend"])
                self.dpg.add_text("", tag=self._perf_tags["pipeline"])
                self.dpg.add_text("", tag=self._perf_tags["cpu_model"])
                self.dpg.add_text("", tag=self._perf_tags["ram_total"])
                self.dpg.add_text("", tag=self._perf_tags["gpu_model"])
                self.dpg.add_text("", tag=self._perf_tags["vram_total"])
                self.dpg.add_text("", tag=self._perf_tags["detections"])
            with self.dpg.child_window(border=True, autosize_x=True):
                self.dpg.add_text("Performance", color=self._colors["muted"])
                self.dpg.add_separator()
                self.dpg.add_text("", tag=self._perf_tags["camera_fps"])
                self.dpg.add_text("", tag=self._perf_tags["inference_ms"])
                self.dpg.add_text("", tag=self._perf_tags["budget"])
                self.dpg.add_text("", tag=self._perf_tags["headroom"])
            with self.dpg.child_window(border=True, autosize_x=True):
                self.dpg.add_text("System", color=self._colors["muted"])
                self.dpg.add_separator()
                self.dpg.add_text("", tag=self._perf_tags["sys_cpu"])
                self.dpg.add_text("", tag=self._perf_tags["sys_ram"])
                self.dpg.add_text("", tag=self._perf_tags["gpu"])
                self.dpg.add_text("", tag=self._perf_tags["vram"])
                self.dpg.add_text("", tag=self._perf_tags["power"])
                self.dpg.add_text("", tag=self._perf_tags["energy"])
            with self.dpg.child_window(border=True, autosize_x=True):
                self.dpg.add_text("Process", color=self._colors["muted"])
                self.dpg.add_separator()
                self.dpg.add_text("", tag=self._perf_tags["proc"])
            with self.dpg.child_window(border=True, autosize_x=True, height=420):
                self.dpg.add_text("Trends", color=self._colors["muted"])
                self.dpg.add_separator()
                with self.dpg.plot(
                    label="Performance",
                    height=180,
                    width=-1,
                    tag=self._plot_tags["perf_plot"],
                ):
                    self.dpg.add_plot_legend()
                    self.dpg.add_plot_axis(
                        self.dpg.mvXAxis,
                        label="Frames",
                        tag=self._plot_tags["perf_x"],
                    )
                    y_axis = self.dpg.add_plot_axis(
                        self.dpg.mvYAxis,
                        label="Value",
                        tag=self._plot_tags["perf_y"],
                    )
                    self.dpg.add_line_series(
                        [],
                        [],
                        label="Camera FPS",
                        parent=y_axis,
                        tag=self._plot_tags["perf_camera_fps"],
                    )
                    self.dpg.add_line_series(
                        [],
                        [],
                        label="Inference ms",
                        parent=y_axis,
                        tag=self._plot_tags["perf_inference_ms"],
                    )
                with self.dpg.plot(
                    label="System",
                    height=180,
                    width=-1,
                    tag=self._plot_tags["sys_plot"],
                ):
                    self.dpg.add_plot_legend()
                    self.dpg.add_plot_axis(
                        self.dpg.mvXAxis,
                        label="Frames",
                        tag=self._plot_tags["sys_x"],
                    )
                    y_axis = self.dpg.add_plot_axis(
                        self.dpg.mvYAxis,
                        label="Percent",
                        tag=self._plot_tags["sys_y"],
                    )
                    self.dpg.add_line_series(
                        [],
                        [],
                        label="CPU %",
                        parent=y_axis,
                        tag=self._plot_tags["sys_cpu"],
                    )
                    self.dpg.add_line_series(
                        [],
                        [],
                        label="RAM %",
                        parent=y_axis,
                        tag=self._plot_tags["sys_ram"],
                    )
                    self.dpg.add_line_series(
                        [],
                        [],
                        label="GPU %",
                        parent=y_axis,
                        tag=self._plot_tags["sys_gpu"],
                    )

        with self.dpg.window(
            label="Detections",
            tag="det_window",
            pos=(self.width + 30, 420),
            width=320,
            height=180,
        ):
            self.dpg.add_text("Current", color=self._colors["muted"])
            self.dpg.add_separator()
            self.dpg.add_text("", tag=self._det_tags["detections"])
            self.dpg.add_text("", tag=self._det_tags["class"])

        with self.dpg.window(
            label="Logs",
            tag="log_window",
            pos=(self.width + 30, 610),
            width=320,
            height=260,
        ):
            self.dpg.add_text("Recent logs", color=self._colors["muted"])
            self.dpg.add_separator()
            self.dpg.add_input_text(
                tag=self._log_tag,
                multiline=True,
                readonly=True,
                width=-1,
                height=-1,
            )

        self.dpg.setup_dearpygui()
        self.dpg.show_viewport()

    def _create_texture(self, width: int, height: int) -> None:
        """Create or recreate the DearPyGui texture."""
        if self.dpg.does_item_exist(self._texture_tag):
            self.dpg.delete_item(self._texture_tag)
        frame_data = np.zeros((height, width, 3), dtype=np.float32)
        self.dpg.add_raw_texture(
            width,
            height,
            frame_data.ravel().tolist(),
            format=self.dpg.mvFormat_Float_rgb,
            tag=self._texture_tag,
            parent=self._texture_registry_tag,
        )

    def _create_theme(self) -> int:
        """Create and return a consistent dark theme."""
        with self.dpg.theme() as theme:
            with self.dpg.theme_component(self.dpg.mvAll):
                self.dpg.add_theme_color(
                    self.dpg.mvThemeCol_WindowBg, self._colors["bg"]
                )
                self.dpg.add_theme_color(
                    self.dpg.mvThemeCol_ChildBg, self._colors["panel"]
                )
                self.dpg.add_theme_color(
                    self.dpg.mvThemeCol_Border, self._colors["border"]
                )
                self.dpg.add_theme_color(self.dpg.mvThemeCol_Text, self._colors["text"])
                self.dpg.add_theme_color(
                    self.dpg.mvThemeCol_FrameBg, self._colors["panel_alt"]
                )
                self.dpg.add_theme_color(
                    self.dpg.mvThemeCol_FrameBgHovered, self._colors["panel"]
                )
                self.dpg.add_theme_color(
                    self.dpg.mvThemeCol_FrameBgActive, self._colors["panel"]
                )
                self.dpg.add_theme_color(
                    self.dpg.mvThemeCol_TitleBg, self._colors["panel"]
                )
                self.dpg.add_theme_color(
                    self.dpg.mvThemeCol_TitleBgActive, self._colors["panel"]
                )
                self.dpg.add_theme_color(
                    self.dpg.mvThemeCol_Header, self._colors["panel_alt"]
                )
                self.dpg.add_theme_color(
                    self.dpg.mvThemeCol_HeaderHovered, self._colors["panel"]
                )
                self.dpg.add_theme_color(
                    self.dpg.mvThemeCol_HeaderActive, self._colors["panel"]
                )
                self.dpg.add_theme_style(self.dpg.mvStyleVar_WindowRounding, 6)
                self.dpg.add_theme_style(self.dpg.mvStyleVar_ChildRounding, 6)
                self.dpg.add_theme_style(self.dpg.mvStyleVar_FrameRounding, 6)
                self.dpg.add_theme_style(self.dpg.mvStyleVar_WindowPadding, 10, 10)
                self.dpg.add_theme_style(self.dpg.mvStyleVar_ItemSpacing, 8, 6)
                self.dpg.add_theme_style(self.dpg.mvStyleVar_FramePadding, 8, 6)
        return theme

    def is_open(self) -> bool:
        """Return True if the DearPyGui window is still open."""
        return self.dpg.is_dearpygui_running()

    def _update_frame_texture(self, frame: np.ndarray) -> None:
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame_rgb = frame_rgb.astype(np.float32) / 255.0
        h, w = frame_rgb.shape[:2]
        if (w, h) != self._frame_size:
            self._frame_size = (w, h)
            self._create_texture(w, h)
            self.dpg.configure_item(self._image_tag, width=w, height=h)
            self.dpg.configure_item(
                "video_window",
                width=w + 20,
                height=h + 20,
            )

        self.dpg.set_value(self._texture_tag, frame_rgb.ravel())

    def _update_perf_panel(
        self,
        *,
        perf_metrics: PerformanceMetrics | None,
        sys_stats: SystemStats | None,
        proc_stats: dict | None,
        camera_info: dict | None,
        detections_count: int | None,
        hardware_info: dict | None,
        power_info: dict | None,
        frame_size: tuple[int, int],
    ) -> None:
        if (
            perf_metrics is None
            or sys_stats is None
            or proc_stats is None
            or camera_info is None
        ):
            return

        w, h = frame_size
        self.dpg.set_value(
            self._perf_tags["resolution"],
            f"Resolution: {w}x{h}",
        )
        self._update_camera_info(camera_info, detections_count)
        self._update_hardware_info(hardware_info)
        self._update_perf_metrics(perf_metrics)
        self._update_system_stats(sys_stats)
        self._update_power_info(power_info)
        self._update_process_stats(proc_stats)
        self._update_plots(perf_metrics, sys_stats)

    def _update_camera_info(
        self,
        camera_info: dict,
        detections_count: int | None,
    ) -> None:
        backend_display = camera_info.get("backend", "unknown")
        self.dpg.set_value(
            self._perf_tags["capture"],
            f"Capture: {backend_display}",
        )
        self.dpg.set_value(
            self._perf_tags["backend"],
            f"Backend: {backend_display}",
        )
        pipeline = camera_info.get("pipeline", "")
        if pipeline:
            if len(pipeline) > 70:
                pipeline = pipeline[:67] + "..."
            self.dpg.set_value(
                self._perf_tags["pipeline"],
                f"Pipeline: {pipeline}",
            )
        if detections_count is not None:
            self.dpg.set_value(
                self._perf_tags["detections"],
                f"Detections: {detections_count}",
            )

    def _update_hardware_info(self, hardware_info: dict | None) -> None:
        if hardware_info is None:
            return
        cpu_model = hardware_info.get("cpu_model", "N/A")
        ram_total = hardware_info.get("ram_total_gb", 0.0)
        gpu_model = hardware_info.get("gpu_model", "N/A")
        vram_total = hardware_info.get("vram_total_gb", 0.0)
        self.dpg.set_value(
            self._perf_tags["cpu_model"],
            f"CPU: {cpu_model}",
        )
        self.dpg.set_value(
            self._perf_tags["ram_total"],
            f"RAM: {ram_total:.1f} GB",
        )
        if gpu_model and gpu_model != "N/A":
            self.dpg.set_value(
                self._perf_tags["gpu_model"],
                f"GPU: {gpu_model}",
            )
            self.dpg.set_value(
                self._perf_tags["vram_total"],
                f"VRAM: {vram_total:.1f} GB",
            )
        else:
            self.dpg.set_value(self._perf_tags["gpu_model"], "")
            self.dpg.set_value(self._perf_tags["vram_total"], "")

    def _update_perf_metrics(self, perf_metrics: PerformanceMetrics) -> None:
        self.dpg.set_value(
            self._perf_tags["camera_fps"],
            f"Camera Input: {perf_metrics.camera_fps:.1f} FPS",
        )
        self.dpg.set_value(
            self._perf_tags["inference_ms"],
            f"Inference Latency: {perf_metrics.inference_ms:.1f} ms/frame",
        )
        self.dpg.set_value(
            self._perf_tags["budget"],
            f"Frame Budget Used: {perf_metrics.frame_budget_percent:.1f}%",
        )
        headroom = 100 - perf_metrics.frame_budget_percent
        self.dpg.set_value(
            self._perf_tags["headroom"],
            "GPU Headroom: "
            f"{headroom:.0f}% (capacity: {perf_metrics.inference_capacity_fps:.0f} FPS)",
        )

    def _update_system_stats(self, sys_stats: SystemStats) -> None:
        self.dpg.set_value(
            self._perf_tags["sys_cpu"],
            f"System CPU: {sys_stats.cpu_percent:.1f}%",
        )
        self.dpg.set_value(
            self._perf_tags["sys_ram"],
            "System RAM: "
            f"{sys_stats.ram_used_gb:.1f}/{sys_stats.ram_total_gb:.1f} GB "
            f"({sys_stats.ram_percent:.1f}%)",
        )
        if sys_stats.gpu_name != "N/A":
            self.dpg.set_value(
                self._perf_tags["gpu"],
                "GPU Load: "
                f"{sys_stats.gpu_percent:.0f}% | Temp: {sys_stats.gpu_temp_celsius:.0f}C "
                f"| {sys_stats.gpu_power_watts:.0f}W",
            )
            self.dpg.set_value(
                self._perf_tags["vram"],
                "VRAM: "
                f"{sys_stats.gpu_memory_used_gb:.1f}/{sys_stats.gpu_memory_total_gb:.1f} GB "
                f"({sys_stats.gpu_memory_percent:.0f}%)",
            )
        else:
            self.dpg.set_value(self._perf_tags["gpu"], "")
            self.dpg.set_value(self._perf_tags["vram"], "")

    def _update_power_info(self, power_info: dict | None) -> None:
        if power_info is None:
            return
        system_power = power_info.get("system_power_watts", 0.0)
        cpu_power = power_info.get("cpu_power_watts", 0.0)
        gpu_power = power_info.get("gpu_power_watts", 0.0)
        energy_wh = power_info.get("energy_wh", 0.0)
        if system_power > 0.0:
            self.dpg.set_value(
                self._perf_tags["power"],
                f"Power: {system_power:.0f}W (CPU {cpu_power:.0f}W, GPU {gpu_power:.0f}W)",
            )
        elif gpu_power > 0.0:
            self.dpg.set_value(
                self._perf_tags["power"],
                f"Power: GPU {gpu_power:.0f}W",
            )
        else:
            self.dpg.set_value(self._perf_tags["power"], "")

        if energy_wh > 0.0:
            self.dpg.set_value(
                self._perf_tags["energy"],
                f"Energy: {energy_wh:.3f} Wh",
            )
        else:
            self.dpg.set_value(self._perf_tags["energy"], "")

    def _update_process_stats(self, proc_stats: dict) -> None:
        self.dpg.set_value(
            self._perf_tags["proc"],
            "CPU: "
            f"{proc_stats['cpu_percent']:.1f}% | RAM: {proc_stats['memory_mb']:.0f}MB "
            f"| Threads: {proc_stats['threads']}",
        )

    def _update_plots(
        self,
        perf_metrics: PerformanceMetrics,
        sys_stats: SystemStats,
    ) -> None:
        self._plot_index += 1
        self._plot_data["x"].append(self._plot_index)
        self._plot_data["camera_fps"].append(float(perf_metrics.camera_fps))
        self._plot_data["inference_ms"].append(float(perf_metrics.inference_ms))
        self._plot_data["sys_cpu"].append(float(sys_stats.cpu_percent))
        self._plot_data["sys_ram"].append(float(sys_stats.ram_percent))
        if sys_stats.gpu_name != "N/A":
            self._plot_data["sys_gpu"].append(float(sys_stats.gpu_percent))
        else:
            self._plot_data["sys_gpu"].append(0.0)

        x_values = list(self._plot_data["x"])
        self.dpg.set_value(
            self._plot_tags["perf_camera_fps"],
            [x_values, list(self._plot_data["camera_fps"])],
        )
        self.dpg.set_value(
            self._plot_tags["perf_inference_ms"],
            [x_values, list(self._plot_data["inference_ms"])],
        )
        self.dpg.set_value(
            self._plot_tags["sys_cpu"],
            [x_values, list(self._plot_data["sys_cpu"])],
        )
        self.dpg.set_value(
            self._plot_tags["sys_ram"],
            [x_values, list(self._plot_data["sys_ram"])],
        )
        self.dpg.set_value(
            self._plot_tags["sys_gpu"],
            [x_values, list(self._plot_data["sys_gpu"])],
        )
        self.dpg.fit_axis_data(self._plot_tags["perf_x"])
        self.dpg.fit_axis_data(self._plot_tags["perf_y"])
        self.dpg.fit_axis_data(self._plot_tags["sys_x"])
        self.dpg.fit_axis_data(self._plot_tags["sys_y"])

    def _update_detection_panel(
        self,
        *,
        detections_count: int | None,
        classification: dict | None,
    ) -> None:
        if detections_count is not None:
            self.dpg.set_value(
                self._det_tags["detections"],
                f"Detections: {detections_count}",
            )
        if classification is not None:
            class_label = classification.get("label", "unknown")
            class_score = classification.get("score", 0.0)
            self.dpg.set_value(
                self._det_tags["class"],
                f"Class: {class_label} ({class_score:.2f})",
            )

    def _update_log_panel(self, *, log_lines: list[str] | None) -> None:
        if log_lines is not None:
            self.dpg.set_value(self._log_tag, "\n".join(log_lines))

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
        """Render a frame and update UI panels."""
        if not self.dpg.is_dearpygui_running():
            return
        self._update_frame_texture(frame)
        frame_size = (self._frame_size[0], self._frame_size[1])

        self._update_perf_panel(
            perf_metrics=perf_metrics,
            sys_stats=sys_stats,
            proc_stats=proc_stats,
            camera_info=camera_info,
            detections_count=detections_count,
            hardware_info=hardware_info,
            power_info=power_info,
            frame_size=frame_size,
        )
        self._update_detection_panel(
            detections_count=detections_count,
            classification=classification,
        )
        self._update_log_panel(log_lines=log_lines)

        self.dpg.render_dearpygui_frame()

    def close(self) -> None:
        """Close the viewer and destroy the DearPyGui context."""
        if self.dpg.is_dearpygui_running():
            self.dpg.destroy_context()
