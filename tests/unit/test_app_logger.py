"""Tests for L2 application logger."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import pytest

from src.app_logger import _JsonFormatter, get_app_logger, log_request


@pytest.fixture()
def tmp_log_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "app"


def test_json_formatter() -> None:
    formatter = _JsonFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="Hello %s", args=("world",), exc_info=None,
    )
    result = formatter.format(record)
    entry = json.loads(result)
    assert entry["level"] == "INFO"
    assert entry["message"] == "Hello world"
    assert "timestamp" in entry


def test_json_formatter_extra_fields(tmp_log_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.app_logger
    src.app_logger._app_logger = None
    monkeypatch.setattr("src.app_logger.APP_LOG_DIR", tmp_log_dir)

    logger = get_app_logger(name="test-extra")
    record = logging.LogRecord(
        name="test-extra", level=logging.WARNING, pathname="", lineno=0,
        msg="Something happened", args=(), exc_info=None,
    )
    record.status_code = 500
    formatter = _JsonFormatter()
    result = formatter.format(record)
    entry = json.loads(result)
    assert entry["status_code"] == 500
    src.app_logger._app_logger = None


def test_json_formatter_exception(tmp_log_dir: Path) -> None:
    formatter = _JsonFormatter()
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname="", lineno=0,
        msg="Error occurred", args=(),
        exc_info=(ValueError, ValueError("test error"), None),
    )
    result = formatter.format(record)
    entry = json.loads(result)
    assert "exception" in entry
    assert "ValueError" in entry["exception"]


def test_log_request_helper(tmp_log_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.app_logger
    src.app_logger._app_logger = None
    monkeypatch.setattr("src.app_logger.APP_LOG_DIR", tmp_log_dir)

    log_request("GET", "/health", 200, 12.5, user_id="alice")
    src.app_logger._app_logger = None


def test_singleton_returns_same(tmp_log_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.app_logger
    src.app_logger._app_logger = None
    monkeypatch.setattr("src.app_logger.APP_LOG_DIR", tmp_log_dir)

    logger1 = get_app_logger(name="test-singleton")
    logger2 = get_app_logger(name="test-singleton")
    assert logger1 is logger2
    src.app_logger._app_logger = None
