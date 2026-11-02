"""Bundling local items: layout, exclusions, limits, manifest, and loader round trip."""
from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from lightfall.content import publish
from lightfall.content.publish import (
    LocalItem,
    build_bundle,
    local_items,
    make_manifest,
    policy_problems,
)
from lightfall.content.sources import synced_items
from lightfall.content.store import ContentStore
from lightfall.scopes.client import ContentListing, ContentPolicy
from lightfall.utils.git_tracker import GitTracker
from tests.content.conftest import item_for


def _names(bundle):
    with zipfile.ZipFile(bundle.path) as zf:
        return sorted(zf.namelist())


def _agent_text(name: str) -> str:
    return f"---\nname: {name}\ndescription: does things\n---\nBe helpful.\n"


@pytest.fixture(autouse=True)
def reset_git():
    GitTracker.reset_instance()
    yield
    GitTracker.reset_instance()


def test_plan_bundle_layout(tmp_path):
    f = tmp_path / "scan.py"
    f.write_bytes(b"x = 1\n")
    b = build_bundle(LocalItem("plan", "scan", f), tmp_path / "out")
    assert _names(b) == ["scan.py"]
    assert b.files == ("scan.py",)
    assert (b.file_count, b.uncompressed_size) == (1, 6)
    assert b.size == b.path.stat().st_size
    assert b.sha256 == hashlib.sha256(b.path.read_bytes()).hexdigest()


def test_single_file_plugin_bundle(tmp_path):
    f = tmp_path / "tool.py"
    f.write_bytes(b"y = 2\n")
    b = build_bundle(LocalItem("plugin", "tool", f), tmp_path / "out")
    assert _names(b) == ["tool.py"]


def test_folder_plugin_bundle_round_trips_through_sources(tmp_path):
    pkg = tmp_path / "mine" / "pkg"
    (pkg / "sub" / "deep").mkdir(parents=True)
    (pkg / "__pycache__").mkdir()
    (pkg / ".git").mkdir()
    (pkg / "sub" / "__pycache__").mkdir()
    (pkg / "__init__.py").write_bytes(b"a = 1\n")
    (pkg / "sub" / "deep" / "m.py").write_bytes(b"bb = 2\n")
    (pkg / "sub" / "data.txt").write_bytes(b"ccc")
    (pkg / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"junk")
    (pkg / "sub" / "__pycache__" / "y.pyc").write_bytes(b"junk")
    (pkg / "loose.pyc").write_bytes(b"junk")
    (pkg / ".git" / "HEAD").write_bytes(b"ref")
    (pkg / ".DS_Store").write_bytes(b"j")
    (pkg / "sub" / "Thumbs.db").write_bytes(b"j")
    b = build_bundle(LocalItem("plugin", "pkg", pkg), tmp_path / "out")
    assert _names(b) == ["__init__.py", "sub/data.txt", "sub/deep/m.py"]
    assert (b.file_count, b.uncompressed_size) == (3, 6 + 7 + 3)

    scope = "branch:7.0.1.1"
    store = ContentStore(tmp_path / "root")
    store.save_chain(scope, ["facility", scope])
    item = item_for("plugin", "pkg", 1, b.path.read_bytes(), dict.fromkeys(b.files, b""),
                    uncompressed_size=b.uncompressed_size)
    store.install(scope, item, b.path)
    store.save_listing(ContentListing(scope=scope, items=(item,)))
    got = synced_items(store, scope, "plugin")
    assert [g.name for g in got] == ["pkg"]
    assert got[0].path == store.item_path(scope, "plugin", "pkg", 1)
    assert (got[0].path / "sub" / "deep" / "m.py").read_bytes() == b"bb = 2\n"
    assert not (got[0].path / "__pycache__").exists()
    assert not (got[0].path / ".git").exists()


def test_agent_bundle_named_by_frontmatter(tmp_path):
    f = tmp_path / "whatever-file.md"
    f.write_text(_agent_text("helper"), encoding="utf-8", newline="")
    b = build_bundle(LocalItem("agent", "helper", f), tmp_path / "out")
    assert _names(b) == ["helper.md"]
    with zipfile.ZipFile(b.path) as zf:
        assert zf.read("helper.md").decode() == _agent_text("helper")


def test_skill_bundle_excludes_drafts(tmp_path):
    sk = tmp_path / "tips"
    (sk / "_drafts").mkdir(parents=True)
    (sk / "ref").mkdir()
    (sk / "SKILL.md").write_text("# tips")
    (sk / "ref" / "a.md").write_text("a")
    (sk / "_drafts" / "wip.md").write_text("wip")
    b = build_bundle(LocalItem("skill", "tips", sk), tmp_path / "out")
    assert _names(b) == ["SKILL.md", "ref/a.md"]


def test_local_items_skips_invalid_names(tmp_path):
    root = tmp_path / "lightfall"
    for d in ("plans", "plugins", "agents", "skills"):
        (root / d).mkdir(parents=True)
    (root / "plans" / "good.py").write_text("")
    (root / "plans" / "_private.py").write_text("")
    (root / "plans" / "bad name.py").write_text("")
    (root / "plans" / "notes.txt").write_text("")
    (root / "plugins" / "single.py").write_text("")
    (root / "plugins" / "pkg").mkdir()
    (root / "plugins" / "pkg" / "__init__.py").write_text("")
    (root / "plugins" / "nopkg").mkdir()
    (root / "plugins" / "__pycache__").mkdir()
    (root / "agents" / "file.md").write_text(_agent_text("helper"))
    (root / "agents" / "broken.md").write_text("no frontmatter")
    (root / "agents" / "badname.md").write_text(_agent_text("../evil"))
    (root / "skills" / "tips").mkdir()
    (root / "skills" / "tips" / "SKILL.md").write_text("x")
    (root / "skills" / "_drafts").mkdir()
    (root / "skills" / "_drafts" / "SKILL.md").write_text("x")
    (root / "skills" / "empty").mkdir()
    got = {(i.type, i.name): i.path for i in local_items(root)}
    assert got == {
        ("plan", "good"): root / "plans" / "good.py",
        ("plugin", "single"): root / "plugins" / "single.py",
        ("plugin", "pkg"): root / "plugins" / "pkg",
        ("agent", "helper"): root / "agents" / "file.md",
        ("skill", "tips"): root / "skills" / "tips",
    }


def test_local_items_missing_root_is_empty(tmp_path):
    assert local_items(tmp_path / "nope") == []


MB = 1024 * 1024


def test_policy_problems_lists_each_limit(tmp_path):
    b = publish.Bundle(path=tmp_path / "x.zip", sha256="0" * 64, size=int(12.34 * MB),
                       file_count=2500, uncompressed_size=300 * MB, files=())
    policy = ContentPolicy(max_bundle_bytes=10 * MB, max_uncompressed_bytes=200 * MB,
                           max_files=2000)
    assert policy_problems(b, policy) == [
        "12.3 MB compressed; the limit is 10 MB",
        "300.0 MB uncompressed; the limit is 200 MB",
        "2,500 files; the limit is 2,000",
    ]
    ok = publish.Bundle(tmp_path / "x.zip", "0" * 64, 10, 1, 10, ())
    assert policy_problems(ok, policy) == []
    small = ContentPolicy(max_bundle_bytes=2048, max_uncompressed_bytes=1 << 30, max_files=5)
    big = publish.Bundle(tmp_path / "x.zip", "0" * 64, 5000, 1, 10, ())
    assert policy_problems(big, small) == ["4.9 KB compressed; the limit is 2.0 KB"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout.strip()


def test_manifest_fields_and_local_commit_only_when_clean(tmp_path, monkeypatch):
    root = tmp_path / "lightfall"
    (root / "plans").mkdir(parents=True)
    f = root / "plans" / "scan.py"
    f.write_bytes(b"x = 1\n")
    tracker = GitTracker(repo_root=root)
    GitTracker._instance = tracker
    tracker.ensure_repo()
    monkeypatch.setattr(publish.socket, "gethostname", lambda: "bl-pc")
    item = LocalItem("plan", "scan", f)
    b = build_bundle(item, tmp_path / "out")

    m = make_manifest(item, b, message="first", author=None, agent=False)
    assert set(m) == {"type", "name", "file_count", "uncompressed_size", "sha256",
                      "message", "agent", "source"}
    assert m["source"] == {"machine": "bl-pc", "path": str(f)}
    assert (m["type"], m["name"], m["file_count"], m["uncompressed_size"], m["sha256"],
            m["message"], m["agent"]) == ("plan", "scan", 1, 6, b.sha256, "first", False)

    tracker.commit([f], "add scan")
    head = _git(root, "rev-parse", "HEAD")
    m = make_manifest(item, b, message="m", author="Ron", agent=True)
    assert m["source"]["local_commit"] == head
    assert m["author"] == "Ron" and m["agent"] is True
    assert all(isinstance(v, str) for v in m["source"].values())

    f.write_bytes(b"x = 2\n")  # dirty
    m = make_manifest(item, b, message="m", author=None, agent=False)
    assert "local_commit" not in m["source"]


def test_manifest_limits(tmp_path):
    f = tmp_path / "scan.py"
    f.write_bytes(b"x")
    item = LocalItem("plan", "scan", f)
    b = build_bundle(item, tmp_path / "out")
    with pytest.raises(ValueError, match="1000"):
        make_manifest(item, b, message="m" * 1001, author=None, agent=False)
    with pytest.raises(ValueError, match="256"):
        make_manifest(item, b, message="m", author="a" * 257, agent=False)
    deep = LocalItem("plan", "scan", tmp_path.joinpath(*(["d" * 300] * 8), "scan.py"))
    m = make_manifest(deep, b, message="m", author=None, agent=False)
    assert len(json.dumps(m["source"]).encode()) <= 2048
    assert m["source"]["path"].endswith("scan.py")
    assert m["source"]["path"].startswith("...")


def test_upload_sends_the_built_bundle_bytes(tmp_path):
    f = tmp_path / "scan.py"
    f.write_bytes(b"x = 1\n")
    b = build_bundle(LocalItem("plan", "scan", f), tmp_path / "out")
    f.write_bytes(b"x = 'edited afterwards, much longer'\n")
    assert hashlib.sha256(b.path.read_bytes()).hexdigest() == b.sha256
    with zipfile.ZipFile(b.path) as zf:
        assert zf.read("scan.py") == b"x = 1\n"
