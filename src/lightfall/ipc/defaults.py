"""IPC defaults shared by startup (core/application.py) and Settings → IPC.

A facility bootstrap can override both through preferences (ipc_topic_prefix,
ipc_nats_url). Deriving the prefix from the machine binding is setup wizard W2.
"""
from __future__ import annotations

DEFAULT_IPC_TOPIC_PREFIX = "als.7011"
DEFAULT_IPC_NATS_URL = "nats://bcgnats.als.private.lbl.gov:4222"
