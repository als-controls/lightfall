"""Backend-enable resolution from preferences (incl. the all-off fix)."""
from __future__ import annotations

from lightfall.main import _resolve_enabled_backends


def _get(prefs: dict):
    """A prefs.get(key, default)-style callable backed by a dict."""
    return lambda key, default=None: prefs.get(key, default)


def test_fresh_config_falls_back_to_mock():
    # Nothing set at all -> mock fallback.
    assert _resolve_enabled_backends(_get({})) == (True, False, False)


def test_explicit_all_off_is_honored_not_overridden():
    # The reported bug: user turned everything off; must NOT be forced to mock.
    prefs = {
        "device_mock_enabled": False,
        "device_bcs_enabled": False,
        "device_happi_enabled": False,
    }
    assert _resolve_enabled_backends(_get(prefs)) == (False, False, False)


def test_explicit_happi_only():
    prefs = {
        "device_mock_enabled": False,
        "device_bcs_enabled": False,
        "device_happi_enabled": True,
    }
    assert _resolve_enabled_backends(_get(prefs)) == (False, False, True)


def test_legacy_device_backend_mock():
    assert _resolve_enabled_backends(_get({"device_backend": "mock"})) == (True, False, False)


def test_legacy_device_backend_bcs():
    assert _resolve_enabled_backends(_get({"device_backend": "bcs"})) == (False, True, False)


def test_legacy_backend_present_but_unrecognized_does_not_force_mock():
    # A legacy key is a preference, so the mock fallback must not fire.
    assert _resolve_enabled_backends(_get({"device_backend": "happi"})) == (False, False, False)


def test_explicit_flag_overrides_legacy():
    prefs = {"device_backend": "mock", "device_mock_enabled": False}
    assert _resolve_enabled_backends(_get(prefs)) == (False, False, False)
