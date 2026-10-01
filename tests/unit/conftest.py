"""Shared pytest setup for the unit tests."""

from __future__ import annotations

import matplotlib as mpl


# The Windows CI container has Tk but no display, and TkAgg crashes there in wm_iconphoto (0xC0000005).
mpl.use("Agg")
