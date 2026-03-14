"""Tests for pipeline data structures."""

from __future__ import annotations

import pytest

from orchestr_ant_ion.pipeline.types import (
    DEVICE_INDEX_MIN,
    FPS_MAX,
    FPS_MIN,
    RESOLUTION_MAX,
    RESOLUTION_MIN,
    CameraConfig,
    CaptureBackend,
)


class TestCameraConfig:
    """Tests for CameraConfig validation."""

    def test_valid_config(self) -> None:
        """Test creating a valid camera configuration."""
        config = CameraConfig(
            device_index=0,
            width=1920,
            height=1080,
            fps=30,
            backend=CaptureBackend.OPENCV,
        )
        assert config.device_index == 0
        assert config.width == 1920
        assert config.height == 1080
        assert config.fps == 30
        assert config.backend == CaptureBackend.OPENCV

    def test_default_config(self) -> None:
        """Test default values are valid."""
        config = CameraConfig()
        assert config.device_index == 0
        assert config.backend == CaptureBackend.OPENCV

    def test_invalid_device_index(self) -> None:
        """Test validation rejects negative device index."""
        with pytest.raises(ValueError, match="device_index"):
            CameraConfig(device_index=-1)

    def test_invalid_width_too_small(self) -> None:
        """Test validation rejects width below minimum."""
        with pytest.raises(ValueError, match="width"):
            CameraConfig(width=RESOLUTION_MIN - 1)

    def test_invalid_width_too_large(self) -> None:
        """Test validation rejects width above maximum."""
        with pytest.raises(ValueError, match="width"):
            CameraConfig(width=RESOLUTION_MAX + 1)

    def test_invalid_height_too_small(self) -> None:
        """Test validation rejects height below minimum."""
        with pytest.raises(ValueError, match="height"):
            CameraConfig(height=RESOLUTION_MIN - 1)

    def test_invalid_height_too_large(self) -> None:
        """Test validation rejects height above maximum."""
        with pytest.raises(ValueError, match="height"):
            CameraConfig(height=RESOLUTION_MAX + 1)

    def test_invalid_fps_too_low(self) -> None:
        """Test validation rejects FPS below minimum."""
        with pytest.raises(ValueError, match="fps"):
            CameraConfig(fps=FPS_MIN - 1)

    def test_invalid_fps_too_high(self) -> None:
        """Test validation rejects FPS above maximum."""
        with pytest.raises(ValueError, match="fps"):
            CameraConfig(fps=FPS_MAX + 1)

    def test_boundary_values(self) -> None:
        """Test boundary values are accepted."""
        config_min = CameraConfig(
            width=RESOLUTION_MIN,
            height=RESOLUTION_MIN,
            fps=FPS_MIN,
        )
        assert config_min.width == RESOLUTION_MIN
        assert config_min.height == RESOLUTION_MIN
        assert config_min.fps == FPS_MIN

        config_max = CameraConfig(
            width=RESOLUTION_MAX,
            height=RESOLUTION_MAX,
            fps=FPS_MAX,
        )
        assert config_max.width == RESOLUTION_MAX
        assert config_max.height == RESOLUTION_MAX
        assert config_max.fps == FPS_MAX
