from lightfall.monitor.context_provider import (
    ExperimentContextProvider, experiment_context_pre_submit,
)
from lightfall.monitor.models import ExperimentContext


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
    assert out is None  # nothing to merge; explicit value preserved
    ExperimentContextProvider.reset_instance()
