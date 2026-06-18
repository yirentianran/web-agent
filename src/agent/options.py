"""Unified AgentOptions builder — serves both local and container modes.

Merges ``build_sdk_options()`` and ``build_container_options_dict()``
into a single builder. The returned ``AgentOptions`` dataclass is converted
to SDK-specific ``ClaudeAgentOptions`` by ``LocalAgentExecutor`` or
serialized to a JSON dict by ``ContainerAgentExecutor``.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentOptions:
    """Unified agent configuration — consumed by both executor implementations."""

    model: str = ""
    system_prompt: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: set[str] = field(default_factory=set)
    max_turns: int = 200
    mcp_servers: dict[str, Any] | None = None
    env: dict[str, str] | None = None
    include_partial_messages: bool = True
    max_buffer_size: int = 10 * 1024 * 1024
    permission_mode: str = "acceptEdits"
    cwd: str | None = None  # container-only: workspace path inside container
    resume_session_id: str | None = None
    # SDK-specific: path to user data dir for HOME override (local mode only)
    user_data_dir: str | None = None
    # SDK-specific: skills dir (local mode only)
    skills_dir: str | None = None


async def build_agent_options(
    user_id: str,
    *,
    skills_override: dict[str, dict[str, Any]] | None = None,
    system_prompt_override: str | None = None,
    resume_session_id: str | None = None,
    language: str | None = None,
    container_mode: bool = False,
) -> AgentOptions:
    """Build unified AgentOptions for both local and container modes.

    Calls ``_build_sdk_config()`` from main_server for the shared config
    layer, then constructs the appropriate AgentOptions depending on mode.
    """
    from main_server import (  # noqa: PLC0415 — lazy to avoid circular import at module level
        _build_sdk_config,
        _get_container_manager,
        load_mcp_config,
        load_skills,
        user_data_dir,
        user_workspace_dir,
    )

    mcp_config = await load_mcp_config()
    if skills_override is not None:
        skills = skills_override
    else:
        skills = await load_skills(user_id)

    workspace = user_workspace_dir(user_id)

    cfg = await _build_sdk_config(
        user_id,
        mcp_config,
        skills,
        workspace,
        language,
        user_data_dir_override=user_data_dir(user_id) if not container_mode else None,
        system_prompt_override=system_prompt_override,
    )

    options = AgentOptions(
        model=cfg["model"] or "",
        system_prompt=cfg["system_prompt"],
        allowed_tools=cfg["allowed_tools"],
        disallowed_tools=cfg["disallowed_tools"],
        max_turns=cfg["max_turns"],
        mcp_servers=cfg["mcp_servers"],
        env=cfg["sdk_env"],
        include_partial_messages=cfg["include_partial_messages"],
        max_buffer_size=cfg["max_buffer_size"],
        permission_mode="acceptEdits",
        resume_session_id=resume_session_id,
    )

    if container_mode:
        cm = _get_container_manager()
        options = dataclasses.replace(options, cwd=str(
            cm.container_workspace_dir(user_id)
        ) if cm else "/workspace")
    else:
        options = dataclasses.replace(
            options,
            user_data_dir=str(user_data_dir(user_id)),
            skills_dir=str(workspace / ".claude" / "skills"),
        )

    return options
