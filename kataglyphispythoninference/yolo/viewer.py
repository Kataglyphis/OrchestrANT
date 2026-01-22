from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from kataglyphispythoninference.yolo.types import PerformanceMetrics, SystemStats


class DearPyGuiViewer:
    """Minimal DearPyGui viewer for rendering frames as a texture."""

    def __init__(self, width: int, height: int, title: str = "YOLO Monitor") -> None:
        import dearpygui.dearpygui as dpg

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
            "proc": "perf_proc",
        }

        self._det_tags = {
            "detections": "det_count",
            "class": "det_class",
        }

        self._log_tag = "log_output"

        self.dpg.create_context()
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
            self.dpg.add_text("YOLO Monitor", tag=self._perf_tags["title"])
            self.dpg.add_text("", tag=self._perf_tags["resolution"])
            self.dpg.add_text("", tag=self._perf_tags["capture"])
            self.dpg.add_text("", tag=self._perf_tags["backend"])
            self.dpg.add_text("", tag=self._perf_tags["pipeline"])
            self.dpg.add_text("", tag=self._perf_tags["cpu_model"])
            self.dpg.add_text("", tag=self._perf_tags["ram_total"])
            self.dpg.add_text("", tag=self._perf_tags["gpu_model"])
            self.dpg.add_text("", tag=self._perf_tags["vram_total"])
            self.dpg.add_text("", tag=self._perf_tags["detections"])
            self.dpg.add_separator()
            self.dpg.add_text("Performance")
            self.dpg.add_text("", tag=self._perf_tags["camera_fps"])
            self.dpg.add_text("", tag=self._perf_tags["inference_ms"])
            self.dpg.add_text("", tag=self._perf_tags["budget"])
            self.dpg.add_text("", tag=self._perf_tags["headroom"])
            self.dpg.add_separator()
            self.dpg.add_text("System")
            self.dpg.add_text("", tag=self._perf_tags["sys_cpu"])
            self.dpg.add_text("", tag=self._perf_tags["sys_ram"])
            self.dpg.add_text("", tag=self._perf_tags["gpu"])
            self.dpg.add_text("", tag=self._perf_tags["vram"])
            self.dpg.add_separator()
            self.dpg.add_text("Process")
            self.dpg.add_text("", tag=self._perf_tags["proc"])

        with self.dpg.window(
            label="Detections",
            tag="det_window",
            pos=(self.width + 30, 420),
            width=320,
            height=180,
        ):
            self.dpg.add_text("", tag=self._det_tags["detections"])
            self.dpg.add_text("", tag=self._det_tags["class"])

        with self.dpg.window(
            label="Logs",
            tag="log_window",
            pos=(self.width + 30, 610),
            width=320,
            height=260,
        ):
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

    def is_open(self) -> bool:
        return self.dpg.is_dearpygui_running()

    def render(
        self,
        frame: np.ndarray,
        perf_metrics: Optional[PerformanceMetrics] = None,
        sys_stats: Optional[SystemStats] = None,
        proc_stats: Optional[dict] = None,
        camera_info: Optional[dict] = None,
        detections_count: Optional[int] = None,
        classification: Optional[dict] = None,
        log_lines: Optional[list[str]] = None,
        hardware_info: Optional[dict] = None,
    ) -> None:
        if not self.dpg.is_dearpygui_running():
            return

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

        if (
            perf_metrics is not None
            and sys_stats is not None
            and proc_stats is not None
        ):
            self.dpg.set_value(
                self._perf_tags["resolution"],
                f"Resolution: {w}x{h}",
            )
            if camera_info is not None:
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

            if hardware_info is not None:
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

            self.dpg.set_value(
                self._perf_tags["proc"],
                "CPU: "
                f"{proc_stats['cpu_percent']:.1f}% | RAM: {proc_stats['memory_mb']:.0f}MB "
                f"| Threads: {proc_stats['threads']}",
            )

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

        if log_lines is not None:
            self.dpg.set_value(self._log_tag, "\n".join(log_lines))

        self.dpg.render_dearpygui_frame()

    def close(self) -> None:
        if self.dpg.is_dearpygui_running():
            self.dpg.destroy_context()
