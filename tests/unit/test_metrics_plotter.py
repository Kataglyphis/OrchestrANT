"""Unit tests for metrics plotting functionality."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from kataglyphispythoninference.monitoring import (
    MetricsPlotter,
    SystemMetrics,
    quick_plot,
)


def create_sample_metrics(
    n: int = 10,
    *,
    with_gpu: bool = False,
) -> list[SystemMetrics]:
    """Helper to create sample metrics for testing."""
    metrics = []
    base_time = time.time()

    for i in range(n):
        m = SystemMetrics(
            timestamp=base_time + i * 0.5,
            cpu_percent=50.0 + i * 2,
            memory_percent=60.0 + i,
            memory_used_mb=8000.0 + i * 100,
            memory_available_mb=8000.0 - i * 100,
        )

        if with_gpu:
            m.gpu_utilization = 70.0 + i
            m.gpu_memory_used_mb = 4000.0 + i * 50
            m.gpu_memory_total_mb = 8000.0
            m.gpu_temperature = 60.0 + i * 0.5

        metrics.append(m)

    return metrics


class TestMetricsPlotter:
    """Tests for MetricsPlotter class."""

    def test_initialization_empty_metrics(self) -> None:
        """Test that initialization with empty metrics raises error."""
        with pytest.raises(ValueError, match="No metrics provided"):
            MetricsPlotter([])

    def test_initialization_with_metrics(self) -> None:
        """Test successful initialization."""
        metrics = create_sample_metrics(n=5)
        plotter = MetricsPlotter(metrics)

        assert len(plotter.metrics) == 5
        assert plotter.has_gpu is False
        assert plotter.fig is None

    def test_initialization_with_gpu_metrics(self) -> None:
        """Test initialization with GPU metrics."""
        metrics = create_sample_metrics(n=5, with_gpu=True)
        plotter = MetricsPlotter(metrics)

        assert plotter.has_gpu is True

    def test_prepare_time_data(self) -> None:
        """Test time data preparation."""
        metrics = create_sample_metrics(n=5)
        plotter = MetricsPlotter(metrics)

        relative_times, datetime_objects = plotter.prepare_time_data()

        assert len(relative_times) == 5
        assert len(datetime_objects) == 5
        assert relative_times[0] == 0.0  # First should be 0
        assert relative_times[-1] > 0  # Last should be positive

    @patch("matplotlib.pyplot.subplots")
    def test_plot_cpu_memory(self, mock_subplots: MagicMock) -> None:
        """Test CPU and memory plotting."""
        metrics = create_sample_metrics(n=10)
        plotter = MetricsPlotter(metrics)

        mock_fig = MagicMock()
        mock_ax = MagicMock()
        mock_subplots.return_value = (mock_fig, mock_ax)

        plotter.plot_cpu_memory()

        # Verify plot was called
        assert mock_ax.plot.called
        assert mock_ax.set_xlabel.called
        assert mock_ax.set_ylabel.called
        assert mock_ax.set_title.called

    def test_plot_gpu_without_gpu_data(self) -> None:
        """Test GPU plotting without GPU data."""
        metrics = create_sample_metrics(n=5, with_gpu=False)
        plotter = MetricsPlotter(metrics)

        result = plotter.plot_gpu_utilization()
        assert result is None

        result = plotter.plot_gpu_memory()
        assert result is None

        result = plotter.plot_gpu_temperature()
        assert result is None

    @patch("matplotlib.pyplot.subplots")
    def test_plot_gpu_with_gpu_data(self, mock_subplots: MagicMock) -> None:
        """Test GPU plotting with GPU data."""
        metrics = create_sample_metrics(n=10, with_gpu=True)
        plotter = MetricsPlotter(metrics)

        mock_fig = MagicMock()
        mock_ax = MagicMock()
        mock_subplots.return_value = (mock_fig, mock_ax)

        # Test GPU utilization plot
        plotter.plot_gpu_utilization()
        assert mock_ax.plot.called

        # Test GPU memory plot
        plotter.plot_gpu_memory()
        assert mock_ax.plot.called

        # Test GPU temperature plot
        plotter.plot_gpu_temperature()
        assert mock_ax.plot.called

    @patch("matplotlib.pyplot.subplots")
    def test_plot_all_without_gpu(self, mock_subplots: MagicMock) -> None:
        """Test plotting all metrics without GPU."""
        metrics = create_sample_metrics(n=10, with_gpu=False)
        plotter = MetricsPlotter(metrics)

        mock_fig = MagicMock()
        mock_axes = [MagicMock(), MagicMock()]
        mock_subplots.return_value = (mock_fig, mock_axes)

        plotter.plot_all()

        assert plotter.fig is not None
        assert plotter.axes is not None

    @patch("matplotlib.pyplot.subplots")
    def test_plot_all_with_gpu(self, mock_subplots: MagicMock) -> None:
        """Test plotting all metrics with GPU."""
        metrics = create_sample_metrics(n=10, with_gpu=True)
        plotter = MetricsPlotter(metrics)

        mock_fig = MagicMock()
        mock_axes = [MagicMock() for _ in range(5)]
        mock_subplots.return_value = (mock_fig, mock_axes)

        plotter.plot_all()

        assert plotter.fig is not None

    @patch("matplotlib.pyplot.subplots")
    @patch("pathlib.Path.mkdir")
    def test_save_figure(
        self,
        mock_mkdir: MagicMock,
        mock_subplots: MagicMock,
    ) -> None:
        """Test saving figure to file."""
        metrics = create_sample_metrics(n=5)
        plotter = MetricsPlotter(metrics)

        mock_fig = MagicMock()
        mock_axes = [MagicMock(), MagicMock()]
        mock_subplots.return_value = (mock_fig, mock_axes)

        plotter.plot_all()
        plotter.save_figure("test_output.png")

        assert mock_mkdir.called
        assert mock_fig.savefig.called

    def test_save_figure_without_plot(self) -> None:
        """Test saving figure without creating plot first."""
        metrics = create_sample_metrics(n=5)
        plotter = MetricsPlotter(metrics)

        # Should handle gracefully
        plotter.save_figure("test_output.png")

    @patch("matplotlib.pyplot.show")
    @patch("matplotlib.pyplot.subplots")
    def test_show(self, mock_subplots: MagicMock, mock_show: MagicMock) -> None:
        """Test showing plot interactively."""
        metrics = create_sample_metrics(n=5)
        plotter = MetricsPlotter(metrics)

        mock_fig = MagicMock()
        mock_axes = [MagicMock(), MagicMock()]
        mock_subplots.return_value = (mock_fig, mock_axes)

        plotter.plot_all()
        plotter.show()

        assert mock_show.called

    @patch("matplotlib.pyplot.show")
    @patch("matplotlib.pyplot.subplots")
    def test_quick_plot(
        self,
        mock_subplots: MagicMock,
        mock_show: MagicMock,
    ) -> None:
        """Test quick_plot convenience function."""
        metrics = create_sample_metrics(n=10)

        mock_fig = MagicMock()
        mock_axes = [MagicMock(), MagicMock()]
        mock_subplots.return_value = (mock_fig, mock_axes)

        quick_plot(metrics, output_path=None, show=False)

        assert mock_subplots.called
        assert not mock_show.called
