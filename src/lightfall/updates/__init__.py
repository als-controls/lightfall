"""Update checks for installed Lightfall packages.

Finds the installed Lightfall distributions, works out whether each is an
editable source checkout or an index (PyPI) install, checks git remotes or the
package index for newer versions, and applies fast-forward / pip updates. Only
:mod:`lightfall.updates.checker` touches Qt; the rest is plain Python.
"""
