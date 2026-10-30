"""Tests for finding installed Lightfall distributions and how they were installed."""

from __future__ import annotations

import json
from pathlib import Path

from lightfall.updates.discovery import (
    InstalledDist,
    classify,
    discover_installed,
    group_by_repo,
)


class FakeDist:
    def __init__(self, name: str, version: str = "1.0", direct_url: dict | None = None):
        self.metadata = {"Name": name}
        self.version = version
        self._direct_url = direct_url

    def read_text(self, filename: str) -> str | None:
        if filename == "direct_url.json" and self._direct_url is not None:
            return json.dumps(self._direct_url)
        return None


def editable(path: Path) -> dict:
    return {"url": path.as_uri(), "dir_info": {"editable": True}}


def test_index_install_is_pypi():
    item = classify(FakeDist("lightfall-deck", "0.2.0"))
    assert item == InstalledDist("lightfall-deck", "0.2.0", "pypi", None)


def test_editable_git_install_is_source(clone):
    item = classify(FakeDist("lightfall", direct_url=editable(clone)))
    assert item is not None
    assert item.kind == "source"
    assert item.repo_root.resolve() == clone.resolve()


def test_editable_without_git_is_unsupported(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert classify(FakeDist("lightfall", direct_url=editable(plain))) is None
    assert classify(FakeDist("lightfall", direct_url=editable(tmp_path / "gone"))) is None


def test_vcs_install_is_unsupported():
    url = {"url": "https://example.com/x.git", "vcs_info": {"vcs": "git", "commit_id": "abc"}}
    assert classify(FakeDist("lightfall", direct_url=url)) is None


def test_discover_selects_lightfall_and_entry_point_dists():
    dists = [FakeDist("lightfall"), FakeDist("Lightfall_Utils"), FakeDist("numpy"),
             FakeDist("stxm-live")]
    found = discover_installed(dists=dists, entry_point_dists=["stxm-live"])
    assert {d.name for d in found} == {"lightfall", "lightfall-utils", "stxm-live"}


def test_discover_deduplicates_by_canonical_name():
    found = discover_installed(dists=[FakeDist("lightfall"), FakeDist("Lightfall")],
                               entry_point_dists=[])
    assert [d.name for d in found] == ["lightfall"]


def test_group_by_repo(tmp_path):
    root = tmp_path / "repo"
    items = [
        InstalledDist("lightfall-a", "1", "source", root),
        InstalledDist("lightfall-b", "1", "source", root),
        InstalledDist("lightfall-c", "1", "pypi", None),
    ]
    assert group_by_repo(items) == {root: ("lightfall-a", "lightfall-b")}
