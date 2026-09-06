"""Configuration file for the Sphinx documentation builder."""

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[2]

# NOTE: this used to also put
#   third_party/ContainerHub/docs/source_templates/sphinx-python
# on sys.path. That directory no longer exists — the shared Sphinx tooling moved
# out to DocumANTation (ContainerHub vendors it under third_party/), so
# the insert had been pointing at nothing. Nothing here imported from it, so the
# build was unaffected; it was dead code, removed 2026-08-11. See the note at the
# bottom of this file for how to adopt the shared theme deliberately.
sys.path.insert(0, str(_repo_root))

version_file = _repo_root / "VERSION.txt"
if version_file.exists():
    version = version_file.read_text().strip()
else:
    version = "0.0.1"

project = "OrchestrANT"
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

# No html_css_files. This used to load "ContainerHubStatic/css/custom.css"
# through a symlink into ContainerHub's docs/_static. That directory no longer
# exists there - the shared Sphinx theme moved out to DocumANTation
# and is consumed as the pip package `sphinx_kataglyphis` - so the symlink was
# dangling and the stylesheet had silently not been applied for some time.
# Removed together with the two dead symlinks (2026-08-11).
#
# To adopt the shared theme here, install `sphinx-kataglyphis-theme` and use
# `from sphinx_kataglyphis import setup_theme`, the way ContainerHub's own
# docs/conf.py does. That is a deliberate theme change rather than a cleanup:
# this project currently renders with sphinx_book_theme, set above.
