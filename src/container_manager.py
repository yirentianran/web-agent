"""Docker container lifecycle management for per-user isolation.

Each user gets an isolated container with:
- Separate workspace volume
- Separate Skills directories (shared ro + personal rw)
- Separate Claude data (sessions, settings, cache)
- MCP configuration injected via environment variable
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import docker

logger = logging.getLogger(__name__)

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))
CONTAINER_IMAGE = "web-agent-user:latest"
CONTAINER_PORT = 8000

_client: docker.DockerClient | None = None


def get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


# ── user paths ────────────────────────────────────────────────────


def user_data_dir(user_id: str) -> Path:
    return DATA_ROOT / "users" / user_id


def ensure_user_dirs(user_id: str) -> None:
    """Create all per-user directories on the host."""
    dirs = [
        user_data_dir(user_id) / "workspace" / "uploads",
        user_data_dir(user_id) / "workspace" / "reports",
        user_data_dir(user_id) / "skills",
        user_data_dir(user_id) / "claude-data" / "sessions",
        user_data_dir(user_id) / "claude-data" / "memory",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


# ── volume configuration ──────────────────────────────────────────


def get_user_volumes(user_id: str) -> dict[str, dict[str, str]]:
    """Return Docker volume bindings for a user's container."""
    base = user_data_dir(user_id)
    return {
        # Public Skills — read-only
        str(DATA_ROOT / "shared-skills"): {
            "bind": "/home/agent/.claude/shared-skills",
            "mode": "ro",
        },
        # Personal Skills — read-write
        str(base / "skills"): {
            "bind": "/home/agent/.claude/personal-skills",
            "mode": "rw",
        },
        # Workspace
        str(base / "workspace"): {
            "bind": "/workspace",
            "mode": "rw",
        },
        # Claude data (sessions, settings, cache, memory) — persistent
        str(base / "claude-data"): {
            "bind": "/home/agent/.claude",
            "mode": "rw",
        },
        # Hook scripts
        str(Path(__file__).parent / "hooks"): {
            "bind": "/hooks",
            "mode": "ro",
        },
    }


# ── environment ───────────────────────────────────────────────────


def get_user_env(user_id: str, mcp_config: dict | None = None) -> dict[str, str]:
    """Build environment variables for a user's container."""
    env: dict[str, str] = {
        "USER_ID": user_id,
        "ANTHROPIC_API_KEY": os.getenv(
            f"ANTHROPIC_API_KEY_{user_id.upper()}",
            os.getenv("ANTHROPIC_API_KEY", ""),
        ),
        "CLAUDE_SKILLS_DIRS": (
            "/home/agent/.claude/shared-skills,"
            "/home/agent/.claude/personal-skills"
        ),
    }
    if mcp_config:
        env["MCP_CONFIG_JSON"] = json.dumps(mcp_config)

    # Write settings.json into the container's .claude directory
    settings_json = json.dumps({
        "allowedTools": _build_allowed_tools(mcp_config),
        "disallowedTools": [],
        "permissionMode": "bypassPermissions",
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "command": "python /hooks/pre_tool_use.py"}
            ],
            "PostToolUse": [
                {"matcher": "Write|Edit", "command": "python /hooks/post_tool_use.py"}
            ],
            "Stop": [
                {"command": "python /hooks/on_stop.py"}
            ],
        },
    })
    settings_path = user_data_dir(user_id) / "claude-data" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(settings_json)

    return env


def _build_allowed_tools(mcp_config: dict | None) -> list[str]:
    """Expand all MCP tool names to their fully-qualified form."""
    tools = ["Read", "Edit", "Write", "Glob", "Grep", "Bash",
             "WebFetch", "WebSearch", "Agent", "Skill"]
    if mcp_config:
        for server_name in mcp_config.get("mcpServers", {}):
            cfg = mcp_config["mcpServers"][server_name]
            for tool_name in cfg.get("enabled_tools", []):
                tools.append(f"mcp__{server_name}__{tool_name}")
    return tools


# ── container lifecycle ───────────────────────────────────────────


def container_name(user_id: str) -> str:
    return f"web-agent-{user_id}"


def ensure_container(user_id: str, mcp_config: dict | None = None) -> str:
    """Ensure a container is running for the user.

    Returns the container's internal API URL (for WebSocket bridge).
    Creates the container if it doesn't exist, unpauses if paused.
    """
    client = get_client()
    name = container_name(user_id)
    ensure_user_dirs(user_id)

    try:
        container = client.containers.get(name)
        if container.status == "paused":
            container.unpause()
            logger.info("Unpaused container for user %s", user_id)
        elif container.status == "exited":
            container.start()
            logger.info("Restarted container for user %s", user_id)
        else:
            logger.debug("Container for user %s already running (%s)", user_id, container.status)
    except docker.errors.NotFound:
        logger.info("Creating new container for user %s", user_id)
        container = client.containers.run(
            image=CONTAINER_IMAGE,
            name=name,
            volumes=get_user_volumes(user_id),
            environment=get_user_env(user_id, mcp_config),
            ports={f"{CONTAINER_PORT}/tcp": None},  # ephemeral host port
            detach=True,
            mem_limit="4g",
            cpu_quota=100000,  # 1 CPU
            restart_policy={"Name": "unless-stopped"},
        )

    # Get the dynamically assigned host port
    container.reload()
    port = container.attrs["NetworkSettings"]["Ports"].get(f"{CONTAINER_PORT}/tcp")
    if port and port[0]:
        return f"http://localhost:{port[0]['HostPort']}"
    # Fallback: use Docker internal network
    return f"http://{name}:{CONTAINER_PORT}"


def pause_container(user_id: str) -> None:
    client = get_client()
    try:
        container = client.containers.get(container_name(user_id))
        container.pause()
        logger.info("Paused container for user %s", user_id)
    except docker.errors.NotFound:
        pass


def destroy_container(user_id: str) -> None:
    client = get_client()
    try:
        container = client.containers.get(container_name(user_id))
        container.remove(force=True)
        logger.info("Destroyed container for user %s", user_id)
    except docker.errors.NotFound:
        pass


def list_active_containers() -> list[dict[str, str]]:
    """Return list of running user containers."""
    client = get_client()
    containers = client.containers.list(filters={"ancestor": CONTAINER_IMAGE})
    return [
        {"name": c.name.replace("web-agent-", ""), "status": c.status}
        for c in containers
    ]
