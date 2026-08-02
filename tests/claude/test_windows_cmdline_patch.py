"""Verify Windows command-line monkey-patch for Claude Agent SDK.

The patch extends the SDK's handling of command-line length limits on Windows
by writing large arguments (including --agents) to temporary files.
"""
from __future__ import annotations

import inspect
import platform

import pytest

from lightfall.claude import agent as agent_module


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only patch")
def test_windows_cmdline_patch_applied() -> None:
    """Verify the Windows patch is applied and sets the _cmdline_patched flag."""
    # Import lightfall.claude.agent which applies the patch on module load
    try:
        from claude_agent_sdk._internal.transport import subprocess_cli
    except ImportError:
        pytest.skip("claude_agent_sdk structure not available")

    # After importing agent module, the patch should be applied on Windows
    assert hasattr(subprocess_cli, '_cmdline_patched')
    assert subprocess_cli._cmdline_patched is True


def test_windows_cmdline_patch_handles_agents() -> None:
    """Verify the patch source includes handling for --agents argument.

    Large --agents payloads (from subagent definitions) must be written to
    temp files to avoid exceeding the 8191 character Windows CLI limit.
    """
    source = inspect.getsource(agent_module._patch_sdk_for_windows_cmdline_limit)
    assert "--agents" in source, (
        "Patch must handle --agents in atfile_args set to support large "
        "subagent payloads"
    )
