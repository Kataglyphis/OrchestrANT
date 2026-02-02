"""Unit tests for system monitoring functionality."""

from __future__ import annotations

import time
from unittest.mock import Mock, patch

from orchestr_ant_ion.monitoring import SystemMetrics, SystemMonitor


class TestSystemMetrics:
    """Tests for SystemMetrics dataclass."""

    def test_create_metrics_basic(self) -> None:
        """Test creating basic metrics without GPU."""
        metrics = SystemMetrics(
            timestamp=time.time(),
            cpu_percent=50.0,
            memory_percent=60.0,
            memory_used_mb=8000.0,
            memory_available_mb=8000.0,
        )

        assert metrics.cpu_percent == 50.0
        assert metrics.memory_percent == 60.0
        assert metrics.gpu_utilization is None

    def test_create_metrics_with_gpu(self) -> None:
        """Test creating metrics with GPU data."""
        metrics = SystemMetrics(
            timestamp=time.time(),
            cpu_percent=50.0,
            memory_percent=60.0,
            memory_used_mb=8000.0,
            memory_available_mb=8000.0,
            gpu_utilization=75.0,
            gpu_memory_used_mb=4000.0,
            gpu_memory_total_mb=8000.0,
            gpu_temperature=65.0,
        )

        assert metrics.gpu_utilization == 75.0
        assert metrics.gpu_memory_used_mb == 4000.0
        assert metrics.gpu_temperature == 65.0


class TestSystemMonitor:
    """Tests for SystemMonitor class."""

    @patch("orchestr_ant_ion.monitoring.system.psutil")
    def test_monitor_initialization(self, mock_psutil: Mock) -> None:
        """Test monitor initialization."""
        assert mock_psutil is not None
        monitor = SystemMonitor(interval=1.0)

        assert monitor.interval == 1.0
        assert monitor.metrics == []
        assert monitor.is_monitoring is False

    @patch("orchestr_ant_ion.monitoring.system.psutil")
    def test_start_stop_monitoring(self, mock_psutil: Mock) -> None:
        """Test starting and stopping monitoring."""
        assert mock_psutil is not None
        monitor = SystemMonitor()

        monitor.start()
        assert monitor.is_monitoring is True
        assert monitor.metrics == []

        monitor.stop()
        assert monitor.is_monitoring is False

    @patch("orchestr_ant_ion.monitoring.system.psutil")
    def test_collect_metrics(self, mock_psutil: Mock) -> None:
        """Test collecting metrics."""
        # Mock psutil
        mock_psutil.cpu_percent.return_value = 45.0
        mock_memory = Mock()
        mock_memory.percent = 55.0
        mock_memory.used = 8 * 1024**3  # 8 GB
        mock_memory.available = 8 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_memory

        monitor = SystemMonitor()
        monitor.start()
        monitor.record()

        assert len(monitor.metrics) == 1
        assert monitor.metrics[0].cpu_percent == 45.0
        assert monitor.metrics[0].memory_percent == 55.0

    @patch("orchestr_ant_ion.monitoring.system.psutil")
    def test_multiple_records(self, mock_psutil: Mock) -> None:
        """Test recording multiple metrics."""
        # Mock psutil
        mock_psutil.cpu_percent.return_value = 50.0
        mock_memory = Mock()
        mock_memory.percent = 60.0
        mock_memory.used = 8 * 1024**3
        mock_memory.available = 8 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_memory

        monitor = SystemMonitor()
        monitor.start()

        for _ in range(5):
            monitor.record()
            time.sleep(0.01)

        monitor.stop()

        assert len(monitor.metrics) == 5
        # Verify timestamps are increasing
        for i in range(1, 5):
            assert monitor.metrics[i].timestamp > monitor.metrics[i - 1].timestamp

    @patch("orchestr_ant_ion.monitoring.system.psutil")
    def test_get_metrics(self, mock_psutil: Mock) -> None:
        """Test retrieving metrics."""
        mock_psutil.cpu_percent.return_value = 50.0
        mock_memory = Mock()
        mock_memory.percent = 60.0
        mock_memory.used = 8 * 1024**3
        mock_memory.available = 8 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_memory

        monitor = SystemMonitor()
        monitor.start()
        monitor.record()
        monitor.stop()

        metrics = monitor.get_metrics()
        assert len(metrics) == 1
        assert isinstance(metrics[0], SystemMetrics)

    @patch("orchestr_ant_ion.monitoring.system.psutil")
    def test_print_summary_no_metrics(
        self,
        mock_psutil: Mock,
        caplog: object,
    ) -> None:
        """Test print summary with no metrics."""
        assert mock_psutil is not None
        assert caplog is not None
        monitor = SystemMonitor()
        monitor.print_summary()
        # Should log a warning
        assert len(monitor.metrics) == 0

    @patch("orchestr_ant_ion.monitoring.system.psutil")
    def test_print_summary_with_metrics(self, mock_psutil: Mock) -> None:
        """Test print summary with metrics."""
        mock_psutil.cpu_percent.return_value = 50.0
        mock_memory = Mock()
        mock_memory.percent = 60.0
        mock_memory.used = 8 * 1024**3
        mock_memory.available = 8 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_memory

        monitor = SystemMonitor()
        monitor.start()

        for _ in range(3):
            monitor.record()
            time.sleep(0.01)

        monitor.stop()

        # Should not raise any exceptions
        monitor.print_summary()

    @patch("orchestr_ant_ion.monitoring.system.NVIDIA_AVAILABLE", new=False)
    @patch("orchestr_ant_ion.monitoring.system.psutil")
    def test_no_gpu_available(self, mock_psutil: Mock) -> None:
        """Test monitor when GPU is not available."""
        assert mock_psutil is not None
        monitor = SystemMonitor()
        assert monitor.gpu_handle is None

