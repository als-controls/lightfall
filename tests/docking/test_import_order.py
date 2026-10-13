"""Import-order regressions for the docking package.

lightfall.ui.theme registers the docking QSS contributor at import time, so importing
lightfall.ui.docking.theme must not drag in the docking manager -> panels -> widgets chain.
Each case runs in a fresh interpreter because the cycle only bites on a cold import.
"""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "lightfall.ui.widgets",
        "lightfall.ui.widgets.device_control",
        "lightfall.ui.theme",
        "lightfall.ui.docking",
        "lightfall.ui.panels",
    ],
)
def test_cold_import(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_docking_exports_still_resolve():
    from lightfall.ui import docking

    assert set(docking.__all__) <= set(dir(docking))
    assert docking.DockingManager.__name__ == "DockingManager"
    assert docking.PanelDockWidget.__name__ == "PanelDockWidget"
