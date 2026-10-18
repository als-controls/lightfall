"""Facility and Network step logic (no widgets)."""
from __future__ import annotations

import pytest

from lightfall.setup.bootstrap import Bootstrap, NetworkError, apply_bootstrap, has_bootstrap
from lightfall.setup.facilities import Facility
from lightfall.setup.step import StepError, StepState, StepStatus
from lightfall.setup.steps import network as net
from lightfall.setup.steps.facility import FacilityStep
from lightfall.setup.steps.network import NetworkStep

BOOT = Bootstrap("http://srv.example:8000", {"schema": 1})


def test_facility_detect(ctx):
    step = FacilityStep()
    assert step.detect(ctx) == StepState(StepStatus.NEEDS_INPUT, "Choose where you work")
    apply_bootstrap(Bootstrap("http://s", {"schema": 1, "facility": {
        "id": "als", "name": "Advanced Light Source"}}), ctx.config)
    assert step.detect(ctx) == StepState(StepStatus.SATISFIED, "Advanced Light Source")


def test_facility_validate_and_apply(ctx):
    step = FacilityStep()
    assert "Choose your facility" in step.validate(ctx)
    ctx.facility = Facility("x", "X", "ftp://bad")
    assert step.validate(ctx)
    ctx.facility = Facility("als", "ALS", "http://new:8000")
    assert step.validate(ctx) is None
    ctx.bootstrap = Bootstrap("http://old:8000", {"schema": 1})
    step.apply(ctx)
    assert ctx.bootstrap is None
    ctx.bootstrap = Bootstrap("http://new:8000", {"schema": 1})
    step.apply(ctx)
    assert ctx.bootstrap is not None


def test_facility_step_lists_the_packaged_facilities():
    assert [f.id for f in FacilityStep().facilities()] == ["als"]


def test_network_detect(ctx, monkeypatch):
    step = NetworkStep()
    assert step.detect(ctx) == StepState(StepStatus.NEEDS_INPUT, "Choose a facility first")
    ctx.facility = Facility("als", "ALS", "http://srv.example:8000")

    def down(server, **kwargs):
        raise NetworkError("could not connect")

    monkeypatch.setattr(net, "probe_health", down)
    assert step.detect(ctx) == StepState(StepStatus.FAILED, "srv.example not reachable")
    monkeypatch.setattr(net, "probe_health", lambda server, **kwargs: None)
    assert step.detect(ctx) == StepState(StepStatus.NEEDS_INPUT, "Facility settings not loaded")
    apply_bootstrap(BOOT, ctx.config)
    assert step.detect(ctx) == StepState(StepStatus.SATISFIED, "Direct")


def test_network_validate(ctx):
    assert NetworkStep().validate(ctx)
    ctx.bootstrap = BOOT
    assert NetworkStep().validate(ctx) is None


def test_network_apply_writes_the_bootstrap_and_reconfigures(ctx, monkeypatch):
    seen = []
    monkeypatch.setattr(net, "reconfigure_services", lambda config: seen.append(config) or [])
    ctx.facility = Facility("als", "ALS", "http://srv.example:8000", proxy_domains=("*.lbl.gov",))
    ctx.bootstrap = Bootstrap("http://srv.example:8000",
                              {"schema": 1, "agent": {"backend": "agy"}})
    NetworkStep().apply(ctx)
    assert seen == [ctx.config]
    assert ctx.config.get("preferences.agent_backend") == "agy"
    assert ctx.config.get("preferences.proxy_auto_detect_domains") == ["*.lbl.gov"]


def test_network_apply_reports_reconfigure_errors(ctx, monkeypatch):
    monkeypatch.setattr(net, "reconfigure_services", lambda config: ["Tiled: boom"])
    ctx.bootstrap = BOOT
    with pytest.raises(StepError, match="Tiled: boom"):
        NetworkStep().apply(ctx)
    assert has_bootstrap(ctx.config)


def test_connect_probes_then_fetches(monkeypatch):
    order = []
    monkeypatch.setattr(net, "probe_health", lambda s, proxy: order.append(("probe", proxy)))
    monkeypatch.setattr(net, "fetch_bootstrap",
                        lambda s, proxy: order.append(("fetch", proxy)) or BOOT)
    assert net.connect("http://s", "socks5://h:1") is BOOT
    assert order == [("probe", "socks5://h:1"), ("fetch", "socks5://h:1")]


def test_probe_socks_is_false_on_a_closed_port():
    assert net.probe_socks("127.0.0.1", 9, timeout=0.5) is False


def test_route_label(prefs):
    assert net.route_label("http://bcg.lbl.gov") == "Direct"
    prefs.set("proxy_auto_detect", True)
    assert net.route_label("http://bcg.lbl.gov") == "Via SOCKS proxy"
