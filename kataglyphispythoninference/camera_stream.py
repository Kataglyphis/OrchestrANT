"""Flask app for streaming camera frames over HTTP."""

from __future__ import annotations

import os

from flask import Flask, Response, render_template, stream_with_context
from loguru import logger

from kataglyphispythoninference.camera_capture import FrameCapture
from kataglyphispythoninference.logging_config import setup_logging
from kataglyphispythoninference.streaming import gen_frames


setup_logging()

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # Disable caching for static files

frame_capture = FrameCapture()


@app.route("/video_feed")
def video_feed() -> Response:
    """Return multipart MJPEG stream of camera frames."""
    response = Response(
        stream_with_context(gen_frames(frame_capture)),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )
    # Disable caching so the browser always loads the newest frame
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/")
def index() -> str:
    """Render the streaming index page."""
    return render_template("index.html")


if __name__ == "__main__":
    try:
        # Run the Flask app with threading enabled and disable the reloader for stability
        host = os.getenv("KATAGLYPHIS_STREAM_HOST", "127.0.0.1")
        debug = os.getenv("KATAGLYPHIS_STREAM_DEBUG", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        app.run(
            host=host,
            port=5000,
            debug=debug,
            threaded=True,
            use_reloader=False,
        )
    except KeyboardInterrupt:
        logger.info("Shutting down video stream")
    finally:
        frame_capture.stop()
