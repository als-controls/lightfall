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
    """Mock SessionManager-style object.

    Mirrors the auth-v2 lucid.auth.session.Session shape: the bearer is
    discarded post-mint so ``.token`` is None, and the decoded claims dict
    lives on ``.user.attributes`` (where orcid/sub live).
    """
    s = MagicMock()
    s.session = MagicMock()
    s.session.token = None  # auth-v2: bearer discarded post-mint
    s.session.user = MagicMock()
    s.session.user.attributes = {
        "orcid": "0000-0001-9363-2557",
        "sub": "abc-uuid-1234",
        "email": "test@lbl.gov",
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
async def test_stamp_no_session_raises(fake_alshub, fake_settings_no_override):
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
async def test_stamp_no_user_raises(fake_alshub, fake_settings_no_override):
    """Auth-v2 presence-check gates on `session.user`, not `session.token`."""
    from lucid.services.access_stamper import AccessStamper, MissingSessionError

    session_without_user = MagicMock()
    session_without_user.token = None
    session_without_user.user = None
    stamper = AccessStamper(
        beamline="4.0.2",
        alshub_client=fake_alshub,
        session_provider=lambda: session_without_user,
        settings_provider=lambda: fake_settings_no_override,
    )
    with pytest.raises(MissingSessionError):
        await stamper.build_blob()


def test_compute_access_tags_emits_one_per_predicate():
    from lucid.services.access_stamper import compute_access_tags

    blob = {
        "esaf_id": "BLS-00480-001",
        "beamline": "4.0.2",
        "participants": [
            {"keycloak_sub": "abc", "orcid": "0000-0001-9363-2557"},
            {"keycloak_sub": "def", "orcid": None},
        ],
    }
    tags = set(compute_access_tags(blob))
    assert tags == {
        "esaf:BLS-00480-001",
        "beamline:4.0.2",
        "participant:keycloak_sub:abc",
        "participant:orcid:0000-0001-9363-2557",
        "participant:keycloak_sub:def",
    }


def test_install_appends_preprocessor(
    fake_alshub, fake_settings_no_override, fake_session,
):
    """Verify install adds a preprocessor that injects both access_blob
    (for audit metadata) and tiled_access_tags (for Tiled's access column,
    which is what AccessBlobFilter actually queries) into open_run."""
    from bluesky import Msg
    from unittest.mock import MagicMock

    from lucid.services.access_stamper import AccessStamper, install_into_run_engine

    stamper = AccessStamper(
        beamline="4.0.2",
        alshub_client=fake_alshub,
        session_provider=lambda: fake_session.session,
        settings_provider=lambda: fake_settings_no_override,
    )

    re = MagicMock()
    re.preprocessors = []
    install_into_run_engine(stamper, re)
    assert len(re.preprocessors) == 1

    preprocessor = re.preprocessors[0]

    def plan():
        yield Msg("open_run")
        yield Msg("close_run")

    msgs = list(preprocessor(plan()))
    open_run_msg = next(m for m in msgs if m.command == "open_run")

    # Audit blob (lands in metadata.start.access_blob).
    assert "access_blob" in open_run_msg.kwargs
    blob = open_run_msg.kwargs["access_blob"]
    assert blob["beamline"] == "4.0.2"
    assert blob["esaf_id"] == "BLS-00480-001"
    assert blob["esaf_source"] == "schedule"
    assert blob["participants"][0]["orcid"] == "0000-0001-9363-2557"

    # Read-side gate (popped by TiledWriter and routed to access_blob column).
    assert "tiled_access_tags" in open_run_msg.kwargs
    tags = set(open_run_msg.kwargs["tiled_access_tags"])
    assert "esaf:BLS-00480-001" in tags
    assert "beamline:4.0.2" in tags
    assert "participant:keycloak_sub:abc-uuid-1234" in tags
    assert "participant:orcid:0000-0001-9363-2557" in tags


def test_install_is_idempotent(
    fake_alshub, fake_settings_no_override, fake_session,
):
    """Re-installing should replace, not stack."""
    from unittest.mock import MagicMock

    from lucid.services.access_stamper import AccessStamper, install_into_run_engine

    stamper = AccessStamper(
        beamline="4.0.2",
        alshub_client=fake_alshub,
        session_provider=lambda: fake_session.session,
        settings_provider=lambda: fake_settings_no_override,
    )

    re = MagicMock()
    re.preprocessors = [lambda plan: plan]  # an unrelated existing preprocessor
    install_into_run_engine(stamper, re)
    install_into_run_engine(stamper, re)
    install_into_run_engine(stamper, re)

    stamper_count = sum(
        1 for p in re.preprocessors if getattr(p, "_is_access_stamper", False)
    )
    assert stamper_count == 1
    # Pre-existing preprocessor preserved
    assert len(re.preprocessors) == 2
