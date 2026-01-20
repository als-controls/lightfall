"""NCS data acquisition module.

Provides Qt-integrated Bluesky RunEngine for scientific data acquisition.
"""

from ncs.acquire.runengine import QRunEngine, get_run_engine

__all__ = ["QRunEngine", "get_run_engine"]
