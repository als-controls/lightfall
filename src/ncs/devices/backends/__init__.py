"""Device storage backends for NCS.

Available backends:
- MockBackend: In-memory backend with ophyd.sim devices
- SQLiteBackend: Local SQLite database storage
"""

from ncs.devices.backends.mock import MockBackend

__all__ = [
    "MockBackend",
]
