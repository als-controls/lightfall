"""site: section of the machine config (scoped settings binding)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from lightfall.config.schema import LFConfig


def test_site_defaults_to_unbound_als():
    cfg = LFConfig()
    assert cfg.site.facility == "als"
    assert cfg.site.bind is None


@pytest.mark.parametrize("bind", ["facility", "beamline:7.0.1", "branch:7.0.1.1"])
def test_site_accepts_scope_binds(bind):
    assert LFConfig(site={"bind": bind}).site.bind == bind


def test_site_empty_bind_means_unbound():
    assert LFConfig(site={"bind": ""}).site.bind is None


@pytest.mark.parametrize("bind", ["user:alice", "branch:", "7.0.1", "branch:7.0.1.1/x",
                                  "beamline:_all"])
def test_site_rejects_bad_binds(bind):
    with pytest.raises(ValidationError):
        LFConfig(site={"bind": bind})


def test_get_site_bind_falls_back_to_facility(monkeypatch):
    from lightfall.scopes import site

    def boom():
        raise RuntimeError("no app")

    monkeypatch.setattr(site, "_site_config", boom)
    assert site.get_site_bind() == "facility"


def test_default_cache_dir_is_under_ncs_config(monkeypatch, tmp_path):
    from lightfall.scopes import site

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert site.default_cache_dir() == tmp_path / "ncs" / "scope-cache"


def test_scopes_available_in_dev_venv():
    from lightfall.scopes import scopes_available

    assert scopes_available() is True
