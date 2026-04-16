"""Sandbox Adapter — abstract interface for code execution isolation.

Provides a protocol-based approach for running agent code in isolated environments.
Docker implementation included; stub for Daytona/Blaxel adapters.

Usage:
    from src.sandbox import get_sandbox, DockerSandboxAdapter

    sandbox = get_sandbox()
    result = await sandbox.execute(
        user_id="alice",
        code="print('hello')",
        timeout_seconds=30,
        memory_limit_mb=512,
    )
    print(result.output, result.exit_code, result.duration_ms)
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExecutionResult:
    """Result of a sandbox execution."""

    output: str
    exit_code: int
    duration_ms: float
    memory_used_mb: float = 0.0
    error: str | None = None


class SandboxAdapter(ABC):
    """Abstract interface for sandbox execution."""

    @abstractmethod
    async def execute(
        self,
        user_id: str,
        code: str,
        timeout_seconds: int = 60,
        memory_limit_mb: int = 1024,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute code in an isolated environment."""
        ...

    @abstractmethod
    async def create_environment(self, user_id: str) -> dict[str, Any]:
        """Create an isolated execution environment."""
        ...

    @abstractmethod
    async def destroy_environment(self, user_id: str) -> None:
        """Destroy an isolated execution environment."""
        ...

    @abstractmethod
    async def get_stats(self, user_id: str) -> dict[str, Any]:
        """Get resource stats for the sandbox."""
        ...


class DockerSandboxAdapter(SandboxAdapter):
    """Docker-based sandbox implementation.

    Uses docker-py to create per-user isolated containers with
    configurable resource limits (CPU, memory, disk, network).
    """

    def __init__(self) -> None:
        import docker

        self._client = docker.from_env()
        self._image = os.getenv("SANDBOX_IMAGE", "python:3.12-slim")
        self._network_mode = os.getenv("SANDBOX_NETWORK", "none")  # isolated by default
        self._cpu_quota = int(os.getenv("SANDBOX_CPU_QUOTA", "50000"))  # 50% of one core
        self._cpu_period = int(os.getenv("SANDBOX_CPU_PERIOD", "100000"))
        self._mem_limit = os.getenv("SANDBOX_MEM_LIMIT", "1g")

    def _container_name(self, user_id: str) -> str:
        return f"web-agent-sandbox-{user_id}"

    async def execute(
        self,
        user_id: str,
        code: str,
        timeout_seconds: int = 60,
        memory_limit_mb: int = 1024,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute Python code in an isolated Docker container."""
        container_name = self._container_name(user_id)
        start = time.time()

        try:
            container = self._client.containers.run(
                self._image,
                command=["python", "-c", code],
                name=container_name,
                detach=True,
                mem_limit=f"{memory_limit_mb}m",
                cpu_quota=self._cpu_quota,
                cpu_period=self._cpu_period,
                network_mode=self._network_mode,
                environment=env or {},
                remove=False,
            )
            result = container.wait(timeout=timeout_seconds)
            logs = container.logs().decode("utf-8", errors="replace")
            exit_code = result.get("StatusCode", 1)
            container.remove()
        except Exception as e:
            return ExecutionResult(
                output="",
                exit_code=1,
                duration_ms=(time.time() - start) * 1000,
                error=str(e),
            )

        return ExecutionResult(
            output=logs,
            exit_code=exit_code,
            duration_ms=(time.time() - start) * 1000,
        )

    async def create_environment(self, user_id: str) -> dict[str, Any]:
        """Ensure a sandbox container exists for the user."""
        container_name = self._container_name(user_id)
        try:
            container = self._client.containers.get(container_name)
            return {"status": container.status, "name": container_name}
        except Exception:
            container = self._client.containers.run(
                self._image,
                command=["tail", "-f", "/dev/null"],
                name=container_name,
                detach=True,
                mem_limit=self._mem_limit,
                cpu_quota=self._cpu_quota,
                cpu_period=self._cpu_period,
                network_mode=self._network_mode,
            )
            return {"status": "created", "name": container_name}

    async def destroy_environment(self, user_id: str) -> None:
        """Remove the sandbox container."""
        container_name = self._container_name(user_id)
        try:
            container = self._client.containers.get(container_name)
            container.remove(force=True)
        except Exception:
            pass

    async def get_stats(self, user_id: str) -> dict[str, Any]:
        """Get resource stats for the user's sandbox."""
        container_name = self._container_name(user_id)
        try:
            container = self._client.containers.get(container_name)
            stats = container.stats(stream=False)
            return {
                "name": container_name,
                "status": container.status,
                "cpu_usage": stats.get("cpu_stats", {}).get("cpu_usage", {}),
                "memory_usage": stats.get("memory_stats", {}),
            }
        except Exception as e:
            return {"name": container_name, "status": "not_found", "error": str(e)}


class StubSandboxAdapter(SandboxAdapter):
    """No-op implementation for development/testing."""

    async def execute(
        self,
        user_id: str,
        code: str,
        timeout_seconds: int = 60,
        memory_limit_mb: int = 1024,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            output="Sandbox stub: execution skipped",
            exit_code=0,
            duration_ms=0.0,
        )

    async def create_environment(self, user_id: str) -> dict[str, Any]:
        return {"status": "stub", "name": f"stub-{user_id}"}

    async def destroy_environment(self, user_id: str) -> None:
        pass

    async def get_stats(self, user_id: str) -> dict[str, Any]:
        return {"status": "stub", "name": f"stub-{user_id}"}


def get_sandbox() -> SandboxAdapter:
    """Return the configured sandbox adapter.

    Uses Docker if docker-py is installed and SANDBOX_MODE=docker.
    Falls back to stub for development.
    """
    if os.getenv("SANDBOX_MODE") == "docker":
        try:
            return DockerSandboxAdapter()
        except Exception:
            return StubSandboxAdapter()
    return StubSandboxAdapter()
