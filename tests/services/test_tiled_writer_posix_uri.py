"""POSIX Resource paths must become POSIX ``file://`` URIs on any client OS.

Regression for run cc1e0aed (bl602esv): the Marana's legacy ``AD_HDF5``
Resource (root ``/data``, ``path_semantics="posix"``) was converted by
upstream ``ensure_uri`` -> ``Path.absolute()`` into
``file://localhost/C:\\data\\...`` on the Windows acquisition host, and
bcgtiled (readable_storage ``/data``) refused to register it (403).
"""
from __future__ import annotations

import ntpath
from pathlib import PureWindowsPath
from unittest.mock import MagicMock, patch

import pytest
from bluesky_tiled_plugins.writing.tiled_writer import RunNormalizer

from lightfall.services.tiled_writer_patch import (
    PosixAwareRunNormalizer,
    TiledWriter,
    _posix_resource_uri,
)

MARANA_URI = "file://localhost/data/6.0.2/marana01/2026/11/19/x_000000.h5"


def _resource(**overrides):
    doc = {
        "uid": "res-1",
        "run_start": "start-1",
        "spec": "AD_HDF5",
        "root": "/data",
        "resource_path": "6.0.2/marana01/2026/11/19/x_000000.h5",
        "resource_kwargs": {"frame_per_point": 1},
        "path_semantics": "posix",
    }
    doc.update(overrides)
    return doc


def _normalizer():
    return PosixAwareRunNormalizer(
        patches={}, spec_to_mimetype={"AD_HDF5": "application/x-hdf5", "TIFF": "image/tiff"}
    )


class _WindowsPath:
    """Stand-in for ``pathlib.Path`` that behaves like Windows' ``absolute()``."""

    def __init__(self, p):
        self._p = str(p)

    def absolute(self):
        p = self._p.replace("/", "\\")
        if not ntpath.isabs(p) or not ntpath.splitdrive(p)[0]:
            p = ntpath.join("C:\\", p.lstrip("\\"))
        return PureWindowsPath(p)


@pytest.fixture
def windows_paths():
    """Force upstream ``ensure_uri`` to Windows path behaviour on any test host."""
    with patch("tiled.utils.Path", _WindowsPath):
        yield


def test_upstream_would_produce_windows_uri(windows_paths):
    """Sanity: the fixture reproduces the field bug with the stock normalizer."""
    stock = RunNormalizer(patches={}, spec_to_mimetype={"AD_HDF5": "application/x-hdf5"})
    uri = stock._convert_resource_to_stream_resource(_resource())["uri"]
    assert "C:" in uri


def test_posix_resource_gets_exact_posix_uri(windows_paths):
    sres = _normalizer()._convert_resource_to_stream_resource(_resource())
    assert sres["uri"] == MARANA_URI
    # Rest of the upstream conversion is untouched.
    assert sres["mimetype"] == "application/x-hdf5"
    assert sres["parameters"]["dataset"] == "/entry/data/data"
    assert sres["parameters"]["spec"] == "AD_HDF5"
    assert sres["parameters"]["multiplier"] == 1


def test_explicit_dataset_parameter_survives(windows_paths):
    doc = _resource(resource_kwargs={"frame_per_point": 1, "dataset": "/entry/custom"})
    sres = _normalizer()._convert_resource_to_stream_resource(doc)
    assert sres["parameters"]["dataset"] == "/entry/custom"
    assert sres["uri"] == MARANA_URI


def test_trailing_slash_directory_convention_kept(windows_paths):
    doc = _resource(spec="TIFF", resource_path="6.0.2/marana01/2026/11/19/")
    sres = _normalizer()._convert_resource_to_stream_resource(doc)
    assert sres["uri"] == "file://localhost/data/6.0.2/marana01/2026/11/19/"


def test_root_and_absolute_resource_path_join_like_upstream():
    assert _posix_resource_uri(_resource(root="/data/", resource_path="/a/b.h5")) == (
        "file://localhost/data/a/b.h5"
    )
    assert _posix_resource_uri(_resource(root="", resource_path="/data/a.h5")) == (
        "file://localhost/data/a.h5"
    )


def test_missing_semantics_with_posix_absolute_root(windows_paths):
    doc = _resource()
    del doc["path_semantics"]
    sres = _normalizer()._convert_resource_to_stream_resource(doc)
    assert sres["uri"] == MARANA_URI


@pytest.mark.parametrize(
    "overrides",
    [
        {"path_semantics": "windows", "root": "C:\\data", "resource_path": "x.h5"},
        {"root": "", "resource_path": "relative/x.h5"},  # relative: upstream decides
    ],
)
def test_non_posix_cases_defer_to_upstream(overrides, windows_paths):
    doc = _resource(**overrides)
    assert _posix_resource_uri(doc) is None
    stock = RunNormalizer(patches={}, spec_to_mimetype={"AD_HDF5": "application/x-hdf5"})
    expected = stock._convert_resource_to_stream_resource(dict(doc))["uri"]
    assert _normalizer()._convert_resource_to_stream_resource(dict(doc))["uri"] == expected


def test_current_schema_stream_resource_untouched():
    doc = {"uid": "s", "mimetype": "application/x-hdf5", "uri": "file://localhost/x.h5"}
    assert _posix_resource_uri(doc) is None


def test_tiled_writer_uses_posix_aware_normalizer_by_default():
    writer = TiledWriter(MagicMock(name="client"))
    [cb], _ = writer._factory("start", {})
    assert isinstance(cb, PosixAwareRunNormalizer)


def test_tiled_writer_respects_explicit_normalizer():
    class Custom(RunNormalizer):
        pass

    writer = TiledWriter(MagicMock(name="client"), normalizer=Custom)
    [cb], _ = writer._factory("start", {})
    assert type(cb) is Custom
