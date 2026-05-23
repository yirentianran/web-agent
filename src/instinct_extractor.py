"""Instinct extraction, clustering, and skill generation — the core evolution pipeline."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are analyzing agent execution events to find patterns for improvement.

Given a set of observation events from agent sessions, identify behavioral patterns that could become "instincts" — atomic learned behaviors.

For each pattern found, output a JSON object with:
- domain: "tool_usage" or "task_orchestration"
- normalized_trigger: a short label (3-6 words, English or Chinese) used to group similar patterns across batches. Same concept = same label. Examples: "large-file-read", "grep-before-edit", "multi-step-refactor", "大文件读取策略"
- trigger: full description of when this pattern applies
- action: specific behavior to adopt
- confidence: 0.3 (initial)

Return a JSON array. If no patterns found, return [].

Events:
{events}"""

GENERATION_PROMPT = """You are evolving a skill definition for an AI agent system.

Given these learned instincts that all apply to the skill "{skill_name}", update the SKILL.md to incorporate them.

Current SKILL.md:
```markdown
{current_skill}
```

Instincts to incorporate:
{instincts}

Return the complete updated SKILL.md content. Keep the existing structure. Add instinct-driven guidance where it fits naturally. Do not add explanations outside the markdown."""


class InstinctStore:
    """CRUD for instincts table."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def upsert(
        self,
        *,
        domain: str,
        normalized_trigger: str,
        trigger: str,
        action: str,
        confidence: float = 0.3,
        evidence_json: str = "",
    ) -> int:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT id, confidence, source_count, unique_user_count
                   FROM instincts
                   WHERE normalized_trigger = ? AND action = ? AND domain = ?""",
                (normalized_trigger, action, domain),
            )
            existing = await cursor.fetchone()

            if existing:
                new_id = existing[0]
                new_source_count = existing[2] + 1
                new_confidence = min(0.9, existing[1] + 0.05)
                await conn.execute(
                    """UPDATE instincts
                       SET source_count = ?, confidence = ?, updated_at = ?
                       WHERE id = ?""",
                    (new_source_count, new_confidence, time.time(), new_id),
                )
            else:
                cursor = await conn.execute(
                    """INSERT INTO instincts
                       (domain, normalized_trigger, trigger, action, confidence,
                        source_count, evidence_json)
                       VALUES (?, ?, ?, ?, ?, 1, ?)""",
                    (domain, normalized_trigger, trigger, action, confidence, evidence_json),
                )
                new_id = cursor.lastrowid
        return new_id

    async def adjust_confidence(self, instinct_id: int, delta: float) -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                """UPDATE instincts
                   SET confidence = MAX(0.1, MIN(0.9, confidence + ?)),
                       scope = CASE WHEN confidence + ? < 0.3 THEN 'deprecated' ELSE scope END,
                       updated_at = ?
                   WHERE id = ?""",
                (delta, delta, time.time(), instinct_id),
            )

    async def get_active(self, domain: str = "") -> list[dict[str, Any]]:
        async with self.db.connection() as conn:
            if domain:
                cursor = await conn.execute(
                    """SELECT id, domain, normalized_trigger, trigger, action,
                              confidence, source_count, unique_user_count, evidence_json
                       FROM instincts WHERE scope = 'active' AND domain = ?
                       ORDER BY confidence DESC""",
                    (domain,),
                )
            else:
                cursor = await conn.execute(
                    """SELECT id, domain, normalized_trigger, trigger, action,
                              confidence, source_count, unique_user_count, evidence_json
                       FROM instincts WHERE scope = 'active'
                       ORDER BY confidence DESC""",
                )
            rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "domain": r[1], "normalized_trigger": r[2],
                "trigger": r[3], "action": r[4], "confidence": r[5],
                "source_count": r[6], "unique_user_count": r[7],
                "evidence_json": r[8],
            }
            for r in rows
        ]

    async def list_instincts(
        self, *, domain: str = "", scope: str = "", page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        conditions = []
        params: list[Any] = []
        if domain:
            conditions.append("domain = ?")
            params.append(domain)
        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        async with self.db.connection() as conn:
            count_row = await conn.execute_fetchall(
                f"SELECT COUNT(*) FROM instincts {where}", params
            )
            total = count_row[0][0] if count_row else 0

            offset = (page - 1) * page_size
            cursor = await conn.execute(
                f"""SELECT id, domain, normalized_trigger, trigger, action,
                           confidence, source_count, unique_user_count, scope, created_at
                    FROM instincts {where}
                    ORDER BY confidence DESC
                    LIMIT ? OFFSET ?""",
                params + [page_size, offset],
            )
            rows = await cursor.fetchall()

        return {
            "items": [
                {
                    "id": r[0], "domain": r[1], "normalized_trigger": r[2],
                    "trigger": r[3], "action": r[4], "confidence": r[5],
                    "source_count": r[6], "unique_user_count": r[7],
                    "scope": r[8], "created_at": r[9],
                }
                for r in rows
            ],
            "total": total,
            "page": page,
        }

    async def get_by_id(self, instinct_id: int) -> dict[str, Any] | None:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT id, domain, normalized_trigger, trigger, action,
                          confidence, source_count, unique_user_count, scope,
                          evidence_json, created_at, updated_at
                   FROM instincts WHERE id = ?""",
                (instinct_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0], "domain": row[1], "normalized_trigger": row[2],
                "trigger": row[3], "action": row[4], "confidence": row[5],
                "source_count": row[6], "unique_user_count": row[7],
                "scope": row[8], "evidence_json": row[9],
                "created_at": row[10], "updated_at": row[11],
            }

    async def get_active_count(self) -> int:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM instincts WHERE scope = 'active'"
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def link_to_evolution(self, instinct_ids: list[int], evolution_id: int) -> None:
        async with self.db.connection() as conn:
            for iid in instinct_ids:
                await conn.execute(
                    "UPDATE instincts SET source_evolution_id = ? WHERE id = ?",
                    (evolution_id, iid),
                )


class InstinctExtractor:
    """Periodic scanner: reads observations, extracts instincts via Haiku,
    clusters by normalized_trigger, generates SKILL.md changes."""

    def __init__(
        self,
        db: Any,
        obs_store: Any,
        instinct_store: InstinctStore,
        evolution_store: Any,
        skill_manager: Any,
        data_root: str,
    ) -> None:
        self.db = db
        self.obs_store = obs_store
        self.instinct_store = instinct_store
        self.evolution_store = evolution_store
        self.skill_manager = skill_manager
        self.data_root = data_root
        self._last_scan_at = time.time()
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def _filter_significant_events(
        self, events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Rule-based filtering: find events worth analyzing."""
        if len(events) < 3:
            return []

        # Group by session
        by_session: dict[str, list[dict[str, Any]]] = {}
        for e in events:
            by_session.setdefault(e["session_id"], []).append(e)

        significant: list[dict[str, Any]] = []

        for sid, sess_events in by_session.items():
            # Pattern 1: consecutive failures of same tool (2+)
            for i in range(len(sess_events) - 1):
                a, b = sess_events[i], sess_events[i + 1]
                if (
                    a["event_type"] == "tool_call_end"
                    and b["event_type"] == "tool_call_end"
                    and a["tool_name"] == b["tool_name"]
                    and not a.get("success")
                    and not b.get("success")
                ):
                    significant.extend([a, b])

            # Pattern 2: user_correct — grab preceding tool_call_end
            for i, e in enumerate(sess_events):
                if e["event_type"] in ("user_correct", "user_retry"):
                    for j in range(i - 1, -1, -1):
                        if sess_events[j]["event_type"] == "tool_call_end":
                            significant.append(sess_events[j])
                            break
                    significant.append(e)

        return significant

    def _find_repeated_sequences(
        self, events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Pattern 3: same tool sequence appearing in 3+ sessions."""
        from collections import Counter

        # Build session tool sequences
        by_session: dict[str, list[str]] = {}
        for e in events:
            if e["event_type"] == "tool_call_start" and e.get("tool_name"):
                by_session.setdefault(e["session_id"], []).append(e["tool_name"])

        # Count 2-tool and 3-tool sequences
        seq_counter: Counter = Counter()
        session_seqs: dict[str, list[tuple[str, ...]]] = {}
        for sid, tools in by_session.items():
            seqs = []
            for w in (2, 3):
                for i in range(len(tools) - w + 1):
                    seq = tuple(tools[i : i + w])
                    seq_counter[seq] += 1
                    seqs.append(seq)
            session_seqs[sid] = seqs

        # Collect events from sessions that contain repeated sequences
        repeated_sids: set[str] = set()
        for seq, count in seq_counter.items():
            if count >= 3:
                for sid, seqs in session_seqs.items():
                    if seq in seqs:
                        repeated_sids.add(sid)

        return [e for e in events if e["session_id"] in repeated_sids]

    async def run_once(self) -> dict[str, Any]:
        """Single extraction cycle. Returns summary dict."""
        if not self._api_key:
            logger.warning("ANTHROPIC_API_KEY not set, skipping extraction")
            return {"extracted": 0, "clusters": 0, "applied": 0, "proposed": 0}

        # 1. Check event threshold
        new_count = await self.obs_store.count_since(self._last_scan_at)
        if new_count < 30:
            return {"extracted": 0, "clusters": 0, "applied": 0, "proposed": 0, "skipped": True}

        # 2. Get new events
        events = await self.obs_store.get_new_since(self._last_scan_at)
        self._last_scan_at = time.time()

        if not events:
            return {"extracted": 0, "clusters": 0, "applied": 0, "proposed": 0}

        # 3. Filter significant events
        sig_events = self._filter_significant_events(events)
        seq_events = self._find_repeated_sequences(events)
        all_candidates = sig_events + seq_events

        if not all_candidates:
            return {"extracted": 0, "clusters": 0, "applied": 0, "proposed": 0}

        # 4. Call Haiku to extract instincts
        events_text = json.dumps(
            [
                {
                    "session": e["session_id"][:8],
                    "type": e["event_type"],
                    "tool": e.get("tool_name", ""),
                    "ok": e.get("success"),
                    "error": e.get("error_message", ""),
                }
                for e in all_candidates
            ],
            ensure_ascii=False,
        )
        prompt = EXTRACTION_PROMPT.format(events=events_text)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 2000,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            data = resp.json()
            candidates = json.loads(data["content"][0]["text"])

        if not isinstance(candidates, list):
            candidates = []

        # 5. Upsert instincts (dedup by normalized_trigger + action)
        extracted = 0
        for c in candidates:
            if not all(k in c for k in ("domain", "normalized_trigger", "trigger", "action")):
                continue
            await self.instinct_store.upsert(
                domain=c["domain"],
                normalized_trigger=c["normalized_trigger"],
                trigger=c["trigger"],
                action=c["action"],
                confidence=c.get("confidence", 0.3),
                evidence_json=json.dumps(
                    {"event_ids": [e["id"] for e in all_candidates[:10]]}
                ),
            )
            extracted += 1

        # 6. Cluster by normalized_trigger within each domain
        result = {"extracted": extracted, "clusters": 0, "applied": 0, "proposed": 0}

        for domain in ("tool_usage", "task_orchestration"):
            instincts = await self.instinct_store.get_active(domain=domain)

            # Group by normalized_trigger
            clusters: dict[str, list[dict[str, Any]]] = {}
            for inst in instincts:
                clusters.setdefault(inst["normalized_trigger"], []).append(inst)

            for norm_trigger, cluster in clusters.items():
                if len(cluster) < 2:
                    continue

                avg_confidence = sum(i["confidence"] for i in cluster) / len(cluster)
                if avg_confidence < 0.5:
                    continue

                # 7. Determine target skill
                target_skill = self._infer_target_skill(cluster)
                if not target_skill:
                    continue

                # 8. Generate SKILL.md change
                skill_content = self._read_current_skill(target_skill)
                if not skill_content:
                    continue

                gen_prompt = GENERATION_PROMPT.format(
                    skill_name=target_skill,
                    current_skill=skill_content,
                    instincts="\n".join(
                        f"- [{i['normalized_trigger']}] {i['trigger']} → {i['action']}"
                        for i in cluster
                    ),
                )

                async with httpx.AsyncClient() as client:
                    gen_resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": self._api_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json={
                            "model": "claude-haiku-4-5-20251001",
                            "max_tokens": 4000,
                            "messages": [{"role": "user", "content": gen_prompt}],
                        },
                        timeout=60.0,
                    )
                    gen_resp.raise_for_status()
                    gen_data = gen_resp.json()
                    new_content = gen_data["content"][0]["text"]

                # Strip markdown fences if present
                from src.text_utils import strip_markdown_fences
                new_content = strip_markdown_fences(new_content)

                # 9. Apply or propose
                instinct_ids = [i["id"] for i in cluster]
                result["clusters"] += 1

                if avg_confidence >= 0.7:
                    await self._apply_skill_change(
                        target_skill, new_content, instinct_ids, cluster
                    )
                    result["applied"] += 1
                else:
                    await self._propose_skill_change(
                        target_skill, new_content, instinct_ids, cluster
                    )
                    result["proposed"] += 1

        return result

    def _infer_target_skill(self, cluster: list[dict[str, Any]]) -> str:
        """Heuristic: map instinct cluster to a skill name."""
        from pathlib import Path
        skills_dir = Path(self.data_root) / "shared-skills"
        if not skills_dir.exists():
            return ""
        existing = [
            d.name for d in skills_dir.iterdir()
            if d.is_dir() and (d / "SKILL.md").exists()
        ]
        return existing[0] if existing else ""

    def _read_current_skill(self, skill_name: str) -> str:
        from pathlib import Path
        skill_file = Path(self.data_root) / "shared-skills" / skill_name / "SKILL.md"
        if not skill_file.exists():
            return ""
        return skill_file.read_text()

    async def _apply_skill_change(
        self,
        skill_name: str,
        new_content: str,
        instinct_ids: list[int],
        cluster: list[dict[str, Any]],
    ) -> None:
        """Write new SKILL.md, archive old version, create evolution_log."""
        from pathlib import Path
        import shutil

        skill_dir = Path(self.data_root) / "shared-skills" / skill_name
        skill_file = skill_dir / "SKILL.md"

        # Archive current version
        versions_dir = skill_dir / "versions"
        versions_dir.mkdir(parents=True, exist_ok=True)
        existing = [d.name for d in versions_dir.iterdir() if d.name.startswith("v")]
        next_v = len(existing) + 1
        v_dir = versions_dir / f"v{next_v}"
        v_dir.mkdir()
        if skill_file.exists():
            shutil.copy2(skill_file, v_dir / "SKILL.md")

        # Write new content
        skill_file.write_text(new_content)

        # Create evolution log
        log = await self.evolution_store.create_log(
            skill_name=skill_name,
            from_version=f"v{next_v}",
            to_version=f"v{next_v + 1}",
            source="instinct_extractor",
            evolve_reason=f"Auto-applied cluster: {cluster[0]['normalized_trigger']}",
            proposed_content="",
            status="active",
        )

        # Link instincts to evolution
        await self.instinct_store.link_to_evolution(instinct_ids, log["id"])

    async def _propose_skill_change(
        self,
        skill_name: str,
        new_content: str,
        instinct_ids: list[int],
        cluster: list[dict[str, Any]],
    ) -> None:
        """Write proposed evolution_log entry for admin review."""
        log = await self.evolution_store.create_log(
            skill_name=skill_name,
            from_version="current",
            to_version="proposed",
            source="instinct_extractor",
            evolve_reason=f"Proposed cluster: {cluster[0]['normalized_trigger']} "
                          f"(avg confidence {sum(i['confidence'] for i in cluster) / len(cluster):.2f})",
            proposed_content=new_content,
            status="proposed",
        )
        await self.instinct_store.link_to_evolution(instinct_ids, log["id"])
