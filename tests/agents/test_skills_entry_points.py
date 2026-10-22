"""Skills advertised by installed packages through the ``agent_skills`` entry points."""

import importlib.metadata
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lightfall.agents import skills_store

GROUP = skills_store.ENTRY_POINT_GROUP


def _skill(root: Path, name: str, body: str = "b") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}", encoding="utf-8")
    return d


def _skills_package(site: Path, pkg: str) -> Path:
    """A temp package with one valid skill, one folder without SKILL.md, and a draft."""
    root = site / pkg
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    _skill(root, f"{pkg}-good")
    (root / "not-a-skill").mkdir()
    (root / "not-a-skill" / "README.md").write_text("no SKILL.md here", encoding="utf-8")
    _skill(root / "_drafts", "unfinished")
    return root


@pytest.fixture
def fake_entry_points(tmp_path, monkeypatch):
    """Install a fake set of ``agent_skills`` entry points; returns a setter."""
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: tmp_path / "nobuiltin")
    monkeypatch.setattr(skills_store, "user_skills_dir", lambda: tmp_path / "nouser")
    eps: list[importlib.metadata.EntryPoint] = []

    def fake(**kwargs):
        assert kwargs == {"group": GROUP}
        return importlib.metadata.EntryPoints(eps)

    monkeypatch.setattr(importlib.metadata, "entry_points", fake)

    def add(name: str, value: str) -> None:
        eps.append(importlib.metadata.EntryPoint(name=name, value=value, group=GROUP))

    yield site, add
    for mod in [m for m in sys.modules if m.startswith("lf_test_")]:
        del sys.modules[mod]


def test_collects_only_dirs_with_skill_md(fake_entry_points):
    site, add = fake_entry_points
    root = _skills_package(site, "lf_test_pkg")
    add("lf_test_pkg", "lf_test_pkg")

    dirs = skills_store.entry_point_skill_dirs()

    assert dirs == {"lf_test_pkg-good": root / "lf_test_pkg-good"}


def test_missing_module_warns_and_keeps_the_rest(fake_entry_points, monkeypatch):
    site, add = fake_entry_points
    _skills_package(site, "lf_test_ok")
    add("a_missing", "lf_test_does_not_exist")
    add("b_ok", "lf_test_ok")
    log = MagicMock()
    monkeypatch.setattr(skills_store, "logger", log)

    dirs = skills_store.entry_point_skill_dirs()

    assert list(dirs) == ["lf_test_ok-good"]
    assert log.warning.call_count == 1
    assert "a_missing" in log.warning.call_args.args


def test_entry_point_raising_on_load_warns_and_keeps_the_rest(fake_entry_points, monkeypatch):
    site, add = fake_entry_points
    boom = site / "lf_test_boom"
    boom.mkdir()
    (boom / "__init__.py").write_text("raise RuntimeError('broken on import')", encoding="utf-8")
    _skills_package(site, "lf_test_fine")
    add("boom", "lf_test_boom")
    add("fine", "lf_test_fine")
    log = MagicMock()
    monkeypatch.setattr(skills_store, "logger", log)

    dirs = skills_store.entry_point_skill_dirs()

    assert list(dirs) == ["lf_test_fine-good"]
    assert log.warning.call_count == 1
    assert "boom" in log.warning.call_args.args
    assert isinstance(log.warning.call_args.args[-1], RuntimeError)


def test_entry_point_loading_to_a_non_package_warns(fake_entry_points, monkeypatch):
    site, add = fake_entry_points
    (site / "lf_test_mod.py").write_text("def thing():\n    pass\n", encoding="utf-8")
    add("attr", "lf_test_mod:thing")
    log = MagicMock()
    monkeypatch.setattr(skills_store, "logger", log)

    assert skills_store.entry_point_skill_dirs() == {}
    assert log.warning.call_count == 1


def test_resolve_skills_precedence(fake_entry_points, tmp_path, monkeypatch):
    """builtin < entry points < beamline extra_dirs."""
    site, add = fake_entry_points
    root = _skills_package(site, "lf_test_prec")
    _skill(root, "shared")
    _skill(root, "ep-over-builtin")
    add("prec", "lf_test_prec")
    builtin, beamline = tmp_path / "builtin", tmp_path / "beamline"
    _skill(builtin, "ep-over-builtin")
    _skill(beamline, "shared")
    monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: builtin)

    resolved = skills_store.resolve_skills(extra_dirs=[beamline])

    assert resolved["ep-over-builtin"] == root / "ep-over-builtin"
    assert resolved["shared"] == beamline / "shared"
    assert resolved["lf_test_prec-good"] == root / "lf_test_prec-good"
    assert "not-a-skill" not in resolved
    assert "unfinished" not in resolved


def test_same_skill_from_entry_point_and_plugin_extra_materializes_once(
    fake_entry_points, tmp_path, monkeypatch
):
    """gpCAM's skills reach a session both via the entry point and via the
    autonomous_experiment plugin's get_extra_skill_dirs(); both name the same
    folder, so the skill is copied once and nothing warns."""
    site, add = fake_entry_points
    root = _skills_package(site, "lf_test_dup")
    add("dup", "lf_test_dup")
    monkeypatch.setattr(
        skills_store, "plugin_extra_skill_dirs", lambda: {"lf_test_dup-good": root / "lf_test_dup-good"}
    )
    log = MagicMock()
    monkeypatch.setattr(skills_store, "logger", log)

    done = skills_store.materialize_skills(("lf_test_dup-good",), tmp_path / "session")

    assert done == ["lf_test_dup-good"]
    log.warning.assert_not_called()


def test_clash_between_two_entry_points_warns_and_later_wins(fake_entry_points, monkeypatch):
    site, add = fake_entry_points
    first = _skills_package(site, "lf_test_one")
    second = _skills_package(site, "lf_test_two")
    _skill(first, "clash")
    _skill(second, "clash")
    add("one", "lf_test_one")
    add("two", "lf_test_two")
    log = MagicMock()
    monkeypatch.setattr(skills_store, "logger", log)

    dirs = skills_store.entry_point_skill_dirs()

    assert dirs["clash"] == second / "clash"
    assert log.warning.call_count == 1


def _installed_gpcam_skills() -> list[str]:
    if importlib.util.find_spec("gpcam") is None:
        return []
    eps = importlib.metadata.entry_points(group=GROUP)
    return [ep.name for ep in eps if ep.name == "gpcam"]


@pytest.mark.skipif(
    not _installed_gpcam_skills(), reason="installed gpCAM has no agent_skills entry point"
)
def test_installed_gpcam_skills_resolve():
    expected = {
        "acquisition-functions", "cost-functions", "experiment-designer",
        "gp2scale-advanced", "kernel-designer", "multi-task-advanced",
        "noise-functions", "prior-mean-functions", "transformed-optimizers-advanced",
        "troubleshooting", "uncertainty-calibration",
    }
    resolved = skills_store.resolve_skills()
    assert expected <= set(resolved)
    refs = resolved["acquisition-functions"] / "references"
    assert (refs / "choosing-an-acquisition-function.md").is_file()
