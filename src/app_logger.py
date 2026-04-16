"""L2 Application Logger — structured JSON with rotation.

Wraps Python's logging module to emit structured JSONL entries.
Configured with 30-day retention, per-day log files.

Usage:
    from src.app_logger import get_app_logger

    logger = get_app_logger()
    logger.info("request_completed", extra={"method": "GET", "path": "/health", "status": 200, "duration_ms": 12})
    logger.error("upload_failed", extra={"user_id": "alice", "filename": "report.pdf", "reason": "type_blocked"})
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

APP_LOG_DIR = Path(os.getenv("APP_LOG_DIR", "/data/logs/app"))
APP_LOG_RETENTION_DAYS = int(os.getenv("APP_LOG_RETENTION_DAYS", "30"))


class _JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        # Attach extra fields
        for key, value in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "created", "exc_info", "exc_text",
                "stack_info", "levelname", "levelno", "pathname", "filename",
                "module", "lineno", "funcName", "thread", "threadName",
                "process", "message", "taskName", "relativeCreated",
                "msecs", "asctime",
            ):
                entry[key] = value
        return json.dumps(entry, ensure_ascii=False, default=str)


def _create_handler(log_dir: Path) -> TimedRotatingFileHandler:
    """Create a daily-rotating file handler with retention."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"
    handler = TimedRotatingFileHandler(
        str(log_file),
        when="midnight",
        backupCount=APP_LOG_RETENTION_DAYS,
        encoding="utf-8",
        utc=True,
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(_JsonFormatter())
    return handler


_app_logger: logging.Logger | None = None


def get_app_logger(name: str = "web-agent") -> logging.Logger:
    """Return a structured JSON application logger."""
    global _app_logger
    if _app_logger is not None:
        return _app_logger

    _app_logger = logging.getLogger(name)
    _app_logger.setLevel(logging.INFO)

    handler = _create_handler(APP_LOG_DIR)
    _app_logger.addHandler(handler)

    # Also emit to stderr for container logs
    console = logging.StreamHandler()
    console.setFormatter(_JsonFormatter())
    _app_logger.addHandler(console)

    return _app_logger


def log_request(method: str, path: str, status: int, duration_ms: float, user_id: str | None = None) -> None:
    """Log an HTTP request/response pair."""
    logger = get_app_logger()
    extra: dict[str, Any] = {
        "method": method,
        "path": path,
        "status": status,
        "duration_ms": round(duration_ms, 2),
        "event": "http_request",
    }
    if user_id:
        extra["user_id"] = user_id
    logger.info("HTTP %s %s %d", method, path, status, extra=extra)
