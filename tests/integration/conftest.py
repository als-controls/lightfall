"""Integration test conftest.

Pre-import pandas before PySide6 to avoid the shiboken/six/dateutil
import hook conflict (PySide6's shiboken monkey-patches inspect, which
breaks when six's _SixMetaPathImporter is encountered during pandas import).
"""

import pandas  # noqa: F401  — must happen before PySide6
