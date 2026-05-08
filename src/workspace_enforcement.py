"""Shared workspace path enforcement — usable by both host (main_server) and container (agent_server).

Provides a path-context abstraction so the same enforcement logic works in both
environments:

- Host: workspace = ``DATA_ROOT/users/{user_id}/workspace``, user_dir = ``DATA_ROOT/users/{user_id}``
- Container: workspace = ``/workspace``, user_dir = ``/home/agent``
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


# ── Path Context ────────────────────────────────────────────────────


class PathContext(Protocol):
    """Protocol for workspace path resolution. Both HostPaths and ContainerPaths satisfy this."""

    @property
    def workspace(self) -> Path: ...
    @property
    def user_dir(self) -> Path: ...


@dataclass(frozen=True)
class HostPaths:
    """Path context for enforcement running on the host (main_server.py)."""

    user_id: str
    data_root: Path

    @property
    def workspace(self) -> Path:
        return self.data_root / "users" / self.user_id / "workspace"

    @property
    def user_dir(self) -> Path:
        return self.data_root / "users" / self.user_id


@dataclass(frozen=True)
class ContainerPaths:
    """Path context for enforcement running inside a Docker container (agent_server.py).

    workspace is typically ``/workspace``, home_dir is ``/home/agent``.
    """

    workspace: Path
    home_dir: Path

    @property
    def user_dir(self) -> Path:
        return self.home_dir


# ── Enforcement Functions ───────────────────────────────────────────


def is_path_within_workspace(file_path: str, paths: PathContext) -> bool:
    """Check if a file path (relative or absolute) resolves within the workspace."""
    path = Path(file_path)
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (paths.workspace / path).resolve()
    return str(resolved).startswith(str(paths.workspace.resolve()))


def is_path_within_user_dir(file_path: str, paths: PathContext) -> bool:
    """Check if a file path resolves within the user's data directory.

    Broader than ``is_path_within_workspace`` — also permits access to
    the memory/ directory (host) or .claude/ directory (container),
    which are outside the workspace but within the user's isolated data scope.
    """
    path = Path(file_path)
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (paths.workspace / path).resolve()
    return str(resolved).startswith(str(paths.user_dir.resolve()))


def rewrite_path_to_workspace(file_path: str, paths: PathContext) -> str:
    """Rewrite an absolute external path to a workspace-relative path under outputs/."""
    path = Path(file_path)
    if not path.is_absolute():
        return file_path
    resolved = path.resolve()
    if str(resolved).startswith(str(paths.workspace.resolve())):
        return file_path
    return f"outputs/{path.name}"


def check_bash_command_for_external_writes(cmd: str, paths: PathContext) -> str | None:
    """Return an error message if the command writes outside workspace, or None if safe."""
    outside_patterns = [
        r"(?:>\s*|\w+\s+)(/Users/[^\s'\"]+)",
        r"(?:>\s*|\w+\s+)(/tmp/[^\s'\"]+)",
        r"(?:>\s*|\w+\s+)(/home/[^\s'\"]+)",
        r"(?:>\s*|\w+\s+)(/var/[^\s'\"]+)",
        r"(?:>\s*|\w+\s+)(/etc/[^\s'\"]+)",
        r"(?:>\s*|\w+\s+)(/root/[^\s'\"]+)",
    ]
    _user_dir = str(paths.user_dir.resolve())
    for pat in outside_patterns:
        match = re.search(pat, cmd)
        if match:
            target = match.group(1) if match.lastindex else match.group(0)
            target_path = Path(target)
            # Allow writes inside the user's isolated data directory,
            # even if the path matches a pattern (e.g., /home/... when
            # HOST_DATA_ROOT is under /home/ in docker-compose deployment).
            if target_path.is_absolute() and str(target_path.resolve()).startswith(_user_dir):
                continue
            return (
                f"Command writes to '{target}' which is outside the workspace. "
                "Save all files within the workspace directory (use outputs/ for generated files)."
            )
    return None


def _rewrite_bash_command(cmd: str, paths: PathContext) -> str:
    """Rewrite a bash command so that output redirections point inside workspace."""
    ws = str(paths.workspace.resolve())

    def replace_external_path(match: re.Match) -> str:
        target = match.group(2)
        target_path = Path(target)
        if target_path.is_absolute() and not str(target_path.resolve()).startswith(ws):
            replacement = f"outputs/{target_path.name}"
            return match.group(0).replace(target, replacement, 1)
        return match.group(0)

    patterns = [
        r"(>\s*)(/[^\s'\"]+)",
        r"(>>\s*)(/[^\s'\"]+)",
        r"(-o\s+)(/[^\s'\"]+)",
        r"(--output\s+)(/[^\s'\"]+)",
        r">\s*\'(/[^\']+)\'",
        r'>\s*"(/[^"]+)"',
    ]
    result = cmd
    for pat in patterns:
        result = re.sub(pat, replace_external_path, result)
    return result
