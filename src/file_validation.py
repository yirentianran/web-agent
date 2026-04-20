"""File upload validation — type whitelist + size limits."""

from __future__ import annotations

import os

# Allowed file extensions
ALLOWED_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".rb", ".php", ".c", ".cpp", ".h",
    ".html", ".css", ".scss", ".sql",
    ".log", ".cfg", ".ini", ".toml",
    ".pdf", ".docx", ".xlsx",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
}

# Maximum upload size in bytes (50 MB default)
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(200 * 1024 * 1024)))


def validate_extension(filename: str) -> str | None:
    """Return error message if extension is not allowed, None if OK."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return f"File type '{ext}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
    return None


def validate_size(size_bytes: int) -> str | None:
    """Return error message if file exceeds size limit, None if OK."""
    if size_bytes > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES / (1024 * 1024)
        actual_mb = size_bytes / (1024 * 1024)
        return f"File size ({actual_mb:.1f} MB) exceeds limit of {limit_mb:.1f} MB"
    return None
