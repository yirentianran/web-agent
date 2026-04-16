"""Tests for sandbox adapter protocol and stub implementation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.sandbox import ExecutionResult, StubSandboxAdapter


class TestStubSandboxAdapter:
    @pytest.fixture()
    def sandbox(self) -> StubSandboxAdapter:
        return StubSandboxAdapter()

    def test_execute_returns_stub_result(self, sandbox: StubSandboxAdapter) -> None:
        async def run() -> None:
            result = await sandbox.execute(user_id="alice", code="print('hi')")
            assert isinstance(result, ExecutionResult)
            assert result.exit_code == 0
            assert "stub" in result.output.lower()

        asyncio.run(run())

    def test_create_environment_returns_stub(self, sandbox: StubSandboxAdapter) -> None:
        async def run() -> None:
            env = await sandbox.create_environment(user_id="bob")
            assert env["status"] == "stub"
            assert "bob" in env["name"]

        asyncio.run(run())

    def test_destroy_environment_noop(self, sandbox: StubSandboxAdapter) -> None:
        async def run() -> None:
            await sandbox.destroy_environment(user_id="charlie")

        asyncio.run(run())

    def test_get_stats_returns_stub(self, sandbox: StubSandboxAdapter) -> None:
        async def run() -> None:
            stats = await sandbox.get_stats(user_id="alice")
            assert stats["status"] == "stub"

        asyncio.run(run())


class TestExecutionResult:
    def test_frozen_dataclass(self) -> None:
        result = ExecutionResult(output="ok", exit_code=0, duration_ms=100.0)
        with pytest.raises(Exception):  # frozen, cannot modify
            result.output = "modified"  # type: ignore[misc]

    def test_with_error(self) -> None:
        result = ExecutionResult(output="", exit_code=1, duration_ms=50.0, error="command failed")
        assert result.error == "command failed"
        assert result.exit_code == 1


class TestGetSandbox:
    def test_default_returns_stub(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        from src.sandbox import get_sandbox
        sandbox = get_sandbox()
        assert isinstance(sandbox, StubSandboxAdapter)

    def test_docker_mode_falls_back_to_stub_when_no_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.sandbox
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        sandbox = src.sandbox.get_sandbox()
        # Without docker-py or docker daemon, should fall back to stub
        assert isinstance(sandbox, StubSandboxAdapter)
