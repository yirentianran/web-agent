"""Sub-agent orchestration — parent-child task tracking with status machine.

Integrates with the SDK's TaskCreate / TaskUpdate / TaskList tools.
Tracks task lifecycle: pending → in_progress → completed | failed | cancelled.

Usage:
    from src.sub_agent import SubAgentManager

    mgr = SubAgentManager(user_id="alice")
    task_id = mgr.create_task(subject="Analyze data", active_form="Analyzing data")
    mgr.update_task(task_id, status="in_progress", activeForm="Analyzing data")
    mgr.update_task(task_id, status="completed")
    tasks = mgr.list_tasks()
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))


class SubAgentManager:
    """Per-user sub-agent task management."""

    def __init__(self, user_id: str, data_root: Path = DATA_ROOT) -> None:
        self.user_id = user_id
        self._tasks_dir = data_root / "users" / user_id / "tasks"
        self._tasks_dir.mkdir(parents=True, exist_ok=True)

    def create_task(
        self,
        subject: str,
        description: str = "",
        active_form: str = "",
        blocked_by: list[str] | None = None,
        parent_task_id: str | None = None,
    ) -> str:
        """Create a new sub-agent task. Returns task_id."""
        task_id = str(uuid.uuid4().hex[:12])
        task = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "activeForm": active_form or subject,
            "blocked_by": blocked_by or [],
            "parent_task_id": parent_task_id,
            "created_at": time.time(),
            "updated_at": time.time(),
            "completed_at": None,
        }
        self._save_task(task_id, task)
        return task_id

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        subject: str | None = None,
        active_form: str | None = None,
        description: str | None = None,
        blocked_by: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Update a task's fields. Returns the updated task or None if not found."""
        task = self._load_task(task_id)
        if task is None:
            return None

        if status is not None:
            task["status"] = status
        if subject is not None:
            task["subject"] = subject
        if active_form is not None:
            task["activeForm"] = active_form
        if description is not None:
            task["description"] = description
        if blocked_by is not None:
            task["blocked_by"] = blocked_by

        task["updated_at"] = time.time()
        if status in ("completed", "failed", "cancelled"):
            task["completed_at"] = time.time()

        if status == "deleted":
            self._delete_task(task_id)
            return None

        self._save_task(task_id, task)
        return task

    def list_tasks(self, status: str | None = None) -> list[dict[str, Any]]:
        """List all tasks, optionally filtered by status."""
        tasks: list[dict[str, Any]] = []
        for task_file in sorted(self._tasks_dir.glob("*.json")):
            try:
                task = json.loads(task_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if status and task.get("status") != status:
                continue
            tasks.append(task)
        return tasks

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a single task by ID, or None."""
        return self._load_task(task_id)

    def delete_task(self, task_id: str) -> bool:
        """Delete a task. Returns True if deleted, False if not found."""
        task = self._load_task(task_id)
        if task is None:
            return False
        self._delete_task(task_id)
        return True

    def get_children(self, task_id: str) -> list[dict[str, Any]]:
        """Get all tasks that are blocked by (depend on) the given task."""
        children: list[dict[str, Any]] = []
        for task in self.list_tasks():
            if task_id in task.get("blocked_by", []):
                children.append(task)
        return children

    def get_parent(self, task_id: str) -> dict[str, Any] | None:
        """Get the parent task of a given task."""
        task = self._load_task(task_id)
        if task is None or not task.get("parent_task_id"):
            return None
        return self._load_task(task["parent_task_id"])

    def _task_file(self, task_id: str) -> Path:
        return self._tasks_dir / f"{task_id}.json"

    def _load_task(self, task_id: str) -> dict[str, Any] | None:
        path = self._task_file(task_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _save_task(self, task_id: str, task: dict[str, Any]) -> None:
        path = self._task_file(task_id)
        path.write_text(json.dumps(task, ensure_ascii=False, indent=2))

    def _delete_task(self, task_id: str) -> None:
        path = self._task_file(task_id)
        if path.exists():
            path.unlink()
