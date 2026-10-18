"""Step protocol, registry, context and saved progress."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lightfall.setup.progress import (
    SetupProgress,
    is_setup_completed,
    load_progress,
    mark_setup_completed,
    save_progress,
)
from lightfall.setup.registry import SetupRegistry
from lightfall.setup.step import SetupStep, StepState, StepStatus


class _Step:
    def __init__(self, step_id):
        self.id = step_id
        self.title = step_id.title()

    def detect(self, ctx):
        return StepState(StepStatus.NEEDS_INPUT)

    def widget(self, ctx):
        raise NotImplementedError

    def validate(self, ctx):
        return None

    def apply(self, ctx):
        return None


def test_step_status_members():
    assert [s.name for s in StepStatus] == ["SATISFIED", "NEEDS_INPUT", "FAILED", "SKIPPED"]


def test_step_state():
    assert StepState(StepStatus.SATISFIED, "ALS").satisfied
    assert not StepState(StepStatus.NEEDS_INPUT).satisfied
    assert StepState(StepStatus.FAILED).value == ""


def test_fake_step_satisfies_the_protocol():
    assert isinstance(_Step("a"), SetupStep)


def test_registry_keeps_order_and_rejects_duplicates():
    reg = SetupRegistry()
    reg.register(_Step("facility"))
    reg.register(_Step("network"))
    assert [s.id for s in reg.steps()] == ["facility", "network"]
    assert len(reg) == 2
    with pytest.raises(ValueError):
        reg.register(_Step("facility"))
    reg.steps().clear()
    assert len(reg) == 2


def test_registry_rejects_non_steps():
    with pytest.raises(TypeError):
        SetupRegistry().register(object())


def test_progress_round_trip(cfg, config_dirs):
    from lightfall.config import ConfigManager

    progress = load_progress(cfg)
    assert progress == SetupProgress([], [])
    progress.mark_completed("facility")
    progress.mark_skipped("network")
    progress.mark_completed("network")
    progress.mark_skipped("signin")
    save_progress(cfg, progress)
    fresh = load_progress(ConfigManager())
    assert fresh.completed == ["facility", "network"]
    assert fresh.skipped == ["signin"]


@pytest.mark.parametrize("raw", ["oops", ["a"], {"completed": "facility", "skipped": [1, "x"]}])
def test_progress_tolerates_hand_edited_yaml(cfg, raw):
    cfg.set("setup.progress", raw, persist=True)
    progress = load_progress(cfg)
    assert progress.completed == []
    assert progress.skipped == (["x"] if isinstance(raw, dict) else [])


def test_setup_completed_flag(cfg, config_dirs):
    from lightfall.config import ConfigManager

    assert is_setup_completed(cfg) is False
    cfg.set("setup.completed", "yes", persist=True)
    assert is_setup_completed(cfg) is False
    mark_setup_completed(cfg)
    assert is_setup_completed(ConfigManager()) is True
    assert cfg.layers.get_layer("user").get("setup.completed") is True


def test_context_server_url_and_labels(ctx):
    from lightfall.setup.bootstrap import Bootstrap, apply_bootstrap
    from lightfall.setup.facilities import Facility

    assert ctx.server_url() is None
    assert ctx.facility_label() == "your facility"
    apply_bootstrap(Bootstrap("http://stored:8000", {"schema": 1, "facility": {
        "id": "als", "name": "ALS"}}), ctx.config)
    assert ctx.server_url() == "http://stored:8000"
    assert ctx.facility_label() == "ALS"
    ctx.facility = Facility("als", "Advanced Light Source", "http://new:8000", short_name="ALS")
    assert ctx.server_url() == "http://new:8000"
    assert ctx.facility_name() == "Advanced Light Source"


def test_step_logic_modules_have_no_module_level_qt_imports():
    import lightfall.setup as setup_pkg

    root = Path(setup_pkg.__file__).parent
    qt_free = [p for p in root.glob("*.py") if p.name not in ("wizard.py", "page.py")]
    qt_free += list((root / "steps").glob("*.py"))
    for path in qt_free:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(n.split(".")[0] in ("PySide6", "shiboken6") for n in names), path
