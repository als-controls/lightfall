import pytest

from lightfall.agents import drafts, skills_store


@pytest.fixture()
def user_root(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(skills_store, "user_skills_dir", lambda: root)
    monkeypatch.setattr(drafts, "user_skills_dir", lambda: root)
    monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: tmp_path / "none")
    return root


def test_save_new_draft(user_root):
    path, is_rev = drafts.save_draft(
        "cryo-stall-triage", "Triage cryocooler stalls", "Steps...",
        author="observer", session_id="s1")
    assert not is_rev
    assert path == user_root / "_drafts" / "cryo-stall-triage" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    assert "name: cryo-stall-triage" in text
    assert "author: observer" in text
    assert "Steps..." in text


def test_collision_with_active_skill_writes_proposed(user_root):
    active = user_root / "existing-skill"
    active.mkdir()
    (active / "SKILL.md").write_text("---\nname: existing-skill\ndescription: d\n---\nb",
                                     encoding="utf-8")
    path, is_rev = drafts.save_draft("existing-skill", "improved", "new body",
                                     author="lightfall")
    assert is_rev
    assert path.name == "SKILL.md.proposed"


def test_redraft_overwrites(user_root):
    drafts.save_draft("x", "d", "v1", author="a")
    path, _ = drafts.save_draft("x", "d", "v2", author="a")
    assert "v2" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("bad", ["Bad Name", "", "-lead", "a" * 65])
def test_invalid_name_rejected(user_root, bad):
    with pytest.raises(drafts.DraftError):
        drafts.save_draft(bad, "d", "b", author="a")


def test_drafts_are_inert_to_resolution(user_root):
    drafts.save_draft("sneaky", "d", "b", author="a")
    assert "sneaky" not in skills_store.resolve_skills()


def test_list_drafts(user_root):
    drafts.save_draft("b-skill", "d", "body", author="observer")
    drafts.save_draft("a-skill", "d", "body", author="lightfall")
    listing = drafts.list_drafts()
    assert [d["name"] for d in listing] == ["a-skill", "b-skill"]
    assert listing[0]["author"] == "lightfall"
    assert listing[0]["is_revision"] is False
