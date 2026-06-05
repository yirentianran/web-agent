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

import base64
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.database import Database

logger = logging.getLogger(__name__)

# --- MCP Credential Encryption ---
_MCP_ENCRYPTION_KEY = os.getenv("MCP_ENCRYPTION_KEY", "")
_encryption_available = False
_cipher = None

if _MCP_ENCRYPTION_KEY:
    try:
        from cryptography.fernet import Fernet

        # Derive a 32-byte key and encode as url-safe base64 for Fernet
        key_bytes = _MCP_ENCRYPTION_KEY.encode("utf-8").ljust(32, b"\x00")[:32]
        fernet_key = base64.urlsafe_b64encode(key_bytes)
        _cipher = Fernet(fernet_key)
        _encryption_available = True
    except Exception as e:
        logger.warning("Failed to initialize MCP credential encryption: %s", e)
else:
    logger.warning("MCP_ENCRYPTION_KEY not set — MCP credentials stored as plaintext")

_SENSITIVE_FIELDS = {"headers", "env"}


def _encrypt_sensitive_fields(data: dict) -> dict:
    """Encrypt headers and env fields. Returns a new dict (does not mutate input)."""
    if not _encryption_available or _cipher is None:
        return data
    result = dict(data)
    for field in _SENSITIVE_FIELDS:
        if field in result and result[field]:
            json_str = json.dumps(result[field])
            encrypted = _cipher.encrypt(json_str.encode("utf-8"))
            result[field] = base64.urlsafe_b64encode(encrypted).decode("ascii")
    return result


def _decrypt_sensitive_fields(data: dict) -> dict:
    """Decrypt headers and env fields. Handles plaintext gracefully."""
    if not _encryption_available or _cipher is None:
        return data
    result = dict(data)
    for field in _SENSITIVE_FIELDS:
        if field in result and result[field] and isinstance(result[field], str):
            try:
                encrypted = base64.urlsafe_b64decode(result[field].encode("ascii"))
                decrypted_json = _cipher.decrypt(encrypted)
                result[field] = json.loads(decrypted_json)
            except Exception:
                # Already plaintext — backward compatible, leave as-is
                pass
    return result


class MCPServerStore:
    """Async CRUD store for MCP server configurations."""

    def __init__(self, db: "Database") -> None:
        self.db = db

    async def list_all(self) -> list[dict[str, Any]]:
        """Return all MCP servers."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id, name, type, command, args, url, headers, env, tools, "
                "resources, prompts, description, enabled, access, created_at, updated_at "
                "FROM mcp_servers ORDER BY name"
            )
            rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        """Return a single server by name, or None."""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id, name, type, command, args, url, headers, env, tools, "
                "resources, prompts, description, enabled, access, created_at, updated_at "
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

        server_to_store = _encrypt_sensitive_fields(server)

        now = _now()
        async with self.db.connection() as conn:
            await conn.execute(
                """INSERT INTO mcp_servers
                   (name, type, command, args, url, headers, env, tools,
                    resources, prompts, description, enabled, access, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    server_to_store["name"],
                    server_to_store.get("type", "stdio"),
                    server_to_store.get("command"),
                    json.dumps(server_to_store.get("args", [])),
                    server_to_store.get("url"),
                    json.dumps(server_to_store.get("headers", {})),
                    json.dumps(server_to_store.get("env", {})),
                    json.dumps(server_to_store.get("tools", [])),
                    json.dumps(server_to_store.get("resources", [])),
                    json.dumps(server_to_store.get("prompts", [])),
                    server_to_store.get("description", ""),
                    1 if server_to_store.get("enabled", True) else 0,
                    server_to_store.get("access", "all"),
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

        updated_data = {**existing, **patch}
        updated_data = _encrypt_sensitive_fields(updated_data)
        now = _now()

        async with self.db.connection() as conn:
            await conn.execute(
                """UPDATE mcp_servers SET
                   name = ?, type = ?, command = ?, args = ?, url = ?,
                   headers = ?, env = ?, tools = ?, resources = ?, prompts = ?,
                   description = ?, enabled = ?, access = ?, updated_at = ?
                   WHERE name = ?""",
                (
                    updated_data["name"],
                    updated_data.get("type", "stdio"),
                    updated_data.get("command"),
                    json.dumps(updated_data.get("args", [])),
                    updated_data.get("url"),
                    json.dumps(updated_data.get("headers", {})),
                    json.dumps(updated_data.get("env", {})),
                    json.dumps(updated_data.get("tools", [])),
                    json.dumps(updated_data.get("resources", [])),
                    json.dumps(updated_data.get("prompts", [])),
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
        result = {
            "id": data["id"],
            "name": data["name"],
            "type": data["type"],
            "command": data["command"],
            "args": json.loads(data["args"]) if data["args"] else [],
            "url": data["url"],
            "headers": json.loads(data["headers"]) if data["headers"] else {},
            "env": json.loads(data["env"]) if data["env"] else {},
            "tools": json.loads(data["tools"]) if data["tools"] else [],
            "resources": json.loads(data["resources"]) if data["resources"] else [],
            "prompts": json.loads(data["prompts"]) if data["prompts"] else [],
            "description": data["description"],
            "enabled": bool(data["enabled"]),
            "access": data["access"],
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
        }
        return _decrypt_sensitive_fields(result)


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
