"""Model name resolution utilities."""

from __future__ import annotations

import os


def get_flash_model() -> str | None:
    """Resolve lightweight-task model: FLASH_MODEL → MODEL, no default."""
    return os.getenv("FLASH_MODEL") or os.getenv("MODEL") or None
