from datetime import datetime, timedelta

from lightfall.monitor.data_window import DataWindow
from lightfall.monitor.feeds.beam_status import BeamStatusFeed
from lightfall.monitor.models import ExperimentContext
from lightfall.services.als_beam_status import ALSBeamData


def _beam(available=True, current=500.0, age_s=0.0):
    return ALSBeamData(beam_current=current, beam_available=available,
                       comment="RF trip", timestamp=datetime.now() - timedelta(seconds=age_s))


def _win(uid="u"):
    return DataWindow(run_uid=uid)


class _Source:
    def __init__(self, data):
        self.data = data

    def __call__(self):
        return self.data


def test_offline_is_silent():
    feed = BeamStatusFeed(source=lambda: None)
    assert feed.evaluate(ExperimentContext(), _win(), []) is None


def test_beam_lost_during_run_is_critical_once():
    src = _Source(_beam())
    feed = BeamStatusFeed(source=src)
    ctx = ExperimentContext()
    assert feed.evaluate(ctx, _win(), []) is None
    src.data = _beam(available=False)
    obs = feed.evaluate(ctx, _win(), [])
    assert obs is not None and obs.severity == "critical"
    assert obs.state_key == "beam_status:beam"
    assert "RF trip" in obs.message
    assert feed.evaluate(ctx, _win(), []) is None


def test_beam_restored_after_loss_is_info():
    src = _Source(_beam())
    feed = BeamStatusFeed(source=src)
    ctx = ExperimentContext()
    feed.evaluate(ctx, _win(), [])
    src.data = _beam(available=False)
    feed.evaluate(ctx, _win(), [])
    src.data = _beam(current=480.0)
    obs = feed.evaluate(ctx, _win(), [])
    assert obs is not None and obs.state_key == "beam_status:beam"
    assert obs.severity == "info"


def test_run_started_without_beam_stays_silent():
    feed = BeamStatusFeed(source=_Source(_beam(available=False)))
    assert feed.evaluate(ExperimentContext(), _win(), []) is None


def test_new_run_resets_state():
    src = _Source(_beam())
    feed = BeamStatusFeed(source=src)
    ctx = ExperimentContext()
    feed.evaluate(ctx, _win("a"), [])
    src.data = _beam(available=False)
    assert feed.evaluate(ctx, _win("b"), []) is None


def test_low_current_only_when_configured():
    feed = BeamStatusFeed(source=_Source(_beam(current=100.0)))
    assert feed.evaluate(ExperimentContext(), _win(), []) is None
    ctx = ExperimentContext(feed_config={"beam_status": {"min_current_ma": 400}})
    obs = feed.evaluate(ctx, _win(), [])
    assert obs is not None and obs.state_key == "beam_status:low_current"
    assert obs.severity == "warn"


def test_stale_data_is_ignored():
    src = _Source(_beam())
    feed = BeamStatusFeed(source=src)
    ctx = ExperimentContext()
    feed.evaluate(ctx, _win(), [])
    src.data = _beam(available=False, age_s=3600)
    assert feed.evaluate(ctx, _win(), []) is None


def test_default_source_never_creates_the_service():
    from lightfall.services.als_beam_status import ALSBeamStatusService

    ALSBeamStatusService.reset()
    feed = BeamStatusFeed()
    assert feed.evaluate(ExperimentContext(), _win(), []) is None
    assert ALSBeamStatusService._instance is None


def test_second_beam_loss_in_same_run_surfaces_again():
    """Loss and restoration share a state key, so the rate limiter sees the
    severity flip each way instead of suppressing the second loss."""
    from lightfall.monitor.rate_limiter import RateLimiter

    src = _Source(_beam())
    feed = BeamStatusFeed(source=src)
    ctx = ExperimentContext()
    limiter = RateLimiter()
    feed.evaluate(ctx, _win(), [])
    surfaced = []
    for data in (_beam(available=False), _beam(), _beam(available=False)):
        src.data = data
        obs = feed.evaluate(ctx, _win(), [])
        assert obs is not None
        if limiter.should_surface(obs):
            surfaced.append(obs.severity)
    assert surfaced == ["critical", "info", "critical"]
