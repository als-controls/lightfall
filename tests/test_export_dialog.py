"""Tests for export dialog parameter assembly."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lucid.ui.dialogs.export_dialog import build_job_message


class TestBuildJobMessage:
    def test_builds_noop_job(self):
        records = [MagicMock(uid="uid1"), MagicMock(uid="uid2")]
        msg = build_job_message(
            records=records,
            export_type="noop",
            output_dir="/tmp/export",
            tiled_url="https://tiled.example.com",
            auth_token="tok123",
            extra_params={},
        )
        assert msg["run_uids"] == ["uid1", "uid2"]
        assert msg["export_type"] == "noop"
        assert msg["params"]["output_dir"] == "/tmp/export"
        assert msg["tiled_url"] == "https://tiled.example.com"
        assert msg["auth_token"] == "tok123"
        assert "job_id" in msg

    def test_builds_nxsas_job_with_roi(self):
        records = [MagicMock(uid="uid1")]
        roi = {"x": 10, "y": 20, "width": 50, "height": 40}
        msg = build_job_message(
            records=records,
            export_type="nxsas",
            output_dir="/data/out",
            tiled_url="https://tiled.example.com",
            auth_token=None,
            extra_params={"roi": roi},
        )
        assert msg["export_type"] == "nxsas"
        assert msg["params"]["roi"] == roi
