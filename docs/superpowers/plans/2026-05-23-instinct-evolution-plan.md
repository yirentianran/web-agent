# Instinct Evolution 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 ECC 风格的四阶段流水线（事件捕获 → 本能提取 → 聚类生成 → 信号跟踪）替换现有的 session_learner + evolution_evaluator 进化系统。

**Architecture:** 在 agent 执行循环中埋点写入 observations 表 → 每 10 分钟批量扫描、Haiku 提取归一化本能 → 标签匹配聚类、confidence ≥ 0.7 自动写入 SKILL.md → 每日跟踪成功率和失败趋势、退化自动回滚。

**Tech Stack:** Python/FastAPI 后端 + SQLite (aiosqlite) + React/TypeScript 前端 + Haiku (via httpx to Anthropic API)

---

## 文件结构

```
新建:
  src/observation.py              — 事件捕获 API
  src/instinct_extractor.py       — 定时提取 + 聚类 + 生成
  src/evolution_signals.py        — 每日评估 + 退化回滚
  frontend/src/pages/evolution/StatsCards.tsx
  frontend/src/pages/evolution/PipelineFunnel.tsx
  frontend/src/pages/evolution/InstinctList.tsx
  frontend/src/pages/evolution/ObservationBrowser.tsx

修改:
  src/database.py                 — 新增 observations + instincts 表
  src/evolution_log.py            — 扩展查询方法
  src/collective_intelligence.py  — 精简为两个定时器
  main_server.py                  — 移除旧分析调用 + 更新 API 端点 + 埋点
  agent_server.py                 — 埋点 tool_call_start/end
  frontend/src/pages/EvolutionPage.tsx          — 重构为面板布局
  frontend/src/pages/evolution/EvolutionDetail.tsx — 增加 instinct 展示
  frontend/src/hooks/useEvolutionApi.ts         — 扩展接口
  frontend/src/App.tsx                          — 移除 EvolutionPanel 路由

移除:
  src/session_learner.py
  src/evolution_evaluator.py
  src/auto_evolve.py
  src/skill_feedback.py
  frontend/src/components/EvolutionPanel.tsx
  frontend/src/hooks/useSkillEvolutionApi.ts
```

---

### Task 1: 数据库 — 新增 observations + instincts 表

**Files:**
- Modify: `src/database.py:239-271` (在 evolution_log 表定义之后)

- [ ] **Step 1: 添加 observations 和 instincts 的 CREATE TABLE 语句和索引**

在 `evolution_log` 和 `skill_eval_snapshots` 建表语句之后（约 line 271），添加：

```python
# ---- observations ----
await conn.execute("""
    CREATE TABLE IF NOT EXISTS observations (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id      TEXT    NOT NULL,
        user_id         TEXT    NOT NULL,
        event_type      TEXT    NOT NULL,
        tool_name       TEXT,
        tool_input_summary  TEXT,
        tool_output_summary TEXT,
        success         INTEGER,
        error_message   TEXT,
        duration_ms     INTEGER,
        created_at      REAL    NOT NULL DEFAULT (strftime('%s', 'now'))
    )
""")
await conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_obs_session
    ON observations(session_id)
""")
await conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_obs_event_type
    ON observations(event_type)
""")
await conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_obs_created_at
    ON observations(created_at)
""")

# ---- instincts ----
await conn.execute("""
    CREATE TABLE IF NOT EXISTS instincts (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        domain              TEXT    NOT NULL,
        normalized_trigger  TEXT    NOT NULL,
        trigger             TEXT    NOT NULL,
        action              TEXT    NOT NULL,
        confidence          REAL    NOT NULL DEFAULT 0.3,
        source_count        INTEGER NOT NULL DEFAULT 1,
        unique_user_count   INTEGER NOT NULL DEFAULT 1,
        scope               TEXT    NOT NULL DEFAULT 'active',
        source_evolution_id INTEGER,
        evidence_json       TEXT,
        created_at          REAL    NOT NULL DEFAULT (strftime('%s', 'now')),
        updated_at          REAL    NOT NULL DEFAULT (strftime('%s', 'now'))
    )
""")
await conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_instincts_domain
    ON instincts(domain)
""")
await conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_instincts_norm_trigger
    ON instincts(normalized_trigger, domain)
""")
await conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_instincts_scope
    ON instincts(scope)
""")
```

- [ ] **Step 2: 添加 migrate_v6() 方法，在 init() 调用链中注册**

在 `migrate_collective_intelligence()` 之后添加：

```python
async def migrate_v6(self) -> None:
    """Add observations and instincts tables for instinct-based evolution."""
    async with self.connection() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      TEXT    NOT NULL,
                user_id         TEXT    NOT NULL,
                event_type      TEXT    NOT NULL,
                tool_name       TEXT,
                tool_input_summary  TEXT,
                tool_output_summary TEXT,
                success         INTEGER,
                error_message   TEXT,
                duration_ms     INTEGER,
                created_at      REAL    NOT NULL DEFAULT (strftime('%s', 'now'))
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_obs_event_type ON observations(event_type)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_obs_created_at ON observations(created_at)"
        )
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS instincts (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                domain              TEXT    NOT NULL,
                normalized_trigger  TEXT    NOT NULL,
                trigger             TEXT    NOT NULL,
                action              TEXT    NOT NULL,
                confidence          REAL    NOT NULL DEFAULT 0.3,
                source_count        INTEGER NOT NULL DEFAULT 1,
                unique_user_count   INTEGER NOT NULL DEFAULT 1,
                scope               TEXT    NOT NULL DEFAULT 'active',
                source_evolution_id INTEGER,
                evidence_json       TEXT,
                created_at          REAL    NOT NULL DEFAULT (strftime('%s', 'now')),
                updated_at          REAL    NOT NULL DEFAULT (strftime('%s', 'now'))
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_instincts_domain ON instincts(domain)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_instincts_norm_trigger ON instincts(normalized_trigger, domain)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_instincts_scope ON instincts(scope)"
        )
```

在 `init()` 方法中的 `_migrate_drop_session_fks()` 调用之后、其他 migration 调用附近的合适位置，添加：

```python
await self.migrate_v6()
```

- [ ] **Step 3: 验证迁移**

```bash
cd /Users/mac/Documents/Projects/web-agent && uv run python -c "
import asyncio
from pathlib import Path
from src.database import Database

async def test():
    db = Database(Path('data/web-agent.db'))
    await db.init()
    async with db.connection() as conn:
        tables = await conn.execute_fetchall(
            \"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\"
        )
        print([r[0] for r in tables])
        assert 'observations' in [r[0] for r in tables]
        assert 'instincts' in [r[0] for r in tables]
        print('PASS')
    await db.close()

asyncio.run(test())
"
```

- [ ] **Step 4: Commit**

```bash
git add src/database.py
git commit -m "feat: add observations and instincts tables for instinct evolution"
```

---

### Task 2: observation 模块 — 事件捕获 API

**Files:**
- Create: `src/observation.py`
- Test: `tests/unit/test_observation.py`

- [ ] **Step 1: 创建 observation.py**

```python
"""Event capture for instinct evolution. Writes structured observations to SQLite."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class ObservationStore:
    """Write and query tool-call and user-interaction events."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def record(
        self,
        *,
        session_id: str,
        user_id: str,
        event_type: str,
        tool_name: str = "",
        tool_input_summary: str = "",
        tool_output_summary: str = "",
        success: bool | None = None,
        error_message: str = "",
        duration_ms: int = 0,
    ) -> int:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """INSERT INTO observations
                   (session_id, user_id, event_type, tool_name,
                    tool_input_summary, tool_output_summary,
                    success, error_message, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, user_id, event_type, tool_name,
                    tool_input_summary[:500], tool_output_summary[:500],
                    1 if success else 0 if success is not None else None,
                    error_message[:500], duration_ms,
                ),
            )
            return cursor.lastrowid

    async def count_since(self, since_timestamp: float) -> int:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM observations WHERE created_at > ?",
                (since_timestamp,),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_new_since(
        self, since_timestamp: float, limit: int = 500
    ) -> list[dict[str, Any]]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """SELECT id, session_id, user_id, event_type, tool_name,
                          tool_input_summary, tool_output_summary,
                          success, error_message, duration_ms, created_at
                   FROM observations
                   WHERE created_at > ?
                   ORDER BY created_at ASC
                   LIMIT ?""",
                (since_timestamp, limit),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "id": r[0], "session_id": r[1], "user_id": r[2],
                    "event_type": r[3], "tool_name": r[4],
                    "tool_input_summary": r[5], "tool_output_summary": r[6],
                    "success": bool(r[7]) if r[7] is not None else None,
                    "error_message": r[8], "duration_ms": r[9], "created_at": r[10],
                }
                for r in rows
            ]

    async def list_events(
        self,
        *,
        session_id: str = "",
        event_type: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        conditions = []
        params: list[Any] = []
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        async with self.db.connection() as conn:
            count_row = await conn.execute_fetchall(
                f"SELECT COUNT(*) FROM observations {where}", params
            )
            total = count_row[0][0] if count_row else 0

            offset = (page - 1) * page_size
            cursor = await conn.execute(
                f"""SELECT id, session_id, user_id, event_type, tool_name,
                           success, error_message, duration_ms, created_at
                    FROM observations {where}
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?""",
                params + [page_size, offset],
            )
            rows = await cursor.fetchall()

        return {
            "items": [
                {
                    "id": r[0], "session_id": r[1], "user_id": r[2],
                    "event_type": r[3], "tool_name": r[4],
                    "success": bool(r[5]) if r[5] is not None else None,
                    "error_message": r[6], "duration_ms": r[7], "created_at": r[8],
                }
                for r in rows
            ],
            "total": total,
            "page": page,
        }

    async def get_stats(self) -> dict[str, Any]:
        """Return dashboard stats: today's events, by-type breakdown."""
        now = time.time()
        today_start = now - (now % 86400)
        week_start = now - 7 * 86400

        async with self.db.connection() as conn:
            today_total = await conn.execute_fetchall(
                "SELECT COUNT(*) FROM observations WHERE created_at >= ?",
                (today_start,),
            )
            week_auto = await conn.execute_fetchall(
                """SELECT COUNT(*) FROM observations
                   WHERE created_at >= ? AND event_type = 'session_complete'""",
                (week_start,),
            )
        return {
            "today_events": today_total[0][0] if today_total else 0,
            "week_completions": week_auto[0][0] if week_auto else 0,
        }
```

- [ ] **Step 2: 创建单元测试**

```python
# tests/unit/test_observation.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.observation import ObservationStore


@pytest.mark.asyncio
async def test_record_observation():
    db = MagicMock()
    conn = AsyncMock()
    db.connection.return_value.__aenter__.return_value = conn
    cursor = AsyncMock()
    cursor.lastrowid = 42
    conn.execute.return_value = cursor

    store = ObservationStore(db)
    obs_id = await store.record(
        session_id="s1", user_id="u1", event_type="tool_call_end",
        tool_name="Read", success=True, duration_ms=150,
    )
    assert obs_id == 42
    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "INSERT INTO observations" in sql


@pytest.mark.asyncio
async def test_count_since():
    db = MagicMock()
    conn = AsyncMock()
    db.connection.return_value.__aenter__.return_value = conn
    conn.execute.return_value = AsyncMock()
    conn.execute.return_value.fetchone.return_value = (15,)

    store = ObservationStore(db)
    count = await store.count_since(1000.0)
    assert count == 15
```

- [ ] **Step 3: 运行测试**

```bash
cd /Users/mac/Documents/Projects/web-agent && uv run pytest tests/unit/test_observation.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/observation.py tests/unit/test_observation.py
git commit -m "feat: add ObservationStore for event capture"
```

---

### Task 3: 埋点 — 在 agent 执行循环中写入事件

**Files:**
- Modify: `agent_server.py` (tool_call_start/end)
- Modify: `main_server.py` (session_complete/error, user_correct/retry/interrupt)

- [ ] **Step 1: agent_server.py 中埋点 tool_call_start**

在 `_CliRunner._run_cli()` 中找到工具调用的入口点（通常是通过 SDK stream 处理 tool_use 事件的位置），在工具调用前写入：

```python
from src.observation import ObservationStore

# 在 _CliRunner 的 __init__ 或 run 方法中接收 observation_store 参数
# 在工具调用之前:
await self._obs_store.record(
    session_id=self._session_id,
    user_id=self._user_id,
    event_type="tool_call_start",
    tool_name=tool_name,
    tool_input_summary=str(tool_input)[:500],
)
```

- [ ] **Step 2: agent_server.py 中埋点 tool_call_end**

工具调用完成后：

```python
await self._obs_store.record(
    session_id=self._session_id,
    user_id=self._user_id,
    event_type="tool_call_end",
    tool_name=tool_name,
    tool_output_summary=str(tool_result)[:500],
    success=(error is None),
    error_message=str(error)[:500] if error else "",
    duration_ms=duration_ms,
)
```

- [ ] **Step 3: main_server.py 中埋点 session 生命周期**

在 `run_agent_task()` 和 `run_agent_task_container()` 的三个出口：

```python
# completed 出口（现有 line ~2349）
await obs_store.record(
    session_id=session_id, user_id=user_id,
    event_type="session_complete",
)

# error 出口（现有 line ~2379, ~2436）
await obs_store.record(
    session_id=session_id, user_id=user_id,
    event_type="session_error",
    error_message=str(error)[:500],
)
```

- [ ] **Step 4: main_server.py 中埋点用户交互事件**

在消息处理循环中（WebSocket 消息处理），检测用户行为模式：

```python
# user_correct: 用户发送新消息覆盖/重新描述之前的任务
await obs_store.record(
    session_id=session_id, user_id=user_id,
    event_type="user_correct",
)

# user_retry: 用户连续发送相似内容
await obs_store.record(
    session_id=session_id, user_id=user_id,
    event_type="user_retry",
)

# user_interrupt: 取消处理中
await obs_store.record(
    session_id=session_id, user_id=user_id,
    event_type="user_interrupt",
)
```

- [ ] **Step 5: 传递 ObservationStore 实例**

在 `main_server.py` 全局初始化处：

```python
from src.observation import ObservationStore

_obs_store = ObservationStore(_db)
```

在启动 agent 任务时传递给 agent_server 进程或容器。

- [ ] **Step 6: Commit**

```bash
git add agent_server.py main_server.py
git commit -m "feat: add observation instrumentation to agent execution loop"
```

---

### Task 4: instinct_extractor — 定时提取 + 聚类 + 生成

**Files:**
- Create: `src/instinct_extractor.py`
- Test: `tests/unit/test_instinct_extractor.py`

这是核心模块。由于代码较长，拆分为多个步骤。

- [ ] **Step 1: 创建基础结构和 InstinctStore 类**

```python
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
```

- [ ] **Step 2: 创建 InstinctExtractor 类 — 规则筛选**

```python
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
```

- [ ] **Step 3: 创建 run_once — Haiku 提取 + 聚类 + 生成**

```python
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
        """Heuristic: map instinct cluster to a skill name.
        For tool_usage, check if any instinct evidence mentions a specific tool
        that maps to a known skill."""
        # Check evidence for skill references (simplified: return most common domain skill)
        from pathlib import Path
        skills_dir = Path(self.data_root) / "shared-skills"
        if not skills_dir.exists():
            return ""
        existing = [
            d.name for d in skills_dir.iterdir()
            if d.is_dir() and (d / "SKILL.md").exists()
        ]
        # For now, target the most-used skill. Future: use evidence to determine.
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
        from pathlib import Path, shutil
        from datetime import UTC, datetime

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
```

- [ ] **Step 4: 单元测试**

```python
# tests/unit/test_instinct_extractor.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.instinct_extractor import InstinctStore, InstinctExtractor


class TestInstinctStore:
    @pytest.mark.asyncio
    async def test_upsert_new(self):
        db = MagicMock()
        conn = AsyncMock()
        db.connection.return_value.__aenter__.return_value = conn
        conn.execute.return_value = AsyncMock()
        conn.execute.return_value.fetchone.return_value = None
        conn.execute.return_value.lastrowid = 1

        store = InstinctStore(db)
        new_id = await store.upsert(
            domain="tool_usage", normalized_trigger="grep-first",
            trigger="when editing files", action="run Grep before Edit",
        )
        assert new_id == 1

    @pytest.mark.asyncio
    async def test_upsert_existing_merges(self):
        db = MagicMock()
        conn = AsyncMock()
        db.connection.return_value.__aenter__.return_value = conn
        conn.execute.return_value = AsyncMock()
        conn.execute.return_value.fetchone.return_value = (5, 0.5, 3, 2)
        conn.execute.return_value.lastrowid = 99

        store = InstinctStore(db)
        new_id = await store.upsert(
            domain="tool_usage", normalized_trigger="grep-first",
            trigger="when editing", action="run Grep before Edit",
        )
        assert new_id == 5  # returns existing ID

    @pytest.mark.asyncio
    async def test_adjust_confidence(self):
        db = MagicMock()
        conn = AsyncMock()
        db.connection.return_value.__aenter__.return_value = conn

        store = InstinctStore(db)
        await store.adjust_confidence(1, -0.1)
        conn.execute.assert_called_once()


class TestInstinctExtractor:
    def test_filter_consecutive_failures(self):
        extractor = InstinctExtractor(
            db=MagicMock(), obs_store=MagicMock(),
            instinct_store=MagicMock(), evolution_store=MagicMock(),
            skill_manager=MagicMock(), data_root="/tmp",
        )
        events = [
            {"id": 1, "session_id": "s1", "event_type": "tool_call_end",
             "tool_name": "Read", "success": False},
            {"id": 2, "session_id": "s1", "event_type": "tool_call_end",
             "tool_name": "Read", "success": False},
            {"id": 3, "session_id": "s1", "event_type": "tool_call_end",
             "tool_name": "Write", "success": True},
        ]
        result = extractor._filter_significant_events(events)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2

    def test_filter_skips_isolated_failures(self):
        extractor = InstinctExtractor(
            db=MagicMock(), obs_store=MagicMock(),
            instinct_store=MagicMock(), evolution_store=MagicMock(),
            skill_manager=MagicMock(), data_root="/tmp",
        )
        events = [
            {"id": 1, "session_id": "s1", "event_type": "tool_call_end",
             "tool_name": "Read", "success": False},
            {"id": 2, "session_id": "s1", "event_type": "tool_call_end",
             "tool_name": "Write", "success": True},
        ]
        result = extractor._filter_significant_events(events)
        assert len(result) == 0
```

- [ ] **Step 5: 运行测试**

```bash
cd /Users/mac/Documents/Projects/web-agent && uv run pytest tests/unit/test_instinct_extractor.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/instinct_extractor.py tests/unit/test_instinct_extractor.py
git commit -m "feat: add InstinctExtractor with rule filtering, Haiku extraction, clustering, and skill generation"
```

---

### Task 5: evolution_signals — 每日评估 + 退化回滚

**Files:**
- Create: `src/evolution_signals.py`
- Test: `tests/unit/test_evolution_signals.py`

- [ ] **Step 1: 创建 evolution_signals.py**

```python
"""Daily evaluation of evolution quality using observation-derived signals."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class EvolutionSignals:
    """Tracks skill success rate and failure trends. Triggers rollback on degradation."""

    def __init__(self, db: Any, evolution_store: Any, skill_manager: Any) -> None:
        self.db = db
        self.evolution_store = evolution_store
        self.skill_manager = skill_manager

    async def run_daily_eval(self) -> dict[str, Any]:
        """Run daily evaluation for all active evolutions."""
        active = await self.evolution_store.get_active_evolutions()
        result = {"evaluated": 0, "degraded": 0, "rolled_back": 0}

        yesterday = time.strftime(
            "%Y-%m-%d", time.localtime(time.time() - 86400)
        )

        for log in active:
            snapshot = await self._compute_daily_snapshot(log, yesterday)
            if snapshot:
                await self.evolution_store.create_snapshot(**snapshot)
                result["evaluated"] += 1

            # Check last 7 snapshots for degradation
            recent = await self.evolution_store.get_last_snapshots(log["id"], count=7)
            if len(recent) >= 7:
                baseline = log.get("baseline_composite") or self._compute_baseline(log)
                all_below = all(
                    (s.get("composite_score") or 0) < baseline for s in recent
                )
                if all_below:
                    await self.evolution_store.update_status(
                        log["id"],
                        "under_review",
                        auto_rollback_at=time.time() + 48 * 3600,
                    )
                    result["degraded"] += 1

        # Process expired reviews
        expired = await self.evolution_store.get_expired_reviews()
        for log in expired:
            await self._rollback(log)
            result["rolled_back"] += 1

        return result

    async def _compute_daily_snapshot(
        self, log: dict[str, Any], date_str: str
    ) -> dict[str, Any] | None:
        """Compute one day's snapshot using observation data."""
        skill_name = log["skill_name"]
        date_start = date_str
        date_end = date_str + "T23:59:59"

        async with self.db.connection() as conn:
            # Tool success rate for sessions using this skill (approximation by time range)
            # Count successful vs total tool calls in the date range
            rows = await conn.execute_fetchall(
                """SELECT success, COUNT(*) as cnt FROM observations
                   WHERE created_at >= ? AND created_at <= ?
                   AND event_type = 'tool_call_end'
                   AND success IS NOT NULL
                   GROUP BY success""",
                (date_start, date_end),
            )
            total = sum(r[1] for r in rows)
            success_count = sum(r[1] for r in rows if r[0] == 1)
            tool_success_rate = success_count / total if total > 0 else 1.0

            # Session completion rate
            session_rows = await conn.execute_fetchall(
                """SELECT event_type, COUNT(DISTINCT session_id) as cnt
                   FROM observations
                   WHERE created_at >= ? AND created_at <= ?
                   AND event_type IN ('session_complete', 'session_error')
                   GROUP BY event_type""",
                (date_start, date_end),
            )
            sc = {r[0]: r[1] for r in session_rows}
            completed = sc.get("session_complete", 0)
            errored = sc.get("session_error", 0)
            session_rate = completed / (completed + errored) if (completed + errored) > 0 else 1.0

            # Usage count (tool calls as proxy for skill usage)
            usage = total

            # Unique users
            user_rows = await conn.execute_fetchall(
                """SELECT COUNT(DISTINCT user_id) FROM observations
                   WHERE created_at >= ? AND created_at <= ?""",
                (date_start, date_end),
            )
            unique_users = user_rows[0][0] if user_rows else 0

            # Composite score
            composite = 0.5 * tool_success_rate + 0.3 * session_rate + 0.2 * min(1.0, usage / 50)

            return {
                "evolution_log_id": log["id"],
                "snapshot_date": date_str,
                "usage_count": usage,
                "unique_users": unique_users,
                "avg_rating": 0,  # Not collected anymore
                "session_success_rate": session_rate,
                "composite_score": round(composite, 4),
            }

    def _compute_baseline(self, log: dict[str, Any]) -> float:
        return log.get("baseline_composite") or 0.6

    async def _rollback(self, log: dict[str, Any]) -> None:
        """Restore previous version of the skill."""
        try:
            skill_name = log["skill_name"]
            await self.skill_manager.rollback_version(skill_name)
            await self.evolution_store.update_status(log["id"], "rolled_back")
            logger.info("Rolled back %s (evolution #%d)", skill_name, log["id"])
        except Exception as exc:
            logger.error("Rollback failed for evolution #%d: %s", log["id"], exc)
```

- [ ] **Step 2: 单元测试**

```python
# tests/unit/test_evolution_signals.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.evolution_signals import EvolutionSignals


@pytest.mark.asyncio
async def test_compute_daily_snapshot():
    db = MagicMock()
    conn = AsyncMock()
    db.connection.return_value.__aenter__.return_value = conn
    conn.execute_fetchall.return_value = [(1, 80), (0, 20)]

    signals = EvolutionSignals(
        db=db, evolution_store=MagicMock(), skill_manager=MagicMock()
    )
    snapshot = await signals._compute_daily_snapshot(
        {"id": 1, "skill_name": "test-skill"}, "2026-05-22"
    )
    assert snapshot is not None
    assert snapshot["evolution_log_id"] == 1
    assert snapshot["snapshot_date"] == "2026-05-22"
    assert 0 <= snapshot["composite_score"] <= 1


@pytest.mark.asyncio
async def test_run_daily_eval_no_degradation():
    db = MagicMock()
    evo_store = MagicMock()
    evo_store.get_active_evolutions.return_value = [
        {"id": 1, "skill_name": "test", "baseline_composite": 0.5}
    ]
    evo_store.get_last_snapshots.return_value = [
        {"composite_score": 0.8}, {"composite_score": 0.75},
        {"composite_score": 0.7}, {"composite_score": 0.8},
        {"composite_score": 0.85}, {"composite_score": 0.9},
        {"composite_score": 0.95},
    ]
    evo_store.create_snapshot.return_value = 1

    signals = EvolutionSignals(
        db=db, evolution_store=evo_store, skill_manager=MagicMock()
    )
    # Patch _compute_daily_snapshot to return a known value
    signals._compute_daily_snapshot = AsyncMock(return_value={
        "evolution_log_id": 1, "snapshot_date": "2026-05-22",
        "usage_count": 50, "unique_users": 5, "avg_rating": 0,
        "session_success_rate": 0.9, "composite_score": 0.85,
    })

    result = await signals.run_daily_eval()
    assert result["evaluated"] >= 1
    assert result["degraded"] == 0
```

- [ ] **Step 3: 运行测试**

```bash
cd /Users/mac/Documents/Projects/web-agent && uv run pytest tests/unit/test_evolution_signals.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/evolution_signals.py tests/unit/test_evolution_signals.py
git commit -m "feat: add EvolutionSignals for daily evaluation and degradation rollback"
```

---

### Task 6: 扩展 evolution_log + 精简 CI engine

**Files:**
- Modify: `src/evolution_log.py` — 扩展查询方法
- Modify: `src/collective_intelligence.py` — 精简为两个定时器

- [ ] **Step 1: 扩展 evolution_log.py — 添加 instinct 关联查询**

在 `EvolutionLogStore` 类中添加方法：

```python
async def get_log_with_instincts(self, log_id: int) -> dict[str, Any] | None:
    """Get evolution log with linked instincts."""
    log = await self.get_log(log_id)
    if not log:
        return None
    async with self.db.connection() as conn:
        cursor = await conn.execute(
            """SELECT id, domain, normalized_trigger, trigger, action, confidence
               FROM instincts WHERE source_evolution_id = ?""",
            (log_id,),
        )
        rows = await cursor.fetchall()
    log["instincts"] = [
        {
            "id": r[0], "domain": r[1], "normalized_trigger": r[2],
            "trigger": r[3], "action": r[4], "confidence": r[5],
        }
        for r in rows
    ]
    return log

async def get_overview_stats(self) -> dict[str, Any]:
    """Dashboard stats: evolution counts by status, plus instinct and observation counts."""
    async with self.db.connection() as conn:
        status_rows = await conn.execute_fetchall(
            "SELECT status, COUNT(*) FROM evolution_log GROUP BY status"
        )
        status_counts = {r[0]: r[1] for r in status_rows}

        instinct_total = await conn.execute_fetchall(
            "SELECT COUNT(*) FROM instincts WHERE scope = 'active'"
        )
        instinct_active = instinct_total[0][0] if instinct_total else 0

        obs_today = await conn.execute_fetchall(
            "SELECT COUNT(*) FROM observations WHERE created_at >= ?",
            (time.time() - (time.time() % 86400),),
        )
        today_events = obs_today[0][0] if obs_today else 0

        week_applied = await conn.execute_fetchall(
            """SELECT COUNT(*) FROM evolution_log
               WHERE status = 'active' AND source = 'instinct_extractor'
               AND created_at >= ?""",
            (time.time() - 7 * 86400,),
        )
        week_auto = week_applied[0][0] if week_applied else 0

    return {
        "today_events": today_events,
        "active_instincts": instinct_active,
        "pending_reviews": status_counts.get("proposed", 0),
        "week_auto_applied": week_auto,
        "funnel": {
            "observations": today_events,
            "active_instincts": instinct_active,
            "active_evolutions": status_counts.get("active", 0),
            "proposed_evolutions": status_counts.get("proposed", 0),
        },
    }
```

- [ ] **Step 2: 精简 collective_intelligence.py**

将 `CollectiveIntelligenceEngine` 的 `start_background_jobs()` 替换为：

```python
async def start_background_jobs(self) -> None:
    """Start the instinct evolution background loops."""
    from src.instinct_extractor import InstinctExtractor, InstinctStore
    from src.observation import ObservationStore

    obs_store = ObservationStore(self.db)
    instinct_store = InstinctStore(self.db)

    self._extractor = InstinctExtractor(
        db=self.db,
        obs_store=obs_store,
        instinct_store=instinct_store,
        evolution_store=self._evo_log_store,
        skill_manager=self._skill_manager,
        data_root=str(self.data_root),
    )

    # Loop 1: instinct extraction every 10 minutes
    asyncio.create_task(self._extraction_loop())

    # Loop 2: daily eval at 02:00
    asyncio.create_task(self._daily_eval_loop())

async def _extraction_loop(self) -> None:
    while True:
        try:
            result = await self._extractor.run_once()
            if not result.get("skipped"):
                logger.info(
                    "Extraction cycle: %d extracted, %d clusters, %d applied, %d proposed",
                    result["extracted"], result["clusters"],
                    result["applied"], result["proposed"],
                )
        except Exception as exc:
            logger.error("Extraction cycle failed: %s", exc)
        await asyncio.sleep(10 * 60)  # 10 minutes

async def _daily_eval_loop(self) -> None:
    from src.evolution_signals import EvolutionSignals

    while True:
        now = time.localtime()
        seconds_until_0200 = (
            (24 - now.tm_hour - 2) % 24 * 3600
            - now.tm_min * 60
            - now.tm_sec
        )
        if seconds_until_0200 <= 0:
            seconds_until_0200 = 24 * 3600
        await asyncio.sleep(seconds_until_0200)

        try:
            signals = EvolutionSignals(
                self.db, self._evo_log_store, self._skill_manager
            )
            result = await signals.run_daily_eval()
            logger.info(
                "Daily eval: %d evaluated, %d degraded, %d rolled back",
                result["evaluated"], result["degraded"], result["rolled_back"],
            )
        except Exception as exc:
            logger.error("Daily eval failed: %s", exc)
```

删除旧的方法：`_wiki_mining_loop`、`_pattern_extraction_loop`、`_auto_promotion_loop`、`_eval_snapshot_loop`。

- [ ] **Step 3: Commit**

```bash
git add src/evolution_log.py src/collective_intelligence.py
git commit -m "feat: extend evolution_log with instinct queries, simplify CI engine to extraction + eval loops"
```

---

### Task 7: API 端点 — 新增 + 改造

**Files:**
- Modify: `main_server.py` — 替换旧端点，新增 stats/instincts/observations 端点

- [ ] **Step 1: 移除旧 API 端点**

删除以下端点函数（从 main_server.py）：
- `POST /api/skills/{skill_name}/evolve-agent` (line ~5096)
- `GET /api/skills/{skill_name}/evolve-status/{task_id}`
- `POST /api/skills/{skill_name}/activate-version`
- `POST /api/skills/{skill_name}/rollback`
- `GET /api/skills/{skill_name}/version`
- `GET /api/skills/{skill_name}/version/{version_name}`
- `GET /api/admin/skills/evolution-candidates` (line ~5463)
- 移除 `build_evolution_prompt()`、`run_evolution_agent()`、`_build_evolution_sdk_options()`、`trigger_skill_evolution_agent()` 辅助函数

- [ ] **Step 2: 添加新 API 端点**

在现有 evolution admin 端点附近（~line 6306 之后）添加：

```python
# ---- Instinct Evolution Admin APIs ----

@app.get("/api/admin/evolution/stats")
async def evolution_stats(
    current_user: str = Depends(require_admin),
) -> dict[str, Any]:
    """Dashboard stats for the instinct evolution panel."""
    store = EvolutionLogStore(_db)
    return await store.get_overview_stats()


@app.get("/api/admin/instincts")
async def list_instincts(
    domain: str = "",
    scope: str = "",
    page: int = 1,
    page_size: int = 20,
    current_user: str = Depends(require_admin),
) -> dict[str, Any]:
    """List instincts with optional filters."""
    from src.instinct_extractor import InstinctStore
    store = InstinctStore(_db)
    return await store.list_instincts(
        domain=domain, scope=scope, page=page, page_size=page_size
    )


@app.get("/api/admin/instincts/{instinct_id}")
async def get_instinct_detail(
    instinct_id: int,
    current_user: str = Depends(require_admin),
) -> dict[str, Any]:
    """Get instinct detail with source events."""
    from src.instinct_extractor import InstinctStore
    store = InstinctStore(_db)
    instinct = await store.get_by_id(instinct_id)
    if not instinct:
        raise HTTPException(status_code=404, detail="Instinct not found")
    return {"instinct": instinct}


@app.get("/api/admin/observations")
async def list_observations(
    session_id: str = "",
    event_type: str = "",
    page: int = 1,
    page_size: int = 50,
    current_user: str = Depends(require_admin),
) -> dict[str, Any]:
    """Browse observation events."""
    from src.observation import ObservationStore
    store = ObservationStore(_db)
    return await store.list_events(
        session_id=session_id, event_type=event_type,
        page=page, page_size=page_size,
    )
```

- [ ] **Step 3: 更新 evolution overview 端点以返回 instinct 信息**

在现有的 `evolution_overview` handler (line ~6306) 中，修改返回数据以包含 instinct 数量：

```python
# 在 list_logs 结果中添加 instinct count
for item in result["items"]:
    async with _db.connection() as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM instincts WHERE source_evolution_id = ?",
            (item["id"],),
        )
        row = await cursor.fetchone()
        item["instinct_count"] = row[0] if row else 0
```

- [ ] **Step 4: Commit**

```bash
git add main_server.py
git commit -m "feat: add evolution stats, instincts, observations API endpoints; remove old agent-driven evolution APIs"
```

---

### Task 8: 清理 — 移除旧代码

**Files:**
- Remove: `src/session_learner.py`, `src/evolution_evaluator.py`, `src/auto_evolve.py`, `src/skill_feedback.py`
- Modify: `main_server.py` — 移除 `_analyze_completed_session` 和对其的调用

- [ ] **Step 1: 移除旧文件和导入**

```bash
cd /Users/mac/Documents/Projects/web-agent
rm src/session_learner.py src/evolution_evaluator.py src/auto_evolve.py src/skill_feedback.py
```

- [ ] **Step 2: main_server.py 中移除旧调用**

删除 `_analyze_completed_session` 函数定义（line ~530-547）。

删除两处 `asyncio.ensure_future(_analyze_completed_session(session_id))` 调用（line ~2357 和 ~2553）。

删除 `from src.session_learner import SessionLearner` 导入。

删除 `from src.skill_feedback import DBSkillFeedbackManager` 导入。

- [ ] **Step 3: 验证服务启动**

```bash
cd /Users/mac/Documents/Projects/web-agent && uv run python -c "
import asyncio
from main_server import app

async def test():
    # Just verify the app loads without import errors
    print('App loaded successfully')
    print('Routes:', len(app.routes))

asyncio.run(test())
"
```

- [ ] **Step 4: Commit**

```bash
git add -u src/session_learner.py src/evolution_evaluator.py src/auto_evolve.py src/skill_feedback.py main_server.py
git commit -m "refactor: remove old evolution code (session_learner, evolution_evaluator, auto_evolve, skill_feedback)"
```

---

### Task 9: 前端 — API hook 扩展

**Files:**
- Modify: `frontend/src/hooks/useEvolutionApi.ts`

- [ ] **Step 1: 扩展 TypeScript 接口和 API 函数**

在现有 `useEvolutionApi.ts` 中添加新接口和函数：

```typescript
// 新接口
export interface InstinctItem {
  id: number;
  domain: 'tool_usage' | 'task_orchestration';
  normalized_trigger: string;
  trigger: string;
  action: string;
  confidence: number;
  source_count: number;
  unique_user_count: number;
  scope: 'active' | 'deprecated';
  created_at: number;
}

export interface ObservationItem {
  id: number;
  session_id: string;
  user_id: string;
  event_type: string;
  tool_name: string;
  success: boolean | null;
  error_message: string;
  duration_ms: number;
  created_at: number;
}

export interface EvolutionStats {
  today_events: number;
  active_instincts: number;
  pending_reviews: number;
  week_auto_applied: number;
  funnel: {
    observations: number;
    active_instincts: number;
    active_evolutions: number;
    proposed_evolutions: number;
  };
}

// 扩展 EvolutionDetail 接口
// 在现有接口中添加:
// instincts?: InstinctItem[];

// 扩展 EvolutionApi 接口
export interface EvolutionApi {
  overview: AsyncState<{ items: EvolutionItem[]; total: number; page: number }>;
  stats: AsyncState<EvolutionStats | null>;
  instincts: AsyncState<{ items: InstinctItem[]; total: number; page: number }>;
  observations: AsyncState<{ items: ObservationItem[]; total: number; page: number }>;
  fetchDetail: (id: number) => Promise<EvolutionDetail | null>;
  fetchDiff: (id: number) => Promise<EvolutionDiff | null>;
  review: (id: number, decision: 'keep' | 'rollback' | 'discard') => Promise<boolean>;
  fetchStats: () => Promise<void>;
  fetchInstincts: (params?: { domain?: string; scope?: string; page?: number }) => Promise<void>;
  fetchObservations: (params?: { session_id?: string; event_type?: string; page?: number }) => Promise<void>;
  refetch: () => void;
}

// fetchStats 实现
const fetchStats = async () => {
  const resp = await fetch(`${API_BASE}/stats`);
  if (!resp.ok) throw new Error('Failed to fetch stats');
  return resp.json();
};

// fetchInstincts 实现
const fetchInstincts = async (params?: { domain?: string; scope?: string; page?: number }) => {
  const qs = new URLSearchParams();
  if (params?.domain) qs.set('domain', params.domain);
  if (params?.scope) qs.set('scope', params.scope);
  if (params?.page) qs.set('page', String(params.page));
  const resp = await fetch(`/api/admin/instincts?${qs.toString()}`);
  if (!resp.ok) throw new Error('Failed to fetch instincts');
  return resp.json();
};

// fetchObservations 实现
const fetchObservations = async (params?: { session_id?: string; event_type?: string; page?: number }) => {
  const qs = new URLSearchParams();
  if (params?.session_id) qs.set('session_id', params.session_id);
  if (params?.event_type) qs.set('event_type', params.event_type);
  if (params?.page) qs.set('page', String(params.page));
  const resp = await fetch(`/api/admin/observations?${qs.toString()}`);
  if (!resp.ok) throw new Error('Failed to fetch observations');
  return resp.json();
};
```

- [ ] **Step 2: Commit**

```bash
cd /Users/mac/Documents/Projects/web-agent/frontend && npx tsc --noEmit --pretty false 2>&1 | head -20
git add src/hooks/useEvolutionApi.ts
git commit -m "feat: extend useEvolutionApi with stats, instincts, observations interfaces and functions"
```

---

### Task 10: 前端 — StatsCards + PipelineFunnel

**Files:**
- Create: `frontend/src/pages/evolution/StatsCards.tsx`
- Create: `frontend/src/pages/evolution/PipelineFunnel.tsx`

- [ ] **Step 1: 创建 StatsCards 组件**

```tsx
// frontend/src/pages/evolution/StatsCards.tsx
import React from 'react';
import type { EvolutionStats } from '../../hooks/useEvolutionApi';

interface Props {
  stats: EvolutionStats | null;
  loading: boolean;
}

const CARD_CONFIG = [
  { key: 'today_events', label: '今日事件', icon: '📡' },
  { key: 'active_instincts', label: '活跃本能', icon: '🧬' },
  { key: 'pending_reviews', label: '待审核', icon: '⏳' },
  { key: 'week_auto_applied', label: '本周自动应用', icon: '⚡' },
] as const;

export const StatsCards: React.FC<Props> = ({ stats, loading }) => (
  <div className="stats-cards">
    {CARD_CONFIG.map(({ key, label, icon }) => (
      <div className="stats-card" key={key}>
        <span className="stats-card-icon">{icon}</span>
        <div className="stats-card-body">
          <span className="stats-card-value">
            {loading ? '—' : stats?.[key as keyof EvolutionStats] ?? 0}
          </span>
          <span className="stats-card-label">{label}</span>
        </div>
      </div>
    ))}
  </div>
);
```

**CSS (追加到 evolution.css):**
```css
.stats-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1rem;
  margin-bottom: 1.5rem;
}
.stats-card {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem 1.25rem;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
}
.stats-card-icon { font-size: 1.5rem; }
.stats-card-value { font-size: 1.5rem; font-weight: 700; display: block; }
.stats-card-label { font-size: 0.8rem; color: var(--color-muted); }
```

- [ ] **Step 2: 创建 PipelineFunnel 组件**

```tsx
// frontend/src/pages/evolution/PipelineFunnel.tsx
import React from 'react';
import type { EvolutionStats } from '../../hooks/useEvolutionApi';

interface Props {
  stats: EvolutionStats | null;
}

export const PipelineFunnel: React.FC<Props> = ({ stats }) => {
  if (!stats) return null;
  const { funnel } = stats;
  const stages = [
    { label: 'Observations', value: funnel.observations, key: 'observations' },
    { label: 'Instincts', value: funnel.active_instincts, key: 'instincts' },
    { label: 'Evolutions', value: funnel.active_evolutions, key: 'evolutions' },
    { label: 'Proposed', value: funnel.proposed_evolutions, key: 'proposed' },
  ];
  const maxVal = Math.max(...stages.map((s) => s.value), 1);

  return (
    <div className="pipeline-funnel">
      {stages.map(({ label, value, key }, i) => (
        <React.Fragment key={key}>
          {i > 0 && <span className="funnel-arrow">→</span>}
          <div className="funnel-stage">
            <span className="funnel-label">{label}</span>
            <span className="funnel-value">{value}</span>
            <div className="funnel-bar">
              <div
                className="funnel-bar-fill"
                style={{ width: `${(value / maxVal) * 100}%` }}
              />
            </div>
          </div>
        </React.Fragment>
      ))}
    </div>
  );
};
```

**CSS:**
```css
.pipeline-funnel {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 1rem 1.25rem;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
}
.funnel-stage { flex: 1; min-width: 80px; }
.funnel-label { font-size: 0.75rem; color: var(--color-muted); display: block; }
.funnel-value { font-size: 1.25rem; font-weight: 700; display: block; margin: 0.25rem 0; }
.funnel-bar { height: 4px; background: var(--color-border); border-radius: 2px; overflow: hidden; }
.funnel-bar-fill { height: 100%; background: var(--color-accent); border-radius: 2px; transition: width 0.3s; }
.funnel-arrow { color: var(--color-muted); font-size: 1.25rem; align-self: center; }
```

- [ ] **Step 3: Commit**

```bash
cd /Users/mac/Documents/Projects/web-agent/frontend
git add src/pages/evolution/StatsCards.tsx src/pages/evolution/PipelineFunnel.tsx src/pages/evolution/evolution.css
git commit -m "feat: add StatsCards and PipelineFunnel components for evolution dashboard"
```

---

### Task 11: 前端 — InstinctList + ObservationBrowser

**Files:**
- Create: `frontend/src/pages/evolution/InstinctList.tsx`
- Create: `frontend/src/pages/evolution/ObservationBrowser.tsx`

- [ ] **Step 1: InstinctList 组件**

```tsx
// frontend/src/pages/evolution/InstinctList.tsx
import React, { useState } from 'react';
import type { InstinctItem } from '../../hooks/useEvolutionApi';

interface Props {
  data: { items: InstinctItem[]; total: number; page: number } | null;
  loading: boolean;
  error: string | null;
  onFilterChange: (filters: { domain?: string; scope?: string }) => void;
}

export const InstinctList: React.FC<Props> = ({ data, loading, error, onFilterChange }) => {
  const [domain, setDomain] = useState('');
  const [scope, setScope] = useState('');

  const handleFilter = () => onFilterChange(
    { domain: domain || undefined, scope: scope || undefined }
  );

  if (loading) return <div className="evo-loading">Loading instincts...</div>;
  if (error) return <div className="evo-error">{error}</div>;
  if (!data || data.items.length === 0) return <div className="evo-empty">No instincts found</div>;

  return (
    <div>
      <div className="filter-bar">
        <select value={domain} onChange={(e) => setDomain(e.target.value)}>
          <option value="">All domains</option>
          <option value="tool_usage">Tool Usage</option>
          <option value="task_orchestration">Task Orchestration</option>
        </select>
        <select value={scope} onChange={(e) => setScope(e.target.value)}>
          <option value="">All scopes</option>
          <option value="active">Active</option>
          <option value="deprecated">Deprecated</option>
        </select>
        <button onClick={handleFilter} className="btn-filter">Filter</button>
      </div>
      <table className="evo-table">
        <thead>
          <tr>
            <th>Label</th>
            <th>Trigger</th>
            <th>Action</th>
            <th>Confidence</th>
            <th>Sources</th>
            <th>Users</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((inst) => (
            <tr key={inst.id} className="evo-row">
              <td><span className="evo-badge">{inst.normalized_trigger}</span></td>
              <td>{inst.trigger}</td>
              <td>{inst.action}</td>
              <td>{(inst.confidence * 100).toFixed(0)}%</td>
              <td>{inst.source_count}</td>
              <td>{inst.unique_user_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="evo-pagination">Total: {data.total}</div>
    </div>
  );
};
```

- [ ] **Step 2: ObservationBrowser 组件**

```tsx
// frontend/src/pages/evolution/ObservationBrowser.tsx
import React, { useState } from 'react';
import type { ObservationItem } from '../../hooks/useEvolutionApi';

interface Props {
  data: { items: ObservationItem[]; total: number; page: number } | null;
  loading: boolean;
  error: string | null;
  onFilterChange: (filters: { session_id?: string; event_type?: string }) => void;
}

const EVENT_TYPES = [
  'tool_call_start', 'tool_call_end', 'user_correct',
  'user_retry', 'user_interrupt', 'session_complete', 'session_error',
];

export const ObservationBrowser: React.FC<Props> = ({ data, loading, error, onFilterChange }) => {
  const [sessionId, setSessionId] = useState('');
  const [eventType, setEventType] = useState('');

  const handleFilter = () => onFilterChange(
    { session_id: sessionId || undefined, event_type: eventType || undefined }
  );

  if (loading) return <div className="evo-loading">Loading observations...</div>;
  if (error) return <div className="evo-error">{error}</div>;
  if (!data || data.items.length === 0) return <div className="evo-empty">No observations found</div>;

  return (
    <div>
      <div className="filter-bar">
        <input
          type="text" placeholder="Session ID"
          value={sessionId} onChange={(e) => setSessionId(e.target.value)}
        />
        <select value={eventType} onChange={(e) => setEventType(e.target.value)}>
          <option value="">All types</option>
          {EVENT_TYPES.map((et) => (
            <option key={et} value={et}>{et}</option>
          ))}
        </select>
        <button onClick={handleFilter} className="btn-filter">Filter</button>
      </div>
      <table className="evo-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Session</th>
            <th>Type</th>
            <th>Tool</th>
            <th>Success</th>
            <th>Time</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((obs) => (
            <tr key={obs.id} className="evo-row">
              <td>{obs.id}</td>
              <td>{obs.session_id.substring(0, 12)}...</td>
              <td><span className="evo-badge">{obs.event_type}</span></td>
              <td>{obs.tool_name || '—'}</td>
              <td>{obs.success === null ? '—' : obs.success ? '✓' : '✗'}</td>
              <td>{new Date(obs.created_at * 1000).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="evo-pagination">Total: {data.total}</div>
    </div>
  );
};
```

**CSS 追加:**
```css
.filter-bar {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
  align-items: center;
}
.filter-bar input,
.filter-bar select {
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-surface);
  color: var(--color-text);
  font-size: 0.85rem;
}
.btn-filter {
  padding: 0.4rem 0.8rem;
  background: var(--color-accent);
  color: #fff;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.85rem;
}
.evo-pagination {
  margin-top: 0.5rem;
  font-size: 0.8rem;
  color: var(--color-muted);
}
```

- [ ] **Step 3: Commit**

```bash
cd /Users/mac/Documents/Projects/web-agent/frontend
git add src/pages/evolution/InstinctList.tsx src/pages/evolution/ObservationBrowser.tsx src/pages/evolution/evolution.css
git commit -m "feat: add InstinctList and ObservationBrowser components"
```

---

### Task 12: 前端 — 重构 EvolutionPage + 移除 EvolutionPanel

**Files:**
- Modify: `frontend/src/pages/EvolutionPage.tsx` — 完全重构
- Modify: `frontend/src/pages/evolution/EvolutionDetail.tsx` — 添加 instinct 展示
- Modify: `frontend/src/App.tsx` — 移除 EvolutionPanel 路由
- Remove: `frontend/src/components/EvolutionPanel.tsx`
- Remove: `frontend/src/hooks/useSkillEvolutionApi.ts`

- [ ] **Step 1: 重构 EvolutionPage**

```tsx
// frontend/src/pages/EvolutionPage.tsx
import React, { useState, useEffect, useCallback } from 'react';
import { StatsCards } from './evolution/StatsCards';
import { PipelineFunnel } from './evolution/PipelineFunnel';
import { OverviewTable } from './evolution/OverviewTable';
import { EvolutionDetail } from './evolution/EvolutionDetail';
import { InstinctList } from './evolution/InstinctList';
import { ObservationBrowser } from './evolution/ObservationBrowser';
import { useEvolutionApi } from '../hooks/useEvolutionApi';
import './evolution/evolution.css';

type TabId = 'evolutions' | 'instincts' | 'observations';

export const EvolutionPage: React.FC = () => {
  const api = useEvolutionApi('');
  const [activeTab, setActiveTab] = useState<TabId>('evolutions');
  const [detailId, setDetailId] = useState<number | null>(null);

  useEffect(() => {
    api.fetchStats();
    api.fetchInstincts({});
    api.fetchObservations({});
  }, []);

  const handleInstinctFilter = useCallback(
    (filters: { domain?: string; scope?: string }) => {
      api.fetchInstincts(filters);
    },
    [api]
  );

  const handleObsFilter = useCallback(
    (filters: { session_id?: string; event_type?: string }) => {
      api.fetchObservations(filters);
    },
    [api]
  );

  if (detailId !== null) {
    return (
      <div className="evolution-page">
        <button className="evolution-back" onClick={() => setDetailId(null)}>
          ← Back to overview
        </button>
        <EvolutionDetail evolutionId={detailId} api={api} />
      </div>
    );
  }

  const TABS: { id: TabId; label: string }[] = [
    { id: 'evolutions', label: '进化列表' },
    { id: 'instincts', label: '本能列表' },
    { id: 'observations', label: '事件浏览' },
  ];

  return (
    <div className="evolution-page">
      <div className="evolution-header">
        <h1>Evolution Monitor</h1>
      </div>

      <StatsCards stats={api.stats.data ?? null} loading={api.stats.loading} />
      <PipelineFunnel stats={api.stats.data ?? null} />

      <div className="status-tabs">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            className={`tab-btn ${activeTab === id ? 'active' : ''}`}
            onClick={() => setActiveTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {activeTab === 'evolutions' && (
        <OverviewTable
          data={api.overview.data}
          loading={api.overview.loading}
          error={api.overview.error}
          onRowClick={(item) => setDetailId(item.id)}
        />
      )}
      {activeTab === 'instincts' && (
        <InstinctList
          data={api.instincts.data}
          loading={api.instincts.loading}
          error={api.instincts.error}
          onFilterChange={handleInstinctFilter}
        />
      )}
      {activeTab === 'observations' && (
        <ObservationBrowser
          data={api.observations.data}
          loading={api.observations.loading}
          error={api.observations.error}
          onFilterChange={handleObsFilter}
        />
      )}
    </div>
  );
};
```

- [ ] **Step 2: 更新 EvolutionDetail — 展示关联 instinct**

在 `EvolutionDetail.tsx` 的数据获取后添加 instinct 展示区域（在现有内容之前）：

```tsx
{detail?.instincts && detail.instincts.length > 0 && (
  <div className="evo-instincts">
    <h3>Source Instincts ({detail.instincts.length})</h3>
    <div className="instinct-list">
      {detail.instincts.map((inst) => (
        <div key={inst.id} className="instinct-item">
          <span className="evo-badge">{inst.normalized_trigger}</span>
          <span className="instinct-confidence">
            {(inst.confidence * 100).toFixed(0)}%
          </span>
          <p className="instinct-desc">{inst.trigger} → {inst.action}</p>
        </div>
      ))}
    </div>
  </div>
)}
```

**CSS 追加:**
```css
.evo-instincts { margin-bottom: 1.5rem; }
.evo-instincts h3 { font-size: 0.95rem; margin-bottom: 0.5rem; }
.instinct-list { display: flex; flex-direction: column; gap: 0.5rem; }
.instinct-item {
  display: flex; align-items: flex-start; gap: 0.5rem;
  padding: 0.5rem; background: var(--color-surface); border-radius: 4px;
}
.instinct-confidence { font-size: 0.8rem; color: var(--color-accent); font-weight: 600; white-space: nowrap; }
.instinct-desc { font-size: 0.85rem; margin: 0; }
```

- [ ] **Step 3: 移除 EvolutionPanel**

在 `App.tsx` 中：
- 删除 `import { EvolutionPanel } from './components/EvolutionPanel';` (line ~20)
- 删除 `<Route path="/evolution" element={...EvolutionPanel...} />` (line ~1582-1591)
- 将 header 导航中的 "/evolution" 改为 "/dashboard/evolution"

删除文件：
```bash
rm frontend/src/components/EvolutionPanel.tsx
rm frontend/src/components/EvolutionPanel.test.tsx
rm frontend/src/hooks/useSkillEvolutionApi.ts
```

- [ ] **Step 4: 类型检查**

```bash
cd /Users/mac/Documents/Projects/web-agent/frontend && npx tsc --noEmit --pretty false 2>&1 | head -30
```

- [ ] **Step 5: Commit**

```bash
cd /Users/mac/Documents/Projects/web-agent
git add frontend/src/pages/EvolutionPage.tsx \
        frontend/src/pages/evolution/EvolutionDetail.tsx \
        frontend/src/pages/evolution/evolution.css \
        frontend/src/App.tsx
git add -u frontend/src/components/EvolutionPanel.tsx \
          frontend/src/components/EvolutionPanel.test.tsx \
          frontend/src/hooks/useSkillEvolutionApi.ts
git commit -m "feat: refactor EvolutionPage with dashboard layout, remove legacy EvolutionPanel"
```

---

### Task 13: 端到端集成验证 + 回滚测试

**Files:**
- 无新建文件 — 验证运行

- [ ] **Step 1: 后端启动验证**

```bash
cd /Users/mac/Documents/Projects/web-agent && uv run python -c "
import asyncio, sys
sys.path.insert(0, '.')
from main_server import app
print('Routes:')
for route in app.routes:
    if hasattr(route, 'path'):
        print(f'  {route.methods} {route.path}')
"
```

- [ ] **Step 2: 运行全部现有后端测试确保无回归**

```bash
cd /Users/mac/Documents/Projects/web-agent && uv run pytest tests/unit/ -v --tb=short 2>&1 | tail -30
```

- [ ] **Step 3: 前端构建验证**

```bash
cd /Users/mac/Documents/Projects/web-agent/frontend && npm run build 2>&1 | tail -10
```

- [ ] **Step 4: 进化全链路手动验证**

启动后端服务，执行一个 agent 会话，验证：
1. `observations` 表有新记录
2. 10 分钟后 `instincts` 表有新记录
3. `GET /api/admin/evolution/stats` 返回非零数据
4. 管理面板 `/dashboard/evolution` 显示指标卡片和漏斗

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: end-to-end verification of instinct evolution pipeline"
```

---

## 验证清单

- [ ] 数据库迁移 v6 成功运行，observations + instincts 表存在
- [ ] agent 会话产生 observations 记录
- [ ] CI engine 每 10 分钟触发提取（≥30 条事件时）
- [ ] Haiku 提取产生 instinct 候选，去重合并生效
- [ ] 标签匹配聚类正确合并同名 normalized_trigger
- [ ] confidence ≥ 0.7 的聚类自动写入 SKILL.md
- [ ] confidence < 0.7 的聚类创建 proposed evolution
- [ ] 每日评估计算成功率和失败趋势
- [ ] 退化检测：7 天低于基线 → under_review → 48h 回滚
- [ ] 管理面板展示 stats cards、funnel、instincts、observations
- [ ] 旧 EvolutionPanel 路由返回 404，新 Panel 正常
- [ ] 旧 session_learner/evolution_evaluator/auto_evolve 代码已移除
- [ ] 全部现有后端测试通过（无回归）
- [ ] 前端构建成功，类型检查通过
