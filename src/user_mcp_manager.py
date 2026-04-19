"""Per-user MCP server management.

Each user has their own MCP server configuration stored as a JSON file
in their data directory. This manager handles CRUD operations and
integration with the agent session builder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


class UserMcpManager:
    """Manages MCP server configurations for a single user."""

    def __init__(self, user_id: str, data_root: Path) -> None:
        self.user_id = user_id
        self._data_dir = data_root / "users" / user_id
        self._config_file = self._data_dir / "mcp-servers.json"

    def _ensure_dir(self) -> None:
        """Ensure the user data directory exists."""
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        """Load servers from JSON file. Returns empty dict if not found."""
        if self._config_file.exists():
            return json.loads(self._config_file.read_text())
        return {}

    def _save(self, data: dict[str, Any]) -> None:
        """Persist servers to JSON file."""
        self._ensure_dir()
        self._config_file.write_text(json.dumps(data, indent=2))

    def list_servers(self) -> list[dict[str, Any]]:
        """Return all MCP servers for this user."""
        data = self._load()
        return list(data.values())

    def get_server(self, name: str) -> Optional[dict[str, Any]]:
        """Return a single server by name, or None."""
        data = self._load()
        return data.get(name)

    def create_server(self, config: dict[str, Any]) -> dict[str, Any]:
        """Create a new MCP server. Raises ValueError if name exists."""
        data = self._load()
        if config["name"] in data:
            raise ValueError(f"Server '{config['name']}' already exists")
        data[config["name"]] = config
        self._save(data)
        return config

    def update_server(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        """Update an existing MCP server. Raises ValueError if not found."""
        data = self._load()
        if name not in data:
            raise ValueError(f"Server '{name}' not found")
        data[name] = config
        self._save(data)
        return config

    def delete_server(self, name: str) -> None:
        """Delete an MCP server. Raises ValueError if not found."""
        data = self._load()
        if name not in data:
            raise ValueError(f"Server '{name}' not found")
        del data[name]
        self._save(data)

    def toggle_server(self, name: str, enabled: bool) -> None:
        """Enable or disable an MCP server. Raises ValueError if not found."""
        data = self._load()
        if name not in data:
            raise ValueError(f"Server '{name}' not found")
        data[name]["enabled"] = enabled
        self._save(data)

    def get_active_config(self) -> dict[str, Any]:
        """Return SDK-compatible mcpServers config for enabled servers only.

        Format matches what Claude Agent SDK expects:
        {
            "mcpServers": {
                "server-name": {
                    "command": "...",
                    "args": [...],
                    "env": {...},
                }
            },
            "allowed_tools": ["mcp__server__tool", ...]
        }
        """
        servers = self.list_servers()
        mcp_config: dict[str, Any] = {}
        allowed_tools: list[str] = []

        for server in servers:
            if not server.get("enabled", True):
                continue

            safe_name = server["name"]
            if server["type"] == "stdio":
                mcp_config[safe_name] = {
                    "command": server.get("command", ""),
                    "args": server.get("args", []),
                }
                if server.get("env"):
                    mcp_config[safe_name]["env"] = server["env"]
            elif server["type"] == "http":
                mcp_config[safe_name] = {
                    "url": server.get("url", ""),
                }

            # Build allowed tool names: mcp__{server}__{tool}
            for tool in server.get("tools", []):
                allowed_tools.append(f"mcp__{safe_name}__{tool}")

        return {
            "mcpServers": mcp_config,
            "allowed_tools": allowed_tools,
        }
