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
        """Main loop: extract recent conversations -> generate Wiki pages."""
        generated: list[str] = []

        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT DISTINCT sf.session_id, sf.skill_name, sf.rating,
                          sf.comment, sf.conversation_snippet
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
            })

        for topic, entries in topics.items():
            if len(entries) < 2:
                continue
            avg_rating = sum(e["rating"] for e in entries) / len(entries)
            if avg_rating < 4.0:
                continue

            page_id = self._generate_id(topic)
            existing = await self._get_page(page_id)

            if existing:
                await self._update_validation(page_id, len(entries))
            else:
                await self._generate_page(topic, entries, page_id)
                generated.append(topic)

        return generated

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
