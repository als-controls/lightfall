from lightfall.monitor.data_window import DataWindow
from lightfall.monitor.feeds.acquisition_health import AcquisitionHealthFeed
from lightfall.monitor.models import ExperimentContext


def _ctx():
    return ExperimentContext(feed_config={"acquisition_health": {
        "count_field": "det", "min_rate": 1.0, "min_samples": 3, "stall_after_s": 60.0,
    }})


def test_warns_on_count_rate_collapse():
    win = DataWindow(run_uid="u", events={"det": [0.0, 0.0, 0.0]},
                     event_count=3, age_s=1.0)
    obs = AcquisitionHealthFeed().evaluate(_ctx(), win, [])
    assert obs is not None and obs.state_key == "acquisition_health:low_rate"
    assert obs.severity == "warn"


def test_warns_on_stall():
    win = DataWindow(run_uid="u", events={"det": [10.0, 10.0, 10.0]},
                     event_count=3, age_s=120.0)
    obs = AcquisitionHealthFeed().evaluate(_ctx(), win, [])
    assert obs is not None and obs.state_key == "acquisition_health:stalled"


def test_healthy_returns_none():
    win = DataWindow(run_uid="u", events={"det": [10.0, 11.0, 12.0]},
                     event_count=3, age_s=1.0)
    assert AcquisitionHealthFeed().evaluate(_ctx(), win, []) is None


def test_insufficient_samples_returns_none():
    win = DataWindow(run_uid="u", events={"det": [0.0]}, event_count=1, age_s=1.0)
    assert AcquisitionHealthFeed().evaluate(_ctx(), win, []) is None
