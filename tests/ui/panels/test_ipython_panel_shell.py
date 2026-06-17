"""Tests for IPythonPanel public kernel accessor."""
from __future__ import annotations

import pytest

pytest.importorskip("qtconsole")
pytest.importorskip("ipykernel")

from lightfall.ui.panels.ipython_panel import IPythonPanel


def test_ensure_kernel_then_shell_available(qapp):
    panel = IPythonPanel()
    assert panel.ensure_kernel() is True
    shell = panel.shell
    assert shell is not None
    # The shell is a usable IPython InteractiveShell: it has a user namespace.
    assert hasattr(shell, "user_ns")
