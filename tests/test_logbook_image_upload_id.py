"""Image uploads keep the client's image_id so image fragments resolve on the server."""
from __future__ import annotations

import sqlite3

import pytest

from tests.test_logbook_sync_scoping import _make_db

IMAGE_ID = "0b6f3c1e-2d4a-4f6b-9c8d-7e5f4a3b2c1d"


def _seed_pending_image(tmp_path) -> str:
    db_path = tmp_path / "logbook.db"
    _make_db(db_path)
    img = tmp_path / f"{IMAGE_ID}.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    db = sqlite3.connect(str(db_path))
    db.execute(
        "INSERT INTO image_sync (image_id, local_path, sync_status) VALUES (?, ?, 'pending_upload')",
        (IMAGE_ID, str(img)),
    )
    db.commit()
    db.close()
    return str(db_path)


def _image_status(db_path: str) -> str:
    db = sqlite3.connect(db_path)
    (status,) = db.execute(
        "SELECT sync_status FROM image_sync WHERE image_id = ?", (IMAGE_ID,)
    ).fetchone()
    db.close()
    return status


@pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False, assert_all_requests_were_expected=False
)
@pytest.mark.parametrize(
    ("returned_id", "expected_status"),
    [(IMAGE_ID, "synced"), ("server-minted-id", "pending_upload")],
)
def test_upload_sends_local_image_id(tmp_path, httpx_mock, returned_id, expected_status):
    from lightfall.logbook.client import _run_sync

    db_path = _seed_pending_image(tmp_path)
    httpx_mock.add_response(
        method="POST",
        url=f"http://logbook.test/logbook/images?image_id={IMAGE_ID}",
        status_code=201,
        json={"image_id": returned_id, "mime_type": "image/png", "size_bytes": 24},
    )

    _run_sync(db_path, "http://logbook.test", user_id="alice")

    uploads = [r for r in httpx_mock.get_requests() if r.url.path == "/logbook/images"]
    assert len(uploads) == 1
    assert uploads[0].url.params["image_id"] == IMAGE_ID
    # An old server that mints its own id leaves the image pending for a later retry.
    assert _image_status(db_path) == expected_status
