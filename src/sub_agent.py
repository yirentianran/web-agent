"""Sub-agent task management with SQLite backend and file fallback.

Integrates with the SDK's TaskCreate / TaskUpdate / TaskList tools.
Tracks task lifecycle: pending → in_progress → completed | failed | cancelled.

Usage (DB mode):
    from src.sub_agent import SubAgentManager
    mgr = SubAgentManager(user_id="alice", db=database)
    task_id = await mgr.create_task(subject="Analyze data")

Usage (file fallback mode):
    mgr = SubAgentManager(user_id="alice", data_root=tmp_path, db=None)
    task_id = await mgr.create_task(subject="Analyze data")
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.database import Database

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))


class SubAgentManager:
    """Per-user sub-agent task management with SQLite backend."""

    def __init__(
        self,
        user_id: str,
        db: "Database | None" = None,
        data_root: Path = DATA_ROOT,
    ) -> None:
        self.user_id = user_id
        self.db = db
        self._tasks_dir = data_root / "users" / user_id / "tasks"
        if db is None:
            self._tasks_dir.mkdir(parents=True, exist_ok=True)

    async def create_task(
        self,
        subject: str,
        description: str = "",
        active_form: str = "",
        blocked_by: list[str] | None = None,
        parent_task_id: str | None = None,
    ) -> str:
        """Create a new sub-agent task. Returns task_id."""
        task_id = str(uuid.uuid4().hex[:12])
        now = time.time()
        task = {
            "id": task_id,
            "user_id": self.user_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "activeForm": active_form or subject,
            "blocked_by": blocked_by or [],
            "parent_task_id": parent_task_id,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        await self._save_task(task_id, task)
        return task_id

    async def update_task(
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
        task = await self._load_task(task_id)
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
            await self._delete_task(task_id)
            return None

        await self._save_task(task_id, task)
        return task

    async def list_tasks(self, status: str | None = None) -> list[dict[str, Any]]:
        """List all tasks, optionally filtered by status."""
        tasks = await self._load_all_tasks()
        if status:
            tasks = [t for t in tasks if t.get("status") == status]
        return tasks

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a single task by ID, or None."""
        return await self._load_task(task_id)

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task. Returns True if deleted, False if not found."""
        task = await self._load_task(task_id)
        if task is None:
            return False
        await self._delete_task(task_id)
        return True

    async def get_children(self, task_id: str) -> list[dict[str, Any]]:
        """Get all tasks that are blocked by (depend on) the given task."""
        children: list[dict[str, Any]] = []
        for task in await self._load_all_tasks():
            if task_id in task.get("blocked_by", []):
                children.append(task)
        return children

    async def get_parent(self, task_id: str) -> dict[str, Any] | None:
        """Get the parent task of a given task."""
        task = await self._load_task(task_id)
        if task is None or not task.get("parent_task_id"):
            return None
        return await self._load_task(task["parent_task_id"])

    # ── Backend-specific implementations ─────────────────────────

    async def _save_task(self, task_id: str, task: dict[str, Any]) -> None:
        """Save a task to DB or file."""
        if self.db is not None and self.db._pool is not None:
            await self._db_save(task)
        else:
            self._file_save(task)

    async def _load_task(self, task_id: str) -> dict[str, Any] | None:
        """Load a task from DB or file."""
        if self.db is not None and self.db._pool is not None:
            return await self._db_load(task_id)
        return self._file_load(task_id)

    async def _load_all_tasks(self) -> list[dict[str, Any]]:
        """Load all tasks from DB or file."""
        if self.db is not None and self.db._pool is not None:
            return await self._db_load_all()
        return self._file_load_all()

    async def _delete_task(self, task_id: str) -> None:
        """Delete a task from DB or file."""
        if self.db is not None and self.db._pool is not None:
            await self._db_delete(task_id)
        else:
            self._file_delete(task_id)

    # ── DB operations ────────────────────────────────────────────

    async def _db_save(self, task: dict[str, Any]) -> None:
        import aiosqlite

        try:
            async with self.db.connection() as conn:  # type: ignore[union-attr]
                await conn.execute(
                    """INSERT OR REPLACE INTO tasks
                       (id, user_id, subject, description, active_form, status,
                        blocked_by, parent_task_id, created_at, updated_at, completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        task["id"],
                        self.user_id,
                        task["subject"],
                        task.get("description", ""),
                        task.get("activeForm", ""),
                        task.get("status", "pending"),
                        json.dumps(task.get("blocked_by", [])),
                        task.get("parent_task_id"),
                        task.get("created_at", time.time()),
                        task.get("updated_at", time.time()),
                        task.get("completed_at"),
                    ),
                )
                await conn.commit()
        except aiosqlite.OperationalError:
            # DB write failed — fall back to file
            self._file_save(task)

    async def _db_load(self, task_id: str) -> dict[str, Any] | None:
        try:
            async with self.db.connection() as conn:  # type: ignore[union-attr]
                cursor = await conn.execute(
                    "SELECT * FROM tasks WHERE id = ? AND user_id = ?",
                    (task_id, self.user_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                return self._db_row_to_dict(row)
        except Exception:
            return self._file_load(task_id)

    async def _db_load_all(self) -> list[dict[str, Any]]:
        try:
            async with self.db.connection() as conn:  # type: ignore[union-attr]
                cursor = await conn.execute(
                    "SELECT * FROM tasks WHERE user_id = ? ORDER BY created_at DESC",
                    (self.user_id,),
                )
                rows = await cursor.fetchall()
                return [self._db_row_to_dict(r) for r in rows]
        except Exception:
            return self._file_load_all()

    async def _db_delete(self, task_id: str) -> None:
        try:
            async with self.db.connection() as conn:  # type: ignore[union-attr]
                await conn.execute(
                    "DELETE FROM tasks WHERE id = ? AND user_id = ?",
                    (task_id, self.user_id),
                )
                await conn.commit()
        except Exception:
            self._file_delete(task_id)

    def _db_row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert a database row to a task dict matching the file format."""
        data = dict(row) if not isinstance(row, dict) else row
        return {
            "id": data["id"],
            "subject": data["subject"],
            "description": data.get("description", ""),
            "status": data.get("status", "pending"),
            "activeForm": data.get("active_form", ""),
            "blocked_by": json.loads(data.get("blocked_by", "[]") or "[]"),
            "parent_task_id": data.get("parent_task_id"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "completed_at": data.get("completed_at"),
        }

    # ── File operations (fallback) ───────────────────────────────

    def _task_file(self, task_id: str) -> Path:
        return self._tasks_dir / f"{task_id}.json"

    def _file_save(self, task: dict[str, Any]) -> None:
        path = self._task_file(task["id"])
        path.write_text(json.dumps(task, ensure_ascii=False, indent=2))

    def _file_load(self, task_id: str) -> dict[str, Any] | None:
        path = self._task_file(task_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _file_load_all(self) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        for task_file in sorted(self._tasks_dir.glob("*.json")):
            try:
                task = json.loads(task_file.read_text())
                tasks.append(task)
            except (json.JSONDecodeError, OSError):
                continue
        return tasks

    def _file_delete(self, task_id: str) -> None:
        path = self._task_file(task_id)
        if path.exists():
            path.unlink()
