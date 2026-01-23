"""Visualization module for plotting system metrics."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib.pyplot as plt
from loguru import logger


if TYPE_CHECKING:
    from collections.abc import Sequence

    from kataglyphispythoninference.monitoring.system import SystemMetrics


class MetricsPlotter:
    """Create visualizations for system monitoring metrics.

    Example:
        >>> from kataglyphispythoninference.monitoring import SystemMonitor
        >>> from kataglyphispythoninference.monitoring import MetricsPlotter
        >>> monitor = SystemMonitor()
        >>> monitor.start()
        >>> # ... collect metrics ...
        >>> monitor.stop()
        >>> plotter = MetricsPlotter(monitor.get_metrics())
        >>> plotter.plot_all()
        >>> plotter.save_figure("metrics.png")
    """

    def __init__(self, metrics: Sequence[SystemMetrics]) -> None:
        """Initialize the plotter with metrics data.

        Args:
            metrics: List of SystemMetrics to visualize
        """
        if not metrics:
            message = "No metrics provided for plotting"
            raise ValueError(message)

        self.metrics = list(metrics)
        self.fig = None
        self.axes = None

        # Check if GPU metrics are available
        self.has_gpu = any(m.gpu_utilization is not None for m in self.metrics)

        logger.info("Initialized plotter with {} samples", len(self.metrics))
        if self.has_gpu:
            logger.info("GPU metrics available")

    def _prepare_time_data(self) -> tuple[list[float], list[datetime]]:
        """Extract timestamps and convert to relative time."""
        timestamps = [m.timestamp for m in self.metrics]
        start_time = timestamps[0]
        relative_times = [t - start_time for t in timestamps]
        datetime_objects = [
            datetime.fromtimestamp(t, tz=datetime.UTC) for t in timestamps
        ]
        return relative_times, datetime_objects

    def prepare_time_data(self) -> tuple[list[float], list[datetime]]:
        """Public wrapper for preparing time-series data."""
        return self._prepare_time_data()

    def plot_cpu_memory(self, ax: plt.Axes | None = None) -> plt.Axes:
        """Plot CPU and memory usage over time.

        Args:
            ax: Matplotlib axes to plot on. If None, creates new figure.

        Returns:
            The axes object used for plotting
        """
        if ax is None:
            _fig, ax = plt.subplots(figsize=(12, 4))

        relative_times, _ = self._prepare_time_data()
        cpu_values = [m.cpu_percent for m in self.metrics]
        mem_values = [m.memory_percent for m in self.metrics]

        ax.plot(
            relative_times, cpu_values, label="CPU Usage", linewidth=2, color="#1f77b4"
        )
        ax.plot(
            relative_times,
            mem_values,
            label="Memory Usage",
            linewidth=2,
            color="#ff7f0e",
        )

        ax.set_xlabel("Time (seconds)", fontsize=11)
        ax.set_ylabel("Usage (%)", fontsize=11)
        ax.set_title("CPU and Memory Usage Over Time", fontsize=13, fontweight="bold")
        ax.legend(loc="upper right")
        ax.grid(visible=True, alpha=0.3)
        ax.set_ylim(0, 100)

        logger.debug("CPU and memory plot created")
        return ax

    def plot_gpu_utilization(self, ax: plt.Axes | None = None) -> plt.Axes | None:
        """Plot GPU utilization over time.

        Args:
            ax: Matplotlib axes to plot on. If None, creates new figure.

        Returns:
            The axes object used for plotting, or None if no GPU data
        """
        if not self.has_gpu:
            logger.warning("No GPU metrics available for plotting")
            return None

        if ax is None:
            _fig, ax = plt.subplots(figsize=(12, 4))

        relative_times, _ = self._prepare_time_data()
        gpu_values = [
            m.gpu_utilization if m.gpu_utilization is not None else 0
            for m in self.metrics
        ]

        ax.plot(
            relative_times, gpu_values, label="GPU Usage", linewidth=2, color="#2ca02c"
        )
        ax.fill_between(relative_times, gpu_values, alpha=0.3, color="#2ca02c")

        ax.set_xlabel("Time (seconds)", fontsize=11)
        ax.set_ylabel("Utilization (%)", fontsize=11)
        ax.set_title("GPU Utilization Over Time", fontsize=13, fontweight="bold")
        ax.legend(loc="upper right")
        ax.grid(visible=True, alpha=0.3)
        ax.set_ylim(0, 100)

        logger.debug("GPU utilization plot created")
        return ax

    def plot_gpu_memory(self, ax: plt.Axes | None = None) -> plt.Axes | None:
        """Plot GPU memory usage over time.

        Args:
            ax: Matplotlib axes to plot on. If None, creates new figure.

        Returns:
            The axes object used for plotting, or None if no GPU data
        """
        if not self.has_gpu:
            logger.warning("No GPU metrics available for plotting")
            return None

        if ax is None:
            _fig, ax = plt.subplots(figsize=(12, 4))

        relative_times, _ = self._prepare_time_data()
        gpu_mem_used = [
            m.gpu_memory_used_mb if m.gpu_memory_used_mb is not None else 0
            for m in self.metrics
        ]
        gpu_mem_total = self.metrics[0].gpu_memory_total_mb or 1

        ax.plot(
            relative_times,
            gpu_mem_used,
            label="GPU Memory Used",
            linewidth=2,
            color="#d62728",
        )
        ax.axhline(
            y=gpu_mem_total,
            color="gray",
            linestyle="--",
            label=f"Total: {gpu_mem_total:.0f}MB",
            alpha=0.7,
        )

        ax.set_xlabel("Time (seconds)", fontsize=11)
        ax.set_ylabel("Memory (MB)", fontsize=11)
        ax.set_title("GPU Memory Usage Over Time", fontsize=13, fontweight="bold")
        ax.legend(loc="upper right")
        ax.grid(visible=True, alpha=0.3)

        logger.debug("GPU memory plot created")
        return ax

    def plot_gpu_temperature(self, ax: plt.Axes | None = None) -> plt.Axes | None:
        """Plot GPU temperature over time.

        Args:
            ax: Matplotlib axes to plot on. If None, creates new figure.

        Returns:
            The axes object used for plotting, or None if no GPU data
        """
        if not self.has_gpu:
            logger.warning("No GPU metrics available for plotting")
            return None

        if ax is None:
            _fig, ax = plt.subplots(figsize=(12, 4))

        relative_times, _ = self._prepare_time_data()
        gpu_temps = [
            m.gpu_temperature if m.gpu_temperature is not None else 0
            for m in self.metrics
        ]

        ax.plot(
            relative_times,
            gpu_temps,
            label="GPU Temperature",
            linewidth=2,
            color="#ff4500",
        )
        ax.fill_between(relative_times, gpu_temps, alpha=0.3, color="#ff4500")

        ax.set_xlabel("Time (seconds)", fontsize=11)
        ax.set_ylabel("Temperature (°C)", fontsize=11)
        ax.set_title("GPU Temperature Over Time", fontsize=13, fontweight="bold")
        ax.legend(loc="upper right")
        ax.grid(visible=True, alpha=0.3)

        logger.debug("GPU temperature plot created")
        return ax

    def plot_all(self, figsize: tuple[int, int] = (14, 10)) -> plt.Figure:
        """Create a comprehensive dashboard with all available metrics.

        Args:
            figsize: Figure size as (width, height) tuple

        Returns:
            The created figure object
        """
        n_plots = 2 if not self.has_gpu else 5

        self.fig, self.axes = plt.subplots(n_plots, 1, figsize=figsize)
        self.fig.suptitle(
            "System Metrics Dashboard", fontsize=16, fontweight="bold", y=0.995
        )

        axes_list = self.axes

        # Plot CPU and Memory
        self.plot_cpu_memory(axes_list[0])

        # Plot memory in MB
        relative_times, _ = self._prepare_time_data()
        mem_used_mb = [m.memory_used_mb for m in self.metrics]
        mem_available_mb = [m.memory_available_mb for m in self.metrics]

        axes_list[1].plot(
            relative_times, mem_used_mb, label="Used", linewidth=2, color="#ff7f0e"
        )
        axes_list[1].plot(
            relative_times,
            mem_available_mb,
            label="Available",
            linewidth=2,
            color="#2ca02c",
        )
        axes_list[1].set_xlabel("Time (seconds)", fontsize=11)
        axes_list[1].set_ylabel("Memory (MB)", fontsize=11)
        axes_list[1].set_title(
            "Memory Usage (Absolute)", fontsize=13, fontweight="bold"
        )
        axes_list[1].legend(loc="upper right")
        axes_list[1].grid(visible=True, alpha=0.3)

        # GPU plots if available
        if self.has_gpu:
            self.plot_gpu_utilization(axes_list[2])
            self.plot_gpu_memory(axes_list[3])
            self.plot_gpu_temperature(axes_list[4])

        plt.tight_layout()
        logger.info("Complete metrics dashboard created")
        return self.fig

    def save_figure(self, filepath: str, dpi: int = 150) -> None:
        """Save the current figure to a file.

        Args:
            filepath: Path where to save the figure
            dpi: Resolution in dots per inch
        """
        if self.fig is None:
            logger.error(
                "No figure to save. Call plot_all() or other plot methods first."
            )
            return

        output_path = Path(filepath)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        self.fig.savefig(filepath, dpi=dpi, bbox_inches="tight")
        logger.info("Figure saved to: {}", filepath)

    def show(self) -> None:
        """Display the plot interactively."""
        if self.fig is None:
            logger.error(
                "No figure to show. Call plot_all() or other plot methods first."
            )
            return

        plt.show()
        logger.debug("Displaying plot interactively")


def quick_plot(
    metrics: list[SystemMetrics],
    output_path: str | None = None,
    *,
    show: bool = True,
) -> None:
    """Convenience function to quickly plot metrics.

    Args:
        metrics: List of SystemMetrics to plot
        output_path: Optional path to save the figure
        show: Whether to display the plot interactively
    """
    plotter = MetricsPlotter(metrics)
    plotter.plot_all()

    if output_path:
        plotter.save_figure(output_path)

    if show:
        plotter.show()
