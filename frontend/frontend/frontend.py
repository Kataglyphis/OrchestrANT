"""The OrchestrANT web frontend.

    cd frontend
    reflex run

The benchmark dashboard is the first page (``frontend.frontend.benchmarks``);
importing it here registers it on the app.
"""

from __future__ import annotations

import reflex as rx

from frontend import benchmarks  # noqa: F401  (import registers the page)

app = rx.App(theme=rx.theme(accent_color="violet"))
