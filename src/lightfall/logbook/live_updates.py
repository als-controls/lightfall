"""Live logbook updates: subscribe to server change events over NATS and pull.

Notify-and-pull — the NATS event only signals "something changed"; the actual
data comes from the client's normal authz-scoped pull.
"""
from __future__ import annotations

_SUBJECT_PREFIX = "_lightfall.logbook.changed."


def logbook_user_token(user_id: str) -> str:
    """Hex-encode the server user_id into a NATS-subject-safe token.

    Must match ``lightfall_logbook.events.subject_for_user`` on the server.
    """
    return user_id.encode("utf-8").hex()


def subject_for_user(user_id: str) -> str:
    return _SUBJECT_PREFIX + logbook_user_token(user_id)
