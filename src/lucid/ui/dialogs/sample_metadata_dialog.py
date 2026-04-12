"""Dialog for collecting sample metadata before a run.

Provides a SampleMetadataDialog that collects sample name and arbitrary
user-defined metadata fields. Uses pyqtgraph's ParameterTree with a custom
ScalableGroup for dynamically adding typed fields.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSettings, Slot
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)
from pyqtgraph.parametertree import Parameter, ParameterTree
from pyqtgraph.parametertree.parameterTypes import GroupParameter

from lucid.ui.dialogs.base import LucidDialog
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

#: Field names reserved by Bluesky / run engine metadata.
RESERVED_FIELDS = frozenset(
    {
        "uid",
        "time",
        "scan_id",
        "plan_name",
        "plan_type",
        "plan_args",
        "plan_pattern_args",
        "plan_pattern_module",
        "num_points",
        "num_intervals",
        "hints",
        "detectors",
        "motors",
        "sample_name",
    }
)

_SETTINGS_KEY = "lucid.dialogs.sample_metadata.v1"

# Default values per type for new ScalableGroup children.
_DEFAULT_VALUES: dict[str, Any] = {
    "str": "",
    "float": 0.0,
    "int": 0,
}


class ScalableGroup(GroupParameter):
    """A group parameter that lets the user add new typed children.

    Provides an "Add" button with a type dropdown (str, float, int).
    Each added child is removable and renamable.

    This pattern originates from Xi-CAM and is used for letting users
    define arbitrary metadata fields.
    """

    def __init__(self, **opts: Any) -> None:
        opts.setdefault("addText", "Add")
        opts.setdefault("addList", ["str", "float", "int"])
        super().__init__(**opts)

    def addNew(self, typ: str | None = None) -> None:  # noqa: N802 – pyqtgraph convention
        """Add a new child parameter of the given type.

        Args:
            typ: One of 'str', 'float', 'int'. Defaults to 'str'.
        """
        if typ is None:
            typ = "str"
        existing_names = {c.name() for c in self.children()}
        # Find a unique default name.
        base = f"new_{typ}"
        name = base
        idx = 1
        while name in existing_names:
            name = f"{base}_{idx}"
            idx += 1
        child = Parameter.create(
            name=name,
            type=typ,
            value=_DEFAULT_VALUES.get(typ, ""),
            removable=True,
            renamable=True,
        )
        self.addChild(child)


class SampleMetadataDialog(LucidDialog):
    """Dialog for collecting sample name and arbitrary metadata before a run.

    Layout::

        +-- Sample Metadata --------------------------+
        |  Sample Name: [___________________________] |
        |  (!) warning text                           |
        |  +-- Additional Metadata -----------------+ |
        |  |  ParameterTree with ScalableGroup      | |
        |  +----------------------------------------+ |
        |                       [Cancel] [Run/Force]  |
        +---------------------------------------------+

    Args:
        reserved: Extra field names to block beyond the default
            :data:`RESERVED_FIELDS`.
        parent: Parent widget.
    """

    def __init__(
        self,
        reserved: set[str] | frozenset[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._reserved = RESERVED_FIELDS | (reserved or frozenset())
        self._force_mode: bool = False
        self._setup_ui()
        self._connect_signals()
        self._restore_state()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the dialog layout."""
        self.setWindowTitle("Sample Metadata")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        # -- Sample name row --
        form = QFormLayout()
        self._sample_name_edit = QLineEdit()
        self._sample_name_edit.setPlaceholderText("e.g. my_sample")
        form.addRow("Sample Name:", self._sample_name_edit)
        layout.addLayout(form)

        # -- Warning label --
        self._warning_label = QLabel()
        self._warning_label.setStyleSheet("color: red;")
        self._warning_label.setWordWrap(True)
        self._warning_label.hide()
        layout.addWidget(self._warning_label)

        # -- Parameter tree for arbitrary metadata --
        self._metadata_group = ScalableGroup(name="Additional Metadata")
        self._param_tree = ParameterTree(showHeader=False)
        self._param_tree.setParameters(self._metadata_group, showTop=True)
        self._param_tree.setMinimumHeight(150)
        layout.addWidget(self._param_tree)

        # -- Button row --
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        self._accept_btn = QPushButton("Run")
        self._accept_btn.clicked.connect(self._on_accept_clicked)
        self._accept_btn.setDefault(True)
        btn_layout.addWidget(self._accept_btn)

        layout.addLayout(btn_layout)

    def _connect_signals(self) -> None:
        """Wire up signals."""
        self._sample_name_edit.textChanged.connect(self._on_sample_name_changed)

    # ------------------------------------------------------------------
    # Validation / accept
    # ------------------------------------------------------------------

    @Slot()
    def _on_accept_clicked(self) -> None:
        """Handle the Run/Force button click.

        Validation flow:
        1. sample_name must be non-empty.
        2. Arbitrary field names must not collide with reserved names.
        3. If _force_mode → accept immediately (skip duplicate check).
        4. Check Tiled for duplicate sample_name → warn + set force mode.
        5. Otherwise → accept.
        """
        sample_name = self._sample_name_edit.text().strip()

        # 1. Non-empty check
        if not sample_name:
            self._show_warning("Sample name is required.")
            return

        # 2. Reserved-field check
        for child in self._metadata_group.children():
            if child.name() in self._reserved:
                self._show_warning(
                    f'"{child.name()}" is a reserved field name and cannot be used.'
                )
                return

        # 3. Force mode → skip duplicate check, just accept
        if self._force_mode:
            self._save_state()
            self.accept()
            return

        # 4. Duplicate check against Tiled
        if self._check_duplicate_sample_name(sample_name):
            self._show_warning(
                f'"{sample_name}" already exists in Tiled. '
                "Press Force to use it anyway."
            )
            self._force_mode = True
            self._accept_btn.setText("Force")
            return

        # 5. All clear
        self._save_state()
        self.accept()

    @Slot(str)
    def _on_sample_name_changed(self, _text: str) -> None:
        """Reset force mode whenever the sample name changes."""
        self._force_mode = False
        self._accept_btn.setText("Run")
        self._warning_label.setText("")
        self._warning_label.hide()

    # ------------------------------------------------------------------
    # Tiled duplicate check
    # ------------------------------------------------------------------

    def _check_duplicate_sample_name(self, sample_name: str) -> bool:
        """Check whether *sample_name* already exists in Tiled.

        Returns ``True`` if a duplicate is found, ``False`` otherwise
        (including when Tiled is unavailable).
        """
        try:
            from lucid.services.tiled_service import TiledService

            service = TiledService.get_instance()
            if not service.is_connected or service._client is None:
                return False
            from tiled.queries import Key

            results = service._client.search(Key("start.sample_name") == sample_name)
            return len(results) > 0
        except Exception as exc:
            logger.warning("Failed to check duplicate sample name: {}", exc)
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_metadata(self) -> dict[str, Any]:
        """Return the collected metadata as a flat dict.

        Always includes ``sample_name``. Arbitrary fields whose names
        collide with :data:`RESERVED_FIELDS` are silently skipped.
        """
        md: dict[str, Any] = {"sample_name": self._sample_name_edit.text().strip()}
        for child in self._metadata_group.children():
            name = child.name()
            if name not in self._reserved:
                md[name] = child.value()
        return md

    # ------------------------------------------------------------------
    # Persistence (QSettings)
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Persist the ScalableGroup state to QSettings."""
        settings = QSettings()
        try:
            state = self._metadata_group.saveState()
            settings.setValue(_SETTINGS_KEY, json.dumps(state))
        except Exception as exc:
            logger.warning("Failed to save sample metadata state: {}", exc)

    def _restore_state(self) -> None:
        """Restore ScalableGroup state from QSettings."""
        settings = QSettings()
        raw = settings.value(_SETTINGS_KEY)
        if raw is None:
            return
        try:
            state = json.loads(raw)
            self._metadata_group.restoreState(state)
        except Exception as exc:
            logger.debug("Could not restore sample metadata state: {}", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _show_warning(self, text: str) -> None:
        """Display *text* in the warning label."""
        self._warning_label.setText(text)
        self._warning_label.show()
