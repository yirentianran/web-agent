"""Auto-evolve policy classifier — decides how to evolve skills based on feedback type.

Strategy tiers (safest to most cautious):
1. APPLY_EDITS   — user provided known-correct edits, merge directly
2. AUTO_FIX      — specific bug described, agent generates fix
3. PROPOSE       — vague feedback, generate improvement suggestion for review
4. REQUIRE_REVIEW — high-usage skill with rating drop, mandatory human review
5. SKIP          — insufficient signal
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.database import Database

if TYPE_CHECKING:
    from src.database import Database

logger = logging.getLogger(__name__)

# ── Evolution thresholds ────────────────────────────────────────
SHOULD_EVOLVE_MIN_COUNT = 5
SHOULD_EVOLVE_MAX_RATING = 4.0
HIGH_QUALITY_MIN_RATING = 4

HIGH_USAGE_THRESHOLD = 50


class EvolveAction(str, Enum):
    APPLY_EDITS = "apply_edits"
    AUTO_FIX = "auto_fix"
    PROPOSE = "propose"
    REQUIRE_REVIEW = "require_review"
    SKIP = "skip"


@dataclass(frozen=True)
class FeedbackSummary:
    skill_name: str
    total_feedback: int
    avg_rating: float
    has_user_edits: bool
    user_edits_content: str | None
    has_specific_bugs: bool
    specific_bugs: list[str]
    is_vague: bool
    vague_feedback: list[str]
    uses_count: int


@dataclass(frozen=True)
class EvolveDecision:
    skill_name: str
    action: EvolveAction
    reason: str
    data: dict[str, Any] | None = None


# Keywords that indicate specific, actionable bug reports
BUG_KEYWORDS = [
    "hardcod", "missing", "timeout", "crash", "error", "fail", "broken",
    "null", "none", "empty", "wrong path", "incorrect", "doesn't handle",
    "does not handle", "no validation", "unhandled", "exception",
    "traceback", "stack", "overflow", "memory", "leak",
]

# Keywords that indicate vague/non-actionable feedback
VAGUE_KEYWORDS = [
    "slow", "bad", "not good", "confusing", "doesn't work", "not working",
    "useless", "poor", "terrible", "annoying", "frustrating",
]


class AutoEvolvePolicy:
    """Classify skill feedback and decide evolution strategy."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def analyze_skill(self, skill_name: str) -> EvolveDecision:
        """Analyze feedback for a skill and decide evolution action."""
        summary = await self._summarize_feedback(skill_name)

        if summary.total_feedback == 0:
            return EvolveDecision(
                skill_name=skill_name,
                action=EvolveAction.SKIP,
                reason="No feedback available",
            )

        # Strategy 1: User provided known-correct edits (safest)
        if summary.has_user_edits:
            return EvolveDecision(
                skill_name=skill_name,
                action=EvolveAction.APPLY_EDITS,
                reason="User provided correct edits — safest merge",
                data={"user_edits": summary.user_edits_content},
            )

        # Strategy 2: Specific bug described
        if summary.has_specific_bugs:
            return EvolveDecision(
                skill_name=skill_name,
                action=EvolveAction.AUTO_FIX,
                reason=f"Specific bugs identified: {', '.join(summary.specific_bugs[:3])}",
                data={"bugs": summary.specific_bugs},
            )

        # Strategy 4: High-usage skill with rating drop (most cautious)
        if summary.uses_count >= HIGH_USAGE_THRESHOLD:
            return EvolveDecision(
                skill_name=skill_name,
                action=EvolveAction.REQUIRE_REVIEW,
                reason=f"High-usage skill ({summary.uses_count} uses) — mandatory human review",
            )

        # Strategy 3: Vague feedback
        if summary.is_vague:
            return EvolveDecision(
                skill_name=skill_name,
                action=EvolveAction.PROPOSE,
                reason=f"Vague feedback: {', '.join(summary.vague_feedback[:3])}",
            )

        # Strategy 5: Insufficient signal
        return EvolveDecision(
            skill_name=skill_name,
            action=EvolveAction.SKIP,
            reason="Feedback exists but no clear action identified",
        )

    async def analyze_all_candidates(self) -> list[EvolveDecision]:
        """Analyze all evolution candidates and return decisions."""
        from src.skill_feedback import DBSkillFeedbackManager

        mgr = DBSkillFeedbackManager(db=self.db)
        candidates = await mgr.get_evolution_candidates()

        decisions = []
        for c in candidates:
            decision = await self.analyze_skill(c["skill_name"])
            decisions.append(decision)

        return decisions

    # ── Delegated to DBSkillFeedbackManager ────────────────────────────
    # apply_user_edits() and auto_fix_skill() live in DBSkillFeedbackManager
    # to centralize version management. These are convenience delegates.

    async def apply_user_edits(self, skill_name: str, user_edits: str) -> dict[str, Any] | None:
        """Apply user-provided edits via DBSkillFeedbackManager."""
        from src.skill_feedback import DBSkillFeedbackManager

        mgr = DBSkillFeedbackManager(db=self.db)
        return await mgr.apply_user_edits(skill_name, user_edits)

    async def auto_fix_skill(self, skill_name: str, bugs: list[str]) -> dict[str, Any] | None:
        """Auto-generate a fix via DBSkillFeedbackManager."""
        from src.skill_feedback import DBSkillFeedbackManager

        mgr = DBSkillFeedbackManager(db=self.db)
        return await mgr.auto_fix_skill(skill_name, bugs)

    # ── Internal ──────────────────────────────────────────────────────

    async def _summarize_feedback(self, skill_name: str) -> FeedbackSummary:
        """Aggregate feedback for a skill into a structured summary."""
        from src.skill_feedback import DBSkillFeedbackManager

        mgr = DBSkillFeedbackManager(db=self.db)
        feedback = await mgr.get_feedback_for_evolution(skill_name)

        # Get usage count
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM skill_usage WHERE skill_name = ?", (skill_name,)
            )
            row = await cursor.fetchone()
            uses_count = row[0] if row else 0

        all_comments = []
        has_user_edits = False
        user_edits_content = None
        specific_bugs: list[str] = []
        vague_feedback: list[str] = []

        # Process high quality feedback
        for item in feedback.get("high_quality", []):
            if item.get("comment"):
                all_comments.append(item["comment"])

        # Process low rated feedback
        for item in feedback.get("low_rated", []):
            if item.get("comment"):
                comment = item["comment"]
                all_comments.append(comment)
                lower = comment.lower()

                # Check for specific bug keywords
                found_bugs = [kw for kw in BUG_KEYWORDS if kw in lower]
                if found_bugs:
                    specific_bugs.extend(found_bugs)

                # Check for vague keywords
                found_vague = [kw for kw in VAGUE_KEYWORDS if kw in lower]
                if found_vague:
                    vague_feedback.extend(found_vague)

        # Process user edits
        if feedback.get("user_edits"):
            for item in feedback["user_edits"]:
                if item.get("user_edits"):
                    has_user_edits = True
                    user_edits_content = item["user_edits"]
                    break

        all_items = feedback.get("high_quality", []) + feedback.get("low_rated", [])
        total_feedback = len(all_items)
        all_ratings = [item.get("rating", 3) for item in all_items]
        avg_rating = sum(all_ratings) / len(all_ratings) if all_ratings else 0

        # Deduplicate
        specific_bugs = list(set(specific_bugs))
        vague_feedback = list(set(vague_feedback))

        has_specific = len(specific_bugs) > 0
        is_vague = len(vague_feedback) > 0 and not has_specific

        return FeedbackSummary(
            skill_name=skill_name,
            total_feedback=total_feedback,
            avg_rating=round(avg_rating, 2),
            has_user_edits=has_user_edits,
            user_edits_content=user_edits_content,
            has_specific_bugs=has_specific,
            specific_bugs=specific_bugs,
            is_vague=is_vague,
            vague_feedback=vague_feedback,
            uses_count=uses_count,
        )
