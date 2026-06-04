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


def normalize_write_path(file_path: str, session_id: str) -> str:
    """Redirect a write path to the session's outputs directory.

    Ensures all agent writes land under outputs/{session_id}/ regardless
    of what path the agent specifies. This prevents concurrent sessions
    from interfering with each other's files.
    """
    if file_path.startswith(f"outputs/{session_id}/"):
        return file_path
    if file_path.startswith("outputs/"):
        return f"outputs/{session_id}/{file_path[len('outputs/'):]}".lstrip("/")
    return f"outputs/{session_id}/{file_path}"


_PATH_TRAVERSAL_PATTERN = re.compile(r"\.\.[/\\]")


def _reject_path_traversal(target: str, paths: PathContext) -> str | None:
    """Return an error message if the target path escapes the workspace via ../ traversal."""
    if not _PATH_TRAVERSAL_PATTERN.search(target):
        return None
    try:
        resolved = (paths.workspace / target).resolve()
        if not str(resolved).startswith(str(paths.workspace.resolve())):
            return (
                f"Path '{target}' resolves outside the workspace. "
                "Save all files within the workspace directory."
            )
    except (ValueError, OSError):
        return f"Path '{target}' is invalid."
    return None


_REDIRECT_TARGET_PATTERN = re.compile(
    r"(?:[12&]?>|>>|\s-o\s+|\s--output\s+)\s*([^\s;|&'\"]+)"
)

# cp/mv: last non-flag argument is the destination
_CP_MV_DEST_PATTERN = re.compile(r"""\b(cp|mv)\s+(?:-[a-zA-Z]+\s+)*.*\s+(/[^\s;|&'\"]+)""")

# tee: arguments after 'tee' are output file paths
_TEE_TARGET_PATTERN = re.compile(r"""\btee\s+(?:-[a-zA-Z]+\s+)*([^\s;|&'\"]+)""")


def check_bash_command_for_external_writes(cmd: str, paths: PathContext) -> str | None:
    """Return an error message if the command writes outside workspace, or None if safe."""
    _user_dir = str(paths.user_dir.resolve())

    def _block_if_outside(target: str) -> str | None:
        traversal_err = _reject_path_traversal(target, paths)
        if traversal_err:
            return traversal_err
        target_path = Path(target)
        if target_path.is_absolute() and not str(target_path.resolve()).startswith(_user_dir):
            return (
                f"Command writes to '{target}' which is outside the workspace. "
                "Save all files within the workspace directory (use outputs/ for generated files)."
            )
        return None

    # Check redirect patterns
    for match in _REDIRECT_TARGET_PATTERN.finditer(cmd):
        err = _block_if_outside(match.group(1))
        if err:
            return err

    # Check cp/mv destination
    for match in _CP_MV_DEST_PATTERN.finditer(cmd):
        err = _block_if_outside(match.group(2))
        if err:
            return err

    # Check tee targets
    for match in _TEE_TARGET_PATTERN.finditer(cmd):
        err = _block_if_outside(match.group(1))
        if err:
            return err

    return None


def _rewrite_bash_command(cmd: str, paths: PathContext) -> str:
    """Rewrite a bash command so that output redirections point inside workspace."""
    ws = str(paths.workspace.resolve())

    def _make_safe(external_path: str) -> str:
        return f"outputs/{Path(external_path).name}"

    def replace_external_path(match: re.Match) -> str:
        target = match.group(1) if match.lastindex else match.group(0)
        target_path = Path(target)
        if target_path.is_absolute() and not str(target_path.resolve()).startswith(ws):
            return match.group(0).replace(target, _make_safe(target), 1)
        return match.group(0)

    # fd redirects: >, >>, 1>, 2>, &>, 1>>, 2>>, &>>
    redirect_patterns = [
        r"(?:[12&]?>\s*)([^\s;|&'\"]+)",
        r"(?:[12&]?>>\s*)([^\s;|&'\"]+)",
        r"(-o\s+)(\S+)",
        r"(--output\s+)(\S+)",
        r"(?:[12&]?>\s*)'([^']+)'",
        r'(?:[12&]?>\s*)"([^"]+)"',
    ]
    result = cmd
    for pat in redirect_patterns:
        result = re.sub(pat, replace_external_path, result)

    # tee: rewrite file arguments (not the piped content)
    result = _rewrite_tee_targets(result, ws, _make_safe)

    # cp / mv: rewrite destination (last argument)
    result = _rewrite_cp_mv_dest(result, ws, _make_safe)

    return result


def _rewrite_tee_targets(cmd: str, ws: str, make_safe) -> str:
    """Rewrite file arguments to 'tee' that point outside the workspace."""
    # Match 'tee' followed by one or more file paths
    tee_pat = re.compile(r"\btee\s+(.+)$")
    m = tee_pat.search(cmd)
    if not m:
        return cmd
    args = m.group(1)
    parts = args.split()
    rewritten = []
    for part in parts:
        # Skip flags like -a, --append
        if part.startswith("-"):
            rewritten.append(part)
            continue
        p = Path(part)
        if p.is_absolute() and not str(p.resolve()).startswith(ws):
            rewritten.append(make_safe(part))
        else:
            rewritten.append(part)
    return cmd[: m.start(1)] + " ".join(rewritten)


def _rewrite_cp_mv_dest(cmd: str, ws: str, make_safe) -> str:
    """Rewrite destination of cp/mv commands that point outside workspace."""
    cp_mv_pat = re.compile(r"""\b(cp|mv)\s+.*\s+(/[^\s'\"]+)\s*$""")
    m = cp_mv_pat.search(cmd)
    if not m:
        return cmd
    target = m.group(2)
    p = Path(target)
    if p.is_absolute() and not str(p.resolve()).startswith(ws):
        return cmd[: m.start(2)] + make_safe(target) + cmd[m.end(2) :]
    return cmd
