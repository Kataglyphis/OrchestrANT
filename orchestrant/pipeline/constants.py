"""Configuration constants for the monitoring pipeline."""

from __future__ import annotations


GST_DEFAULT_TIMEOUT_SECONDS: float = 120.0
GST_PROCESS_STARTUP_DELAY: float = 1.0
GST_INSPECT_TIMEOUT: float = 10.0
GST_PROCESS_WAIT_TIMEOUT: float = 2.0
GST_FRAME_QUEUE_TIMEOUT: float = 5.0
GST_FALLBACK_WIDTH = 1280
GST_FALLBACK_HEIGHT = 720

TRACKER_DEFAULT_MAX_AGE_SECONDS: float = 0.75
TRACKER_DEFAULT_MAX_MATCH_DISTANCE: float = 0.08
TRACKER_DEFAULT_MAX_TRAIL_POINTS: int = 40

# The monitor's --conf and the decoder's default; ModelANT's contract cases decode at it too.
DEFAULT_SCORE_THRESHOLD: float = 0.25

ENERGY_WH_INITIAL: float = 0.0
POWER_WATTS_INITIAL: float = 0.0

LOG_INTERVAL_SECONDS: float = 2.0
DEBUG_LOG_INTERVAL_SECONDS: float = 3.0
RESOURCE_LOG_INTERVAL_SECONDS: float = 5.0
STATS_UPDATE_INTERVAL_FRAMES: int = 10
CAMERA_LOSS_TIMEOUT_SECONDS: float = 5.0
CAMERA_RETRY_DELAY_SECONDS: float = 0.05


WINDOWS_GSTREAMER_PATHS = [
    r"C:\Program Files\gstreamer\1.0\msvc_x86_64",
    r"C:\gstreamer\1.0\msvc_x86_64",
    r"C:\Program Files (x86)\gstreamer\1.0\msvc_x86_64",
    r"C:\gstreamer\1.0\x86_64",
]
