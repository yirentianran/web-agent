"""Docker container lifecycle management for per-user isolation.

Each user gets an isolated container with:
- Separate workspace volume
- Separate Skills directories (shared ro + personal rw)
- Separate Claude data (sessions, settings, cache)
- MCP configuration injected via environment variable
- Idle TTL: containers are stopped after inactivity to save resources
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import docker

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.DEBUG if os.getenv("LOG_LEVEL") == "debug" else logging.INFO)

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))
CONTAINER_IMAGE = "web-agent-user:latest"
CONTAINER_PORT = 8000
CONTAINER_IDLE_TTL = int(os.getenv("CONTAINER_IDLE_TTL", "1800"))  # 30 min default
IDLE_CHECK_INTERVAL = 60  # seconds between idle checks

_client: docker.DockerClient | None = None
_last_activity: dict[str, float] = {}
_idle_monitor_task: asyncio.Task | None = None


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
        user_data_dir(user_id) / ".claude" / "memory",
        user_data_dir(user_id) / "logs",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


# ── volume configuration ──────────────────────────────────────────


def get_user_volumes(user_id: str) -> dict[str, dict[str, str]]:
    """Return Docker volume bindings for a user's container."""
    base = user_data_dir(user_id)
    root = DATA_ROOT.resolve()  # Docker requires absolute host paths
    hooks_dir = (Path(__file__).parent / "hooks").resolve()
    return {
        # Public Skills — read-only
        str(root / "shared-skills"): {
            "bind": "/home/agent/.claude/shared-skills",
            "mode": "ro",
        },
        # Personal Skills — read-write
        str(base.resolve() / "skills"): {
            "bind": "/home/agent/.claude/personal-skills",
            "mode": "rw",
        },
        # Workspace
        str(base.resolve() / "workspace"): {
            "bind": "/workspace",
            "mode": "rw",
        },
        # Claude data (sessions, settings, cache, memory) — persistent
        str(base.resolve() / ".claude"): {
            "bind": "/home/agent/.claude",
            "mode": "rw",
        },
        # Hook scripts
        str(hooks_dir): {
            "bind": "/hooks",
            "mode": "ro",
        },
        # Container logs — persist to host so logs survive container restarts
        str(base.resolve() / "logs"): {
            "bind": "/app/logs",
            "mode": "rw",
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

    # Write settings.json into the container's .claude directory.
    # Hooks and tool permissions are now managed programmatically through the
    # SDK API in agent_server.py — no longer set via settings.json.
    settings_json = json.dumps({
        "allowedTools": _build_allowed_tools(mcp_config),
        "disallowedTools": ["WebSearch", "WebFetch"],
    })
    settings_path = user_data_dir(user_id) / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(settings_json)

    return env


def _build_allowed_tools(mcp_config: dict | None) -> list[str]:
    """Expand all MCP tool names to their fully-qualified form."""
    # WebFetch/WebSearch excluded: MCP fetch servers provide web content retrieval
    tools = ["Read", "Edit", "Write", "Glob", "Grep", "Bash",
             "Agent", "Skill"]
    if mcp_config:
        for server_name in mcp_config.get("mcpServers", {}):
            cfg = mcp_config["mcpServers"][server_name]
            for tool_name in cfg.get("tools", []):
                tools.append(f"mcp__{server_name}__{tool_name}")
    return tools


# ── container lifecycle ───────────────────────────────────────────


def container_name(user_id: str) -> str:
    return f"web-agent-{user_id}"


def ensure_container(user_id: str, mcp_config: dict | None = None) -> str:
    """Ensure a container is running for the user.

    Returns the container's internal API URL (for WebSocket bridge).
    Creates the container if it doesn't exist, unpauses if paused.
    Records activity to prevent idle stop.
    """
    touch_user(user_id)
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


def stop_container(user_id: str) -> None:
    """Stop a user's container gracefully (SIGTERM, then SIGKILL after timeout).

    Transitions the container to 'exited' state, preserving volumes.
    ensure_container() will restart it when the user returns.
    """
    client = get_client()
    try:
        container = client.containers.get(container_name(user_id))
        container.stop(timeout=30)
        logger.info("Stopped container for user %s", user_id)
    except docker.errors.NotFound:
        pass


def touch_user(user_id: str) -> None:
    """Record user activity timestamp to prevent idle stop."""
    _last_activity[user_id] = time.time()


def stop_idle_containers() -> int:
    """Stop containers for users who have been idle beyond CONTAINER_IDLE_TTL.

    Returns the number of containers stopped.
    """
    now = time.time()
    stopped = 0
    for user_id, last_ts in list(_last_activity.items()):
        if now - last_ts < CONTAINER_IDLE_TTL:
            continue
        try:
            client = get_client()
            container = client.containers.get(container_name(user_id))
            if container.status in ("running", "paused"):
                container.stop(timeout=30)
                logger.info("Stopped idle container for user %s (idle %.0fs)", user_id, now - last_ts)
                stopped += 1
        except docker.errors.NotFound:
            del _last_activity[user_id]
    return stopped


async def _run_idle_monitor() -> None:
    """Background loop that periodically stops idle containers."""
    logger.info("Container idle monitor started (TTL=%ds, check every %ds)", CONTAINER_IDLE_TTL, IDLE_CHECK_INTERVAL)
    while True:
        await asyncio.sleep(IDLE_CHECK_INTERVAL)
        try:
            stopped = stop_idle_containers()
            if stopped > 0:
                logger.debug("Idle monitor stopped %d container(s)", stopped)
        except Exception:
            logger.exception("Error in container idle monitor")


def start_idle_monitor() -> None:
    """Start the background idle-monitor asyncio task."""
    global _idle_monitor_task
    if _idle_monitor_task is not None and not _idle_monitor_task.done():
        return
    _idle_monitor_task = asyncio.create_task(_run_idle_monitor())


def list_active_containers() -> list[dict[str, str]]:
    """Return list of running user containers."""
    client = get_client()
    containers = client.containers.list(filters={"ancestor": CONTAINER_IMAGE})
    return [
        {"name": c.name.replace("web-agent-", ""), "status": c.status}
        for c in containers
    ]
