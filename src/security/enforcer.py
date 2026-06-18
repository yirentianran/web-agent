"""Shared pre-execution security enforcement.

Used by both local mode (via SDK hooks) and container mode (via
agent_server control_request handlers). Single implementation ensures
consistent behavior across modes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.security.filters import BashCommandFilter, FileAccessFilter

logger = logging.getLogger(__name__)

_INVALID_FILENAMES = frozenset({"null", "undefined", "none", ""})


@dataclass
class SecurityEnforcer:
    """Shared pre-execution security checks for agent tool calls.

    ``user_id``, ``workspace``, and ``user_dir`` define the sandbox
    boundaries. All checks use these to validate paths and commands.

    Used by:
    - LocalAgentExecutor: builds SDK can_use_tool / PreToolUse hooks
    - agent_server._CliRunner: control_request hook_callback handler
    """

    user_id: str
    workspace: Path
    user_dir: Path

    def check_bash(self, command: str) -> tuple[bool, str]:
        """Check if a bash command is safe to execute.

        Returns (True, "") if allowed, (False, reason) if denied.
        """
        if not command or not command.strip():
            return False, "Empty command"
        allowed, reason = BashCommandFilter.check(command)
        if not allowed:
            logger.debug(
                "SecurityEnforcer[Bash]: blocked command for user %s: %s",
                self.user_id,
                reason,
            )
        return allowed, reason

    def check_write_path(self, file_path: str) -> tuple[bool, str]:
        """Check if a file write path is within the allowed sandbox.

        Returns (True, "") if allowed, (False, reason) if denied.
        """
        if not file_path or file_path.lower() in _INVALID_FILENAMES:
            logger.warning(
                "SecurityEnforcer[Write]: blocked invalid file_path '%s' for user %s",
                file_path,
                self.user_id,
            )
            return False, f"Invalid file path: '{file_path}'. Please provide a real filename."

        allowed, reason = FileAccessFilter.check(file_path)
        if not allowed:
            logger.debug(
                "SecurityEnforcer[Write]: blocked sensitive file '%s' for user %s",
                file_path,
                self.user_id,
            )
            return False, reason

        return True, ""

    def check_read_path(self, file_path: str) -> tuple[bool, str]:
        """Check if a file read path is allowed.

        Returns (True, "") if allowed, (False, reason) if denied.
        """
        if not file_path:
            return False, "Empty path"

        allowed, reason = FileAccessFilter.check(file_path)
        if not allowed:
            logger.debug(
                "SecurityEnforcer[Read]: blocked sensitive file '%s' for user %s",
                file_path,
                self.user_id,
            )
        return allowed, reason

    def check_read_size(
        self, file_path: str, max_bytes: int, cwd: str | None = None
    ) -> tuple[bool, str]:
        """Check if a file is within the allowed read size limit.

        Returns (True, "") if allowed, (False, reason) if denied.
        """
        if not file_path or max_bytes <= 0:
            return True, ""

        try:
            resolved = Path(file_path)
            if not resolved.is_absolute() and cwd:
                resolved = Path(cwd) / file_path
            file_size = resolved.stat().st_size
            if file_size > max_bytes:
                size_mb = file_size / (1024 * 1024)
                limit_mb = max_bytes / (1024 * 1024)
                return False, (
                    f"File is {size_mb:.1f}MB. "
                    f"The maximum allowed size for reading "
                    f"is {limit_mb:.0f}MB. Please use Bash "
                    f"commands like 'head' or 'split' to "
                    f"process the file in smaller chunks."
                )
        except OSError:
            pass  # file doesn't exist — let CLI handle it

        return True, ""

    def build_write_input_allow(
        self, tool_input: dict[str, Any]
    ) -> dict[str, tuple[bool, str]]:
        """Return per-field permission results for a Write tool input.

        Used to build PreToolUse hook response that may deny the write.
        """
        file_path = str(tool_input.get("file_path", ""))
        allowed, reason = self.check_write_path(file_path)
        return {"file_path": (allowed, reason)}

    def build_bash_allow(
        self, tool_input: dict[str, Any]
    ) -> tuple[bool, str]:
        """Return permission result for a Bash tool input."""
        cmd = str(tool_input.get("command", ""))
        return self.check_bash(cmd)

    def build_read_allow(
        self,
        tool_input: dict[str, Any],
        max_bytes: int = 0,
        cwd: str | None = None,
    ) -> dict[str, tuple[bool, str]]:
        """Return per-field permission results for a Read tool input."""
        file_path = str(tool_input.get("file_path", ""))
        path_allowed, path_reason = self.check_read_path(file_path)
        if not path_allowed:
            return {"file_path": (False, path_reason)}
        size_allowed, size_reason = self.check_read_size(file_path, max_bytes, cwd)
        if not size_allowed:
            return {"file_size": (False, size_reason)}
        return {"file_path": (True, "")}
