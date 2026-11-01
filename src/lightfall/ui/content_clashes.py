"""Tell the user when scoped content lost a name clash.

Plans and plugins that were not loaded get one warning dialog per batch; agents and
skills that shadow another (nothing is refused) get a toast.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Slot
from PySide6.QtWidgets import QMessageBox, QWidget

from lightfall.content.clashes import Clash, ClashLog

_CODE_TYPES = ("plan", "plugin")
_MAX_LINES = 20
_RENAME_HINT = "Rename the item that was not loaded to use it."


def clash_line(clash: Clash) -> str:
    """One readable line for a clash."""
    if clash.type in _CODE_TYPES:
        return (
            f"{clash.type} {clash.name} from {clash.loser} was not loaded: "
            f"{clash.winner} has one with the same name"
        )
    return f"{clash.type} {clash.name} from {clash.winner} shadows the one from {clash.loser}"


def _message(clashes: list[Clash]) -> str:
    lines = [clash_line(c) for c in clashes[:_MAX_LINES]]
    if len(clashes) > _MAX_LINES:
        lines.append(f"and {len(clashes) - _MAX_LINES} more")
    return "\n".join(lines)


class ClashNotifier(QObject):
    """Shows each clash in ClashLog once per process, batching bursts for ``delay_ms``.

    Holds everything until :meth:`arm` is called, so clashes found at startup (while
    the login dialog is up) and in the post-login plugin wave land in one dialog over
    the shown main window.
    """

    def __init__(
        self,
        parent_widget: QWidget | None,
        *,
        show_dialog: Callable[[str, str], None] | None = None,
        toast: Callable[[str, str], None] | None = None,
        delay_ms: int = 1500,
    ) -> None:
        super().__init__(parent_widget)
        self._parent_widget = parent_widget
        self._show_dialog = show_dialog or self._default_dialog
        self._toast = toast or self._default_toast
        self._shown: set[Clash] = set()
        self._code: list[Clash] = []
        self._shadow: list[Clash] = []
        self._armed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._flush)
        log = ClashLog.get_instance()
        # Queued: reports may come from worker threads; show on this (GUI) thread.
        log.added.connect(self._on_added, Qt.ConnectionType.QueuedConnection)
        self._on_added(log.all())  # clashes found before the window existed

    def arm(self) -> None:
        """Start showing clashes: everything held so far goes out as one batch."""
        if self._armed:
            return
        self._armed = True
        self._start_timer()

    def _start_timer(self) -> None:
        if self._armed and (self._code or self._shadow) and not self._timer.isActive():
            self._timer.start()

    @Slot(list)
    def _on_added(self, clashes: list[Clash]) -> None:
        for clash in clashes:
            if clash in self._shown:
                continue
            self._shown.add(clash)
            (self._code if clash.type in _CODE_TYPES else self._shadow).append(clash)
        self._start_timer()

    def _flush(self) -> None:
        code, self._code = self._code, []
        shadow, self._shadow = self._shadow, []
        if code:
            self._show_dialog("Content not loaded", _message(code) + "\n\n" + _RENAME_HINT)
        if shadow:
            self._toast("Content shadowed", _message(shadow))

    def _default_dialog(self, title: str, text: str) -> None:
        QMessageBox.warning(self._parent_widget, title, text)

    def _default_toast(self, title: str, text: str) -> None:
        from lightfall.ui.toast import ToastManager

        # Parent to the main window, never whatever window happens to be active.
        ToastManager.get_instance().warning(title, text, parent=self._parent_widget)
