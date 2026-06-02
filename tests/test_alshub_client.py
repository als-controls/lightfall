"""Test LUCID's small alshub-api client."""
import re
import pytest


@pytest.mark.asyncio
async def test_active_esaf_found(httpx_mock):
    from lightfall.services._alshub_client import AlshubClient

    httpx_mock.add_response(
        url=re.compile(r"https://alshub.example.com/beamlines/4\.0\.2/active-esaf.*"),
        json={
            "EsafFriendlyId": "BLS-00480-001",
            "ProposalFriendlyId": "BLS-00480",
            "Beamline": "4.0.2",
            "ScheduledStart": "2026-04-26T18:00:00+00:00",
            "ScheduledStop": "2026-04-27T02:00:00+00:00",
        },
    )
    c = AlshubClient(base_url="https://alshub.example.com", api_key="key")
    res = await c.get_active_esaf("4.0.2")
    assert res["EsafFriendlyId"] == "BLS-00480-001"


@pytest.mark.asyncio
async def test_active_esaf_404(httpx_mock):
    from lightfall.services._alshub_client import AlshubClient

    httpx_mock.add_response(
        url=re.compile(r"https://alshub.example.com/beamlines/.*/active-esaf.*"),
        status_code=404,
    )
    c = AlshubClient(base_url="https://alshub.example.com", api_key="key")
    res = await c.get_active_esaf("9.9.9")
    assert res is None


@pytest.mark.asyncio
async def test_active_esaf_unreachable_propagates(httpx_mock):
    from lightfall.services._alshub_client import AlshubClient
    import httpx

    httpx_mock.add_exception(httpx.ConnectError("boom"))
    c = AlshubClient(base_url="https://alshub.example.com", api_key="key")
    with pytest.raises(httpx.NetworkError):
        await c.get_active_esaf("4.0.2")
