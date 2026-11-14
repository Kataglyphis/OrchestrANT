"""A camera that plays one picture: the monitor's real loop with no hardware, for tests and the weekly language check."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np


class StillCamera:
    """Plays `frame` up to `frames` times, then ends the run as Ctrl+C does; `until` can end it earlier."""

    def __init__(
        self,
        frame: np.ndarray,
        *,
        frames: int,
        until: Callable[[], bool] | None = None,
    ) -> None:
        """Count no read yet."""
        self.frame = frame
        self.frames = frames
        self.until = until
        self.reads = 0
        self.released = False

    def __call__(self, _config: object) -> StillCamera:
        """Stand in for the CameraCapture class, which the monitor builds from its camera config."""
        return self

    def open(self) -> bool:
        """Open nothing."""
        return True

    def get_info(self) -> dict[str, object]:
        """Name the stand-in and the picture's size."""
        height, width = self.frame.shape[:2]
        return {"backend": "still", "width": width, "height": height}

    def read(self) -> tuple[bool, np.ndarray]:
        """A copy of the picture, as a camera hands a fresh buffer; past the last frame the run ends."""
        if self.reads >= self.frames or (self.until is not None and self.until()):
            raise KeyboardInterrupt
        self.reads += 1
        return True, self.frame.copy()

    def release(self) -> None:
        """Note that the monitor let go of it."""
        self.released = True
