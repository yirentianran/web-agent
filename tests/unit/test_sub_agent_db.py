"""Tests for SubAgentManager with SQLite backend."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Mock claude_agent_sdk
_mock_sdk = __import__("unittest.mock").mock.MagicMock()
_mock_sdk.ClaudeSDKClient = __import__("unittest.mock").mock.MagicMock()
_mock_sdk.types = __import__("unittest.mock").mock.MagicMock()
sys.modules.setdefault("claude_agent_sdk", _mock_sdk)
sys.modules.setdefault("claude_agent_sdk.types", _mock_sdk.types)

from src.database import Database
from src.sub_agent import SubAgentManager


@pytest.fixture()
async def db(tmp_path: Path) -> Database:
    database = Database(db_path=tmp_path / "test.db")
    await database.init()
    yield database
    await database.close()


@pytest.fixture()
async def mgr(db: Database) -> SubAgentManager:
    return SubAgentManager(user_id="alice", db=db)


# ── Create ───────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSubAgentCreate:
    async def test_create_task(self, mgr: SubAgentManager) -> None:
        task_id = await mgr.create_task(subject="Analyze data")
        task = await mgr.get_task(task_id)
        assert task is not None
        assert task["subject"] == "Analyze data"
        assert task["status"] == "pending"

    async def test_create_task_with_all_fields(self, mgr: SubAgentManager) -> None:
        task_id = await mgr.create_task(
            subject="Full task",
            description="A detailed task",
            active_form="Doing full task",
            blocked_by=["dep-1", "dep-2"],
            parent_task_id="parent-1",
        )
        task = await mgr.get_task(task_id)
        assert task is not None
        assert task["description"] == "A detailed task"
        assert task["activeForm"] == "Doing full task"
        assert task["blocked_by"] == ["dep-1", "dep-2"]
        assert task["parent_task_id"] == "parent-1"


# ── Update ───────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSubAgentUpdate:
    async def test_update_status(self, mgr: SubAgentManager) -> None:
        task_id = await mgr.create_task(subject="Test", active_form="Testing")
        updated = await mgr.update_task(
            task_id, status="in_progress", active_form="Running"
        )
        assert updated is not None
        assert updated["status"] == "in_progress"
        assert updated["activeForm"] == "Running"

    async def test_update_nonexistent(self, mgr: SubAgentManager) -> None:
        result = await mgr.update_task("nonexistent", status="completed")
        assert result is None

    async def test_completed_at_set(self, mgr: SubAgentManager) -> None:
        task_id = await mgr.create_task(subject="Task")
        await mgr.update_task(task_id, status="completed")
        task = await mgr.get_task(task_id)
        assert task is not None
        assert task["completed_at"] is not None

    async def test_deleted_task_removed(self, mgr: SubAgentManager) -> None:
        task_id = await mgr.create_task(subject="Delete me")
        result = await mgr.update_task(task_id, status="deleted")
        assert result is None
        assert await mgr.get_task(task_id) is None


# ── List ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSubAgentList:
    async def test_list_empty(self, mgr: SubAgentManager) -> None:
        assert await mgr.list_tasks() == []

    async def test_list_all(self, mgr: SubAgentManager) -> None:
        await mgr.create_task(subject="T1")
        await mgr.create_task(subject="T2")
        await mgr.create_task(subject="T3")
        tasks = await mgr.list_tasks()
        assert len(tasks) == 3

    async def test_list_filtered(self, mgr: SubAgentManager) -> None:
        t1 = await mgr.create_task(subject="Done")
        await mgr.update_task(t1, status="completed")
        await mgr.create_task(subject="Pending")
        completed = await mgr.list_tasks(status="completed")
        assert len(completed) == 1
        assert completed[0]["subject"] == "Done"


# ── Delete ───────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSubAgentDelete:
    async def test_delete_existing(self, mgr: SubAgentManager) -> None:
        task_id = await mgr.create_task(subject="Delete")
        assert await mgr.delete_task(task_id) is True
        assert await mgr.get_task(task_id) is None

    async def test_delete_nonexistent(self, mgr: SubAgentManager) -> None:
        assert await mgr.delete_task("ghost") is False


# ── Dependencies ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSubAgentDependencies:
    async def test_parent_child(self, mgr: SubAgentManager) -> None:
        parent_id = await mgr.create_task(subject="Parent")
        child_id = await mgr.create_task(subject="Child", parent_task_id=parent_id)
        parent = await mgr.get_parent(child_id)
        assert parent is not None
        assert parent["id"] == parent_id

    async def test_blocked_by(self, mgr: SubAgentManager) -> None:
        t1 = await mgr.create_task(subject="T1")
        await mgr.create_task(subject="T2", blocked_by=[t1])
        children = await mgr.get_children(t1)
        assert len(children) == 1
        assert children[0]["subject"] == "T2"


# ── File fallback (backward compat) ──────────────────────────────


@pytest.mark.asyncio
class TestSubAgentFileFallback:
    """When db=None, SubAgentManager falls back to file storage."""

    async def test_file_mode_create_and_get(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="bob", data_root=tmp_path, db=None)
        task_id = await mgr.create_task(subject="File task")
        task = await mgr.get_task(task_id)
        assert task is not None
        assert task["subject"] == "File task"

    async def test_file_mode_list(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="bob", data_root=tmp_path, db=None)
        await mgr.create_task(subject="F1")
        await mgr.create_task(subject="F2")
        tasks = await mgr.list_tasks()
        assert len(tasks) == 2
