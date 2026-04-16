"""User platform memory — L1 cross-session context + L2 agent memory.

L1: `memory.json` per user with deep-merge updates (file fallback) or SQLite (primary).
L2: `memory/` directory with Markdown files auto-loaded into system prompt.

Usage:
    from src.memory import MemoryManager
    from src.database import Database

    db = Database(db_path=Path("data/web-agent.db"))
    await db.init()
    mgr = MemoryManager(user_id="alice", data_root=Path("data"), db=db)
    mgr.read()
    mgr.update({"preferences": {"theme": "dark"}})
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.database import Database

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge *patch* into *base*, returning a new dict.

    Lists are extended, dicts are recursively merged, scalars are overwritten.
    """
    result = dict(base)
    for key, value in patch.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        elif (
            key in result
            and isinstance(result[key], list)
            and isinstance(value, list)
        ):
            result[key] = result[key] + value
        else:
            result[key] = value
    return result


class MemoryManager:
    """Per-user memory management (L1 + L2)."""

    def __init__(
        self,
        user_id: str,
        data_root: Path = DATA_ROOT,
        db: "Database | None" = None,
    ) -> None:
        self.user_id = user_id
        self.user_dir = data_root / "users" / user_id
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.db: Database | None = db

    # ── L1 Platform Memory (memory.json) ──────────────────────────

    @property
    def _memory_file(self) -> Path:
        return self.user_dir / "memory.json"

    def read(self) -> dict[str, Any]:
        """Read the full memory, returning an empty structure if absent.

        Uses SQLite if db is attached, falls back to memory.json.
        """
        if self.db is not None and self.db._pool is not None:
            import sqlite3
            conn = sqlite3.connect(str(self.db.db_path))
            try:
                cursor = conn.execute(
                    "SELECT preferences, entity_memory, audit_context, file_memory FROM user_memory WHERE user_id = ?",
                    (self.user_id,),
                )
                row = cursor.fetchone()
                if row:
                    return {
                        "user_id": self.user_id,
                        "preferences": json.loads(row[0]),
                        "entity_memory": json.loads(row[1]),
                        "audit_context": json.loads(row[2]),
                        "file_memory": json.loads(row[3]),
                    }
            except (sqlite3.OperationalError, json.JSONDecodeError):
                pass
            finally:
                conn.close()
        # File fallback
        if not self._memory_file.exists():
            return {"user_id": self.user_id}
        try:
            data = json.loads(self._memory_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {"user_id": self.user_id}
        return data

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge a patch into memory and return the updated document.

        Uses SQLite if db is attached (with row-level transaction),
        falls back to memory.json file.
        """
        current = self.read()
        updated = _deep_merge(current, patch)
        updated["updated_at"] = time.time()

        if self.db is not None and self.db._pool is not None:
            import sqlite3
            conn = sqlite3.connect(str(self.db.db_path))
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO user_memory
                       (user_id, preferences, entity_memory, audit_context, file_memory, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        self.user_id,
                        json.dumps(updated.get("preferences", {})),
                        json.dumps(updated.get("entity_memory", {})),
                        json.dumps(updated.get("audit_context", {})),
                        json.dumps(updated.get("file_memory", [])),
                        updated["updated_at"],
                    ),
                )
                conn.commit()
            except sqlite3.OperationalError:
                # DB write failed — fall back to file
                self._write_file(updated)
            finally:
                conn.close()
        else:
            self._write_file(updated)

        return updated

    def _write_file(self, data: dict[str, Any]) -> None:
        """Write memory data to JSON file."""
        self._memory_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )

    def replace(self, data: dict[str, Any]) -> None:
        """Replace the entire memory content.

        Uses SQLite if db is attached, falls back to memory.json file.
        """
        data = dict(data)
        data["updated_at"] = time.time()

        if self.db is not None and self.db._pool is not None:
            import sqlite3
            conn = sqlite3.connect(str(self.db.db_path))
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO user_memory
                       (user_id, preferences, entity_memory, audit_context, file_memory, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        self.user_id,
                        json.dumps(data.get("preferences", {})),
                        json.dumps(data.get("entity_memory", {})),
                        json.dumps(data.get("audit_context", {})),
                        json.dumps(data.get("file_memory", [])),
                        data["updated_at"],
                    ),
                )
                conn.commit()
            except sqlite3.OperationalError:
                self._write_file(data)
            finally:
                conn.close()
        else:
            self._write_file(data)

    # ── L2 Agent Memory (Markdown files in memory/) ───────────────

    @property
    def _agent_memory_dir(self) -> Path:
        return self.user_dir / "memory"

    def write_agent_note(self, filename: str, content: str) -> None:
        """Write a Markdown note to the agent memory directory."""
        self._agent_memory_dir.mkdir(parents=True, exist_ok=True)
        (self._agent_memory_dir / filename).write_text(content)

    def read_agent_note(self, filename: str) -> str:
        """Read a Markdown note. Returns empty string if absent."""
        path = self._agent_memory_dir / filename
        if not path.exists():
            return ""
        return path.read_text()

    def list_agent_notes(self) -> list[dict[str, Any]]:
        """List all agent memory notes with metadata."""
        notes_dir = self._agent_memory_dir
        if not notes_dir.exists():
            return []
        result: list[dict[str, Any]] = []
        for note_file in sorted(notes_dir.glob("*.md")):
            stat = note_file.stat()
            result.append({
                "filename": note_file.name,
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
            })
        return result

    def delete_agent_note(self, filename: str) -> None:
        """Delete an agent memory note if it exists."""
        path = self._agent_memory_dir / filename
        if path.exists():
            path.unlink()

    def load_agent_memory_for_prompt(self) -> str:
        """Load all agent memory Markdown files into a system prompt section."""
        notes = self.list_agent_notes()
        if not notes:
            return ""
        parts = ["## Agent Memory\n"]
        for note in notes:
            content = self.read_agent_note(note["filename"])
            if content.strip():
                parts.append(f"### {note['filename']}\n\n{content}\n")
        return "\n".join(parts)
