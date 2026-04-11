"""Tests for the exporter NATS service."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lucid.exporter.service import ExporterService


class TestExporterService:
    def test_init_sets_hostname_and_nats_url(self):
        svc = ExporterService(nats_url="nats://localhost:4222", hostname="testhost")
        assert svc._hostname == "testhost"
        assert svc._nats_url == "nats://localhost:4222"

    def test_subject_names(self):
        svc = ExporterService(nats_url="nats://localhost:4222", hostname="tsuru")
        assert svc.job_subject == "lucid.export.tsuru"
        assert svc.ping_subject == "lucid.export.tsuru.ping"
        assert svc.progress_subject == "lucid.export.tsuru.progress"


class TestJobDispatch:
    @pytest.fixture
    def svc(self):
        return ExporterService(nats_url="nats://localhost:4222", hostname="testhost")

    def test_parse_valid_job(self, svc):
        job_data = {
            "job_id": "abc-123",
            "tiled_url": "https://tiled.example.com",
            "auth_token": "tok",
            "run_uids": ["uid1", "uid2"],
            "export_type": "noop",
            "params": {"output_dir": "/tmp/export"},
        }
        job = svc._parse_job(job_data)
        assert job.job_id == "abc-123"
        assert job.run_uids == ["uid1", "uid2"]
        assert job.export_type == "noop"

    def test_parse_job_missing_field_raises(self, svc):
        with pytest.raises(KeyError):
            svc._parse_job({"job_id": "x"})

    def test_parse_job_unknown_export_type_raises(self, svc):
        job_data = {
            "job_id": "abc-123",
            "tiled_url": "https://tiled.example.com",
            "auth_token": "tok",
            "run_uids": ["uid1"],
            "export_type": "unknown_format",
            "params": {"output_dir": "/tmp/export"},
        }
        with pytest.raises(ValueError, match="Unknown export type"):
            svc._parse_job(job_data)


class TestPingResponse:
    def test_build_ping_response(self):
        svc = ExporterService(nats_url="nats://localhost:4222", hostname="tsuru")
        resp = svc._build_ping_response()
        assert resp["hostname"] == "tsuru"
        assert resp["status"] == "ready"
        assert "queue_depth" in resp
