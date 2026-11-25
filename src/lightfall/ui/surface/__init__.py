"""The agent UI surface: Lightfall's windows described and operated by role and name.

See ``docs/developer-guide/agent-surface.md``.
"""

from lightfall.ui.surface.names import accessible_name
from lightfall.ui.surface.roles import AgentDescribable, role_of

__all__ = ["AgentDescribable", "accessible_name", "role_of"]
