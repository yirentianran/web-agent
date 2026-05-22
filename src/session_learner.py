"""Session-based skill evolution — ECC-inspired continuous learning.

Triggered at session end. Queries the messages table for full conversation
context, calls Haiku for analysis, and applies improvements / creates new
skills based on confidence scores.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

if __name__ != "__main__":
    from src.database import Database
    from src.evolution_log import EvolutionLogStore

logger = logging.getLogger(__name__)

MIN_SESSION_MESSAGES = 10

ANALYSIS_PROMPT = """Analyze this AI agent session and identify what we can learn.

## Session Messages
{messages}

## Skills Used
{skills_used}

## Existing Feedback for These Skills
{existing_feedback}

## Tasks
1. For each skill used: did it perform well? If not, what went wrong and how should SKILL.md change?
2. Did the user demonstrate any reusable workflow that could become a new skill?

Return ONLY valid JSON (no markdown fences, no explanation):
{{
  "improvements": [
    {{"skill_name": "string", "confidence": 1-10, "issue": "specific description with context", "suggested_fix": "complete fixed SKILL.md content"}}
  ],
  "new_patterns": [
    {{"name": "kebab-case-name", "confidence": 1-10, "description": "what this pattern does and when to use it", "skill_content": "complete SKILL.md content"}}
  ]
}}"""


class SessionLearner:
    """Analyzes completed sessions and evolves skills.

    Uses callback injection to avoid circular imports with main_server:
    - skill_manager: SkillManager instance for DB registration
    - on_skill_changed: callable to bump shared-skills generation counter
    """

    def __init__(
        self,
        db: "Database",
        data_root: Path,
        skill_manager: Any = None,
        on_skill_changed: "Callable[[], None] | None" = None,
    ) -> None:
        self.db = db
        self.data_root = data_root
        self.skill_manager = skill_manager
        self.on_skill_changed = on_skill_changed
        self.store = EvolutionLogStore(db)

    async def analyze_session(self, session_id: str) -> dict:
        """Analyze a completed session. Called as fire-and-forget at session end."""
        # 1. Check minimum message count
        msg_count = await self._count_messages(session_id)
        if msg_count < MIN_SESSION_MESSAGES:
            logger.debug("Session %s too short (%d messages), skipping", session_id, msg_count)
            return {"skipped": True, "reason": "too_short"}

        # 2. Query data
        messages = await self._get_session_messages(session_id)
        skills_used = await self._get_session_skills(session_id)
        if not skills_used:
            return {"skipped": True, "reason": "no_skills"}

        feedback = await self._get_skills_feedback(skills_used)

        # 3. Build prompt and call Haiku
        prompt = self._build_prompt(messages, skills_used, feedback)
        result = await self._call_haiku(prompt)
        if result is None:
            return {"skipped": True, "reason": "haiku_error"}

        # 4. Process results
        applied = []
        proposed = []
        for imp in result.get("improvements", []):
            conf = imp.get("confidence", 0)
            if conf >= 7:
                await self._apply_improvement(imp, session_id)
                applied.append(imp["skill_name"])
            elif conf >= 4:
                await self._propose_improvement(imp, session_id)
                proposed.append(imp["skill_name"])

        new_skills = []
        for pat in result.get("new_patterns", []):
            conf = pat.get("confidence", 0)
            if conf >= 7:
                await self._create_learned_skill(pat, session_id)
                new_skills.append(pat["name"])

        logger.info(
            "Session %s analysis: %d applied, %d proposed, %d new skills",
            session_id, len(applied), len(proposed), len(new_skills),
        )
        return {"applied": applied, "proposed": proposed, "new_skills": new_skills}

    # ── Data fetching ────────────────────────────────────────────

    async def _count_messages(self, session_id: str) -> int:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def _get_session_messages(self, session_id: str) -> list[dict]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT seq, type, name, content
                   FROM messages WHERE session_id = ?
                   ORDER BY seq""",
                (session_id,),
            )
            return [dict(r) for r in await cursor.fetchall()]

    async def _get_session_skills(self, session_id: str) -> list[str]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT DISTINCT skill_name FROM skill_usage WHERE session_id = ?",
                (session_id,),
            )
            return [r[0] for r in await cursor.fetchall()]

    async def _get_skills_feedback(self, skill_names: list[str]) -> dict:
        if not skill_names:
            return {}
        placeholders = ",".join("?" for _ in skill_names)
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                f"""SELECT skill_name, rating, comment
                    FROM skill_feedback
                    WHERE skill_name IN ({placeholders})
                    ORDER BY created_at DESC LIMIT 20""",
                skill_names,
            )
            rows = await cursor.fetchall()
            result: dict[str, list] = {}
            for r in rows:
                result.setdefault(r[0], []).append({"rating": r[1], "comment": r[2]})
            return result

    # ── Prompt & Haiku ────────────────────────────────────────────

    def _build_prompt(
        self,
        messages: list[dict],
        skills_used: list[str],
        feedback: dict,
    ) -> str:
        # Format messages: [{seq}] {type}/{name}: {content[:2000]}
        # Cap at last 200 messages
        lines = []
        for m in messages[-200:]:
            content = (m.get("content") or "")[:2000]
            if not content.strip():
                continue
            name = m.get("name") or ""
            prefix = f"[{m['seq']}] {m['type']}"
            if name:
                prefix += f"/{name}"
            lines.append(f"{prefix}: {content}")
        msg_text = "\n".join(lines)

        skills_text = ", ".join(skills_used)

        fb_lines = []
        for skill, entries in feedback.items():
            for e in entries[:3]:
                fb_lines.append(f"  {skill}: rating={e['rating']} — {e['comment'][:200]}")
        fb_text = "\n".join(fb_lines) if fb_lines else "No existing feedback"

        return ANALYSIS_PROMPT.format(
            messages=msg_text,
            skills_used=skills_text,
            existing_feedback=fb_text,
        )

    async def _call_haiku(self, prompt: str) -> dict | None:
        """Call Haiku following main_server.py pattern (env-based config)."""
        import httpx

        api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("SessionLearner: no API key available")
            return None

        model = os.getenv("MODEL", "claude-haiku-4-5-20251001")
        base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{base_url}/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": 4000,
                        "system": "You analyze AI agent sessions to improve skills. Return ONLY valid JSON.",
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["content"][0]["text"]

                # Strip markdown code fences if present
                if text.startswith("```"):
                    lines = text.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    text = "\n".join(lines)

                return json.loads(text)
        except Exception as e:
            logger.error("SessionLearner Haiku call failed: %s", e)
            return None

    # ── Apply / Propose / Create ──────────────────────────────────

    async def _apply_improvement(self, imp: dict, session_id: str) -> None:
        """Auto-apply a high-confidence skill improvement.

        Delegates to DBSkillFeedbackManager.create_version() for proper backup
        and version tracking (reuses existing skill_versions table).
        """
        skill_name = imp["skill_name"]
        suggested_fix = imp["suggested_fix"]
        skill_dir = self.data_root / "shared-skills" / skill_name
        skill_file = skill_dir / "SKILL.md"

        if not skill_file.exists():
            return

        old_content = skill_file.read_text()
        from_version = self._extract_version(old_content)

        # Try to create a DB-tracked version; fall back to direct write
        to_version = "unknown"
        try:
            from src.skill_feedback import DBSkillFeedbackManager
            mgr = DBSkillFeedbackManager(self.db)
            version_result = await mgr.create_version(
                skill_name=skill_name,
                new_content=suggested_fix,
                change_summary=imp.get("issue", "")[:200],
                created_by="system",
                skills_dir=str(self.data_root / "shared-skills"),
            )
            if version_result is not None:
                to_version = str(version_result["version"])
        except Exception as e:
            logger.error("create_version failed for %s: %s", skill_name, e)

        # Always update SKILL.md with the new content.
        # Back up old content first (create_version may or may not have done it).
        backup_path = skill_dir / f"SKILL_backup_v{from_version}.md"
        if not backup_path.exists():
            backup_path.write_text(old_content)
        skill_file.write_text(suggested_fix)

        if to_version == "unknown":
            to_version = str(len(list(skill_dir.glob("SKILL_backup_v*.md"))))

        # Compute baseline composite score for future degradation detection
        baseline = await self._compute_baseline_composite(skill_name)

        # Create evolution_log entry
        await self.store.create_log(
            skill_name=skill_name,
            from_version=from_version,
            to_version=to_version,
            source="session_learner",
            evolve_reason=imp.get("issue", "")[:500],
            baseline_composite=baseline,
        )

        if self.on_skill_changed:
            self.on_skill_changed()

        logger.info("Applied improvement to %s: v%s → v%s", skill_name, from_version, to_version)

    async def _propose_improvement(self, imp: dict, session_id: str) -> None:
        """Save a medium-confidence (4-6) improvement as a proposal in evolution_log."""
        await self.store.create_log(
            skill_name=imp["skill_name"],
            from_version="—",
            to_version="—",
            source="session_learner",
            evolve_reason=imp.get("issue", "")[:500],
            proposed_content=imp.get("suggested_fix", ""),
            status="proposed",
        )
        logger.info("Proposed improvement for %s", imp["skill_name"])

    async def _create_learned_skill(self, pat: dict, session_id: str) -> None:
        """Create a new learned skill from a discovered pattern."""
        name = pat["name"].strip().lower().replace(" ", "-")
        description = pat.get("description", "")
        skill_dir = self.data_root / "shared-skills" / name

        # Check for name collision
        if skill_dir.exists():
            name = f"{name}-learned"
            skill_dir = self.data_root / "shared-skills" / name
        if skill_dir.exists():
            logger.warning("Skill name conflict for %s, skipping", name)
            return

        skill_dir.mkdir(parents=True, exist_ok=True)

        content = pat.get("skill_content", "")
        if not content:
            content = f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{description}"

        (skill_dir / "SKILL.md").write_text(content)

        # Write skill-meta.json
        meta = {"owner": "system", "description": description, "source": "learned"}
        (skill_dir / "skill-meta.json").write_text(json.dumps(meta, indent=2))

        # Register in DB via SkillManager
        if self.skill_manager is not None:
            await self.skill_manager.register_skill(
                skill_name=name,
                source="learned",
                owner_id="system",
                description=description,
                path=str(skill_dir),
            )

        # Create evolution_log entry
        await self.store.create_log(
            skill_name=name,
            from_version="0",
            to_version="1.0",
            source="session_learner",
            evolve_reason=f"New pattern: {description[:500]}",
        )

        if self.on_skill_changed:
            self.on_skill_changed()

        logger.info("Created learned skill: %s", name)

    async def _compute_baseline_composite(self, skill_name: str) -> float:
        """Compute pre-evolution baseline composite score (7 days before now)."""
        now = int(time.time())
        period_start = now - 7 * 86400

        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT COUNT(*) / 7.0 FROM skill_usage
                   WHERE skill_name = ? AND created_at BETWEEN ? AND ?""",
                (skill_name, period_start, now),
            )
            row = await cursor.fetchone()
            baseline_daily = max(row[0] if row and row[0] else 0, 1)

            cursor = await conn.execute(
                "SELECT AVG(rating) FROM skill_feedback WHERE skill_name = ?",
                (skill_name,),
            )
            row = await cursor.fetchone()
            avg_rating = row[0] if row and row[0] else 0.0

        session_success_rate = 0.8

        from src.evolution_evaluator import W_RATING, W_USAGE, W_SUCCESS
        usage_trend_ratio = 1.0
        return round(
            W_RATING * (avg_rating / 5.0)
            + W_USAGE * usage_trend_ratio
            + W_SUCCESS * session_success_rate,
            4,
        )

    @staticmethod
    def _extract_version(content: str) -> str:
        """Extract version from SKILL.md frontmatter."""
        for line in content.split("\n"):
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip()
        return "1.0"
