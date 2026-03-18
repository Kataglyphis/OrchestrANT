"""Configuration file for the Sphinx documentation builder."""

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[2]
_containerhub_docs = _repo_root / "ExternalLib" / "Kataglyphis-ContainerHub" / "docs"
_sphinx_python = _containerhub_docs / "source_templates" / "sphinx-python"

sys.path.insert(0, str(_sphinx_python))
sys.path.insert(0, str(_repo_root))

version_file = _repo_root / "VERSION.txt"
if version_file.exists():
    version = version_file.read_text().strip()
else:
    version = "0.0.1"

project = "Orchestr-ANT-ion"
copyright = "2025, Jonas Heinle"
author = "Jonas Heinle"
release = version

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinx_design",
]

myst_enable_extensions = [
    "dollarmath",
    "amsmath",
    "colon_fence",
    "deflist",
]

myst_heading_anchors = 6

templates_path = ["_templates"]
exclude_patterns = []

autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "private-members": True,
    "special-members": True,
}

html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "style_nav_header_background": "#6af0ad",
}

html_static_path = ["_static"]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

html_css_files = ["ContainerHubStatic/css/custom.css"]
