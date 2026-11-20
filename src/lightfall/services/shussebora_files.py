"""Tell shussebora that files are complete, so it moves them right away.

A detector that writes its own files (no EPICS file-writer plugin for
shussebora to watch) calls :func:`notify_files_closed` once a file is closed.
Each path goes to the shussebora instance, among those
:class:`~lightfall.services.shussebora_monitor.ShusseboraMonitor` has seen on
the bus, whose ``sources[].root`` covers it (longest root wins), as one
``<prefix>.file.closed`` request per instance::

    request:  {"paths": ["D:/data/12.0.2.1/pi_mte/...h5", ...]}
    reply:    {"status": "ok", "queued": [...], "rejected": [{"path", "reason"}]}

The call never raises and never blocks: routing and the requests run on a
daemon thread, and failures are logged at WARNING. shussebora's catch-up pass
picks up anything a lost message missed. A path no instance covers (a dev box
without shussebora) is logged once at INFO per directory.
"""

from __future__ import annotations

import ntpath
import posixpath
import re
import threading
from collections.abc import Callable, Iterable
from typing import Any

from lightfall.utils.logging import logger

ACTION = "file.closed"
TIMEOUT_MS = 5000

# Same rule as lightfall_crucible.claims._key/_under (kept in sync by hand).
_WIN_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|//)")

_STATE_RANK = {"ok": 3, "unknown": 2, "stale": 1, "dead": 0}

_reported_uncovered: set[str] = set()
_reported_lock = threading.Lock()


def _key(path: str) -> tuple[str, str]:
    """Comparable form of a path and its separator.

    Drive-letter and UNC paths: ``/`` and ``\\`` are equivalent, ``..`` is
    resolved and case is folded. POSIX paths: normalized, case kept.
    """
    if _WIN_RE.match(path):
        return ntpath.normpath(path.replace("/", "\\")).casefold(), "\\"
    return posixpath.normpath(path), "/"


def _under(path: str, root: str) -> bool:
    """Whether ``path`` is ``root`` or lies below it."""
    (p, sep), (r, rsep) = _key(path), _key(root)
    if sep != rsep:
        return False
    return p == r or p.startswith(r if r.endswith(sep) else r + sep)


def _prefix(hostname: str, status: dict[str, Any]) -> str:
    """NATS prefix of an instance: the status' ``prefix``, else derived."""
    prefix = status.get("prefix")
    if prefix:
        return str(prefix)
    beamline = str(status.get("beamline") or "").replace(".", "")
    return f"shussebora.{beamline}.{hostname}" if beamline else f"shussebora.{hostname}"


def _roots(status: dict[str, Any]) -> list[str]:
    roots = []
    for src in status.get("sources") or []:
        if isinstance(src, dict) and src.get("root"):
            roots.append(str(src["root"]))
    return roots


def route(paths: Iterable[str], instances: dict[str, dict[str, Any]]
          ) -> tuple[dict[str, list[str]], list[str]]:
    """Group ``paths`` by the NATS prefix of the instance that covers each.

    Args:
        paths: local file paths.
        instances: :meth:`ShusseboraMonitor.instances` snapshot.

    Returns:
        ``({prefix: [paths]}, uncovered_paths)``. The longest covering root
        wins; on a tie the healthier instance (ok > unknown > stale > dead).
    """
    groups: dict[str, list[str]] = {}
    uncovered: list[str] = []
    for path in paths:
        best: tuple[int, int] | None = None
        best_prefix = ""
        for hostname, inst in instances.items():
            status = inst.get("status") or {}
            rank = _STATE_RANK.get(inst.get("state", "unknown"), 0)
            for root in _roots(status):
                if not _under(path, root):
                    continue
                score = (len(_key(root)[0]), rank)
                if best is None or score > best:
                    best, best_prefix = score, _prefix(hostname, status)
        if best is None:
            uncovered.append(path)
        else:
            groups.setdefault(best_prefix, []).append(path)
    return groups, uncovered


def _report_uncovered(paths: list[str]) -> None:
    for path in paths:
        key, sep = _key(path)
        folder = key.rsplit(sep, 1)[0] if sep in key else key
        with _reported_lock:
            if folder in _reported_uncovered:
                continue
            _reported_uncovered.add(folder)
        logger.info("No shussebora instance covers {}; not notifying (logged once per folder)",
                    path)


def _send(ipc: Any, prefix: str, paths: list[str]) -> None:
    subject = f"{prefix}.{ACTION}"
    reply = ipc.request(subject, {"paths": paths}, timeout_ms=TIMEOUT_MS)
    if not isinstance(reply, dict):
        logger.warning("shussebora {}: no reply for {} file(s); catch-up will move them: {}",
                       subject, len(paths), paths)
        return
    if reply.get("status") != "ok":
        logger.warning("shussebora {}: unexpected reply {!r} for {}", subject, reply, paths)
        return
    for item in reply.get("rejected") or []:
        if isinstance(item, dict):
            logger.warning("shussebora {} rejected {}: {}", subject, item.get("path"),
                           item.get("reason"))
        else:
            logger.warning("shussebora {} rejected {}", subject, item)
    logger.debug("shussebora {} queued {}", subject, reply.get("queued"))


def _work(monitor: Any, paths: list[str]) -> None:
    try:
        if monitor is None:
            from lightfall.services.shussebora_monitor import ShusseboraMonitor

            monitor = ShusseboraMonitor._instance
        instances = monitor.instances() if monitor is not None else {}
        groups, uncovered = route(paths, instances)
        if uncovered:
            _report_uncovered(uncovered)
        if not groups:
            return
        ipc = monitor.ipc()
        if ipc is None:
            logger.warning("shussebora: no IPC service; cannot notify {} file(s)", len(paths))
            return
        for prefix, group in groups.items():
            try:
                _send(ipc, prefix, group)
            except Exception as ex:
                logger.warning("shussebora {}.{} failed: {}", prefix, ACTION, ex)
    except Exception as ex:
        logger.warning("shussebora file.closed notification failed: {}", ex)


def _ensure_monitor() -> Any:
    """The monitor singleton, started on the GUI thread if it isn't yet.

    Returns the instance if it already exists (it may still be starting), else
    ``None``; the worker re-reads it.
    """
    from lightfall.services.shussebora_monitor import ShusseboraMonitor

    monitor = ShusseboraMonitor._instance
    if monitor is not None and monitor.started:
        return monitor

    def start() -> None:
        try:
            ShusseboraMonitor.get_instance().start()
        except Exception as ex:
            logger.warning("Could not start the shussebora monitor: {}", ex)

    # The singleton is a QObject with a QTimer: create and start it on the GUI thread.
    from lightfall.utils.threads import invoke_in_main_thread

    invoke_in_main_thread(start)
    return ShusseboraMonitor._instance


def _spawn(target: Callable[..., None], *args: Any) -> None:
    threading.Thread(target=target, args=args, daemon=True,
                     name="shussebora-file-closed").start()


def notify_files_closed(paths: list[str]) -> None:
    """Tell the covering shussebora instances that ``paths`` are complete.

    Safe to call from any thread; returns at once and never raises.
    """
    try:
        items = [str(p) for p in paths if p]
        if not items:
            return
        monitor = _ensure_monitor()
        _spawn(_work, monitor, items)
    except Exception as ex:
        logger.warning("shussebora file.closed notification failed: {}", ex)
