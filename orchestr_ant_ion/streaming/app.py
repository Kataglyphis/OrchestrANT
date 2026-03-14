"""Flask app for streaming camera frames over HTTP."""

from __future__ import annotations

import os
import secrets
import warnings

from flask import Flask, Response, render_template, stream_with_context
from loguru import logger

from orchestr_ant_ion.logging_config import setup_logging
from orchestr_ant_ion.streaming.capture import FrameCapture
from orchestr_ant_ion.streaming.generator import gen_frames


def create_app(frame_capture: FrameCapture | None = None) -> Flask:
    """Create and configure the streaming Flask app."""
    setup_logging()

    app = Flask(__name__)
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    secret_key = os.getenv("KATAGLYPHIS_SECRET_KEY")
    if secret_key:
        app.config["SECRET_KEY"] = secret_key
    else:
        generated_key = secrets.token_hex(32)
        app.config["SECRET_KEY"] = generated_key
        warnings.warn(
            "KATAGLYPHIS_SECRET_KEY environment variable not set. "
            "Using a randomly generated secret key. Sessions will be invalidated "
            "on restart. Set KATAGLYPHIS_SECRET_KEY for production deployments.",
            UserWarning,
            stacklevel=2,
        )
        logger.warning(
            "KATAGLYPHIS_SECRET_KEY not set - using ephemeral secret key. "
            "Sessions will not persist across restarts."
        )

    capture = frame_capture or FrameCapture()

    @app.route("/video_feed")
    def video_feed() -> Response:
        """Return multipart MJPEG stream of camera frames."""
        response = Response(
            stream_with_context(gen_frames(capture)),
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

    app.extensions["frame_capture"] = capture
    return app


def _get_app() -> Flask:
    """Lazily create the module-level app on first access."""
    if not hasattr(_get_app, "_instance"):
        _get_app._instance = create_app()  # type: ignore[attr-defined]  # noqa: SLF001
    return _get_app._instance  # type: ignore[attr-defined]  # noqa: SLF001


def run() -> None:
    """Run the streaming Flask app."""
    app = _get_app()
    capture = app.extensions.get("frame_capture")
    try:
        host = os.getenv("KATAGLYPHIS_STREAM_HOST", "127.0.0.1")
        app.run(
            host=host,
            port=5000,
            debug=False,
            threaded=True,
            use_reloader=False,
        )
    except KeyboardInterrupt:
        logger.info("Shutting down video stream")
    finally:
        if capture is not None:
            capture.stop()


if __name__ == "__main__":
    run()
