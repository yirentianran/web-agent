"""Tests for sub-agent task management."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.sub_agent import SubAgentManager


class TestSubAgentManager:
    def test_create_task(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="alice", data_root=tmp_path)
        task_id = mgr.create_task(subject="Analyze data")
        task = mgr.get_task(task_id)
        assert task is not None
        assert task["subject"] == "Analyze data"
        assert task["status"] == "pending"

    def test_update_task_status(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="alice", data_root=tmp_path)
        task_id = mgr.create_task(subject="Test task", active_form="Testing")
        updated = mgr.update_task(task_id, status="in_progress", active_form="Running tests")
        assert updated is not None
        assert updated["status"] == "in_progress"
        assert updated["activeForm"] == "Running tests"

    def test_update_nonexistent_task(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="alice", data_root=tmp_path)
        assert mgr.update_task("nonexistent", status="completed") is None

    def test_delete_task(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="alice", data_root=tmp_path)
        task_id = mgr.create_task(subject="Temp task")
        assert mgr.delete_task(task_id) is True
        assert mgr.get_task(task_id) is None

    def test_delete_nonexistent_task(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="alice", data_root=tmp_path)
        assert mgr.delete_task("nonexistent") is False

    def test_list_tasks(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="alice", data_root=tmp_path)
        mgr.create_task(subject="Task 1")
        mgr.create_task(subject="Task 2")
        mgr.create_task(subject="Task 3")
        tasks = mgr.list_tasks()
        assert len(tasks) == 3

    def test_list_tasks_filtered_by_status(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="alice", data_root=tmp_path)
        t1 = mgr.create_task(subject="Done task")
        mgr.update_task(t1, status="completed")
        mgr.create_task(subject="Pending task")
        completed = mgr.list_tasks(status="completed")
        assert len(completed) == 1
        assert completed[0]["subject"] == "Done task"

    def test_parent_child_relationship(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="alice", data_root=tmp_path)
        parent_id = mgr.create_task(subject="Parent")
        child_id = mgr.create_task(subject="Child", parent_task_id=parent_id)

        parent = mgr.get_parent(child_id)
        assert parent is not None
        assert parent["id"] == parent_id

        children = mgr.get_children(parent_id)
        # get_children looks at blocked_by, not parent_task_id
        assert len(children) == 0
        # Use get_parent for parent-child relationships
        assert mgr.get_parent(child_id)["id"] == parent_id

    def test_blocked_by_dependency(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="alice", data_root=tmp_path)
        t1 = mgr.create_task(subject="Task 1")
        mgr.create_task(subject="Task 2", blocked_by=[t1])

        children = mgr.get_children(t1)
        assert len(children) == 1
        assert children[0]["subject"] == "Task 2"

    def test_completed_at_set_on_terminal_status(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="alice", data_root=tmp_path)
        task_id = mgr.create_task(subject="Task")
        mgr.update_task(task_id, status="completed")
        task = mgr.get_task(task_id)
        assert task is not None
        assert task["completed_at"] is not None

    def test_deleted_task_removed_from_disk(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="alice", data_root=tmp_path)
        task_id = mgr.create_task(subject="Delete me")
        mgr.update_task(task_id, status="deleted")
        assert not mgr._task_file(task_id).exists()

    def test_task_lifecycle_full(self, tmp_path: Path) -> None:
        mgr = SubAgentManager(user_id="alice", data_root=tmp_path)
        tid = mgr.create_task(
            subject="Full lifecycle",
            description="Test all states",
            active_form="Starting",
        )
        task = mgr.get_task(tid)
        assert task["status"] == "pending"

        mgr.update_task(tid, status="in_progress", active_form="Working on it")
        assert mgr.get_task(tid)["status"] == "in_progress"  # ty: ignore[non-iterable]

        mgr.update_task(tid, status="completed")
        assert mgr.get_task(tid)["status"] == "completed"

        tasks = mgr.list_tasks(status="completed")
        assert len(tasks) == 1
