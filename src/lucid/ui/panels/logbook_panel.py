"""Fragment-based logbook panel for LUCID.

Split-pane layout: entry list (sidebar) + EntryWidget (fragment view),
backed by the offline-first ``LogbookClient`` (synchronous SQLite).
"""

from __future__ import annotations

import getpass
import json
from datetime import datetime
from typing import Any, ClassVar

from PySide6.QtCore import QEvent, QTimer, Signal, Slot
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from lucid.logbook.client import LogbookClient
from lucid.logbook.entry_widget import EntryData, EntryWidget
from lucid.logbook.fragment_widgets import FragmentData, FragmentType
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.utils.logging import logger


class LogbookPanel(BasePanel):
    """Panel displaying the fragment-based logbook.

    Layout::

        ┌──────────┬──────────────────────┐
        │ Entry    │                      │
        │ List     │   EntryWidget        │
        │ (sidebar)│   (fragment view)    │
        │          │                      │
        └──────────┴──────────────────────┘
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.logbook",
        name="Logbook",
        description="Experiment logbook for recording notes and viewing system events",
        icon="book-open",
        category="Core",
        required_permission=None,
        singleton=True,
        closable=False,
        keywords=["log", "notes", "experiment", "journal", "record"],
        default_area="center",
        sidebar_group="top",
        auto_hide=False,
        sidebar_order=0,
    )

    note_added = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        self._client: LogbookClient | None = None
        self._logbook_id: str | None = None
        self._current_entry_id: str | None = None
        self._entries: dict[str, EntryData] = {}
        super().__init__(parent)

        # Deferred init (after widget is shown)
        QTimer.singleShot(0, self._deferred_init)

    def _setup_ui(self) -> None:
        self._entry_widget = EntryWidget(EntryData())
        self._entries_panel = None  # Resolved in _deferred_init

        # Warning banner (guest mode / connection issues)
        self._warning_banner = QFrame(self)
        self._warning_banner.setObjectName("logbookWarningBanner")
        self._warning_banner.setStyleSheet(
            "#logbookWarningBanner {"
            "  border: 1px solid #ffc107; border-radius: 4px;"
            "  padding: 2px 8px;"
            "}"
        )
        self._warning_banner.setFixedHeight(28)
        banner_layout = QHBoxLayout(self._warning_banner)
        banner_layout.setContentsMargins(8, 2, 8, 2)
        self._warning_label = QLabel()
        self._warning_label.setStyleSheet("background: transparent; border: none;")
        banner_layout.addWidget(self._warning_label)
        self._warning_banner.hide()
        self._layout.addWidget(self._warning_banner)
        self._is_guest = False
        self._is_disconnected = False

        # Image button toolbar
        try:
            import qtawesome as qta
            self._add_image_btn = QPushButton()
            self._add_image_btn.setIcon(qta.icon("fa5s.image", color="#aaa"))
            self._add_image_btn.setToolTip("Add image to entry")
            self._add_image_btn.setFixedSize(28, 28)
            self._add_image_btn.clicked.connect(self._on_add_image_clicked)
            self._layout.addWidget(self._add_image_btn)
        except ImportError:
            logger.debug("qtawesome not available, skipping image button")

        self._layout.addWidget(self._entry_widget)

        # Install event filter on QApplication to catch Ctrl+V with image data
        # (child widgets consume key events before parent event filters see them)
        QApplication.instance().installEventFilter(self)

        # Entry widget signals
        self._entry_widget.fragment_added.connect(self._on_fragment_added)
        self._entry_widget.fragment_changed.connect(self._on_fragment_changed)
        self._entry_widget.fragment_deleted.connect(self._on_fragment_deleted)
        self._entry_widget.fragment_reordered.connect(self._on_fragment_reordered)
        self._entry_widget.claude_requested.connect(self._on_claude_requested)
        self._entry_widget.title_changed.connect(self._on_title_changed)
        self._entry_widget.tags_changed.connect(self._on_tags_changed)

    # ── Init ──────────────────────────────────────────────────────

    def _deferred_init(self) -> None:
        try:
            self._client = LogbookClient.get_instance()
            self._client.init()

            user = getpass.getuser()
            self._logbook_id = self._client.get_or_create_logbook(user)

            # Connect to the LogbookEntriesPanel sidebar
            self._connect_entries_panel()

            # Show/hide guest warning based on current and future auth state
            try:
                from lucid.auth.session import SessionManager
                sm = SessionManager.get_instance()
                self._update_guest_banner(sm.current_user)
                sm.user_changed.connect(self._update_guest_banner)
            except Exception:
                pass

            # Monitor sync connection status
            self._client._on_sync_error_callback = lambda: self._set_disconnected(True)
            self._client._on_sync_restored_callback = lambda: self._set_disconnected(False)
            self._client.set_on_entry_created_callback(self._on_ipc_entry_created)

            self._load_entries()
            self._start_event_listener()

            # Background sync after 10s
            QTimer.singleShot(10_000, self._try_sync)

            logger.info("LogbookPanel initialised (logbook={})", self._logbook_id)
        except Exception as e:
            logger.error("LogbookPanel init failed: {}", e)

    def _connect_entries_panel(self) -> None:
        """Find and connect to the LogbookEntriesPanel sidebar."""
        try:
            from lucid.ui.panels.registry import PanelRegistry

            registry = PanelRegistry.get_instance()
            panel = registry.create("lucid.panels.logbook_entries")
            if panel is not None:
                self._entries_panel = panel
                panel.entry_selected.connect(self._on_entry_selected)
                panel.entry_delete_requested.connect(self._on_entry_deleted)
                panel.new_entry_requested.connect(self._on_new_entry_requested)
                logger.debug("Connected to LogbookEntriesPanel")
            else:
                logger.warning("LogbookEntriesPanel not found in registry")
        except Exception as e:
            logger.warning("Could not connect to LogbookEntriesPanel: {}", e)

    def _update_guest_banner(self, user: Any) -> None:
        """Update guest state and refresh the warning banner."""
        try:
            from lucid.auth.policy import Role
            self._is_guest = user.highest_role == Role.GUEST
        except Exception:
            self._is_guest = True
        self._update_warning_banner()

    def _set_disconnected(self, disconnected: bool) -> None:
        """Update connection state and refresh the warning banner."""
        self._is_disconnected = disconnected
        self._update_warning_banner()

    def _update_warning_banner(self) -> None:
        """Show/hide warning banner based on guest and connection state."""
        if self._is_disconnected:
            self._warning_label.setText(
                "⚠️ Logbook server unreachable — changes may result in sync conflicts."
            )
            self._warning_banner.show()
        elif self._is_guest:
            self._warning_label.setText(
                "⚠️ Guest mode — logbook changes may result in sync conflicts."
            )
            self._warning_banner.show()
        else:
            self._warning_banner.hide()

    def _load_entries(self) -> None:
        if not self._client or not self._logbook_id:
            return

        rows = self._client.list_entries(self._logbook_id)
        entry_datas: list[EntryData] = []
        for row in rows:
            ed = self._row_to_entry_data(row)
            self._entries[ed.id] = ed
            entry_datas.append(ed)

        if self._entries_panel:
            self._entries_panel.set_entries(entry_datas)

        if entry_datas:
            first = entry_datas[0]
            if self._entries_panel:
                self._entries_panel.select_entry(first.id)
            self._select_entry(first.id)

    def _row_to_entry_data(self, row: dict[str, Any]) -> EntryData:
        tags = row.get("tags", "[]")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        created = row.get("created_at", "")
        if isinstance(created, str) and created:
            try:
                created_dt = datetime.fromisoformat(created)
            except ValueError:
                created_dt = datetime.now()
        else:
            created_dt = datetime.now()
        return EntryData(
            id=row["id"],
            title=row.get("title") or "",
            tags=tags,
            created_at=created_dt,
            updated_at=created_dt,
        )

    def _start_event_listener(self) -> None:
        try:
            from lucid.logbook.event_listener import EventListener
            listener = EventListener.get_instance()
            listener.current_entry_id = self._current_entry_id
            listener.fragment_injected.connect(self._on_fragment_injected)
            listener.start()
        except Exception as e:
            logger.debug("Could not start event listener: {}", e)

    # ── Entry selection ───────────────────────────────────────────

    def _select_entry(self, entry_id: str) -> None:
        if not self._client:
            return

        self._current_entry_id = entry_id

        # Update event listener
        try:
            from lucid.logbook.event_listener import EventListener
            EventListener.get_instance().current_entry_id = entry_id
        except Exception:
            pass

        # Load fragments
        frag_rows = self._client.list_fragments(entry_id)
        fragments: list[FragmentData] = []
        for fr in frag_rows:
            data_raw = fr.get("data")
            if isinstance(data_raw, str):
                try:
                    data_raw = json.loads(data_raw)
                except (json.JSONDecodeError, TypeError):
                    data_raw = {}
            kind = fr.get("kind", "text")
            ftype_map = {"text": FragmentType.TEXT, "readonly": FragmentType.READONLY, "image": FragmentType.IMAGE}
            ftype = ftype_map.get(kind, FragmentType.TEXT)
            fragments.append(FragmentData(
                id=fr["id"],
                fragment_type=ftype,
                content=fr.get("content", ""),
                subtype=fr.get("subtype", ""),
                metadata=data_raw or {},
            ))

        entry_data = self._entries.get(entry_id, EntryData(id=entry_id))
        entry_data.fragments = fragments
        self._entry_widget.set_entry(entry_data)

    # ── Slots ─────────────────────────────────────────────────────

    @Slot(str)
    def _on_entry_selected(self, entry_id: str) -> None:
        self._select_entry(entry_id)

    @Slot()
    def _on_new_entry_requested(self) -> None:
        if not self._client or not self._logbook_id:
            return
        try:
            entry_id = self._client.create_entry(self._logbook_id, title="")
            ed = EntryData(id=entry_id, title="")
            self._entries[entry_id] = ed
            if self._entries_panel:
                self._entries_panel.add_entry(ed)
                self._entries_panel.select_entry(entry_id)
            self._select_entry(entry_id)
            logger.info("Created new entry {}", entry_id)
        except Exception as e:
            logger.error("Failed to create entry: {}", e)

    def _on_ipc_entry_created(self, entry_id: str, logbook_id: str) -> None:
        """Handle an entry created outside the panel (e.g. via IPC)."""
        if logbook_id != self._logbook_id:
            return
        if entry_id in self._entries:
            return  # Already known (manual creation path)
        row = self._client.get_entry(entry_id) if self._client else None
        if not row:
            return
        ed = self._row_to_entry_data(row)
        self._entries[entry_id] = ed
        if self._entries_panel:
            self._entries_panel.add_entry(ed)

    @Slot(str, str)
    def _on_fragment_added(self, entry_id: str, fragment_id: str) -> None:
        if not self._client:
            return
        try:
            self._client.add_fragment(entry_id, kind="text", fragment_id=fragment_id)
        except Exception as e:
            logger.error("Failed to persist fragment: {}", e)

    @Slot(str, str, str)
    def _on_fragment_changed(self, entry_id: str, fragment_id: str, content: str) -> None:
        if not self._client:
            return
        try:
            self._client.update_fragment(fragment_id, content=content)
        except Exception as e:
            logger.error("Failed to update fragment: {}", e)

    @Slot(str)
    def _on_fragment_injected(self, entry_id: str) -> None:
        """Refresh the entry view when the EventListener injects a fragment."""
        if entry_id == self._current_entry_id:
            self._select_entry(entry_id)

    @Slot(str, str)
    def _on_title_changed(self, entry_id: str, new_title: str) -> None:
        if not self._client:
            return
        if entry_id in self._entries:
            self._entries[entry_id].title = new_title
        try:
            self._client.update_entry(entry_id, title=new_title)
        except Exception as e:
            logger.error("Failed to update title: {}", e)
        if self._entries_panel:
            self._entries_panel.set_entries(list(self._entries.values()))

    @Slot(str, list)
    def _on_tags_changed(self, entry_id: str, tags: list[str]) -> None:
        if not self._client:
            return
        if entry_id in self._entries:
            self._entries[entry_id].tags = tags
        try:
            self._client.update_entry(entry_id, tags=tags)
        except Exception as e:
            logger.error("Failed to update tags: {}", e)
        if self._entries_panel:
            self._entries_panel.set_entries(list(self._entries.values()))

    @Slot(str)
    def _on_entry_deleted(self, entry_id: str) -> None:
        if not self._client:
            return
        try:
            self._client.delete_entry(entry_id)
            self._entries.pop(entry_id, None)
            logger.info("Deleted entry {}", entry_id)
            # If we deleted the current entry, select another
            if self._current_entry_id == entry_id:
                if self._entries:
                    next_id = next(iter(self._entries))
                    self._select_entry(next_id)
                    if self._entries_panel:
                        self._entries_panel.select_entry(next_id)
                else:
                    self._current_entry_id = None
                    self._entry_widget.set_entry(EntryData())
        except Exception as e:
            logger.error("Failed to delete entry: {}", e)

    @Slot(str, list)
    def _on_fragment_reordered(self, entry_id: str, fragment_ids: list[str]) -> None:
        if not self._client:
            return
        try:
            self._client.reorder_fragments(entry_id, fragment_ids)
        except Exception as e:
            logger.error("Failed to reorder fragments: {}", e)

    @Slot(str, str)
    def _on_fragment_deleted(self, entry_id: str, fragment_id: str) -> None:
        if not self._client:
            return
        try:
            self._client.delete_fragment(fragment_id)
            logger.info("Deleted fragment {} from entry {}", fragment_id, entry_id)
        except Exception as e:
            logger.error("Failed to delete fragment: {}", e)

    @Slot(str, str)
    def _on_claude_requested(self, entry_id: str, fragment_id: str) -> None:
        """Send a fragment's content to Claude and append the response."""
        # Find the fragment content
        entry = self._entries.get(entry_id)
        if not entry:
            return
        frag = next((f for f in entry.fragments if f.id == fragment_id), None)
        if not frag:
            return

        # Build the prompt from fragment content
        content = frag.content or json.dumps(frag.metadata, indent=2, default=str)
        prompt = (
            f"{content}"
        )

        # Try to send to the Claude panel
        try:
            self._send_to_claude(entry_id, prompt)
        except Exception as e:
            logger.error("Failed to send to Claude: {}", e)

    def _get_claude_panel(self):
        """Find or open the Claude panel. Returns (panel, agent) or (None, None)."""
        try:
            from lucid.ui.panels.registry import PanelRegistry

            registry = PanelRegistry.get_instance()
            panel = registry.create("lucid.panels.claude")

            if panel and getattr(panel, "_claude_widget", None) and hasattr(panel._claude_widget, "agent"):
                return panel, panel._claude_widget.agent
        except Exception as e:
            logger.error("Could not get Claude panel: {}", e)
        return None, None

    def _send_to_claude(self, entry_id: str, prompt: str) -> None:
        """Send prompt to Claude and collect the response into a fragment."""
        panel, agent = self._get_claude_panel()
        if not agent:
            logger.warning("Claude panel/agent not available")
            return

        if agent.is_busy():
            logger.warning("Claude is busy, cannot send logbook request")
            return

        # Track pending response
        self._pending_claude_entry = entry_id
        self._pending_claude_messages: list[str] = []

        # Connect to agent signals
        agent.message_received.connect(self._on_claude_message)
        agent.query_completed.connect(self._on_claude_complete)

        # Send via the widget (shows in chat UI too)
        panel.action_send_message(prompt)
        logger.info("Sent logbook fragment to Claude for entry {}", entry_id)

    @Slot(str)
    def _on_claude_message(self, message: str) -> None:
        """Collect Claude message chunks."""
        if hasattr(self, "_pending_claude_messages"):
            self._pending_claude_messages.append(message)

    @Slot()
    def _on_claude_complete(self) -> None:
        """Claude finished responding — inject the response as a fragment."""
        entry_id = getattr(self, "_pending_claude_entry", None)
        messages = getattr(self, "_pending_claude_messages", [])

        # Disconnect signals
        try:
            _, agent = self._get_claude_panel()
            if agent:
                agent.message_received.disconnect(self._on_claude_message)
                agent.query_completed.disconnect(self._on_claude_complete)
        except (RuntimeError, TypeError):
            pass

        if not entry_id or not messages or not self._client:
            self._pending_claude_entry = None
            self._pending_claude_messages = []
            return

        response_text = "\n\n".join(messages)

        try:
            frag_id = self._client.add_fragment(
                entry_id,
                kind="readonly",
                subtype="claude_response",
                content=response_text,
            )
            logger.info("Injected Claude response fragment {} into entry {}", frag_id, entry_id)
            # Refresh the view
            if entry_id == self._current_entry_id:
                self._select_entry(entry_id)
        except Exception as e:
            logger.error("Failed to inject Claude response: {}", e)
        finally:
            self._pending_claude_entry = None
            self._pending_claude_messages = []

    # ── Image support ──────────────────────────────────────────────

    @Slot()
    def _on_add_image_clicked(self) -> None:
        """Open file dialog, add selected image as a fragment."""
        if not self._current_entry_id or not self._client:
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image",
            "",
            "Images (*.png *.jpg *.jpeg *.gif);;All Files (*)",
        )
        if not path:
            return

        from pathlib import Path as P
        file_path = P(path)
        if file_path.stat().st_size > 20 * 1024 * 1024:
            logger.warning("Image too large: {}", file_path)
            return

        try:
            self._client.add_image(
                image=path,
                caption="",
                subtype="clipboard",
                entry_id=self._current_entry_id,
            )
            self._select_entry(self._current_entry_id)
        except Exception as e:
            logger.error("Failed to add image: {}", e)

    def eventFilter(self, obj, event) -> bool:
        """Intercept Ctrl+V when clipboard has an image and focus is in the entry widget."""
        if (
            isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.matches(QKeySequence.StandardKey.Paste)
        ):
            # Check if focused widget is inside our entry widget
            focus = QApplication.focusWidget()
            if focus is not None and self._entry_widget.isAncestorOf(focus):
                clipboard = QApplication.clipboard()
                mime = clipboard.mimeData()
                if mime and mime.hasImage():
                    self._paste_image_from_clipboard()
                    return True
        return super().eventFilter(obj, event)

    def _paste_image_from_clipboard(self) -> None:
        """Grab image from clipboard, add as fragment."""
        if not self._current_entry_id or not self._client:
            return

        clipboard = QApplication.clipboard()
        qimage = clipboard.image()
        if qimage.isNull():
            return

        try:
            self._client.add_image(
                image=qimage,
                caption="",
                subtype="clipboard",
                entry_id=self._current_entry_id,
            )
            self._select_entry(self._current_entry_id)
        except Exception as e:
            logger.error("Failed to paste image: {}", e)

    def _try_sync(self) -> None:
        if self._client:
            self._client.schedule_sync()

    # ── Introspection ─────────────────────────────────────────────

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        return {
            "logbook_id": self._logbook_id,
            "current_entry_id": self._current_entry_id,
            "entry_count": len(self._entries),
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        actions = super()._get_available_actions()
        actions.append({
            "name": "new_entry",
            "description": "Create a new logbook entry",
            "method": "action_new_entry",
        })
        return actions

    def action_new_entry(self) -> bool:
        self._on_new_entry_requested()
        return True
