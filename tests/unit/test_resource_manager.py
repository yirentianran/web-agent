"""Unit tests for resource_manager — CPU/memory/disk monitoring."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.resource_manager as rm


@pytest.fixture(autouse=True)
def reset_container_mode() -> None:
    """Ensure CONTAINER_MODE is reset between tests."""
    original = rm.CONTAINER_MODE
    rm.CONTAINER_MODE = False
    yield
    rm.CONTAINER_MODE = original


@pytest.fixture()
def tmp_data_root(tmp_path: Path) -> Path:
    """Provide a temporary DATA_ROOT."""
    original = rm.DATA_ROOT
    rm.DATA_ROOT = tmp_path
    yield tmp_path
    rm.DATA_ROOT = original


# ── container stats ───────────────────────────────────────────────


class TestContainerStats:
    def test_returns_disabled_when_not_container_mode(self) -> None:
        rm.CONTAINER_MODE = False
        result = rm.get_container_stats("alice")
        assert result["status"] == "container_mode_disabled"

    @patch("src.resource_manager.docker")
    def test_returns_stats_for_running_container(self, mock_docker: MagicMock) -> None:
        rm.CONTAINER_MODE = True
        mock_container = MagicMock()
        mock_container.stats.return_value = {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 1000000000},
                "system_cpu_usage": 5000000000,
                "online_cpus": 4,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 0},
                "system_cpu_usage": 0,
            },
            "memory_stats": {
                "usage": 536870912,  # 512 MB
                "limit": 4294967296,  # 4 GB
            },
        }

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_docker.from_env.return_value = mock_client

        result = rm.get_container_stats("alice")
        assert result["status"] == "ok"
        assert result["cpu_percent"] > 0
        assert result["memory_mb"] == 512.0
        assert result["memory_limit_mb"] == 4096.0

    @patch("src.resource_manager.docker")
    def test_returns_error_for_missing_container(self, mock_docker: MagicMock) -> None:
        rm.CONTAINER_MODE = True
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = Exception("not found")
        mock_docker.from_env.return_value = mock_client

        result = rm.get_container_stats("bob")
        assert result["status"] == "error"


# ── disk usage ────────────────────────────────────────────────────


class TestDiskUsage:
    def test_returns_not_found_for_missing_dir(self, tmp_data_root: Path) -> None:
        result = rm.get_disk_usage("nobody")
        assert result["status"] == "not_found"

    def test_returns_disk_mb_for_existing_dir(self, tmp_data_root: Path) -> None:
        user_dir = tmp_data_root / "users" / "alice"
        user_dir.mkdir(parents=True)
        # Create a small file
        (user_dir / "test.txt").write_text("hello")

        result = rm.get_disk_usage("alice")
        assert result["status"] == "ok"
        assert result["disk_mb"] >= 0.0


# ── quota check ───────────────────────────────────────────────────


class TestQuotaCheck:
    def test_all_ok_when_within_limits(self, tmp_data_root: Path) -> None:
        rm.CONTAINER_MODE = False
        result = rm.check_quota("alice")
        # In non-container mode, container stats are disabled but disk is checked
        assert "cpu_ok" in result
        assert "memory_ok" in result
        assert "disk_ok" in result

    def test_includes_limits_in_details(self, tmp_data_root: Path) -> None:
        rm.CONTAINER_MODE = False
        result = rm.check_quota("alice")
        limits = result["details"]["limits"]
        assert limits["max_cpu_percent"] == 100.0
        assert limits["max_memory_mb"] == 4096.0
        assert limits["max_disk_mb"] == 1024.0


# ── all resources ─────────────────────────────────────────────────


class TestAllResources:
    def test_returns_disabled_when_not_container_mode(self) -> None:
        rm.CONTAINER_MODE = False
        result = rm.get_all_resources()
        assert result["status"] == "container_mode_disabled"

    @patch("src.resource_manager.docker")
    def test_aggregates_active_containers(self, mock_docker: MagicMock, tmp_data_root: Path) -> None:
        rm.CONTAINER_MODE = True
        c1 = MagicMock()
        c1.name = "web-agent-alice"
        c1.stats.return_value = {
            "cpu_stats": {"cpu_usage": {"total_usage": 0}, "system_cpu_usage": 0},
            "precpu_stats": {"cpu_usage": {"total_usage": 0}, "system_cpu_usage": 0},
            "memory_stats": {"usage": 0, "limit": 0},
        }

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [c1]
        mock_docker.from_env.return_value = mock_client

        result = rm.get_all_resources()
        assert "alice" in result
