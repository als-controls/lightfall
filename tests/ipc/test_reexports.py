"""Test that IPC modules are re-exports of lightfall_utils.ipc."""


def test_ipc_names_are_the_utils_objects():
    import lightfall_utils.ipc as u
    from lightfall.ipc import local_server, service, trust
    from lightfall.remote import protocol

    assert service.IPCService is u.IPCService
    assert service.ActionInfo is u.ActionInfo and service.EventInfo is u.EventInfo
    assert trust.TrustManager is u.TrustManager and trust.TrustDialog is u.TrustDialog
    assert trust.TrustState is u.TrustState
    assert local_server.LocalNatsServer is u.LocalNatsServer
    assert local_server.resolve_nats_binary is u.resolve_nats_binary
    assert protocol.error_reply is u.error_reply and protocol.CONTRACT_VERSION == 1
    assert protocol.ERROR_CODES == u.BASE_ERROR_CODES
    assert callable(service.get_ipc_service)  # stays in Lightfall
