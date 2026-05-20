#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sphinx configuration for vivarium-artifact docs."""

import datetime

import vivarium.artifact

# -- Project information -----------------------------------------------------

project = "vivarium.artifact"
author = "The vivarium developers"
# Copyright start year mirrors the LICENSE file. End year tracks the current
# build to signal active maintenance.
copyright = f"2016-{datetime.date.today().year}, Institute for Health Metrics and Evaluation"

version = vivarium.artifact.__version__
release = vivarium.artifact.__version__


# -- General configuration ------------------------------------------------

needs_sphinx = "4.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx_autodoc_typehints",
    "sphinx.ext.intersphinx",
    "sphinx.ext.doctest",
    "sphinx.ext.todo",
    "sphinx.ext.coverage",
    "sphinx.ext.mathjax",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

source_suffix = ".rst"
master_doc = "index"
language = "en"
exclude_patterns: list[str | None] = []
pygments_style = "sphinx"
todo_include_todos = True


# -- Options for HTML output ----------------------------------------------

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_css_files = ["style.css"]
html_sidebars = {
    "**": [
        "globaltoc.html",
        "searchbox.html",
    ]
}

htmlhelp_basename = f"{project}doc"


# -- Options for LaTeX / man / texinfo output -----------------------------

latex_elements: dict[str, str] = {}
latex_documents = [(master_doc, f"{project}.tex", f"{project} Documentation", author, "manual")]
man_pages = [(master_doc, project, f"{project} Documentation", [author], 1)]
texinfo_documents = [
    (
        master_doc,
        project,
        f"{project} Documentation",
        author,
        project,
        "Data artifact storage and access for the vivarium simulation framework.",
        "Miscellaneous",
    ),
]


# -- Intersphinx ---------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
    "pandas": ("https://pandas.pydata.org/docs/", None),
    "tables": ("https://www.pytables.org/", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
    # Used by docstrings that reference :mod:`vivarium` and
    # :mod:`vivarium.framework.artifact`. When vivarium migrates into the
    # monorepo as vivarium-core, swap this URL to that lib's RTD project.
    "vivarium": ("https://vivarium.readthedocs.io/en/latest/", None),
}


# -- Autodoc configuration ------------------------------------------------

autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "undoc-members": True,
    "private-members": False,
}
autodoc_typehints = "description"


# -- nitpicky mode --------------------------------------------------------

nitpicky = True

nitpick_ignore: list[tuple[str, str]] = []
for line in open("../nitpick-exceptions"):
    if line.strip() == "" or line.startswith("#"):
        continue
    dtype, target = line.split(None, 1)
    target = target.strip()
    nitpick_ignore.append((dtype, target))
