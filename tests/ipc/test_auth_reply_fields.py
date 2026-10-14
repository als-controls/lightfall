from unittest.mock import MagicMock

from lightfall.ipc.service import IPCService


def test_application_passes_tiled_fields_as_extra(monkeypatch):
    """The service no longer knows about Tiled; the application hands the
    fields over. Guard the call shape so the wire reply keeps its keys."""
    from lightfall.core.application import LFApplication
    from lightfall.ipc.trust import TrustState

    app = LFApplication.__new__(LFApplication)
    ipc = MagicMock(spec=IPCService)
    ipc.evaluate_trust.return_value = TrustState.APPROVED
    ipc.build_auth_response.return_value = {"status": "approved"}
    app._services = MagicMock()
    app._services.get.return_value = ipc
    app._auth_dialog_active = False
    app._get_current_session = lambda: MagicMock()
    app._get_tiled_url = lambda: "https://tiled"
    monkeypatch.setattr(
        "lightfall.auth.session.SessionManager.get_instance",
        lambda: MagicMock(get_api_key=lambda name: "KEY"),
    )
    app._handle_ipc_auth_request("als.x.auth.request", {"app_name": "camphor-mcp"}, "reply.1")
    kwargs = ipc.build_auth_response.call_args.kwargs
    assert kwargs["approved"] is True and kwargs["app_name"] == "camphor-mcp"
    assert kwargs["extra"]["tiled_url"] == "https://tiled"
    assert kwargs["extra"]["tiled_token"] == "KEY"
    assert "session_id" in kwargs["extra"]


def test_application_denies_when_no_session(monkeypatch):
    """Preserves I-3: a trusted app with no login session gets denied,
    never approved with empty Tiled fields."""
    from lightfall.core.application import LFApplication
    from lightfall.ipc.trust import TrustState

    app = LFApplication.__new__(LFApplication)
    ipc = MagicMock(spec=IPCService)
    ipc.evaluate_trust.return_value = TrustState.APPROVED
    ipc.build_auth_response.return_value = {"status": "denied"}
    app._services = MagicMock()
    app._services.get.return_value = ipc
    app._auth_dialog_active = False
    app._get_current_session = lambda: None
    app._get_tiled_url = lambda: "https://tiled"

    app._handle_ipc_auth_request("als.x.auth.request", {"app_name": "camphor-mcp"}, "reply.1")

    kwargs = ipc.build_auth_response.call_args.kwargs
    assert kwargs == {"approved": False, "reason": "no_session"}
