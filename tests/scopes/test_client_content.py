"""ScopesClient reads of scoped content (listing and bundle download)."""
from __future__ import annotations

import hashlib

import httpx
import pytest

from lightfall.scopes.client import ContentItem, ContentListing, ScopesClient, ScopesError

BASE = "https://lb.test"
BR = "branch:7.0.1.1"
ITEM = {"type": "agent", "name": "helper", "revision": 4, "sha256": "ab" * 32, "size": 10,
        "published_by": "maint", "published_at": "2026-10-31T00:00:00Z", "author": None,
        "file_count": 1, "uncompressed_size": 20}


def _client():
    return ScopesClient(base_url=BASE, auth=httpx.Auth())


def test_content_listing_parses_and_304(httpx_mock):
    httpx_mock.add_response(url=f"{BASE}/logbook/scopes/{BR}/content/",
                            json={"scope": BR, "items": [ITEM], "etag": "e1"})
    listing = _client().content_listing(BR)
    assert listing.etag == "e1" and listing.scope == BR
    assert listing.items == (ContentItem(**ITEM),)
    assert ContentListing.from_dict(listing.to_dict()) == listing
    httpx_mock.add_response(url=f"{BASE}/logbook/scopes/{BR}/content/",
                            match_headers={"If-None-Match": "e1"}, status_code=304)
    assert _client().content_listing(BR, etag="e1") is None


def test_content_listing_drops_malformed_items(httpx_mock):
    old = {k: v for k, v in ITEM.items() if k not in ("file_count", "uncompressed_size")}
    httpx_mock.add_response(url=f"{BASE}/logbook/scopes/{BR}/content/",
                            json={"scope": BR, "items": [{"type": "plan"}, "x", old],
                                  "etag": "e2"})
    items = _client().content_listing(BR).items
    assert len(items) == 1 and items[0].file_count is None


def test_download_bundle_writes_and_hashes(httpx_mock, tmp_path):
    body = b"0123456789"
    httpx_mock.add_response(url=f"{BASE}/logbook/scopes/{BR}/content/agent/helper/bundle?revision=4",
                            content=body)
    dest = tmp_path / "b.zip"
    sha = _client().download_bundle(BR, "agent", "helper", 4, dest, expected_size=10)
    assert sha == hashlib.sha256(body).hexdigest() and dest.read_bytes() == body


def test_download_bundle_refuses_oversize(httpx_mock, tmp_path):
    httpx_mock.add_response(url=f"{BASE}/logbook/scopes/{BR}/content/agent/helper/bundle?revision=4",
                            content=b"x" * 11)
    with pytest.raises(ScopesError):
        _client().download_bundle(BR, "agent", "helper", 4, tmp_path / "b.zip", expected_size=10)


def test_download_bundle_404_raises_with_status(httpx_mock, tmp_path):
    httpx_mock.add_response(url=f"{BASE}/logbook/scopes/{BR}/content/agent/helper/bundle?revision=4",
                            status_code=404, json={"detail": "gone"})
    with pytest.raises(ScopesError) as e:
        _client().download_bundle(BR, "agent", "helper", 4, tmp_path / "b.zip", expected_size=10)
    assert e.value.status == 404
