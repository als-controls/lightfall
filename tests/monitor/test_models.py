from lightfall.monitor.models import Observation, ExperimentContext


def test_observation_to_dict_roundtrips_core_fields():
    obs = Observation(
        severity="warn", feed_name="health", run_uid="abc",
        title="Low count rate", message="rate=0.1", state_key="health:low_rate",
        metrics={"rate": 0.1}, recommendation="check shutter", ts=123.0,
    )
    d = obs.to_dict()
    assert d["severity"] == "warn"
    assert d["state_key"] == "health:low_rate"
    assert d["metrics"] == {"rate": 0.1}


def test_experiment_context_from_start_doc_reads_injected_key():
    doc = {"uid": "u1", "experiment_context": {
        "experiment_type": "xpcs", "intent": "slow dynamics",
        "feed_config": {"acquisition_health": {"min_rate": 1.0}},
    }}
    ctx = ExperimentContext.from_start_doc(doc)
    assert ctx.experiment_type == "xpcs"
    assert ctx.for_feed("acquisition_health") == {"min_rate": 1.0}
    assert ctx.for_feed("missing") == {}


def test_experiment_context_from_start_doc_defaults_when_absent():
    ctx = ExperimentContext.from_start_doc({"uid": "u1"})
    assert ctx.experiment_type == "generic"
    assert ctx.feed_config == {}
