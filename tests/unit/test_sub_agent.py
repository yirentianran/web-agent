"""Tests for SubAgentManager file-based fallback mode."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.sub_agent import SubAgentManager


@pytest.fixture()
def mgr(tmp_path: Path) -> SubAgentManager:
    return SubAgentManager(user_id="alice", data_root=tmp_path, db=None)


class TestSubAgentManager:
    @pytest.mark.asyncio
    async def test_create_task(self, mgr: SubAgentManager) -> None:
        task_id = await mgr.create_task(subject="Analyze data")
        task = await mgr.get_task(task_id)
        assert task is not None
        assert task["subject"] == "Analyze data"
        assert task["status"] == "pending"

    @pytest.mark.asyncio
    async def test_update_task_status(self, mgr: SubAgentManager) -> None:
        task_id = await mgr.create_task(subject="Test task", active_form="Testing")
        updated = await mgr.update_task(task_id, status="in_progress", active_form="Running tests")
        assert updated is not None
        assert updated["status"] == "in_progress"
        assert updated["activeForm"] == "Running tests"

    @pytest.mark.asyncio
    async def test_update_nonexistent_task(self, mgr: SubAgentManager) -> None:
        assert await mgr.update_task("nonexistent", status="completed") is None

    @pytest.mark.asyncio
    async def test_delete_task(self, mgr: SubAgentManager) -> None:
        task_id = await mgr.create_task(subject="Temp task")
        assert await mgr.delete_task(task_id) is True
        assert await mgr.get_task(task_id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_task(self, mgr: SubAgentManager) -> None:
        assert await mgr.delete_task("nonexistent") is False

    @pytest.mark.asyncio
    async def test_list_tasks(self, mgr: SubAgentManager) -> None:
        await mgr.create_task(subject="Task 1")
        await mgr.create_task(subject="Task 2")
        await mgr.create_task(subject="Task 3")
        tasks = await mgr.list_tasks()
        assert len(tasks) == 3

    @pytest.mark.asyncio
    async def test_list_tasks_filtered_by_status(self, mgr: SubAgentManager) -> None:
        t1 = await mgr.create_task(subject="Done task")
        await mgr.update_task(t1, status="completed")
        await mgr.create_task(subject="Pending task")
        completed = await mgr.list_tasks(status="completed")
        assert len(completed) == 1
        assert completed[0]["subject"] == "Done task"

    @pytest.mark.asyncio
    async def test_parent_child_relationship(self, mgr: SubAgentManager) -> None:
        parent_id = await mgr.create_task(subject="Parent")
        child_id = await mgr.create_task(subject="Child", parent_task_id=parent_id)

        parent = await mgr.get_parent(child_id)
        assert parent is not None
        assert parent["id"] == parent_id

        # Use get_parent for parent-child relationships
        assert (await mgr.get_parent(child_id))["id"] == parent_id

    @pytest.mark.asyncio
    async def test_blocked_by_dependency(self, mgr: SubAgentManager) -> None:
        t1 = await mgr.create_task(subject="Task 1")
        await mgr.create_task(subject="Task 2", blocked_by=[t1])

        children = await mgr.get_children(t1)
        assert len(children) == 1
        assert children[0]["subject"] == "Task 2"

    @pytest.mark.asyncio
    async def test_completed_at_set_on_terminal_status(self, mgr: SubAgentManager) -> None:
        task_id = await mgr.create_task(subject="Task")
        await mgr.update_task(task_id, status="completed")
        task = await mgr.get_task(task_id)
        assert task is not None
        assert task["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_deleted_task_removed_from_disk(self, mgr: SubAgentManager) -> None:
        task_id = await mgr.create_task(subject="Delete me")
        await mgr.update_task(task_id, status="deleted")
        assert not mgr._task_file(task_id).exists()

    @pytest.mark.asyncio
    async def test_task_lifecycle_full(self, mgr: SubAgentManager) -> None:
        tid = await mgr.create_task(
            subject="Full lifecycle",
            description="Test all states",
            active_form="Starting",
        )
        task = await mgr.get_task(tid)
        assert task["status"] == "pending"

        await mgr.update_task(tid, status="in_progress", active_form="Working on it")
        updated = await mgr.get_task(tid)
        assert updated is not None
        assert updated["status"] == "in_progress"

        await mgr.update_task(tid, status="completed")
        final = await mgr.get_task(tid)
        assert final is not None
        assert final["status"] == "completed"

        tasks = await mgr.list_tasks(status="completed")
        assert len(tasks) == 1
