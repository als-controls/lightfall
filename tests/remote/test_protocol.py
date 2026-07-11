"""Tests for the remote-control reply protocol helpers."""

import pytest

from lightfall.remote.protocol import CONTRACT_VERSION, ERROR_CODES, error_reply, ok_reply


def test_contract_version_is_1():
    assert CONTRACT_VERSION == 1


def test_ok_reply_merges_fields_and_stamps_version():
    reply = ok_reply(status="submitted", item_id="abc")
    assert reply == {"status": "submitted", "item_id": "abc", "contract_version": 1}


def test_error_reply_shape():
    reply = error_reply("busy", "engine is running")
    assert reply == {
        "status": "error",
        "code": "busy",
        "message": "engine is running",
        "contract_version": 1,
    }


def test_error_reply_rejects_unknown_code():
    with pytest.raises(ValueError):
        error_reply("nonsense", "x")


def test_error_codes_match_spec():
    assert ERROR_CODES == frozenset(
        {"busy", "limits", "timeout", "unknown", "denied", "bad_request", "version_mismatch"}
    )
