SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = source
BUILDDIR      = build
# Not a uv path source: that would fail every --locked sync of a checkout without this submodule.
THEME         = ../third_party/ANTfrastructure/third_party/DocumANTation/sphinx-kataglyphis-theme

# Put it first so that "make" without argument is like "make help".
help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

# The family theme, editable into the active venv; its dependencies are the docs extra's.
theme:
	uv pip install --quiet --no-deps -e "$(THEME)"

.PHONY: help theme Makefile

# Catch-all: unknown targets go to Sphinx's make mode; $(O) is short for $(SPHINXOPTS).
%: Makefile theme
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
