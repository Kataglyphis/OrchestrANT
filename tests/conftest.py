"""Shared pytest setup for every test."""

from __future__ import annotations

import os


# TkAgg crashes headless Windows (0xC0000005); an env var for the matplotlib-less viewer job, here as tests/integration imports matplotlib first.
os.environ["MPLBACKEND"] = "Agg"
