"""Agents & Skills editor panel.

Rendering + dialogs only. Every file mutation goes through
``agent_editor.models`` (agent copy/reset/serialize/save) or
``lightfall.agents.drafts`` (approve/reject), so this module stays Qt glue and
the logic stays unit-testable without a QApplication.
"""

from __future__ import annotations

import difflib
import shutil
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QFileSystemWatcher, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lightfall.agents import drafts as drafts_mod
from lightfall.agents.registry import AgentSpecRegistry
from lightfall.ui.panels.agent_editor import models
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.utils.logging import logger

_ERROR_STYLE = "color: #dc2626;"
_HINT_STYLE = "color: #6b7280; font-size: 11px;"
_SCOPE_LABEL = {"core": "Shipped (core)", "beamline": "Beamline", "user": "User", "unknown": "Unknown"}
_SCOPE_ORDER = ("user", "beamline", "core", "unknown")
_MODEL_CHOICES = ("", "opus", "sonnet", "haiku")
_EFFORT_CHOICES = ("", "low", "medium", "high")
_ON_MESSAGE_CHOICES = ("queue", "auto")

_VARIABLES_HINT = (
    "Template variables available in the prompt: {{beamline}}, {{user}}, {{endstation}}"
)

# Roles carried by list items so selection handlers don't re-derive state.
_ROLE_KIND = Qt.ItemDataRole.UserRole  # "header" | "agent" | "skill" | "draft"
_ROLE_NAME = Qt.ItemDataRole.UserRole + 1


def _header_item(text: str) -> QListWidgetItem:
    item = QListWidgetItem(text)
    item.setData(_ROLE_KIND, "header")
    item.setFlags(Qt.ItemFlag.NoItemFlags)
    return item


class AgentEditorPanel(BasePanel):
    """Two-tab editor: agent definitions, and skills + pending skill drafts."""

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall.panels.agent_editor",
        name="Agents & Skills",
        description="Edit agent definitions, skills, and approve drafts",
        # NOTE: the brief specified default_area="center", but center-area
        # panels never get a sidebar button (DockingManager._on_panel_registered
        # early-returns) and an empty icon draws an invisible one -- see the
        # sidebar-visibility gotchas in docs/developer-guide/plugins/
        # plugin-types/panel.md (commit 507177d). "left" + an icon keeps the
        # panel reachable.
        icon="mdi.robot-outline",
        category="development",
        singleton=True,
        default_area="left",
        proactive_init=False,
    )

    def __init__(
        self,
        parent: QWidget | None = None,
        registry: AgentSpecRegistry | None = None,
    ) -> None:
        # Assigned before super().__init__ because BasePanel calls _setup_ui().
        self._registry = registry or AgentSpecRegistry.get_instance()
        self._agent_rows: dict[str, models.AgentRow] = {}
        self._skill_rows: dict[str, models.SkillRow] = {}
        self._draft_rows: dict[str, models.DraftRow] = {}
        self._current_agent: str | None = None
        self._current_skill: str | None = None
        self._current_draft: str | None = None
        super().__init__(parent)

    # ------------------------------------------------------------------ setup

    def _setup_ui(self) -> None:
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_agents_tab(), "Agents")
        self._tabs.addTab(self._build_skills_tab(), "Skills")
        self._layout.addWidget(self._tabs)

        self.refresh_agents()
        self.refresh_skills()

        self._watcher = QFileSystemWatcher(self)
        try:
            self._watcher.addPath(str(drafts_mod.drafts_dir()))
            self._watcher.directoryChanged.connect(self._on_drafts_dir_changed)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("agent_editor: could not watch drafts dir: {}", e)

    # --- Agents tab -----------------------------------------------------

    def _build_agents_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        buttons = QHBoxLayout()
        self._new_agent_btn = QPushButton("New agent")
        self._new_agent_btn.clicked.connect(self._on_new_agent)
        self._export_btn = QPushButton("Export…")
        self._export_btn.clicked.connect(self._on_export_agent)
        buttons.addWidget(self._new_agent_btn)
        buttons.addWidget(self._export_btn)
        buttons.addStretch(1)
        outer.addLayout(buttons)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._agent_list = QListWidget()
        self._agent_list.currentItemChanged.connect(
            lambda cur, _prev: self._on_agent_selected(cur)
        )
        splitter.addWidget(self._agent_list)
        splitter.addWidget(self._build_agent_detail())
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, 1)
        return page

    def _build_agent_detail(self) -> QWidget:
        detail = QWidget()
        layout = QVBoxLayout(detail)

        self._agent_title = QLabel("")
        self._agent_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._agent_title)

        self._agent_error = QLabel("")
        self._agent_error.setWordWrap(True)
        self._agent_error.setStyleSheet(_ERROR_STYLE)
        layout.addWidget(self._agent_error)

        # Shipped (read-only) controls.
        self._shipped_box = QWidget()
        shipped_layout = QHBoxLayout(self._shipped_box)
        shipped_layout.setContentsMargins(0, 0, 0, 0)
        self._copy_btn = QPushButton("Copy to user scope")
        self._copy_btn.clicked.connect(self._on_copy_agent_to_user)
        self._reset_btn = QPushButton("Reset to default")
        self._reset_btn.clicked.connect(self._on_reset_agent)
        shipped_layout.addWidget(self._copy_btn)
        shipped_layout.addWidget(self._reset_btn)
        shipped_layout.addStretch(1)
        layout.addWidget(self._shipped_box)

        # Editable frontmatter form.
        self._form_box = QGroupBox("Frontmatter")
        form = QFormLayout(self._form_box)
        self._desc_edit = QLineEdit()
        form.addRow("Description", self._desc_edit)
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.addItems(_MODEL_CHOICES)
        form.addRow("Model", self._model_combo)
        self._effort_combo = QComboBox()
        self._effort_combo.setEditable(True)
        self._effort_combo.addItems(_EFFORT_CHOICES)
        form.addRow("Effort", self._effort_combo)
        self._memory_check = QCheckBox("Memory")
        self._subagent_check = QCheckBox("Available as subagent")
        self._openable_check = QCheckBox("Openable in a tab")
        flags = QHBoxLayout()
        flags.addWidget(self._memory_check)
        flags.addWidget(self._subagent_check)
        flags.addWidget(self._openable_check)
        flags_holder = QWidget()
        flags_holder.setLayout(flags)
        form.addRow("Flags", flags_holder)
        self._on_message_combo = QComboBox()
        self._on_message_combo.addItems(_ON_MESSAGE_CHOICES)
        form.addRow("On message", self._on_message_combo)

        self._tools_list = QListWidget()
        self._tools_list.setMaximumHeight(110)
        form.addRow("Tools", self._tools_list)
        self._skills_list = QListWidget()
        self._skills_list.setMaximumHeight(110)
        form.addRow("Skills", self._skills_list)
        layout.addWidget(self._form_box)

        hint = QLabel(_VARIABLES_HINT)
        hint.setStyleSheet(_HINT_STYLE)
        layout.addWidget(hint)

        self._prompt_edit = QPlainTextEdit()
        layout.addWidget(self._prompt_edit, 1)

        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self.save_current_agent)
        layout.addWidget(self._save_btn)
        return detail

    # --- Skills tab -----------------------------------------------------

    def _build_skills_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._skill_list = QListWidget()
        self._skill_list.currentItemChanged.connect(
            lambda cur, _prev: self._on_skill_selected(cur)
        )
        splitter.addWidget(self._skill_list)

        detail = QWidget()
        layout = QVBoxLayout(detail)
        self._skill_title = QLabel("")
        self._skill_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._skill_title)
        self._skill_error = QLabel("")
        self._skill_error.setWordWrap(True)
        self._skill_error.setStyleSheet(_ERROR_STYLE)
        layout.addWidget(self._skill_error)

        self._skill_copy_btn = QPushButton("Copy to user scope")
        self._skill_copy_btn.clicked.connect(self._on_copy_skill_to_user)
        layout.addWidget(self._skill_copy_btn)

        # Side-by-side: active (left) vs proposed (right). For non-revision
        # drafts and plain skills only the right-hand view is shown.
        views = QHBoxLayout()
        self._active_view = QPlainTextEdit()
        self._active_view.setReadOnly(True)
        self._proposed_view = QPlainTextEdit()
        self._proposed_view.setReadOnly(True)
        views.addWidget(self._active_view)
        views.addWidget(self._proposed_view)
        layout.addLayout(views, 1)

        self._diff_view = QPlainTextEdit()
        self._diff_view.setReadOnly(True)
        self._diff_view.setMaximumHeight(150)
        self._diff_view.setVisible(False)
        layout.addWidget(self._diff_view)

        self._draft_buttons = QWidget()
        db = QHBoxLayout(self._draft_buttons)
        db.setContentsMargins(0, 0, 0, 0)
        self._approve_btn = QPushButton("Approve")
        self._approve_btn.clicked.connect(self._on_approve_draft)
        self._reject_btn = QPushButton("Reject")
        self._reject_btn.clicked.connect(self._on_reject_draft)
        self._edit_approve_btn = QPushButton("Edit then approve")
        self._edit_approve_btn.clicked.connect(self._on_edit_then_approve)
        for b in (self._approve_btn, self._reject_btn, self._edit_approve_btn):
            db.addWidget(b)
        db.addStretch(1)
        layout.addWidget(self._draft_buttons)

        splitter.addWidget(detail)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, 1)
        return page

    # --------------------------------------------------------------- refresh

    def refresh_agents(self) -> None:
        selected = self._current_agent
        self._agent_list.clear()
        rows = models.agent_rows(self._registry)
        self._agent_rows = {r.name: r for r in rows}
        for scope in _SCOPE_ORDER:
            in_scope = [r for r in rows if r.scope == scope]
            if not in_scope:
                continue
            self._agent_list.addItem(_header_item(_SCOPE_LABEL.get(scope, scope)))
            for row in in_scope:
                label = row.name
                if row.error:
                    label = f"{row.name}  (error)"
                elif row.shadowed_scopes:
                    label = f"{row.name}  (shadows {', '.join(row.shadowed_scopes)})"
                item = QListWidgetItem(label)
                item.setData(_ROLE_KIND, "agent")
                item.setData(_ROLE_NAME, row.name)
                self._agent_list.addItem(item)
        if selected and selected in self._agent_rows:
            self.select_agent(selected)
        else:
            self._current_agent = None
            self._show_agent_detail(None)

    def refresh_skills(self) -> None:
        self._skill_list.clear()
        drafts = models.draft_rows()
        self._draft_rows = {d.name: d for d in drafts}
        if drafts:
            self._skill_list.addItem(_header_item("Drafts"))
            for draft in drafts:
                suffix = " (revision)" if draft.is_revision else " (new)"
                item = QListWidgetItem(draft.name + suffix)
                item.setData(_ROLE_KIND, "draft")
                item.setData(_ROLE_NAME, draft.name)
                self._skill_list.addItem(item)

        rows = models.skill_rows()
        self._skill_rows = {r.name: r for r in rows}
        for scope in _SCOPE_ORDER:
            in_scope = [r for r in rows if r.scope == scope]
            if not in_scope:
                continue
            self._skill_list.addItem(_header_item(_SCOPE_LABEL.get(scope, scope)))
            for row in in_scope:
                item = QListWidgetItem(row.name)
                item.setData(_ROLE_KIND, "skill")
                item.setData(_ROLE_NAME, row.name)
                self._skill_list.addItem(item)

        self._update_drafts_badge()
        self._current_skill = None
        self._current_draft = None
        self._show_skill_placeholder()

    def _update_drafts_badge(self) -> None:
        n = len(self._draft_rows)
        self._tabs.setTabText(1, f"Skills ({n})" if n else "Skills")

    def _on_drafts_dir_changed(self, _path: str) -> None:
        # Qt drops the watch when a directory is replaced; re-add defensively.
        try:
            path = str(drafts_mod.drafts_dir())
            if path not in self._watcher.directories():
                self._watcher.addPath(path)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("agent_editor: re-watching drafts dir failed: {}", e)
        self.refresh_skills()

    # ------------------------------------------------------- agent selection

    def select_agent(self, name: str) -> bool:
        """Select agent `name` in the list. Returns False if not present."""
        for i in range(self._agent_list.count()):
            item = self._agent_list.item(i)
            if item.data(_ROLE_KIND) == "agent" and item.data(_ROLE_NAME) == name:
                self._agent_list.setCurrentRow(i)
                return True
        return False

    def _on_agent_selected(self, item: QListWidgetItem | None) -> None:
        if item is None or item.data(_ROLE_KIND) != "agent":
            return
        self._current_agent = item.data(_ROLE_NAME)
        self._show_agent_detail(self._agent_rows.get(self._current_agent))

    def _show_agent_detail(self, row: models.AgentRow | None) -> None:
        self._agent_error.setText("")
        if row is None:
            self._agent_title.setText("Select an agent")
            self._shipped_box.setVisible(False)
            self._form_box.setVisible(False)
            self._prompt_edit.clear()
            self._prompt_edit.setReadOnly(True)
            self._save_btn.setEnabled(False)
            return

        self._agent_title.setText(f"{row.name}  —  {_SCOPE_LABEL.get(row.scope, row.scope)}")
        if row.error:
            self._agent_error.setText(row.error)

        editable = row.editable and not row.error
        self._form_box.setVisible(editable)
        self._save_btn.setEnabled(editable)
        self._prompt_edit.setReadOnly(not editable)
        self._shipped_box.setVisible(True)
        self._copy_btn.setEnabled(not row.editable and not row.error)
        self._reset_btn.setEnabled(row.scope == "user" and bool(row.shadowed_scopes))

        spec = self._registry.get(row.name)
        if editable and spec is not None:
            self._desc_edit.setText(spec.description)
            self._model_combo.setCurrentText(spec.model or "")
            self._effort_combo.setCurrentText(spec.effort or "")
            self._memory_check.setChecked(spec.memory)
            self._subagent_check.setChecked(spec.subagent)
            self._openable_check.setChecked(spec.openable)
            self._on_message_combo.setCurrentText(spec.on_message)
            self._populate_checklist(self._tools_list, self._available_tools(), spec.tools)
            self._populate_checklist(self._skills_list, sorted(self._skill_rows), spec.skills)
            self._prompt_edit.setPlainText(spec.prompt)
        else:
            try:
                self._prompt_edit.setPlainText(row.source_path.read_text(encoding="utf-8"))
            except OSError as e:
                self._prompt_edit.setPlainText(f"<unreadable: {e}>")

    @staticmethod
    def _populate_checklist(
        widget: QListWidget, names: list[str], checked: tuple[str, ...]
    ) -> None:
        widget.clear()
        checked_set = set(checked)
        # Any name referenced by the spec but no longer available is still
        # listed (checked) so saving doesn't silently drop it.
        for name in sorted(set(names) | checked_set):
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if name in checked_set else Qt.CheckState.Unchecked
            )
            widget.addItem(item)

    @staticmethod
    def _checked_names(widget: QListWidget) -> tuple[str, ...]:
        return tuple(
            widget.item(i).text()
            for i in range(widget.count())
            if widget.item(i).checkState() == Qt.CheckState.Checked
        )

    @staticmethod
    def _available_tools() -> list[str]:
        try:
            from lightfall.ui.panels.claude.tool_registry import ToolRegistry

            return [p.name for p in ToolRegistry.get_instance().get_plugins()]
        except Exception as e:  # pragma: no cover - registry optional in tests
            logger.debug("agent_editor: tool registry unavailable: {}", e)
            return []

    # ------------------------------------------------------------ agent acts

    def save_current_agent(self) -> bool:
        """Serialize the form + prompt and write it. Returns success."""
        self._agent_error.setText("")
        name = self._current_agent
        row = self._agent_rows.get(name) if name else None
        if row is None or not row.editable:
            return False

        content = models.serialize_agent_file(
            name=row.name,
            description=self._desc_edit.text().strip(),
            prompt=self._prompt_edit.toPlainText(),
            model=self._model_combo.currentText().strip(),
            effort=self._effort_combo.currentText().strip(),
            tools=self._checked_names(self._tools_list),
            skills=self._checked_names(self._skills_list),
            memory=self._memory_check.isChecked(),
            subagent=self._subagent_check.isChecked(),
            openable=self._openable_check.isChecked(),
            on_message=self._on_message_combo.currentText(),
        )
        try:
            models.save_agent_file(row.source_path, content)
        except models.EditorError as e:
            self._agent_error.setText(str(e))
            return False
        self._registry.reload()
        self.refresh_agents()
        return True

    def _on_copy_agent_to_user(self) -> None:
        self._agent_error.setText("")
        if not self._current_agent:
            return
        try:
            models.copy_to_user(self._current_agent, self._registry)
        except models.EditorError as e:
            self._agent_error.setText(str(e))
            return
        self.refresh_agents()

    def _on_reset_agent(self) -> None:
        self._agent_error.setText("")
        name = self._current_agent
        if not name:
            return
        if not self._confirm(
            "Reset to default",
            f"Delete the user-scope copy of '{name}' and revert to the shipped version?",
        ):
            return
        try:
            models.reset_to_default(name)
        except models.EditorError as e:
            self._agent_error.setText(str(e))
            return
        self._registry.reload()
        self.refresh_agents()

    def _on_new_agent(self) -> None:
        self._agent_error.setText("")
        name, ok = QInputDialog.getText(self, "New agent", "Agent name:")
        if not ok or not name.strip():
            return
        try:
            models.new_agent_file(name.strip())
        except models.EditorError as e:
            self._agent_error.setText(str(e))
            return
        self._registry.reload()
        self.refresh_agents()
        self.select_agent(name.strip())

    def _on_export_agent(self) -> None:
        self._agent_error.setText("")
        name = self._current_agent
        row = self._agent_rows.get(name) if name else None
        if row is None:
            self._agent_error.setText("Select an agent to export")
            return
        target = QFileDialog.getExistingDirectory(self, "Export agent to…")
        if not target:
            return
        try:
            shutil.copy2(row.source_path, Path(target) / row.source_path.name)
        except OSError as e:
            self._agent_error.setText(f"Export failed: {e}")

    # ------------------------------------------------------- skill selection

    def _on_skill_selected(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        kind = item.data(_ROLE_KIND)
        if kind == "draft":
            self._current_draft = item.data(_ROLE_NAME)
            self._current_skill = None
            self._show_draft_detail(self._draft_rows[self._current_draft])
        elif kind == "skill":
            self._current_skill = item.data(_ROLE_NAME)
            self._current_draft = None
            self._show_skill_detail(self._skill_rows[self._current_skill])

    def _show_skill_placeholder(self) -> None:
        self._skill_title.setText("Select a skill or draft")
        self._skill_error.setText("")
        self._active_view.clear()
        self._active_view.setVisible(False)
        self._proposed_view.clear()
        self._diff_view.setVisible(False)
        self._draft_buttons.setVisible(False)
        self._skill_copy_btn.setVisible(False)

    def _show_skill_detail(self, row: models.SkillRow) -> None:
        self._skill_error.setText("")
        self._skill_title.setText(
            f"{row.name}  —  {_SCOPE_LABEL.get(row.scope, row.scope)}"
        )
        self._active_view.setVisible(False)
        self._diff_view.setVisible(False)
        self._draft_buttons.setVisible(False)
        self._skill_copy_btn.setVisible(not row.editable)
        self._proposed_view.setPlainText(_read_text(row.path / "SKILL.md"))

    def _show_draft_detail(self, row: models.DraftRow) -> None:
        self._skill_error.setText("")
        kind = "revision" if row.is_revision else "new skill"
        provenance = f"by {row.author or '?'} on {row.created or '?'}"
        self._skill_title.setText(f"Draft: {row.name}  —  {kind}  ({provenance})")
        self._skill_copy_btn.setVisible(False)
        self._draft_buttons.setVisible(True)

        proposed = _read_text(row.path)
        self._proposed_view.setPlainText(proposed)
        if row.diff_target is not None:
            active = _read_text(row.diff_target)
            self._active_view.setPlainText(active)
            self._active_view.setVisible(True)
            self._diff_view.setPlainText(
                "\n".join(
                    difflib.unified_diff(
                        active.splitlines(),
                        proposed.splitlines(),
                        fromfile="active",
                        tofile="proposed",
                        lineterm="",
                    )
                )
            )
            self._diff_view.setVisible(True)
        else:
            self._active_view.setVisible(False)
            self._diff_view.setVisible(False)

    # ------------------------------------------------------------ draft acts

    def _on_approve_draft(self) -> None:
        self._approve(self._current_draft)

    def _approve(self, name: str | None) -> None:
        self._skill_error.setText("")
        if not name:
            return
        try:
            drafts_mod.approve_draft(name)
        except drafts_mod.DraftError as e:
            self._skill_error.setText(str(e))
            return
        self.refresh_skills()

    def _on_reject_draft(self) -> None:
        self._skill_error.setText("")
        name = self._current_draft
        if not name:
            return
        if not self._confirm("Reject draft", f"Permanently delete the draft '{name}'?"):
            return
        try:
            drafts_mod.reject_draft(name)
        except drafts_mod.DraftError as e:
            self._skill_error.setText(str(e))
            return
        self.refresh_skills()

    def _on_edit_then_approve(self) -> None:
        """Let the user amend the proposal, save it back, then approve it."""
        self._skill_error.setText("")
        name = self._current_draft
        row = self._draft_rows.get(name) if name else None
        if row is None:
            return
        text, ok = QInputDialog.getMultiLineText(
            self, f"Edit draft '{name}'", "SKILL.md", _read_text(row.path)
        )
        if not ok:
            return
        if not text.strip():
            self._skill_error.setText("Draft content cannot be empty")
            return
        try:
            row.path.write_text(text, encoding="utf-8")
        except OSError as e:
            self._skill_error.setText(f"Could not save draft: {e}")
            return
        self._approve(name)

    def _on_copy_skill_to_user(self) -> None:
        self._skill_error.setText("")
        if not self._current_skill:
            return
        try:
            models.copy_skill_to_user(self._current_skill)
        except models.EditorError as e:
            self._skill_error.setText(str(e))
            return
        self.refresh_skills()

    # ---------------------------------------------------------------- helpers

    def _confirm(self, title: str, text: str) -> bool:
        return (
            QMessageBox.question(
                self,
                title,
                text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    # Introspection helpers, also used by the wiring tests.

    def tab_labels(self) -> list[str]:
        return [self._tabs.tabText(i) for i in range(self._tabs.count())]

    def agent_names(self) -> list[str]:
        return sorted(self._agent_rows)

    def skill_names(self) -> list[str]:
        return sorted(self._skill_rows)

    def draft_names(self) -> list[str]:
        return sorted(self._draft_rows)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        return f"<unreadable: {e}>"
