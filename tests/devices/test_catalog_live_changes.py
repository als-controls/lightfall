"""Catalog side of live device changes: async reload and backend removal."""

from __future__ import annotations

import threading

from lightfall.devices.backends.mock import MockBackend
from lightfall.devices.catalog import DeviceCatalog


class _ReloadingMock(MockBackend):
    def __init__(self):
        super().__init__(include_noisy=False)
        self.reload_threads = []

    def reload(self) -> bool:
        self.reload_threads.append(threading.current_thread())
        return True


def _loaded(backend) -> DeviceCatalog:
    catalog = DeviceCatalog()  # fresh instance, not the singleton
    catalog.add_backend(backend)
    backend.connect()
    catalog._load_backend_devices(backend)
    return catalog


def test_reload_async_reads_sources_off_the_gui_thread(qtbot):
    backend = _ReloadingMock()
    catalog = _loaded(backend)
    done = []

    catalog.reload_backends_async(lambda ok: done.append((ok, threading.current_thread())))

    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    assert backend.reload_threads and backend.reload_threads[0] is not threading.main_thread()
    assert done == [(True, threading.main_thread())]
    assert len(catalog.get_all_devices()) == len(backend.get_all_devices())


def test_remove_backend_emits_device_removed_for_its_devices(qtbot):
    backend = _ReloadingMock()
    catalog = _loaded(backend)
    ids = {str(d.id) for d in catalog.get_all_devices()}
    assert ids
    removed = []
    catalog.device_removed.connect(removed.append)

    catalog.remove_backend(backend.name)

    assert set(removed) == ids
    assert catalog.get_all_devices() == []
