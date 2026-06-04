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
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import docker

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
logger.setLevel(LOG_LEVEL)

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))

# ── Host path mapping ─────────────────────────────────────────────
# HOST_DATA_ROOT is the absolute path to the data directory on the
# Docker host machine. When main_server runs inside Docker and creates
# per-user containers via the Docker socket, volume source paths are
# resolved on the HOST, not inside the main_server container.
#
# - Explicitly set (docker-compose deployment): HOST_DATA_ROOT=/home/ubuntu/web-agent/data
# - Not set (local dev / main_server on host): defaults to DATA_ROOT.resolve()
_HOST_DATA_ROOT = os.getenv("HOST_DATA_ROOT")
if _HOST_DATA_ROOT:
    HOST_DATA_ROOT = Path(_HOST_DATA_ROOT)
else:
    HOST_DATA_ROOT = DATA_ROOT.resolve()

# Module-level: generate once per process for defense-in-depth
_agent_secret = os.getenv("AGENT_SECRET") or secrets.token_hex(32)

CONTAINER_IMAGE = "web-agent-user:latest"
CONTAINER_PORT = 8000
CONTAINER_IDLE_TTL = int(os.getenv("CONTAINER_IDLE_TTL", "1800"))  # 30 min default
IDLE_CHECK_INTERVAL = 60  # seconds between idle checks
CONTAINER_NAME_PREFIX = "web-agent-"


def _list_user_containers(
    client: docker.DockerClient,
    *,
    all: bool = False,
    status: str | None = None,
) -> list:
    """Return containers matching CONTAINER_IMAGE, optionally filtered by status."""
    filters: dict = {"ancestor": CONTAINER_IMAGE}
    if status:
        filters["status"] = status
    try:
        return client.containers.list(all=all, filters=filters)
    except docker.errors.DockerException:
        logger.exception("Failed to list containers (all=%s, status=%s)", all, status)
        return []


def user_id_from_container_name(name: str) -> str:
    """Extract user_id from a container name like 'web-agent-alice'."""
    if name.startswith(CONTAINER_NAME_PREFIX):
        return name[len(CONTAINER_NAME_PREFIX):]
    return name


def _running_in_docker() -> bool:
    """Return True if the current process is running inside a Docker container."""
    return os.path.exists("/.dockerenv")


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


def container_user_dir(user_id: str) -> Path:
    """Absolute path to the user's data directory INSIDE the per-user container.

    Uses HOST_DATA_ROOT so the path inside the container matches the
    non-container-mode path on the host machine.
    """
    return HOST_DATA_ROOT / "users" / user_id


def container_workspace_dir(user_id: str) -> Path:
    """Absolute path to the user's workspace INSIDE the per-user container."""
    return container_user_dir(user_id) / "workspace"


def ensure_user_dirs(user_id: str) -> None:
    """Create all per-user directories on the host."""
    dirs = [
        user_data_dir(user_id) / "workspace" / "uploads",
        user_data_dir(user_id) / ".claude" / "memory",
        user_data_dir(user_id) / ".cache" / "uv",
        user_data_dir(user_id) / "logs",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    # Ensure .claude.json exists so the CLI inside the container can find it
    claude_json = user_data_dir(user_id) / ".claude.json"
    if not claude_json.exists():
        claude_json.write_text(
            json.dumps(
                {
                    "firstStartTime": datetime.now(timezone.utc).isoformat(),
                    "migrationVersion": 11,
                }
            )
        )


# ── volume configuration ──────────────────────────────────────────


def get_user_volumes(user_id: str) -> dict[str, dict[str, str]]:
    """Return Docker volume bindings for a user's container.

    Both source and bind target use HOST_DATA_ROOT so the path inside the
    container matches the non-container-mode path on the host machine.
    """
    base = container_user_dir(user_id)
    return {
        # Shared Skills — read-only
        str(HOST_DATA_ROOT / "shared-skills"): {
            "bind": str(HOST_DATA_ROOT / "shared-skills"),
            "mode": "ro",
        },
        # Workspace
        str(base / "workspace"): {
            "bind": str(base / "workspace"),
            "mode": "rw",
        },
        # Claude data (sessions, settings, cache, memory) — persistent
        str(base / ".claude"): {
            "bind": str(base / ".claude"),
            "mode": "rw",
        },
        # Container logs — persist to host so logs survive container restarts
        str(base / "logs"): {
            "bind": str(base / "logs"),
            "mode": "rw",
        },
        # Claude user config — CLI needs this at $HOME/.claude.json
        str(base / ".claude.json"): {
            "bind": str(base / ".claude.json"),
            "mode": "rw",
        },
    }


# ── environment ───────────────────────────────────────────────────


def get_user_env(user_id: str, mcp_config: dict | None = None) -> dict[str, str]:
    """Build environment variables for a user's container."""
    base = container_user_dir(user_id)
    workspace = container_workspace_dir(user_id)

    env: dict[str, str] = {
        "USER_ID": user_id,
        "WORKSPACE": str(workspace),
        "HOME": str(base),
        "ANTHROPIC_AUTH_TOKEN": (
            os.getenv(f"ANTHROPIC_AUTH_TOKEN_{user_id.upper()}")
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
            or os.getenv(f"ANTHROPIC_API_KEY_{user_id.upper()}")
            or os.getenv("ANTHROPIC_API_KEY", "")
        ),
        "ANTHROPIC_API_KEY": (
            os.getenv(f"ANTHROPIC_AUTH_TOKEN_{user_id.upper()}")
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
            or os.getenv(f"ANTHROPIC_API_KEY_{user_id.upper()}")
            or os.getenv("ANTHROPIC_API_KEY", "")
        ),
        "ANTHROPIC_BASE_URL": os.getenv("ANTHROPIC_BASE_URL", ""),
        "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "true",
        "CLAUDE_SKILLS_DIRS": (f"{HOST_DATA_ROOT}/shared-skills,{base}/workspace/.claude/skills"),
        "UV_CACHE_DIR": str(base / ".cache" / "uv"),
        "LOG_DIR": str(base / "logs"),
        "AGENT_SECRET": _agent_secret,
        # UID/GID for entrypoint.sh to adapt the agent user at container startup
        "CONTAINER_UID": str(os.getuid()),
        "CONTAINER_GID": str(os.getgid()),
    }
    if mcp_config:
        env["MCP_CONFIG_JSON"] = json.dumps(mcp_config)

    return env


# ── container lifecycle ───────────────────────────────────────────


def container_name(user_id: str) -> str:
    return f"{CONTAINER_NAME_PREFIX}{user_id}"


def wait_for_container_ready(container_url: str, timeout: float = 30.0) -> None:
    """Poll the container's /api/health endpoint until it responds OK.

    Raises TimeoutError if the container doesn't become healthy within *timeout* seconds.
    """
    health_url = f"{container_url}/api/health"
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            req = urllib.request.Request(health_url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read())
                    if data.get("status") == "ok":
                        logger.debug(
                            "Container %s healthy after %.1fs", container_url, time.time() - (deadline - timeout)
                        )
                        return
        except (urllib.error.URLError, OSError, ConnectionError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(0.5)
    raise TimeoutError(f"Container at {container_url} not healthy after {timeout}s") from last_error


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

    needs_health_check = False

    try:
        container = client.containers.get(name)
        if container.status == "paused":
            container.unpause()
            logger.info("Unpaused container for user %s", user_id)
            needs_health_check = True
        elif container.status == "exited":
            container.start()
            logger.info("Restarted container for user %s", user_id)
            needs_health_check = True
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
            # Docker log driver with rotation so logs remain accessible
            # via docker logs without growing unboundedly.
            log_config={
                "type": "json-file",
                "config": {
                    "max-size": "20m",
                    "max-file": "3",
                },
            },
            # Bypass entrypoint.sh — the inline startup script below handles
            # UID/GID adaptation and drops to the agent user.
            entrypoint="",
            user="0:0",
            command=[
                "/bin/sh",
                "-c",
                "target_gid=" + str(os.getgid()) + "; "
                "target_uid=" + str(os.getuid()) + "; "
                'if [ "$(id -u agent 2>/dev/null)" != "$target_uid" ]'
                ' || [ "$(id -g agent 2>/dev/null)" != "$target_gid" ]; then '
                '  if getent group "$target_gid" >/dev/null 2>&1; then '
                '    agent_group=$(getent group "$target_gid" | cut -d: -f1); '
                "  else "
                '    groupmod -g "$target_gid" agent 2>/dev/null && agent_group=agent || agent_group=agent; '
                "  fi; "
                '  usermod -u "$target_uid" -g "$agent_group" agent 2>/dev/null; '
                "  chown -R agent:agent /app /home/agent 2>/dev/null || true; "
                "fi; "
                'chown -R agent:agent "$LOG_DIR" 2>/dev/null || true; '
                "exec runuser -u agent -- /app/.venv/bin/python -m uvicorn "
                "agent_server:app --host 0.0.0.0 --port " + str(CONTAINER_PORT),
            ],
        )
        needs_health_check = True

    # Get the dynamically assigned host port — wait up to 10s for the
    # container to actually start (it may need a moment for UID/GID init).
    deadline = time.time() + 10
    last_status = container.status
    while time.time() < deadline:
        container.reload()
        last_status = container.status
        port = container.attrs["NetworkSettings"]["Ports"].get(f"{CONTAINER_PORT}/tcp")
        if port and port[0] and port[0].get("HostPort"):
            host_ip = "host.docker.internal" if _running_in_docker() else "127.0.0.1"
            url = f"http://{host_ip}:{port[0]['HostPort']}"
            break
        if container.status not in ("running", "restarting", "created"):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(
            f"Container {name}: no host port mapping for {CONTAINER_PORT}/tcp "
            f"after 10s (status={last_status}). "
            "Check docker logs for startup errors."
        )

    # Wait for the container's agent_server to be ready after start/create
    if needs_health_check:
        try:
            wait_for_container_ready(url)
        except TimeoutError:
            logger.error("Container %s did not become healthy in time — may have crashed", name)
            raise

    return url


def destroy_all_containers() -> int:
    """Destroy all user containers from a previous run.

    Called at startup to clean up orphaned containers left by a crash or
    ungraceful shutdown.  ``ensure_container`` recreates them on demand.
    """
    containers = _list_user_containers(get_client(), all=True)
    count = 0
    for c in containers:
        try:
            c.remove(force=True)
            count += 1
        except docker.errors.DockerException:
            logger.exception("Failed to destroy container %s", c.name)
    if count > 0:
        logger.info("Destroyed %d orphaned container(s) from previous run", count)
    return count


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
    """Stop containers idle beyond CONTAINER_IDLE_TTL.

    Enumerates containers from Docker so orphaned containers from a
    previous-crash recovery window are also discovered.  Uses
    ``_last_activity`` only for timestamps; a missing entry means the
    container was never touched by this process and is treated as idle.
    """
    now = time.time()
    stopped = 0
    containers = _list_user_containers(get_client(), status="running")
    known_ids: set[str] = set()
    for c in containers:
        name = c.name
        if not name.startswith(CONTAINER_NAME_PREFIX):
            continue
        uid = user_id_from_container_name(name)
        known_ids.add(uid)
        if now - _last_activity.get(uid, 0) < CONTAINER_IDLE_TTL:
            continue
        try:
            c.stop(timeout=30)
            logger.info("Stopped idle container for user %s (idle %.0fs)", uid, now - _last_activity.get(uid, 0))
            stopped += 1
        except docker.errors.NotFound:
            pass

    # Prune _last_activity entries for users with no running container
    for uid in list(_last_activity):
        if uid not in known_ids:
            del _last_activity[uid]

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
    containers = _list_user_containers(get_client())
    return [
        {"name": user_id_from_container_name(c.name), "status": c.status}
        for c in containers
    ]
