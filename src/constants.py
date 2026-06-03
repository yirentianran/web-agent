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

# Max file size for Read tool — files exceeding this are rejected before
# the CLI reads them. 20 MiB covers large PDF/DOCX/Excel without risking
# JSON buffer overflow (10 MiB base64-encoded ≈ 13.3 MiB raw).
MAX_READ_FILE_BYTES: int = int(os.getenv("MAX_READ_FILE_BYTES", str(20 * 1024 * 1024)))

# Runtime mode — read once at module load. Use this value instead of
# re-reading os.getenv("CONTAINER_MODE") in other modules.
CONTAINER_MODE: bool = os.getenv("CONTAINER_MODE", "false").lower() == "true"
