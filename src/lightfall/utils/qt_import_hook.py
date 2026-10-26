"""Skip PySide6's per-import source scan.

Once PySide6 is imported, shiboken's ``__feature__`` support hooks every
later import: for each new module it calls
``shibokensupport.feature._mod_uses_pyside``, which runs
``inspect.getsource(module)`` and checks the text for ``"PySide6"``. That
means every ``.py`` file Python imports is opened and read a second time
(the import itself reads only the ``.pyc``). Lightfall imports ~4,000
modules at startup, most after PySide6.

Measured on shussebora (2026-10-26, issue #39), importing Lightfall's
startup set after PySide6: 6.5 s with the scan vs 5.5 s without on a warm
file cache, and 32.5 s vs 5.5 s on the first run after the ``.py`` sources
had left the OS file cache.

The scan only decides the default for PySide's opt-in ``__feature__``
switches (``snake_case``, ``true_property``). Nothing in Lightfall or its
plugins uses them, and a module that does ``from __feature__ import ...``
still selects its features explicitly, so answering "no" for every module
is safe. Set ``LIGHTFALL_PYSIDE_FEATURE_SCAN=1`` to keep PySide's default.
"""

from __future__ import annotations

import os
import sys

from lightfall.utils.logging import logger

ENV_VAR = "LIGHTFALL_PYSIDE_FEATURE_SCAN"


def _no_scan(_module) -> bool:
    return False


def skip_pyside_feature_scan() -> bool:
    """Replace shiboken's source scan with a constant answer. Returns True if
    applied. Safe to call more than once and before or after PySide6 loads
    (it imports ``PySide6.QtCore`` to make sure the hook exists)."""
    if os.environ.get(ENV_VAR, "").strip() not in ("", "0"):
        return False
    try:
        import PySide6.QtCore  # noqa: F401  (installs shibokensupport)

        feature = sys.modules.get("shibokensupport.feature")
        if feature is None or not hasattr(feature, "_mod_uses_pyside"):
            logger.debug("PySide feature scan hook not found; nothing to skip")
            return False
        feature._mod_uses_pyside = _no_scan
        return True
    except Exception as e:  # noqa: BLE001 - an optimisation must never stop startup
        logger.debug("Could not skip PySide feature scan: {}", e)
        return False
