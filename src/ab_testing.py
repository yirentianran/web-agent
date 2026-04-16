"""A/B testing for skill versions.

Hash-based 50/50 traffic split, result tracking, and statistical
winner detection.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

MIN_SAMPLES_PER_VERSION = 5
MIN_WIN_THRESHOLD = 0.3  # abs(avg_b - avg_a) must exceed this


@dataclass(frozen=True)
class ABTestResults:
    version_a_avg: float
    version_a_count: int
    version_b_avg: float
    version_b_count: int
    winner: str | None  # "a", "b", or None
    is_decisive: bool


class SkillABTest:
    """A/B test between two versions of a skill."""

    def __init__(
        self,
        skill_name: str,
        version_a: str,
        version_b: str,
        data_root: Path | None = None,
    ) -> None:
        self.skill_name = skill_name
        self.version_a = version_a
        self.version_b = version_b
        self.data_root = data_root or Path("data").resolve()
        self.test_dir = self.data_root / "training" / "skill_outcomes"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.results_file = self.test_dir / f"{skill_name}_ab_test.jsonl"

    def _result_key(self) -> str:
        return f"{self.skill_name}_{self.version_a}_{self.version_b}"

    def assign_version(self, user_id: str) -> str:
        """Deterministic hash-based 50/50 split."""
        h = hashlib.sha256(
            f"{self.skill_name}:{user_id}".encode()
        ).hexdigest()
        # Even hash value → version A, odd → version B (50/50)
        return self.version_a if int(h[-1], 16) % 2 == 0 else self.version_b

    def record_result(
        self, user_id: str, version: str, rating: int
    ) -> dict:
        """Record a single test result. Version must be 'a' or 'b'."""
        if version not in ("a", "b"):
            raise ValueError(f"Version must be 'a' or 'b', got '{version}'")
        if not (1 <= rating <= 5):
            raise ValueError(f"Rating must be between 1 and 5, got {rating}")

        entry = {
            "user_id": user_id,
            "version": version,
            "rating": rating,
            "timestamp": time.time(),
        }
        with open(self.results_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def _load_results(self) -> list[dict]:
        if not self.results_file.exists():
            return []
        entries: list[dict] = []
        try:
            with open(self.results_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except (json.JSONDecodeError, OSError):
            pass
        return entries

    def is_winner(self) -> ABTestResults:
        """Check if a winner can be declared."""
        entries = self._load_results()
        a_ratings = [e["rating"] for e in entries if e["version"] == "a"]
        b_ratings = [e["rating"] for e in entries if e["version"] == "b"]

        a_avg = round(sum(a_ratings) / len(a_ratings), 2) if a_ratings else 0.0
        b_avg = round(sum(b_ratings) / len(b_ratings), 2) if b_ratings else 0.0

        a_count = len(a_ratings)
        b_count = len(b_ratings)

        has_min_samples = (
            a_count >= MIN_SAMPLES_PER_VERSION
            and b_count >= MIN_SAMPLES_PER_VERSION
        )
        diff_exceeds = abs(b_avg - a_avg) > MIN_WIN_THRESHOLD

        winner: str | None = None
        if has_min_samples and diff_exceeds:
            winner = "b" if b_avg > a_avg else "a"

        return ABTestResults(
            version_a_avg=a_avg,
            version_a_count=a_count,
            version_b_avg=b_avg,
            version_b_count=b_count,
            winner=winner,
            is_decisive=winner is not None,
        )

    def get_results(self) -> dict:
        """Return per-version stats."""
        result = self.is_winner()
        return {
            "skill_name": self.skill_name,
            "version_a": self.version_a,
            "version_b": self.version_b,
            "version_a_avg": result.version_a_avg,
            "version_a_count": result.version_a_count,
            "version_b_avg": result.version_b_avg,
            "version_b_count": result.version_b_count,
            "winner": result.winner,
            "is_decisive": result.is_decisive,
        }
