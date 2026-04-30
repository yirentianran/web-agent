"""File utility functions for safe file handling.

Usage:
    from src.file_utils import sanitize_filename, generate_stored_name

    safe = sanitize_filename("user_upload (1).pdf")
    # → "user_upload__1_.pdf"

    stored = generate_stored_name("report.pdf")
    # → "a1b2c3d4_report.pdf"
"""

from __future__ import annotations

import re
import uuid


def sanitize_filename(filename: str) -> str:
    """Remove or replace characters unsafe for filesystem paths.

    Keeps alphanumeric chars, dots, dashes, and underscores.
    Replaces everything else with underscores.
    Collapses consecutive underscores.
    Strips leading/trailing dots and spaces.
    """
    # Replace unsafe characters with underscore
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
    # Collapse consecutive underscores
    safe = re.sub(r"_+", "_", safe)
    # Strip leading/trailing dots and spaces
    safe = safe.strip(". ")
    # Fallback if everything was stripped
    if not safe:
        safe = "untitled"
    return safe


def generate_stored_name(original_filename: str) -> str:
    """Generate a unique stored filename with an 8-char hex prefix.

    Pattern: {uuid_short}_{sanitized_original}
    Example: "a1b2c3d4_report.pdf"
    """
    short = uuid.uuid4().hex[:8]
    safe = sanitize_filename(original_filename)
    return f"{short}_{safe}"
