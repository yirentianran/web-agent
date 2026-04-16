"""Unit tests for file upload validation."""

from __future__ import annotations

from src.file_validation import validate_extension, validate_size


class TestValidateExtension:
    def test_allowed_extension(self) -> None:
        assert validate_extension("report.pdf") is None
        assert validate_extension("data.csv") is None
        assert validate_extension("script.py") is None

    def test_disallowed_extension(self) -> None:
        result = validate_extension("malware.exe")
        assert result is not None
        assert "not allowed" in result.lower()

    def test_case_insensitive(self) -> None:
        assert validate_extension("FILE.PDF") is None
        assert validate_extension("DATA.CSV") is None

    def test_no_extension(self) -> None:
        result = validate_extension("README")
        assert result is not None  # no extension means not in allowed set


class TestValidateSize:
    def test_under_limit(self) -> None:
        assert validate_size(1024) is None  # 1 KB

    def test_over_limit(self) -> None:
        result = validate_size(100 * 1024 * 1024)  # 100 MB, over 50 MB default
        assert result is not None
        assert "exceeds" in result.lower()

    def test_at_limit(self) -> None:
        assert validate_size(50 * 1024 * 1024) is None  # exactly 50 MB
