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


def test_windows_cmdline_patch_uses_system_prompt_file() -> None:
    """Verify the patch rewrites --system-prompt to the CLI-native flag.

    CLI >=2.1.221 silently ignores the ``@file`` convention for
    --system-prompt (the prompt is dropped with no error). The dedicated
    --system-prompt-file flag works and takes a plain path. --agents is no
    longer emitted on the command line by SDK 0.2.93 (sent via the
    initialize request instead), so it must not appear in the rewrite logic.
    """
    source = inspect.getsource(agent_module._rewrite_oversized_args)
    assert "--system-prompt-file" in source
    # --agents may be mentioned in prose (double-backtick markup) explaining
    # why it's gone, but must not appear as a quoted string literal in any
    # executable rewrite set/logic.
    assert '"--agents"' not in source
    assert "'--agents'" not in source


def test_rewrite_oversized_args_rewrites_system_prompt_to_file() -> None:
    """Functional test: an oversized --system-prompt is rewritten to
    --system-prompt-file <plain-path>, with the temp file containing the
    original value and no @-prefixed args left in the command.
    """
    oversized_prompt = "x" * 9000
    cmd = ["claude", "--system-prompt", oversized_prompt, "--model", "sonnet"]

    result, temp_files = agent_module._rewrite_oversized_args(cmd)

    assert "--system-prompt" not in result
    idx = result.index("--system-prompt-file")
    file_path = result[idx + 1]

    assert not file_path.startswith("@")
    assert temp_files == [file_path]

    with open(file_path, encoding="utf-8") as f:
        assert f.read() == oversized_prompt

    assert not any(arg.startswith("@") for arg in result)


def test_rewrite_oversized_args_skips_if_already_rewritten() -> None:
    """Skip-guard: if --system-prompt-file is already present, leave it alone."""
    cmd = ["claude", "--system-prompt-file", "C:\\temp\\already.txt"]

    result, temp_files = agent_module._rewrite_oversized_args(list(cmd))

    assert result == cmd
    assert temp_files == []
