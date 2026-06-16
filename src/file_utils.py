"""File utility helpers for download URL construction and generated-file filtering.

Extracted from main_server.py into a shared module so both container and
non-container code paths can use the same logic.
"""

from __future__ import annotations

from pathlib import Path


# Filenames that indicate a programming error, not a real generated file
INVALID_FILENAMES = {"null", "undefined", "none", ""}
INVALID_FILENAME_STEMS = {"null", "undefined", "none"}

# Allowed extensions for user-facing generated file results (data documents, media, archives)
DATA_EXTS = {
    ".xlsx",
    ".xls",
    ".pdf",
    ".zip",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".txt",
    ".md",
    ".rtf",
    ".odt",
    ".html",
    ".svg",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
    ".mp3",
    ".wav",
    ".mp4",
    ".mov",
    ".avi",
}


def build_download_url(user_id: str, file_path: str, *, directory: str | None = None) -> str:
    """Build a download URL for a file, including the correct directory prefix.

    Handles relative paths, absolute paths, and already-prefixed paths.
    Always produces a clean URL of the form /api/users/{user_id}/download/{dir}/{name}.
    """
    path = Path(file_path)
    # Absolute path — extract just the filename, ignore the directory
    if path.is_absolute():
        return f"/api/users/{user_id}/download/outputs/{path.name}"

    parts = path.parts
    if len(parts) > 1:
        # Path includes directory (e.g., 'outputs/file.txt')
        prefix = "/".join(parts[:-1])
        filename = path.name
    elif directory:
        prefix = directory
        filename = path.name
    else:
        return f"/api/users/{user_id}/download/{path.name}"
    return f"/api/users/{user_id}/download/{prefix}/{filename}"


def should_include_generated_file(filename: str) -> bool:
    """Return True if this file should be offered as a downloadable result.

    Uses a **positive allow-list** (``DATA_EXTS``) for user-facing data files.
    Script/code files (``.py``, ``.js``, ``.sh``, etc.) are excluded by omission.
    """
    if not filename:
        return False
    # Reject filenames that indicate a programming error (e.g. None -> "null")
    name_lower = filename.lower()
    if name_lower in INVALID_FILENAMES:
        return False
    # Also reject when the stem (without extension) is invalid
    stem_lower = Path(filename).stem.lower()
    if stem_lower in INVALID_FILENAME_STEMS:
        return False
    ext = Path(filename).suffix.lower()
    if not ext:
        return False
    # Must be in the positive allow-list
    return ext in DATA_EXTS
