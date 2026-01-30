# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys

# Add source directory to path for autodoc
sys.path.insert(0, os.path.abspath("../src"))

# -- Project information -----------------------------------------------------
project = "LUCID"
copyright = "2024, ALS Controls Team"
author = "ALS Controls Team"

# -- General configuration ---------------------------------------------------
extensions = [
    "myst_parser",  # Markdown support
    "sphinx.ext.autodoc",  # API documentation from docstrings
    "sphinx.ext.viewcode",  # Add links to source code
    "sphinx.ext.intersphinx",  # Link to other projects' documentation
    "sphinx_immaterial",  # Material theme extensions
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Markdown configuration
myst_enable_extensions = [
    "colon_fence",  # ::: directives
    "deflist",  # Definition lists
    "fieldlist",  # Field lists
    "tasklist",  # Task lists with checkboxes
]

# The suffix(es) of source filenames
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# -- Options for HTML output -------------------------------------------------
html_theme = "sphinx_immaterial"
html_static_path = ["_static"]
html_title = "LUCID Documentation"

# Theme options for sphinx-immaterial
html_theme_options = {
    "icon": {
        "repo": "fontawesome/brands/github",
    },
    "site_url": "https://als-computing.github.io/lucid/",
    "repo_url": "https://github.com/als-computing/lucid",
    "repo_name": "lucid",
    "edit_uri": "blob/main/docs",
    "globaltoc_collapse": True,
    "features": [
        "navigation.expand",
        "navigation.sections",
        "navigation.top",
        "search.highlight",
        "search.share",
        "toc.follow",
        "toc.sticky",
        "content.tabs.link",
        "announce.dismiss",
    ],
    "palette": [
        {
            "media": "(prefers-color-scheme: light)",
            "scheme": "default",
            "primary": "blue",
            "accent": "light-blue",
            "toggle": {
                "icon": "material/brightness-7",
                "name": "Switch to dark mode",
            },
        },
        {
            "media": "(prefers-color-scheme: dark)",
            "scheme": "slate",
            "primary": "blue",
            "accent": "light-blue",
            "toggle": {
                "icon": "material/brightness-4",
                "name": "Switch to light mode",
            },
        },
    ],
    "toc_title_is_page_title": True,
}

# -- Intersphinx configuration -----------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "PySide6": ("https://doc.qt.io/qtforpython-6/", None),
}
