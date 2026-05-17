"""Semantic search — FTS5 full-text search (Phase 1).

Embedding vector search is reserved as a Phase 2 optional extension.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.database import Database

logger = logging.getLogger(__name__)


def anonymize_summary(summary: str) -> str:
    """Remove personally identifiable info from session summaries."""
    # Strip file paths
    summary = re.sub(r'[/\\][\w./\\-]+\.(py|md|txt|xlsx|pdf|csv|json)', '<path>', summary)
    # Strip user IDs / usernames
    summary = re.sub(r'(user_id|user)["\s:=]+["\w-]+', 'user: <anonymized>', summary, flags=re.IGNORECASE)
    # Strip session IDs
    summary = re.sub(r'sess_[a-f0-9]{8,}', '<session>', summary)
    return summary


class SemanticSearch:
    """FTS5 full-text search over session summaries and Wiki pages."""

    def __init__(self, db: "Database") -> None:
        self.db = db

    async def search_similar_sessions(
        self, query: str, top_k: int = 3, exclude_user: str | None = None
    ) -> list[dict[str, Any]]:
        """Search for similar past sessions using FTS5."""
        async with self.db.connection() as conn:
            search_terms = query.replace('"', '').strip()
            if not search_terms:
                return []

            candidate_ids = []
            try:
                cursor = await conn.execute(
                    """SELECT session_id, rank FROM session_summary_fts
                       WHERE session_summary_fts MATCH ?
                       ORDER BY rank
                       LIMIT 50""",
                    (search_terms,),
                )
                rows = await cursor.fetchall()
                candidate_ids = [r[0] for r in rows]
            except Exception:
                pass  # FTS5 index may not be populated yet

            if not candidate_ids:
                return []

            placeholders = ",".join("?" for _ in candidate_ids)
            where = f"session_id IN ({placeholders})"
            if exclude_user:
                where += " AND user_id != ?"
                params = [*candidate_ids, exclude_user]
            else:
                params = candidate_ids

            cursor = await conn.execute(
                f"SELECT session_id, summary, user_id, created_at FROM session_summaries WHERE {where} LIMIT ?",
                (*params, top_k),
            )
            rows = await cursor.fetchall()

        return [
            {
                "session_id": r[0],
                "summary": anonymize_summary(r[1]),
                "user_id": "other user",
                "created_at": r[3],
            }
            for r in rows
        ]

    async def search_wiki_pages(
        self, query: str, top_k: int = 2
    ) -> list[dict[str, Any]]:
        """Search Wiki pages by keyword (FTS5)."""
        async with self.db.connection() as conn:
            search_terms = query.replace('"', '').strip()
            if not search_terms:
                return []

            try:
                cursor = await conn.execute(
                    """SELECT wp.id, wp.title, wp.body, wp.confidence, wp.validation_count
                       FROM wiki_fts
                       JOIN wiki_pages wp ON wiki_fts.rowid = wp.rowid
                       WHERE wiki_fts MATCH ?
                         AND wp.status = 'published'
                       ORDER BY rank
                       LIMIT ?""",
                    (search_terms, top_k),
                )
                rows = await cursor.fetchall()
            except Exception:
                return []

        return [
            {
                "id": r[0],
                "title": r[1],
                "body_preview": r[2][:300] if r[2] else "",
                "confidence": r[3],
                "validation_count": r[4],
            }
            for r in rows
        ]
