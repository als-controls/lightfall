"""Test AccessStamper blob construction."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def fake_alshub():
    c = AsyncMock()
    c.get_active_esaf.return_value = {
        "EsafFriendlyId": "BLS-00480-001",
        "ProposalFriendlyId": "BLS-00480",
        "Beamline": "4.0.2",
    }
    return c


@pytest.fixture
def fake_settings_no_override():
    s = MagicMock()
    s.access_override = None  # convention: None = inactive
    return s


@pytest.fixture
def fake_settings_active_override():
    s = MagicMock()
    s.access_override = MagicMock(
        esaf_id="BLS-99999-001",
        start=_now() - timedelta(hours=1),
        end=_now() + timedelta(hours=1),
        set_by="0000-9999-9999-9999",
    )
    return s


@pytest.fixture
def fake_session():
    s = MagicMock()
    s.session = MagicMock()
    s.session.token = MagicMock()
    s.session.token.claims = {
        "orcid": "0000-0001-9363-2557",
        "sub": "abc-uuid-1234",
    }
    return s


@pytest.mark.asyncio
async def test_stamp_schedule_path(fake_alshub, fake_settings_no_override, fake_session):
    from lucid.services.access_stamper import AccessStamper

    stamper = AccessStamper(
        beamline="4.0.2",
        alshub_client=fake_alshub,
        session_provider=lambda: fake_session.session,
        settings_provider=lambda: fake_settings_no_override,
    )
    blob = await stamper.build_blob()

    assert blob["esaf_id"] == "BLS-00480-001"
    assert blob["proposal_id"] == "BLS-00480"
    assert blob["beamline"] == "4.0.2"
    assert blob["esaf_source"] == "schedule"
    assert blob["participants"][0]["orcid"] == "0000-0001-9363-2557"
    assert blob["participants"][0]["keycloak_sub"] == "abc-uuid-1234"


@pytest.mark.asyncio
async def test_stamp_admin_override_wins(
    fake_alshub, fake_settings_active_override, fake_session,
):
    from lucid.services.access_stamper import AccessStamper

    stamper = AccessStamper(
        beamline="4.0.2",
        alshub_client=fake_alshub,
        session_provider=lambda: fake_session.session,
        settings_provider=lambda: fake_settings_active_override,
    )
    blob = await stamper.build_blob()
    assert blob["esaf_id"] == "BLS-99999-001"
    assert blob["esaf_source"] == "admin_override"
    fake_alshub.get_active_esaf.assert_not_called()  # override short-circuits


@pytest.mark.asyncio
async def test_stamp_misconfigured_override_falls_through(
    fake_alshub, fake_session,
):
    from lucid.services.access_stamper import AccessStamper

    bad = MagicMock()
    bad.access_override = MagicMock(
        esaf_id="BLS-99999-001",
        start=_now() + timedelta(hours=1),  # in the future
        end=_now() + timedelta(hours=2),
        set_by="x",
    )
    stamper = AccessStamper(
        beamline="4.0.2",
        alshub_client=fake_alshub,
        session_provider=lambda: fake_session.session,
        settings_provider=lambda: bad,
    )
    blob = await stamper.build_blob()
    assert blob["esaf_id"] == "BLS-00480-001"   # fell through to schedule
    assert blob["esaf_source"] == "schedule"


@pytest.mark.asyncio
async def test_stamp_no_schedule_marks_none(fake_session, fake_settings_no_override):
    from lucid.services.access_stamper import AccessStamper

    bare = AsyncMock()
    bare.get_active_esaf.return_value = None
    stamper = AccessStamper(
        beamline="9.9.9",
        alshub_client=bare,
        session_provider=lambda: fake_session.session,
        settings_provider=lambda: fake_settings_no_override,
    )
    blob = await stamper.build_blob()
    assert blob["esaf_id"] is None
    assert blob["esaf_source"] == "none"
    assert blob["participants"][0]["orcid"] == "0000-0001-9363-2557"


@pytest.mark.asyncio
async def test_stamp_alshub_outage_marks_pending(fake_session, fake_settings_no_override):
    from lucid.services.access_stamper import AccessStamper

    bare = AsyncMock()
    bare.get_active_esaf.side_effect = Exception("connection refused")
    stamper = AccessStamper(
        beamline="4.0.2",
        alshub_client=bare,
        session_provider=lambda: fake_session.session,
        settings_provider=lambda: fake_settings_no_override,
    )
    blob = await stamper.build_blob()
    assert blob["esaf_id"] is None
    assert blob["esaf_source"] == "pending"


@pytest.mark.asyncio
async def test_stamp_no_token_raises(fake_alshub, fake_settings_no_override):
    from lucid.services.access_stamper import AccessStamper, MissingSessionError

    no_session = lambda: None
    stamper = AccessStamper(
        beamline="4.0.2",
        alshub_client=fake_alshub,
        session_provider=no_session,
        settings_provider=lambda: fake_settings_no_override,
    )
    with pytest.raises(MissingSessionError):
        await stamper.build_blob()


@pytest.mark.asyncio
async def test_install_attaches_md_callable(
    fake_alshub, fake_settings_no_override, fake_session,
):
    from lucid.services.access_stamper import AccessStamper, install_into_run_engine
    from unittest.mock import MagicMock

    stamper = AccessStamper(
        beamline="4.0.2",
        alshub_client=fake_alshub,
        session_provider=lambda: fake_session.session,
        settings_provider=lambda: fake_settings_no_override,
    )

    re = MagicMock()
    re.md = {}

    install_into_run_engine(stamper, re)
    assert "access_blob" in re.md or callable(re.md.get("access_blob"))
    blob_field = re.md["access_blob"]
    if callable(blob_field):
        # md callable — call to verify
        result = blob_field()
        # If it's a coroutine, await it
        if hasattr(result, "__await__"):
            result = await result
    else:
        result = blob_field
    assert result["beamline"] == "4.0.2"
    assert result["esaf_id"] == "BLS-00480-001"
