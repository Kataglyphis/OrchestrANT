"""Sphinx configuration: the family theme's setup_theme(), as the hub's docs/conf.py calls it."""

from pathlib import Path

# docs/Makefile installs it from the hub's vendored DocumANTation before every build.
from sphinx_kataglyphis import setup_theme


PROJECT = "OrchestrANT"
VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION.txt"

setup_theme(
    globals(),
    repository_url=f"https://github.com/Kataglyphis/{PROJECT}",
    project_name=PROJECT,
    release=VERSION_FILE.read_text(encoding="utf-8").strip(),
    myst_enable_extensions=["dollarmath", "amsmath", "colon_fence", "deflist"],
)
