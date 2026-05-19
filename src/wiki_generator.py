"""Wiki generator — auto-mines conversation data and generates Wiki pages.

All Wiki content is stored in the wiki_pages database table (no files).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.database import Database

logger = logging.getLogger(__name__)


class WikiGenerator:
    """Generates and maintains LLM Wiki pages from conversation mining.

    Wiki content lives entirely in the wiki_pages table.
    FTS5 full-text search is built directly on the same table.
    """

    def __init__(self, db: "Database") -> None:
        self.db = db

    def _generate_id(self, title: str) -> str:
        """Create a slug-based page ID from a title."""
        slug = unicodedata.normalize("NFKD", title.lower())
        slug = re.sub(r"[^\w\s-]", "", slug).strip()
        slug = re.sub(r"[-\s]+", "-", slug)
        return slug[:100] or "untitled"

    async def mine_and_generate(self, lookback_hours: int = 24) -> list[str]:
        """Main loop: extract recent conversations -> generate Wiki pages.

        Generates three types of pages based on average rating:
        - avg_rating >= 4.0: normal skill pages (status='draft')
        - 2.0 <= avg_rating < 4.0: warning pages (status='warning')
        - avg_rating < 2.0: anti-pattern pages (status='anti-pattern')
        """
        generated: list[str] = []

        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT DISTINCT sf.session_id, sf.skill_name, sf.rating,
                          sf.comment, sf.conversation_snippet, sf.user_edits
                   FROM skill_feedback sf
                   JOIN messages m ON m.session_id = sf.session_id
                   WHERE m.created_at > strftime('%s', 'now') - ?
                   ORDER BY sf.session_id""",
                (lookback_hours * 3600,),
            )
            rows = await cursor.fetchall()

        if not rows:
            return generated

        topics: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            skill = row[1]
            topics.setdefault(skill, []).append({
                "session_id": row[0],
                "rating": row[2],
                "comment": row[3],
                "snippet": row[4],
                "user_edits": row[5],
            })

        for topic, entries in topics.items():
            if len(entries) < 2:
                continue
            avg_rating = sum(e["rating"] for e in entries) / len(entries)

            page_id = self._generate_id(topic)
            existing = await self._get_page(page_id)

            if existing:
                await self._update_validation(page_id, len(entries))
            elif avg_rating >= 4.0:
                await self._generate_page(topic, entries, page_id)
                generated.append(topic)
            elif avg_rating >= 2.0:
                await self._generate_warning_page(topic, entries, page_id)
                generated.append(f"[warn] {topic}")
            else:
                await self._generate_antipattern_page(topic, entries, page_id)
                generated.append(f"[anti] {topic}")

        return generated

    async def _generate_warning_page(
        self, topic: str, entries: list[dict[str, Any]], page_id: str
    ) -> None:
        """Generate a warning-type Wiki page for below-average skills."""
        issues = self._extract_issues(entries)
        user_edits = next((e.get("user_edits") for e in entries if e.get("user_edits")), None)

        body = (
            f"# {topic} — Needs Improvement\n\n"
            f"## Status\n\n"
            f"This skill has received mixed feedback (avg_rating below 4.0).\n\n"
            f"## Reported Issues\n\n"
            f"{issues}\n"
        )
        if user_edits:
            body += f"\n## User-Suggested Fix\n\n{user_edits[:500]}\n"

        snippets = "\n".join(
            f"- Session {e['session_id']} (rating={e['rating']}): {e.get('snippet', '')}"
            for e in entries[:5]
        )
        body += f"\n## Source Sessions\n\n{snippets}\n"

        async with self.db.connection() as conn:
            await conn.execute(
                """INSERT INTO wiki_pages
                   (id, title, body, category, tags, status, confidence, validation_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (page_id, f"{topic} (needs improvement)", body, "skills", "[]", "warning", 0.6, len(entries)),
            )
            await conn.commit()

    async def _generate_antipattern_page(
        self, topic: str, entries: list[dict[str, Any]], page_id: str
    ) -> None:
        """Generate an anti-pattern Wiki page for poorly-rated skills."""
        issues = self._extract_issues(entries)
        user_edits = next((e.get("user_edits") for e in entries if e.get("user_edits")), None)
        low_ratings = [e for e in entries if e["rating"] <= 2]
        common_complaints = self._find_common_complaints(low_ratings)

        body = (
            f"# {topic} — Anti-Pattern Warning\n\n"
            f"## Do Not Use This Skill\n\n"
            f"This skill consistently receives very low ratings (avg_rating < 2.0).\n\n"
            f"## Common Problems\n\n"
            f"{common_complaints}\n\n"
            f"## Reported Issues\n\n"
            f"{issues}\n"
        )
        if user_edits:
            body += f"\n## Community Fix\n\n{user_edits[:500]}\n"

        snippets = "\n".join(
            f"- Session {e['session_id']} (rating={e['rating']}): {e.get('comment', '')[:100]}"
            for e in entries[:5]
        )
        body += f"\n## Feedback Summary\n\n{snippets}\n"

        async with self.db.connection() as conn:
            await conn.execute(
                """INSERT INTO wiki_pages
                   (id, title, body, category, tags, status, confidence, validation_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (page_id, f"{topic} (anti-pattern)", body, "anti-patterns", "[]", "anti-pattern", 0.9, len(entries)),
            )
            await conn.commit()

    def _extract_issues(self, entries: list[dict[str, Any]]) -> str:
        """Extract and format issues from low-rated feedback entries."""
        issues = []
        for e in entries:
            if e["rating"] < 4 and e.get("comment"):
                issues.append(f"- (rating={e['rating']}) {e['comment'][:200]}")
        if not issues:
            return "No specific issues documented."
        return "\n".join(issues[:10])

    def _find_common_complaints(self, low_ratings: list[dict[str, Any]]) -> str:
        """Find recurring themes in very low-rated feedback."""
        from collections import Counter

        keywords = ["slow", "crash", "error", "wrong", "broken", "missing", "timeout", "fail"]
        complaints: Counter = Counter()

        for e in low_ratings:
            comment = (e.get("comment") or "").lower()
            for kw in keywords:
                if kw in comment:
                    complaints[kw] += 1

        if not complaints:
            return "Multiple users reported severe issues (see feedback below)."

        lines = []
        for word, count in complaints.most_common(5):
            lines.append(f"- '{word}' mentioned {count} time(s)")
        return "\n".join(lines)

    async def _get_page(self, page_id: str) -> dict[str, Any] | None:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM wiki_pages WHERE id = ?", (page_id,)
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def _generate_page(
        self, topic: str, entries: list[dict[str, Any]], page_id: str
    ) -> None:
        snippets = "\n".join(
            f"- Session {e['session_id']}: {e.get('snippet', '')}"
            for e in entries[:5]
        )
        body = (
            f"# {topic}\n\n"
            f"## Overview\n\n"
            f"Auto-generated from {len(entries)} validated sessions.\n\n"
            f"## Source Sessions\n\n"
            f"{snippets}\n"
        )
        async with self.db.connection() as conn:
            await conn.execute(
                """INSERT INTO wiki_pages
                   (id, title, body, category, tags, status, confidence, validation_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (page_id, topic, body, "skills", "[]", "draft", 0.75, len(entries)),
            )
            await conn.commit()

    async def _update_validation(self, page_id: str, count: int) -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                """UPDATE wiki_pages
                   SET validation_count = validation_count + ?,
                       updated_at = strftime('%s', 'now')
                   WHERE id = ?""",
                (count, page_id),
            )
            await conn.commit()
