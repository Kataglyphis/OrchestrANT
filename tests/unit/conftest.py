"""Shared pytest setup for the unit tests."""

from __future__ import annotations

import os


# TkAgg crashes in the display-less Windows CI container (0xC0000005); an env var, as the frontend suite lacks matplotlib.
os.environ["MPLBACKEND"] = "Agg"
