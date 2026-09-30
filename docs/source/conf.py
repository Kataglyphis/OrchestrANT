"""Configuration file for the Sphinx documentation builder."""

import sys
from pathlib import Path


_repo_root = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(_repo_root))

version_file = _repo_root / "VERSION.txt"
version = version_file.read_text().strip() if version_file.exists() else "0.0.1"

project = "OrchestrANT"
# Sphinx reads this exact name, so it has to shadow the builtin.
copyright = "2025, Jonas Heinle"  # noqa: A001
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

html_theme = "sphinx_book_theme"
html_theme_options = {
    "repository_url": "https://github.com/Kataglyphis/OrchestrANT",
    "use_repository_button": True,
    "use_edit_page_button": True,
}

html_static_path = ["_static"]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# The family theme is opt-in: sphinx-kataglyphis-theme's setup_theme, as the hub's docs/conf.py does.
