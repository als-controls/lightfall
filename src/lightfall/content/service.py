"""Keep this machine's copy of scoped content current while someone is logged in.

Syncs at login, when the scope chain changes, and on ``lightfall.content.changed``
(notify-and-pull, as settings do). It only downloads: loading is Plan 3's loaders,
which read ``ContentStore.sources(bind)`` and react to ``synced``.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from lightfall.content.store import ContentStore
from lightfall.content.sync import SyncResult, sync_chain
from lightfall.utils.logging import logger

CONTENT_SUBJECT = "lightfall.content.changed"  # lightfall_logbook.content_api.CONTENT_SUBJECT

Runner = Callable[[Callable[[], Any], Callable[[Any], None], Callable[[Exception], None]], None]


def _default_ipc() -> Any:
    from lightfall.ipc.service import get_ipc_service

    return get_ipc_service()


class ContentSync(QObject):
    synced = Signal(object)  # SyncResult, after a sync that installed or removed something

    def __init__(self, *, client: Any, store: ContentStore, bind: Callable[[], str],
                 run: Runner | None = None, ipc: Callable[[], Any] | None = None,
                 debounce_ms: int = 500, poll_ms: int = 300_000,
                 sync: Callable[..., SyncResult] = sync_chain) -> None:
        super().__init__()
        if run is None:
            from lightfall.ui.preferences.scoped_backend import _thread_runner as run
        self._client = client
        self._store = store
        self._bind = bind
        self._run = run
        self._sync = sync
        self._ipc_get = ipc or _default_ipc
        self._ipc: Any = None
        self._chain: tuple[str, ...] = ()
        self._started = False
        self._busy = False
        self._again = False
        self._offline = False
        self._session = 0  # bumped by stop() so results from before it are dropped
        self._pruned = False  # the first sync of this process prunes old folders
        self._debounce_ms = debounce_ms
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.refresh)
        # Retries after an offline login or a failed item (events only say "changed").
        self._poll_ms = poll_ms
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh)

    @property
    def store(self) -> ContentStore:
        return self._store

    @property
    def chain(self) -> tuple[str, ...]:
        return self._chain

    @property
    def offline(self) -> bool:
        return self._offline

    def start(self, chain: Sequence[str]) -> None:
        """A user is logged in and ``chain`` is their resolved scope chain: sync now."""
        self._chain = tuple(chain)
        self._started = True
        self._subscribe()
        if self._poll_ms > 0:
            self._poll.start(self._poll_ms)
        self.refresh()

    def follow(self, info: Any) -> None:
        """Slot for ``PreferencesManager.scopes_changed``: sync while scopes come from the
        server, stop otherwise (logged out, local scope files, scopes off)."""
        if getattr(info, "mode", None) == "server":
            self.start(info.chain)
        elif self._started:
            self.stop()

    def stop(self) -> None:
        """Logged out: stop syncing. The copy on disk stays for guests."""
        self._started = False
        self._chain = ()
        self._session += 1
        # _busy stays set: a worker may still be syncing, and the next session must wait
        # for it (the dropped result clears it) rather than run a second sync beside it.
        self._again = False
        self._debounce.stop()
        self._poll.stop()
        self._unsubscribe()

    @Slot()  # by name: lightfall.content.actions.refresh_sync queues it from workers
    def refresh(self) -> None:
        if not self._started:
            return
        if self._busy:
            self._again = True
            return
        self._busy = True
        self._again = False  # this run covers every request made so far
        session = self._session
        prune, self._pruned = not self._pruned, True
        client, store, bind, chain, sync = (self._client, self._store, self._bind(),
                                            self._chain, self._sync)

        def ok(result: SyncResult) -> None:
            if session == self._session:
                self._on_synced(result)
            else:
                self._on_stale()

        def err(exc: Exception) -> None:
            if session == self._session:
                self._on_failed(exc)
            else:
                self._on_stale()

        self._run(lambda: sync(client, store, bind, chain, prune=prune), ok, err)

    def _on_stale(self) -> None:
        """A sync from before stop() finished: free the slot for the current session."""
        self._busy = False
        if self._started:
            self.refresh()

    def _on_synced(self, result: SyncResult) -> None:
        self._busy = False
        self._offline = result.offline
        if any(s.changed or s.removed for s in result.scopes):
            logger.info("Scoped content updated: {}", sorted(result.changed_types))
            self.synced.emit(result)
        self._after()

    def _on_failed(self, exc: Exception) -> None:
        self._busy = False
        self._offline = True
        logger.warning("Scoped content sync failed: {}", exc)
        self._after()

    def _after(self) -> None:
        if self._again:
            self._again = False
            self.refresh()

    # -- live updates -----------------------------------------------------------------

    def _subscribe(self) -> None:
        if self._ipc is not None:
            return
        try:
            ipc = self._ipc_get()
        except Exception as e:
            logger.debug("No IPC service for content events: {}", e)
            return
        if ipc is None:
            return
        try:
            ipc.subscribe(CONTENT_SUBJECT, self._on_event, main_thread=True)
            ipc.sigConnectionChanged.connect(self._on_connection)
        except Exception as e:
            logger.warning("Could not subscribe to content events: {}", e)
            return
        self._ipc = ipc

    def _unsubscribe(self) -> None:
        ipc, self._ipc = self._ipc, None
        if ipc is None:
            return
        try:
            ipc.unsubscribe(CONTENT_SUBJECT)
            ipc.sigConnectionChanged.disconnect(self._on_connection)
        except Exception:
            pass

    def _on_event(self, subject: str, data: Any, reply: str | None) -> None:
        if not self._started or not isinstance(data, dict) or data.get("scope") not in self._chain:
            return
        if str(data["scope"]).startswith("user:"):
            return
        if self._debounce_ms <= 0:
            self.refresh()
        else:
            self._debounce.start(self._debounce_ms)

    def _on_connection(self, connected: bool) -> None:
        if connected:
            self.refresh()
