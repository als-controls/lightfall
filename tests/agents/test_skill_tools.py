import asyncio

import pytest

from lightfall.agents import drafts, skill_tools, skills_store


@pytest.fixture()
def user_root(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(skills_store, "user_skills_dir", lambda: root)
    monkeypatch.setattr(drafts, "user_skills_dir", lambda: root)
    monkeypatch.setattr(skills_store, "builtin_skills_dir", lambda: tmp_path / "none")
    return root


def _call(tool, args):
    # SDK @tool objects expose .handler as an async callable. asyncio.get_event_loop()
    # raises on this environment's Python (no implicit loop in the main thread), so use
    # asyncio.run() instead -- same convention as tests/agents/test_bus_tools.py.
    return asyncio.run(tool.handler(args))


class _FakeToastManager:
    calls: list[tuple[str, str]] = []

    @classmethod
    def get_instance(cls):
        return cls()

    def info(self, title, message):
        _FakeToastManager.calls.append((title, message))


def _draft_tool(monkeypatch, stub_toast: bool = True):
    monkeypatch.setattr(skill_tools, "run_on_main_thread", lambda fn, *a, **k: fn(*a, **k))
    if stub_toast:
        _FakeToastManager.calls = []
        import lightfall.ui.toast as toast_module

        monkeypatch.setattr(toast_module, "ToastManager", _FakeToastManager)
    tools = skill_tools._make_tools(lambda: "observer", lambda: "s1")
    return next(t for t in tools if t.name == "draft_skill")


def test_draft_skill_happy_path(user_root, monkeypatch):
    draft = _draft_tool(monkeypatch)
    result = _call(
        draft,
        {"name": "cryo-stall-triage", "description": "Triage cryocooler stalls", "body": "Steps..."},
    )
    text = result["content"][0]["text"]
    path = user_root / "_drafts" / "cryo-stall-triage" / "SKILL.md"
    assert path.exists()
    assert str(path) in text
    assert "must approve" in text


def test_draft_skill_revision(user_root, monkeypatch):
    active = user_root / "existing-skill"
    active.mkdir()
    (active / "SKILL.md").write_text(
        "---\nname: existing-skill\ndescription: d\n---\nb", encoding="utf-8"
    )
    draft = _draft_tool(monkeypatch)
    result = _call(
        draft,
        {"name": "existing-skill", "description": "improved", "body": "new body"},
    )
    assert "REVISION" in str(result)


def test_draft_skill_invalid_name(user_root, monkeypatch):
    draft = _draft_tool(monkeypatch)
    result = _call(draft, {"name": "Bad Name", "description": "d", "body": "b"})
    assert result.get("is_error")
    assert "Invalid skill name" in str(result)
    assert not (user_root / "_drafts" / "Bad Name").exists()


def test_draft_skill_invalid_name_survives_main_thread_marshal(user_root, monkeypatch):
    # Regression: the real run_on_main_thread rewraps ANY inner exception as
    # a generic RuntimeError with a traceback dump when marshalling across
    # threads. draft_skill must not rely on DraftError propagating across
    # that hop, or an invalid-name error degrades into an opaque traceback
    # instead of the clean validation message.
    def wrap(fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as exc:  # noqa: BLE001 - mirrors the real wrapper
            raise RuntimeError(f"{exc}\n<traceback>")

    monkeypatch.setattr(skill_tools, "run_on_main_thread", wrap)
    tools = skill_tools._make_tools(lambda: "observer", lambda: "s1")
    draft = next(t for t in tools if t.name == "draft_skill")

    result = _call(draft, {"name": "Bad Name", "description": "d", "body": "b"})
    text = result["content"][0]["text"]
    assert result.get("is_error")
    assert "Invalid skill name" in text
    assert "<traceback>" not in text
    assert not (user_root / "_drafts" / "Bad Name").exists()


def test_draft_skill_fires_toast(user_root, monkeypatch):
    draft = _draft_tool(monkeypatch)

    calls = []

    class FakeToastManager:
        @classmethod
        def get_instance(cls):
            return cls()

        def info(self, title, message):
            calls.append((title, message))

    import lightfall.ui.toast as toast_module

    monkeypatch.setattr(toast_module, "ToastManager", FakeToastManager)

    result = _call(
        draft, {"name": "toast-skill", "description": "d", "body": "b"}
    )
    assert not result.get("is_error")
    assert len(calls) == 1
    title, message = calls[0]
    assert "toast-skill" in title
    assert "observer" in message


def test_draft_skill_toast_failure_does_not_break_result(user_root, monkeypatch):
    draft = _draft_tool(monkeypatch)

    class ExplodingToastManager:
        @classmethod
        def get_instance(cls):
            raise RuntimeError("no GUI thread")

    import lightfall.ui.toast as toast_module

    monkeypatch.setattr(toast_module, "ToastManager", ExplodingToastManager)

    result = _call(
        draft, {"name": "toast-skill-2", "description": "d", "body": "b"}
    )
    assert not result.get("is_error")
    assert "must approve" in str(result)
