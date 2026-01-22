"""Unit tests for system monitoring functionality."""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from kataglyphispythoninference.system_monitor import SystemMonitor, SystemMetrics


class TestSystemMetrics:
    """Tests for SystemMetrics dataclass."""

    def test_create_metrics_basic(self):
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

    def test_create_metrics_with_gpu(self):
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

    @patch("kataglyphispythoninference.system_monitor.psutil")
    def test_monitor_initialization(self, mock_psutil):
        """Test monitor initialization."""
        monitor = SystemMonitor(interval=1.0)

        assert monitor.interval == 1.0
        assert monitor.metrics == []
        assert not monitor._monitoring

    @patch("kataglyphispythoninference.system_monitor.psutil")
    def test_start_stop_monitoring(self, mock_psutil):
        """Test starting and stopping monitoring."""
        monitor = SystemMonitor()

        monitor.start()
        assert monitor._monitoring is True
        assert monitor.metrics == []

        monitor.stop()
        assert monitor._monitoring is False

    @patch("kataglyphispythoninference.system_monitor.psutil")
    def test_collect_metrics(self, mock_psutil):
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

    @patch("kataglyphispythoninference.system_monitor.psutil")
    def test_multiple_records(self, mock_psutil):
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

    @patch("kataglyphispythoninference.system_monitor.psutil")
    def test_get_metrics(self, mock_psutil):
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

    @patch("kataglyphispythoninference.system_monitor.psutil")
    def test_print_summary_no_metrics(self, mock_psutil, caplog):
        """Test print summary with no metrics."""
        monitor = SystemMonitor()
        monitor.print_summary()
        # Should log a warning
        assert len(monitor.metrics) == 0

    @patch("kataglyphispythoninference.system_monitor.psutil")
    def test_print_summary_with_metrics(self, mock_psutil):
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

    @patch("kataglyphispythoninference.system_monitor.NVIDIA_AVAILABLE", False)
    @patch("kataglyphispythoninference.system_monitor.psutil")
    def test_no_gpu_available(self, mock_psutil):
        """Test monitor when GPU is not available."""
        monitor = SystemMonitor()
        assert monitor._gpu_handle is None
