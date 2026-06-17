"""Tests for CLI subprocess reuse in _CliRunner.

Covers _CliRunner without prompt, enqueue_prompt, shutdown, is_alive,
run loop with multiple prompts, cancel handling, and agent_ws runner caching.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_server import _CliRunner


# Dummy ContainerPaths for tests
class _FakePaths:
    workspace = "/tmp/ws"
    home_dir = "/tmp/home"


FAKE_PATHS = _FakePaths()


def _make_runner(session_id: str = "s1") -> _CliRunner:
    return _CliRunner(
        cmd=["claude", "--print"],
        env={"HOME": "/tmp"},
        cwd="/tmp/ws",
        session_id=session_id,
        container_paths=FAKE_PATHS,
    )


# ── Tests ──────────────────────────────────────────────────────────────────


class TestCliRunnerInit:
    """_CliRunner.__init__ should not require prompt."""

    def test_init_without_prompt(self):
        runner = _make_runner()
        assert runner._current_prompt is None
        assert not runner._shutting_down
        assert not runner._prompt_ready.is_set()

    def test_init_fields(self):
        runner = _make_runner()
        assert runner._cmd == ["claude", "--print"]
        assert runner._session_id == "s1"
        assert not runner._cancel_event.is_set()


class TestEnqueuePrompt:
    """enqueue_prompt() should set the prompt and signal _prompt_ready."""

    def test_enqueue_sets_prompt(self):
        runner = _make_runner()
        runner._cancel_event.set()  # dirty state from previous run
        runner._assistant_sent = True

        runner.enqueue_prompt("do something")

        assert runner._current_prompt == "do something"
        assert not runner._cancel_event.is_set()
        assert not runner._assistant_sent
        assert runner._prompt_ready.is_set()

    def test_enqueue_wakes_wait_for_prompt(self):
        runner = _make_runner()

        woken = []

        async def _waiter():
            await runner._wait_for_prompt()
            woken.append(True)

        # Start waiter in a thread
        async def _main():
            task = asyncio.create_task(_waiter())
            await asyncio.sleep(0.05)
            assert not woken  # still waiting
            runner.enqueue_prompt("hi")
            await asyncio.sleep(0.05)
            assert woken  # woken up
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        import contextlib
        asyncio.get_event_loop().run_until_complete(_main())


class TestShutdown:
    """shutdown() should set flags and unblock _wait_for_prompt."""

    def test_shutdown_sets_flags(self):
        runner = _make_runner()
        runner.shutdown()
        assert runner._shutting_down
        assert runner._cancel_event.is_set()
        assert runner._prompt_ready.is_set()

    def test_shutdown_unblocks_wait_for_prompt(self):
        runner = _make_runner()
        woken = []

        async def _waiter():
            await runner._wait_for_prompt()
            woken.append(runner._shutting_down)

        async def _main():
            task = asyncio.create_task(_waiter())
            await asyncio.sleep(0.05)
            assert not woken
            runner.shutdown()
            await asyncio.sleep(0.05)
            assert woken == [True]
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        import contextlib
        asyncio.get_event_loop().run_until_complete(_main())


class TestIsAlive:
    """is_alive() should reflect thread state."""

    def test_not_alive_before_start(self):
        runner = _make_runner()
        assert not runner.is_alive()

    def test_alive_after_start(self):
        # We can't easily start the real thread without a mock subprocess,
        # but we can mock the thread.
        runner = _make_runner()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        runner._thread = mock_thread
        assert runner.is_alive()

    def test_not_alive_when_thread_is_none(self):
        runner = _make_runner()
        runner._thread = None
        assert not runner.is_alive()


class TestRunCliInitFlow:
    """_run_cli sends init request and user messages to the subprocess."""

    def _make_proc(self, init_resp: str, runner: _CliRunner):
        """Build a mock subprocess that responds to init then blocks on stdout.

        The readline mock checks runner._cancel_event so that cancel() / shutdown()
        causes an EOF return, unblocking the read loop.
        """
        fake_proc = AsyncMock()
        fake_proc.pid = 99999
        fake_proc.stdin = AsyncMock()
        fake_proc.stdin.write = MagicMock()
        fake_proc.stdin.drain = AsyncMock()
        fake_proc.stdin.close = MagicMock()
        fake_proc.send_signal = MagicMock()
        fake_proc.kill = MagicMock()
        fake_proc.returncode = 0

        async def _wait():
            return None

        fake_proc.wait = _wait

        stdout_lines: list[bytes] = [init_resp.encode()]

        async def _readline():
            if stdout_lines:
                return stdout_lines.pop(0)
            # Block until cancelled, then return EOF
            while not runner._cancel_event.is_set():
                await asyncio.sleep(0.05)
            return b""

        fake_proc.stdout = AsyncMock()
        fake_proc.stdout.readline = _readline
        fake_proc.stdout._limit = 1024 * 1024

        async def _stderr_readline():
            while not runner._cancel_event.is_set():
                await asyncio.sleep(0.1)
            return b""

        fake_proc.stderr = AsyncMock()
        fake_proc.stderr.readline = _stderr_readline
        fake_proc.stderr._limit = 1024 * 1024

        return fake_proc

    @pytest.mark.asyncio
    async def test_sends_initialize_on_start(self):
        """_run_cli should send a control_request(initialize) to stdin."""
        runner = _make_runner()
        runner.enqueue_prompt("test")

        init_resp = json.dumps({
            "type": "control_response",
            "request_id": "init_test",
            "response": {"subtype": "success"},
        }) + "\n"

        fake_proc = self._make_proc(init_resp, runner)

        with patch("agent_server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_spawn:
            mock_spawn.return_value = fake_proc

            task = asyncio.ensure_future(runner._run_cli())
            await asyncio.sleep(0.3)

            # Check that init was sent
            all_writes = b"".join(
                c[0][0] for c in fake_proc.stdin.write.call_args_list if c[0]
            )
            assert b"initialize" in all_writes, "Should have sent initialize request"
            assert b"user" in all_writes and b"test" in all_writes, (
                "Should have sent user message after init"
            )

            # Clean shutdown: shutdown() sets _cancel_event, unblocking readline
            runner.shutdown()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
                await asyncio.wait_for(task, timeout=3)

    @pytest.mark.asyncio
    async def test_cancel_returns_from_run_cli(self):
        """When cancelled and stdout closes, _run_cli should exit cleanly."""
        runner = _make_runner()
        runner.enqueue_prompt("task")

        import contextlib

        init_resp = json.dumps({
            "type": "control_response",
            "request_id": "init_test",
            "response": {"subtype": "success"},
        }) + "\n"

        fake_proc = self._make_proc(init_resp, runner)

        with patch("agent_server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_spawn:
            mock_spawn.return_value = fake_proc

            task = asyncio.ensure_future(runner._run_cli())
            await asyncio.sleep(0.3)

            # Cancel and then shutdown (shutdown sets _shutting_down, unblocks wait_for_prompt)
            runner.cancel()
            runner.shutdown()

            # Task should complete quickly after shutdown
            await asyncio.wait_for(task, timeout=3)

            # After task completes, process signals should have been called
            fake_proc.send_signal.assert_called()


class TestAgentWsRunnerReuse:
    """Integration-like tests for the agent_ws runner caching pattern."""

    def test_runner_recreated_when_none(self):
        """If runner is None, a new runner should be created."""
        runner = None

        # Simulate what agent_ws does
        if runner is None or not runner.is_alive():
            runner = _make_runner()

        assert runner is not None
        assert isinstance(runner, _CliRunner)

    def test_runner_reused_when_alive(self):
        """If runner is alive, it should be reused."""
        runner = _make_runner()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        runner._thread = mock_thread

        runner_id_before = id(runner)
        reused = False

        if runner is None or not runner.is_alive():
            runner = _make_runner()
        else:
            reused = True

        assert reused
        assert id(runner) == runner_id_before

    def test_runner_recreated_when_dead(self):
        """If runner thread died, a new runner should be created."""
        runner = _make_runner()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        runner._thread = mock_thread

        was_recreated = False

        if runner is None or not runner.is_alive():
            runner = _make_runner()
            was_recreated = True

        assert was_recreated
