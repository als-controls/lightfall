"""Tests for the Trigger ABC."""
from __future__ import annotations

import pytest

from lucid.acquire.triggers.base import Trigger


def test_trigger_is_abstract():
    with pytest.raises(TypeError):
        Trigger()                       # type: ignore[abstract]


def test_concrete_trigger_must_implement_attach_and_detach():
    class Half(Trigger):
        def attach(self, manager):
            pass
        # missing detach
    with pytest.raises(TypeError):
        Half()                          # type: ignore[abstract]
