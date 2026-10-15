"""HappiBackend builds every happi.Client through one overridable hook."""

from __future__ import annotations

import json
from pathlib import Path

from lightfall.devices.backends.happi import HappiBackend


class _CountingHappi(HappiBackend):
    def __init__(self, path: str) -> None:
        super().__init__(path=path, name="counting")
        self.calls = 0

    def _make_client(self):
        self.calls += 1
        return super()._make_client()


def test_connect_load_and_reload_all_use_make_client(tmp_path: Path) -> None:
    db = tmp_path / "db.json"
    db.write_text(json.dumps({}))
    backend = _CountingHappi(str(db))

    # load_metadata before connect builds a temporary client
    backend.load_metadata()
    assert backend.calls == 1

    assert backend.connect() is True
    assert backend.calls == 2

    assert backend.reload() is True
    assert backend.calls == 3
