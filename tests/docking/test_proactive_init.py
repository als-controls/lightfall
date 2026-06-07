"""Tests for proactive deferred-panel initialization."""

from lightfall.ui.panels.base import PanelStatus
from lightfall.ui.panels.registry import PanelRegistry

from .conftest import make_panel_class


def _register(docking, panel_id, *, order=0, proactive=True, register_class=True):
    cls = make_panel_class(panel_id, order=order, proactive=proactive)
    if register_class:
        PanelRegistry.get_instance().register(cls)
    docking.register_deferred_panel(
        panel_id, cls.panel_metadata, cls.panel_metadata.default_area
    )
    return cls


def _wait_done(qtbot, docking):
    qtbot.waitUntil(lambda: not docking._proactive_queue, timeout=5000)
    # one extra spin so the final singleShot drains
    qtbot.wait(50)


class TestProactiveInit:
    def test_initializes_in_sidebar_order(self, qtbot, docking):
        created: list[str] = []
        for pid, order in [("test.a", 0), ("test.b", 1), ("test.c", 2)]:
            cls = _register(docking, pid, order=order)
            cls._setup_ui = (
                lambda self, _pid=pid: created.append(_pid)
            )
        docking.start_proactive_init()
        _wait_done(qtbot, docking)
        assert created == ["test.a", "test.b", "test.c"]
        assert docking.get_panel_status("test.b") is PanelStatus.SUCCESS

    def test_panels_stay_hidden(self, qtbot, docking):
        _register(docking, "test.a")
        docking.start_proactive_init()
        _wait_done(qtbot, docking)
        widget = docking.get_dock_widget("test.a")
        assert widget is not None
        assert not widget.isVisible()

    def test_opt_out_respected(self, qtbot, docking):
        _register(docking, "test.lazy", proactive=False)
        _register(docking, "test.eager")
        docking.start_proactive_init()
        _wait_done(qtbot, docking)
        assert docking.is_panel_deferred("test.lazy")
        assert not docking.is_panel_deferred("test.eager")

    def test_failure_does_not_halt_chain(self, qtbot, docking):
        _register(docking, "test.broken", order=0, register_class=False)
        _register(docking, "test.ok", order=1)
        docking.start_proactive_init()
        _wait_done(qtbot, docking)
        assert docking.get_panel_status("test.broken") is PanelStatus.ERROR
        assert docking.get_panel_status("test.ok") is PanelStatus.SUCCESS

    def test_already_instantiated_panel_skipped(self, qtbot, docking):
        _register(docking, "test.a")
        docking._instantiate_deferred_panel("test.a")  # user clicked first
        docking.start_proactive_init()
        _wait_done(qtbot, docking)
        assert docking.get_panel_status("test.a") is PanelStatus.SUCCESS

    def test_start_is_idempotent(self, qtbot, docking):
        _register(docking, "test.a")
        docking.start_proactive_init()
        docking.start_proactive_init()
        _wait_done(qtbot, docking)
        assert not docking.is_panel_deferred("test.a")

    def test_chain_stops_after_teardown(self, qtbot, docking):
        _register(docking, "test.a")
        # Seed the queue directly so no singleShot is scheduled yet.
        docking._proactive_queue = ["test.a"]
        # Simulate shutdown: delete the inner window and let the event loop
        # process the deletion so shiboken6 marks the wrapper as invalid.
        docking._inner_window.deleteLater()
        qtbot.wait(50)
        # Calling _proactive_init_next now should hit the guard and bail.
        docking._proactive_init_next()
        assert docking._proactive_queue == []
        assert docking.is_panel_deferred("test.a")


class TestImportWarmup:
    def test_warmup_import_defaults_empty(self):
        from lightfall.ui.panels.base import PanelMetadata

        assert PanelMetadata(id="x", name="X").warmup_import == ""

    def test_warmup_imports_module_in_background(
        self, qtbot, docking, tmp_path, monkeypatch
    ):
        import sys

        (tmp_path / "warm_dummy_mod.py").write_text("VALUE = 1\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        sys.modules.pop("warm_dummy_mod", None)

        cls = _register(docking, "test.warm")
        cls.panel_metadata.warmup_import = "warm_dummy_mod"

        docking.start_proactive_init()
        qtbot.waitUntil(
            lambda: "warm_dummy_mod" in sys.modules, timeout=5000
        )
        _wait_done(qtbot, docking)

    def test_no_warmup_no_thread(self, qtbot, docking):
        _register(docking, "test.plain")
        docking.start_proactive_init()
        assert docking._warmup_thread is None
        _wait_done(qtbot, docking)
