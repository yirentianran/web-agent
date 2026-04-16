"""Skill evolution: feedback-driven SKILL.md improvement pipeline.

Analyzes user feedback, determines when a skill should evolve, and uses
the Anthropic API to generate an improved version of the skill prompt.
"""

from __future__ import annotations

import glob
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "data")).resolve()

SHOULD_EVOLVE_MIN_COUNT = 10
SHOULD_EVOLVE_MAX_RATING = 4.5
HIGH_QUALITY_MIN_RATING = 4


@dataclass(frozen=True)
class FeedbackStats:
    count: int
    average_rating: float
    high_quality_count: int
    rating_distribution: dict[str, int]
    versions: list[str]


@dataclass(frozen=True)
class EvolutionCandidate:
    skill_name: str
    stats: FeedbackStats


class SkillEvolutionManager:
    def __init__(self, data_root: Path = DATA_ROOT) -> None:
        self.data_root = data_root
        self.feedback_dir = data_root / "training" / "skill-feedback"
        self.skills_dir = data_root / "skills"

    def _ensure_dirs(self) -> None:
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def _feedback_files(self, skill_name: str) -> list[Path]:
        pattern = str(self.feedback_dir / f"*_{skill_name}.jsonl")
        return [Path(p) for p in glob.glob(pattern)]

    def _load_feedback(self, skill_name: str) -> list[dict]:
        entries: list[dict] = []
        for fp in self._feedback_files(skill_name):
            try:
                with open(fp, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            entries.append(json.loads(line))
            except (json.JSONDecodeError, OSError):
                continue
        return entries

    def collect_feedback(
        self,
        skill_name: str,
        *,
        rating: int,
        user_id: str = "anonymous",
        comment: str = "",
        session_id: str | None = None,
        version: str = "current",
    ) -> dict:
        """Record a single feedback entry. Rating must be 1-5."""
        if not (1 <= rating <= 5):
            raise ValueError(f"Rating must be between 1 and 5, got {rating}")

        self._ensure_dirs()
        timestamp = time.time()
        entry = {
            "skill_name": skill_name,
            "user_id": user_id,
            "rating": rating,
            "comment": comment[:500],
            "session_id": session_id,
            "version": version,
            "timestamp": timestamp,
        }
        filepath = self.feedback_dir / f"{timestamp:.0f}_{skill_name}.jsonl"
        with open(filepath, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def get_feedback_stats(self, skill_name: str) -> FeedbackStats:
        """Compute aggregate stats for a skill's feedback."""
        entries = self._load_feedback(skill_name)
        if not entries:
            return FeedbackStats(
                count=0,
                average_rating=0.0,
                high_quality_count=0,
                rating_distribution={},
                versions=[],
            )

        ratings = [e["rating"] for e in entries if "rating" in e]
        dist: dict[str, int] = {}
        for r in ratings:
            dist[str(r)] = dist.get(str(r), 0) + 1

        versions = sorted(
            {e.get("version", "current") for e in entries if "version" in e}
        )

        return FeedbackStats(
            count=len(ratings),
            average_rating=round(sum(ratings) / len(ratings), 2) if ratings else 0.0,
            high_quality_count=sum(1 for r in ratings if r >= HIGH_QUALITY_MIN_RATING),
            rating_distribution=dist,
            versions=versions,
        )

    def should_evolve(self, skill_name: str) -> bool:
        """Return True if feedback count >= threshold AND avg rating is low enough."""
        stats = self.get_feedback_stats(skill_name)
        return (
            stats.count >= SHOULD_EVOLVE_MIN_COUNT
            and stats.average_rating < SHOULD_EVOLVE_MAX_RATING
        )

    def generate_improved_skill(
        self,
        skill_name: str,
        *,
        anthropic_api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
    ) -> str | None:
        """Use the Anthropic API to generate an improved SKILL.md.

        Reads high-quality feedback entries (rating >= 4) and the current
        SKILL.md, then asks the model to produce an improved version.
        Returns the new version path, or None if generation failed.
        """
        import anthropic

        self._ensure_dirs()

        # Load current SKILL.md
        skill_file = self.skills_dir / skill_name / "SKILL.md"
        if not skill_file.exists():
            return None
        current_content = skill_file.read_text()

        # Gather high-quality feedback for context
        entries = self._load_feedback(skill_name)
        high_quality = [e for e in entries if e.get("rating", 0) >= HIGH_QUALITY_MIN_RATING]
        low_rated = [e for e in entries if e.get("rating", 0) <= 2 and e.get("comment")]

        if not high_quality and not low_rated:
            return None

        # Build context from feedback
        feedback_context = ""
        if high_quality:
            feedback_context += "\n### What users liked (rating >= 4):\n"
            for e in high_quality[:10]:
                if e.get("comment"):
                    feedback_context += f"- {e['comment']}\n"
        if low_rated:
            feedback_context += "\n### What users disliked (rating <= 2):\n"
            for e in low_rated[:10]:
                feedback_context += f"- {e['comment']}\n"

        api_key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None

        client = anthropic.Anthropic(api_key=api_key)

        prompt = (
            f"You are improving a skill prompt for an AI agent.\n\n"
            f"Current SKILL.md:\n```markdown\n{current_content}\n```\n\n"
            f"User feedback:\n{feedback_context}\n\n"
            f"Rewrite the SKILL.md to address the negative feedback while preserving "
            f"what users found helpful. Return ONLY the new markdown content."
        )

        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system="You are a skill prompt optimizer. Return only markdown content.",
                messages=[{"role": "user", "content": prompt}],
            )
            new_content = ""
            for block in response.content:
                if hasattr(block, "text"):
                    new_content += block.text

            # Determine next version number
            existing_versions = list((self.skills_dir / skill_name).glob("SKILL_v*.md"))
            next_ver = len(existing_versions) + 1
            version_path = self.skills_dir / skill_name / f"SKILL_v{next_ver}.md"
            version_path.write_text(new_content)
            return str(version_path)
        except Exception:
            return None

    def get_evolution_candidates(self) -> list[EvolutionCandidate]:
        """Return all skills that should evolve, sorted by avg_rating ASC."""
        self._ensure_dirs()
        # Discover all unique skill names from feedback files
        skill_names: set[str] = set()
        for fp in self.feedback_dir.glob("*.jsonl"):
            parts = fp.stem.rsplit("_", 1)
            if len(parts) == 2:
                skill_names.add(parts[1])

        candidates: list[EvolutionCandidate] = []
        for name in skill_names:
            stats = self.get_feedback_stats(name)
            if self.should_evolve(name):
                candidates.append(EvolutionCandidate(skill_name=name, stats=stats))

        candidates.sort(key=lambda c: c.stats.average_rating)
        return candidates


# Module-level convenience functions (for backwards compat, use manager for testing)
_mgr = SkillEvolutionManager()

collect_feedback = _mgr.collect_feedback
get_feedback_stats = _mgr.get_feedback_stats
should_evolve = _mgr.should_evolve
generate_improved_skill = _mgr.generate_improved_skill
get_evolution_candidates = _mgr.get_evolution_candidates
