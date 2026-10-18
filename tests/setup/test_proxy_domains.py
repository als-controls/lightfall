"""ProxySettingsProvider auto-detect reads the facility's proxy domains."""
from __future__ import annotations

from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider as P


def test_default_domains_keep_the_lbl_behaviour(prefs):
    prefs.set("proxy_auto_detect", True)
    assert P.get_auto_detect_domains() == ["*.lbl.gov"]
    assert P.should_use_proxy_for_url("http://bcglucidlogbook.dhcp.lbl.gov:8000") == (
        "socks5://localhost:1080")
    assert P.should_use_proxy_for_url("http://lbl.gov.evil.example") is None


def test_auto_detect_uses_the_bootstrap_domains(prefs):
    prefs.set("proxy_auto_detect", True)
    prefs.set("proxy_auto_detect_domains", ["*.example.org"])
    assert P.should_use_proxy_for_url("http://srv.EXAMPLE.org/x") == "socks5://localhost:1080"
    assert P.should_use_proxy_for_url("http://bcg.lbl.gov") is None


def test_a_string_domain_pref_is_accepted(prefs):
    prefs.set("proxy_auto_detect_domains", "*.Example.org")
    assert P.get_auto_detect_domains() == ["*.example.org"]


def test_auto_detect_off_means_no_proxy(prefs):
    assert P.should_use_proxy_for_url("http://bcg.lbl.gov") is None


def test_host_matches():
    assert P.host_matches("a.b.lbl.gov.", ["*.lbl.gov"])
    assert not P.host_matches("lbl.gov", ["*.lbl.gov"])
