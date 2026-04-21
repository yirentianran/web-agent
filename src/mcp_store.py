"""MCP server configuration store backed by SQLite.

Replaces the file-based mcp-registry.json with async database operations.

Usage:
    from src.database import Database
    from src.mcp_store import MCPServerStore

    db = Database(db_path=Path("data/web-agent.db"))
    await db.init()
    store = MCPServerStore(db=db)
    servers = await store.list_all()
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.database import Database


class MCPServerStore:
    """Async CRUD store for MCP server configurations."""

    def __init__(self, db: "Database") -> None:
        self.db = db

    async def list_all(self) -> list[dict[str, Any]]:
        """Return all MCP servers."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id, name, type, command, args, url, env, tools, "
                "description, enabled, access, created_at, updated_at "
                "FROM mcp_servers ORDER BY name"
            )
            rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        """Return a single server by name, or None."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id, name, type, command, args, url, env, tools, "
                "description, enabled, access, created_at, updated_at "
                "FROM mcp_servers WHERE name = ?",
                (name,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    async def create(self, server: dict[str, Any]) -> dict[str, Any]:
        """Insert a new MCP server. Raises ValueError if name exists."""
        existing = await self.get_by_name(server["name"])
        if existing is not None:
            raise ValueError(f"MCP server '{server['name']}' already exists")

        now = _now()
        async with self.db.connection() as conn:
            await conn.execute(
                """INSERT INTO mcp_servers
                   (name, type, command, args, url, env, tools,
                    description, enabled, access, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    server["name"],
                    server.get("type", "stdio"),
                    server.get("command"),
                    json.dumps(server.get("args", [])),
                    server.get("url"),
                    json.dumps(server.get("env", {})),
                    json.dumps(server.get("tools", [])),
                    server.get("description", ""),
                    1 if server.get("enabled", True) else 0,
                    server.get("access", "all"),
                    now,
                    now,
                ),
            )
            await conn.commit()

        return await self.get_by_name(server["name"])  # type: ignore[return-value]

    async def update(
        self, name: str, patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update fields of an existing server. Returns updated server or None."""
        existing = await self.get_by_name(name)
        if existing is None:
            return None

        new_name = patch.get("name", name)
        # If name changed and new name exists, remove old entry
        if new_name != name:
            old_entry = await self.get_by_name(new_name)
            if old_entry is not None:
                async with self.db.connection() as conn:
                    await conn.execute(
                        "DELETE FROM mcp_servers WHERE name = ?", (name,)
                    )
                    await conn.commit()

        # Merge patch
        updated_data = {**existing, **patch}
        now = _now()

        async with self.db.connection() as conn:
            await conn.execute(
                """UPDATE mcp_servers SET
                   name = ?, type = ?, command = ?, args = ?, url = ?,
                   env = ?, tools = ?, description = ?,
                   enabled = ?, access = ?, updated_at = ?
                   WHERE name = ?""",
                (
                    updated_data["name"],
                    updated_data.get("type", "stdio"),
                    updated_data.get("command"),
                    json.dumps(updated_data.get("args", [])),
                    updated_data.get("url"),
                    json.dumps(updated_data.get("env", {})),
                    json.dumps(updated_data.get("tools", [])),
                    updated_data.get("description", ""),
                    1 if updated_data.get("enabled", True) else 0,
                    updated_data.get("access", "all"),
                    now,
                    name,  # old name for WHERE clause
                ),
            )
            await conn.commit()

        return await self.get_by_name(updated_data["name"])

    async def delete(self, name: str) -> bool:
        """Delete a server. Returns True if deleted, False if not found."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM mcp_servers WHERE name = ?", (name,)
            )
            await conn.commit()
        return cursor.rowcount > 0

    async def toggle(self, name: str, enabled: bool) -> bool:
        """Enable/disable a server. Returns True if found, False otherwise."""
        existing = await self.get_by_name(name)
        if existing is None:
            return False

        async with self.db.connection() as conn:
            await conn.execute(
                "UPDATE mcp_servers SET enabled = ?, updated_at = ? WHERE name = ?",
                (1 if enabled else 0, _now(), name),
            )
            await conn.commit()
        return True

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert a database row (dict-like) to a Python dict."""
        data = dict(row) if not isinstance(row, dict) else row
        return {
            "id": data["id"],
            "name": data["name"],
            "type": data["type"],
            "command": data["command"],
            "args": json.loads(data["args"]) if data["args"] else [],
            "url": data["url"],
            "env": json.loads(data["env"]) if data["env"] else {},
            "tools": json.loads(data["tools"]) if data["tools"] else [],
            "description": data["description"],
            "enabled": bool(data["enabled"]),
            "access": data["access"],
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
        }


def _now() -> float:
    import time

    return time.time()


async def migrate_from_file(
    registry_path: Path, store: MCPServerStore
) -> int:
    """Read mcp-registry.json and insert all servers into DB.

    Returns the number of servers migrated. Skips servers that already exist.
    """
    if not registry_path.exists():
        return 0

    try:
        config = json.loads(registry_path.read_text())
    except (json.JSONDecodeError, OSError):
        return 0

    servers = config.get("mcpServers", {})
    migrated = 0

    for name, cfg in servers.items():
        existing = await store.get_by_name(name)
        if existing is not None:
            continue  # already migrated

        server_data = {**cfg, "name": cfg.get("name", name)}
        try:
            await store.create(server_data)
            migrated += 1
        except ValueError:
            # Duplicate — skip
            continue

    return migrated
