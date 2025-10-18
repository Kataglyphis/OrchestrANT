from flask import Flask, render_template, Response, stream_with_context
import time
from loguru import logger
import logging
import threading
import picamera2
import cv2
import queue
import numpy as np

# Set up logging to log both to console and a file
log_filename = "logs/catcam.log"  # Log file name
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s",
                    handlers=[
                        logging.StreamHandler(),  # Output to console
                        logging.FileHandler(log_filename, mode='a')  # Output to file
                    ])
logger.add(log_filename, rotation="1 MB",retention=10 ,compression="zip")

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching for static files

# Initialize the PiCamera2 camera object
def initialize_camera():
    global camera
    try:
        camera = picamera2.Picamera2()
        camera.configure("preview")
        camera.start()
        logger.info("Camera initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize camera: {e}")
        raise RuntimeError("Failed to initialize camera.")

initialize_camera()

class FrameCapture:
    def __init__(self):
        self.frame_queue = queue.Queue(maxsize=10)
        self.lock = threading.Lock()  # Lock for thread safety
        self.running = True
        # Start a background thread for continuous frame capture
        self.thread = threading.Thread(target=self.update, daemon=True)
        self.thread.start()

    def update(self):
        while self.running:
            try:
                # Capture a frame using picamera2
                frame = camera.capture_array()
                if frame is None or frame.size == 0:
                    logger.warning("Received empty frame, retrying...")
                    continue

                logger.debug(f"Frame captured successfully with size {frame.shape}")
                frame = cv2.rotate(frame, cv2.ROTATE_180)
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

                # Lock the queue before adding frames
                with self.lock:
                    if not self.frame_queue.full():
                        self.frame_queue.put(frame)
                        logger.debug(f"Frame added to queue. Queue size: {self.frame_queue.qsize()}")
                    else:
                        logger.warning("Frame queue full, discarding frame.")
            except Exception as e:
                logger.error(f"Error during frame capture: {e}")
                self.restart_camera()
            time.sleep(0.03)  # Adjust the capture frequency

    def get_frame(self):
        try:
            # Lock the queue before retrieving frames
            with self.lock:
                if not self.frame_queue.empty():
                    frame = self.frame_queue.get_nowait()
                    logger.debug("Frame retrieved from queue.")
                    return frame
                else:
                    logger.warning("Queue is empty, waiting for frames...")
                    return None
        except queue.Empty:
            logger.warning("Queue is empty, no frame available.")
            return self.get_fallback_frame()

    def stop(self):
        self.running = False
        self.thread.join()

    def restart_camera(self):
        logger.info("Restarting camera...")
        # Reinitialize the camera (if needed)
        initialize_camera()

    def get_fallback_frame(self):
        # Generate a black frame as fallback
        return np.zeros((480, 640, 3), dtype=np.uint8)  # Placeholder black image

# Create a global FrameCapture instance to start capturing frames
frame_capture = FrameCapture()

def gen_frames():
    logger.info("Starting video stream...")
    # Wait until the first frame is available
    while frame_capture.get_frame() is None:
        logger.debug("Waiting for the first frame...")
        time.sleep(0.1)

    while True:
        frame = frame_capture.get_frame()
        if frame is None:
            logger.warning("No frame available; skipping frame.")
            time.sleep(0.5)  # Adjust the wait time to sync better with the frame capture
            continue

        # Encode the frame as JPEG (using cv2 only for encoding)
        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 30])
        if not ret:
            logger.warning("Frame encoding failed; skipping frame...")
            continue
        frame_bytes = buffer.tobytes()

        # Yield the JPEG frame in the multipart MJPEG format
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n\r\n')

@app.route('/video_feed')
def video_feed():
    response = Response(stream_with_context(gen_frames()), mimetype='multipart/x-mixed-replace; boundary=frame')
    # Disable caching so the browser always loads the newest frame
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    try:
        # Run the Flask app with threading enabled and disable the reloader for stability
        app.run(host='0.0.0.0', port=5000, debug=True, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        logger.info("Shutting down video stream")
    finally:
        frame_capture.stop()
