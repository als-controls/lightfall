"""Tests for the SampleMetadataDialog."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def dialog(qapp):
    """Create a SampleMetadataDialog for testing."""
    from lucid.ui.dialogs.sample_metadata_dialog import SampleMetadataDialog

    dlg = SampleMetadataDialog()
    return dlg


class TestSampleMetadataDialogBasics:
    """Tests for basic dialog functionality."""

    def test_dialog_creates(self, dialog) -> None:
        assert dialog is not None

    def test_sample_name_field_exists(self, dialog) -> None:
        assert dialog._sample_name_edit is not None
        assert dialog._sample_name_edit.text() == ""

    def test_get_metadata_includes_sample_name(self, dialog) -> None:
        dialog._sample_name_edit.setText("test_sample")
        metadata = dialog.get_metadata()
        assert metadata["sample_name"] == "test_sample"

    def test_get_metadata_includes_arbitrary_fields(self, dialog) -> None:
        dialog._sample_name_edit.setText("test_sample")
        dialog._metadata_group.addNew("str")
        children = dialog._metadata_group.children()
        assert len(children) > 0
        child = children[-1]
        child.setName("temperature")
        child.setValue("25.0")
        metadata = dialog.get_metadata()
        assert metadata["sample_name"] == "test_sample"
        assert metadata["temperature"] == "25.0"

    def test_empty_sample_name_rejected(self, dialog) -> None:
        dialog._sample_name_edit.setText("")
        dialog._on_accept_clicked()
        assert dialog._warning_label.text() != ""
        assert dialog.result() != dialog.DialogCode.Accepted

    def test_reserved_field_name_rejected(self, dialog) -> None:
        dialog._sample_name_edit.setText("test_sample")
        dialog._metadata_group.addNew("str")
        child = dialog._metadata_group.children()[-1]
        child.setName("uid")
        dialog._on_accept_clicked()
        assert "reserved" in dialog._warning_label.text().lower()

    def test_run_button_exists(self, dialog) -> None:
        assert dialog._accept_btn is not None
        assert dialog._accept_btn.text() == "Run"


class TestDuplicateCheck:
    """Tests for Tiled duplicate sample name checking."""

    @patch("lucid.services.tiled_service.TiledService")
    def test_duplicate_name_shows_warning(self, mock_tiled_cls, dialog) -> None:
        mock_service = MagicMock()
        mock_service.is_connected = True
        mock_service._client.search.return_value = ["existing_run"]
        mock_tiled_cls.get_instance.return_value = mock_service

        dialog._sample_name_edit.setText("existing_sample")
        dialog._on_accept_clicked()

        assert "already exists" in dialog._warning_label.text()
        assert dialog._accept_btn.text() == "Force"
        assert dialog._force_mode is True

    @patch("lucid.services.tiled_service.TiledService")
    def test_force_accepts_duplicate(self, mock_tiled_cls, dialog) -> None:
        mock_service = MagicMock()
        mock_service.is_connected = True
        mock_service._client.search.return_value = ["existing_run"]
        mock_tiled_cls.get_instance.return_value = mock_service

        dialog._sample_name_edit.setText("existing_sample")
        dialog._on_accept_clicked()  # First click triggers warning
        assert dialog._force_mode is True
        assert dialog._accept_btn.text() == "Force"

    @patch("lucid.services.tiled_service.TiledService")
    def test_tiled_not_connected_skips_check(self, mock_tiled_cls, dialog) -> None:
        mock_service = MagicMock()
        mock_service.is_connected = False
        mock_tiled_cls.get_instance.return_value = mock_service

        result = dialog._check_duplicate_sample_name("any_name")
        assert result is False

    def test_changing_name_resets_force_mode(self, dialog) -> None:
        dialog._force_mode = True
        dialog._accept_btn.setText("Force")
        dialog._sample_name_edit.setText("new_name")
        assert dialog._force_mode is False
        assert dialog._accept_btn.text() == "Run"
