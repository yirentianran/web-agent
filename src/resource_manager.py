"""Resource management — CPU/memory/disk monitoring per container."""

from __future__ import annotations

import logging
import os
from pathlib import Path

try:
    import docker
except ImportError:
    docker = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Configurable thresholds
MAX_CPU_PERCENT = float(os.getenv("RESOURCE_MAX_CPU_PERCENT", "100"))
MAX_MEMORY_MB = float(os.getenv("RESOURCE_MAX_MEMORY_MB", "4096"))
MAX_DISK_MB = float(os.getenv("RESOURCE_MAX_DISK_MB", "1024"))

CONTAINER_MODE = os.getenv("CONTAINER_MODE", "false").lower() == "true"

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))


def get_container_stats(user_id: str) -> dict[str, str | float | None]:
    """Get CPU and memory stats for a user's container.

    When CONTAINER_MODE is False, returns a disabled status.
    """
    if not CONTAINER_MODE or docker is None:
        return {"status": "container_mode_disabled"}

    try:
        client = docker.from_env()
        container = client.containers.get(f"web-agent-{user_id}")
        stats = container.stats(stream=False)

        cpu_stats = stats.get("cpu_stats", {})
        precpu_stats = stats.get("precpu_stats", {})

        cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
        system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)

        cpu_percent = 0.0
        if system_delta > 0:
            online_cpus = cpu_stats.get("online_cpus", 1)
            cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0

        mem_stats = stats.get("memory_stats", {})
        memory_usage = mem_stats.get("usage", 0)
        memory_limit = mem_stats.get("limit", 0)
        memory_mb = memory_usage / (1024 * 1024) if memory_usage else 0
        memory_limit_mb = memory_limit / (1024 * 1024) if memory_limit else 0

        return {
            "status": "ok",
            "cpu_percent": round(cpu_percent, 2),
            "memory_mb": round(memory_mb, 2),
            "memory_limit_mb": round(memory_limit_mb, 2),
        }
    except Exception as e:
        logger.warning("Failed to get container stats for %s: %s", user_id, e)
        return {"status": "error", "detail": str(e)}


def get_disk_usage(user_id: str) -> dict[str, str | float]:
    """Get disk usage for a user's data directory."""
    user_dir = DATA_ROOT / "users" / user_id
    if not user_dir.exists():
        return {"status": "not_found", "disk_mb": 0.0}

    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(user_dir):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
    except OSError as e:
        logger.warning("Error scanning disk for %s: %s", user_id, e)
        return {"status": "error", "detail": str(e)}

    return {
        "status": "ok",
        "disk_mb": round(total / (1024 * 1024), 2),
    }


def check_quota(user_id: str) -> dict[str, bool | dict]:
    """Check if a user's container is within resource quotas.

    Returns {cpu_ok, memory_ok, disk_ok, details}.
    """
    stats = get_container_stats(user_id)
    disk = get_disk_usage(user_id)

    cpu_ok = True
    memory_ok = True
    disk_ok = True

    if stats.get("status") == "ok":
        cpu_ok = (stats.get("cpu_percent", 0) or 0) <= MAX_CPU_PERCENT
        memory_ok = (stats.get("memory_mb", 0) or 0) <= MAX_MEMORY_MB

    if disk.get("status") == "ok":
        disk_ok = (disk.get("disk_mb", 0) or 0) <= MAX_DISK_MB

    return {
        "cpu_ok": cpu_ok,
        "memory_ok": memory_ok,
        "disk_ok": disk_ok,
        "details": {
            "cpu_percent": stats.get("cpu_percent"),
            "memory_mb": stats.get("memory_mb"),
            "disk_mb": disk.get("disk_mb"),
            "limits": {
                "max_cpu_percent": MAX_CPU_PERCENT,
                "max_memory_mb": MAX_MEMORY_MB,
                "max_disk_mb": MAX_DISK_MB,
            },
        },
    }


def get_all_resources() -> dict[str, dict]:
    """Return resource stats for all active containers."""
    if not CONTAINER_MODE or docker is None:
        return {"status": "container_mode_disabled"}

    try:
        client = docker.from_env()
        containers = [c for c in client.containers.list() if c.name.startswith("web-agent-")]

        result: dict[str, dict] = {}
        for container in containers:
            user_id = container.name.replace("web-agent-", "")
            result[user_id] = {
                "container": get_container_stats(user_id),
                "disk": get_disk_usage(user_id),
                "quota": check_quota(user_id),
            }
        return result
    except Exception as e:
        logger.error("Failed to list container resources: %s", e)
        return {"status": "error", "detail": str(e)}
