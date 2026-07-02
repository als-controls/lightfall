import pytest
from lightfall.monitor.data_window import DataWindow


def _win():
    return DataWindow(
        run_uid="u1",
        events={"det": [1.0, 2.0, 3.0]},
        seq_nums=[1, 2, 3],
        timestamps=[10.0, 11.0, 12.0],
        event_count=3,
        age_s=4.0,
    )


def test_latest_and_series():
    w = _win()
    assert w.latest("det") == 3.0
    assert w.latest("missing") is None
    assert w.series("det", last_k=2) == [2.0, 3.0]
    assert w.series("det") == [1.0, 2.0, 3.0]


def test_derived_defaults_none_and_pv_get_raises_without_hooks():
    w = _win()
    assert w.derived("xpcs") is None
    with pytest.raises(NotImplementedError):
        w.pv_get("BL:PV")


def test_hooks_are_used_when_set():
    w = _win()
    w.derived_provider = lambda name: {"name": name}
    w.pv_getter = lambda pv: f"val:{pv}"
    assert w.derived("xpcs") == {"name": "xpcs"}
    assert w.pv_get("BL:PV") == "val:BL:PV"
