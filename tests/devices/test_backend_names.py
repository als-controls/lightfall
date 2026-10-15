"""Backends take an instance name so several rows of one type can coexist."""

from __future__ import annotations

from lightfall.devices.backends.bcs import BCSBackend
from lightfall.devices.backends.mock import MockBackend


def test_mock_default_name_unchanged():
    assert MockBackend().name == "mock"


def test_mock_custom_name():
    assert MockBackend(name="mock-2").name == "mock-2"


def test_bcs_default_name_unchanged():
    assert BCSBackend().name == "bcs_zmq"


def test_bcs_custom_name():
    assert BCSBackend(host="h", name="bcs-7011").name == "bcs-7011"
