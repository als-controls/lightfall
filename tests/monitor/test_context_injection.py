import pytest
from lightfall.monitor.context_provider import (
    ExperimentContextProvider, experiment_context_pre_submit,
)
from lightfall.monitor.models import ExperimentContext


@pytest.fixture(scope="module")
def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_pre_submit_injects_current_context():
    ExperimentContextProvider.reset_instance()
    ExperimentContextProvider.get_instance().set_context(
        ExperimentContext(experiment_type="xpcs", intent="slow")
    )
    out = experiment_context_pre_submit("count", {})
    assert out["experiment_context"]["experiment_type"] == "xpcs"
    # Round-trips through the model.
    ctx = ExperimentContext.from_start_doc(out)
    assert ctx.intent == "slow"
    ExperimentContextProvider.reset_instance()


def test_pre_submit_does_not_overwrite_explicit_context():
    ExperimentContextProvider.reset_instance()
    kwargs = {"experiment_context": {"experiment_type": "explicit"}}
    out = experiment_context_pre_submit("count", kwargs)
    assert out == {}  # empty dict: merge nothing, preserve explicit value, do NOT cancel
    ExperimentContextProvider.reset_instance()


def test_hook_does_not_cancel_submission_with_explicit_context(_app):
    """Engine-contract: registering experiment_context_pre_submit must NOT cancel
    a plan submission that already carries an explicit experiment_context kwarg.

    Uses BaseEngine._run_pre_submit_hooks directly (via BlueskyEngine which
    inherits it) to avoid needing a live RunEngine or event loop.  The method
    returns the merged kwargs dict (non-None) when no hook cancels, and None
    when a hook cancels — so asserting non-None proves the submission proceeds.
    """
    from lightfall.acquire.engine.bluesky import BlueskyEngine

    ExperimentContextProvider.reset_instance()
    engine = BlueskyEngine(toast_notifications=False)
    engine.register_pre_submit(experiment_context_pre_submit)

    kwargs = {"experiment_context": {"experiment_type": "explicit"}}
    result = engine._run_pre_submit_hooks("count", kwargs)

    # Non-None result means no hook cancelled; submission proceeds.
    assert result is not None, (
        "experiment_context_pre_submit cancelled a submission that had an explicit "
        "experiment_context kwarg — hook must return {} (not None) in that branch."
    )
    # The explicit context must be preserved unchanged.
    assert result["experiment_context"]["experiment_type"] == "explicit"
    ExperimentContextProvider.reset_instance()
