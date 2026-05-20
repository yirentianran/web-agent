"""Shared constants for tool permissions, blocked features, and runtime mode."""

import os

# Full set of built-in tool names. The actual allowed list excludes DISABLED_TOOLS.
BUILTIN_TOOLS: tuple[str, ...] = (
    "Read", "Edit", "Write", "Glob", "Grep", "Bash",
    "Agent", "Skill", "WebSearch", "WebFetch",
)

# Tools disabled for all users — MCP fetch servers provide web content retrieval.
# Add or remove tool names here to change the global block list.
DISABLED_TOOLS: tuple[str, ...] = ("WebSearch", "WebFetch")
# DISABLED_TOOLS: tuple[str, ...] = ()

# Runtime mode — read once at module load. Use this value instead of
# re-reading os.getenv("CONTAINER_MODE") in other modules.
CONTAINER_MODE: bool = os.getenv("CONTAINER_MODE", "false").lower() == "true"
