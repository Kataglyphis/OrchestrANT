import queue
import threading
import time

import cv2
import numpy as np
import picamera2
from loguru import logger


def initialize_camera() -> picamera2.Picamera2:
    camera = picamera2.Picamera2()
    camera.configure("preview")
    camera.start()
    logger.info("Camera initialized successfully.")
    return camera


class FrameCapture:
    def __init__(self, queue_size: int = 10, capture_interval: float = 0.03):
        self.camera = initialize_camera()
        self.capture_interval = capture_interval
        self.frame_queue = queue.Queue(maxsize=queue_size)
        self.lock = threading.Lock()
        self.running = True
        self.thread = threading.Thread(target=self.update, daemon=True)
        self.thread.start()

    def update(self) -> None:
        while self.running:
            try:
                frame = self.camera.capture_array()
                if frame is None or frame.size == 0:
                    logger.warning("Received empty frame, retrying...")
                    continue

                logger.debug("Frame captured successfully with size %s", frame.shape)
                frame = cv2.rotate(frame, cv2.ROTATE_180)
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

                with self.lock:
                    if not self.frame_queue.full():
                        self.frame_queue.put(frame)
                        logger.debug(
                            "Frame added to queue. Queue size: %s",
                            self.frame_queue.qsize(),
                        )
                    else:
                        logger.warning("Frame queue full, discarding frame.")
            except Exception as exc:
                logger.error("Error during frame capture: %s", exc)
                self.restart_camera()
            time.sleep(self.capture_interval)

    def get_frame(self):
        with self.lock:
            if not self.frame_queue.empty():
                frame = self.frame_queue.get_nowait()
                logger.debug("Frame retrieved from queue.")
                return frame

        logger.warning("Queue is empty, waiting for frames...")
        return None

    def stop(self) -> None:
        self.running = False
        self.thread.join()

    def restart_camera(self) -> None:
        logger.info("Restarting camera...")
        self.camera = initialize_camera()

    @staticmethod
    def get_fallback_frame() -> np.ndarray:
        return np.zeros((480, 640, 3), dtype=np.uint8)
