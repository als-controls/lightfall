from lightfall.monitor.buffer import RollingBuffer


def test_buffer_accumulates_and_snapshots():
    buf = RollingBuffer()
    buf("start", {"uid": "u1", "time": 100.0})
    buf("descriptor", {"name": "primary", "data_keys": {"det": {"shape": []}}})
    buf("event", {"seq_num": 1, "time": 101.0, "data": {"det": 5.0}})
    buf("event", {"seq_num": 2, "time": 102.0, "data": {"det": 6.0}})

    win = buf.snapshot(now=105.0)
    assert win.run_uid == "u1"
    assert win.event_count == 2
    assert win.series("det") == [5.0, 6.0]
    assert win.latest("det") == 6.0
    assert win.age_s == 3.0  # 105 - 102


def test_start_resets_previous_run():
    buf = RollingBuffer()
    buf("start", {"uid": "u1", "time": 0.0})
    buf("event", {"seq_num": 1, "time": 1.0, "data": {"det": 1.0}})
    buf("start", {"uid": "u2", "time": 10.0})
    win = buf.snapshot(now=10.0)
    assert win.run_uid == "u2"
    assert win.event_count == 0


def test_snapshot_with_no_events_has_none_age():
    buf = RollingBuffer()
    buf("start", {"uid": "u1", "time": 0.0})
    assert buf.snapshot(now=5.0).age_s is None
