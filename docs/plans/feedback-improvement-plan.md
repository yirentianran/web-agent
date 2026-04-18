# Feedback System Improvement Plan

> Generated: 2026-04-18
> Scope: Frontend (SkillFeedbackWidget, ChatArea) + Backend (main_server.py, skill_feedback.py, skill_evolution.py, database.py)

---

## 1. 需求概述

当前 Feedback 系统处于 MVP 状态，存在 8 个已知问题。本计划按优先级分三阶段修复：

| 优先级 | 问题 | 影响 |
|--------|------|------|
| HIGH | Skill 名称硬编码为 "general" | 所有反馈无法区分具体技能 |
| HIGH | user_id 默认 "anonymous" | 无法追踪用户归属 |
| MEDIUM | SQLite 表定义但未使用 | 激活 DB 写入，废弃 JSONL |
| MEDIUM | JSONL 存储模式不统一 | 迁移到 SQLite，废弃 JSONL |
| MEDIUM | 无自动进化触发 | 低评分技能无法自动改进 |
| LOW | 前端无错误处理 | 提交失败用户无感知 |
| LOW | 无反馈管理页面 | 数据在 DB 中但无可视化 |
| LOW | user_edits 字段未收集 | 模型有但 UI/端点不接受 |

---

## 2. Phase 1: 关键数据完整性 (HIGH)

### 1.1 后端解析认证头获取 user_id

**文件:** `main_server.py` (~line 2309)

**当前:**
```python
async def submit_skill_feedback(skill_name, req, user_id: str = "anonymous"):
```

**修改为:**
```python
async def submit_skill_feedback(skill_name, req, authorization: str | None = None):
    user_id = _get_user_id_from_header(authorization)
```

复用已有的 `_get_user_id_from_header` (line ~2606)，与其他端点保持一致。

### 1.2 前端发送认证 Token

**文件:** `frontend/src/components/ChatArea.tsx` (~line 268-276)

- `ChatAreaProps` 新增 `authToken: string | null`
- fetch 请求添加 `Authorization: Bearer ${authToken}` header

### 1.3 从会话消息中推导 skill 名称

**文件:** `frontend/src/components/ChatArea.tsx` + `frontend/src/App.tsx`

在 ChatArea 中添加 `useMemo` 提取消息中的 tool_use 名称：

```typescript
const skillsUsed = useMemo(() => {
  const skillTools = new Set<string>()
  for (const msg of messages) {
    if (msg.type === 'tool_use' && msg.name) {
      skillTools.add(msg.name)
    }
  }
  return Array.from(skillTools)
}, [messages])
```

- 仅 1 个 skill → 使用其名称
- 0 个或多个 → fallback 到 "general"
- 多个时允许用户选择要评分的 skill

**App.tsx** 中传递 `authToken` 给 ChatArea。

### 1.4 将 feedback 存储迁移到 SQLite

**当前方案问题：** feedback 写入 JSONL 文件，但 session/task 数据已经在 SQLite 中。两套存储系统导致：
- 无法 JOIN 查询（如"某用户所有 feedback"）
- JSONL 无事务保护、并发写入风险
- 聚合分析需遍历文件，效率低

**修改方案：** 使用已有的 `skill_feedback` 表

**文件:** `src/database.py` — **保留**现有表定义（无需修改）

**文件:** `src/skill_feedback.py` — 重写为 DB-backed：

```python
class SkillFeedbackManager:
    def __init__(self, db: Database) -> None:
        self.db = db  # 复用现有 Database 实例

    async def submit_feedback(
        self,
        skill_name: str,
        *,
        user_id: str,
        rating: int,
        comment: str = "",
        session_id: str | None = None,
        user_edits: str = "",
        skill_version: str = "",
    ) -> dict[str, Any]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                """INSERT INTO skill_feedback
                   (skill_name, user_id, session_id, rating, comment, skill_version)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (skill_name, user_id, session_id, rating, comment, skill_version),
            )
            return {"id": cursor.lastrowid, ...}

    async def get_analytics(self, skill_name: str) -> dict[str, Any]:
        async with self.db.connection() as conn:
            # 平均分、总数、分布 — 一条 SQL 搞定
            cursor = await conn.execute(
                """SELECT COUNT(*) as count,
                          AVG(rating) as avg_rating,
                          SUM(CASE WHEN rating=1 THEN 1 ELSE 0 END) as dist_1,
                          ...
                   FROM skill_feedback WHERE skill_name = ?""",
                (skill_name,),
            )
            ...

    async def get_user_feedback(self, user_id: str) -> list[dict]:
        """获取某用户的所有反馈（JSONL 无法实现）"""
        ...

    async def get_evolution_candidates(self) -> list[dict]:
        """找出平均分低 + 反馈数足够的 skill"""
        ...
```

**文件:** `main_server.py` (~line 2761 startup) — 注入 db 实例：
```python
feedback_manager = SkillFeedbackManager(db=_db)
```

**迁移策略：** 如果已有 JSONL 文件，startup 时一次性导入 SQLite，然后删除 JSONL 文件。

```python
async def _migrate_jsonl_to_db(self) -> None:
    """导入已有 JSONL 文件到 SQLite"""
    for f in self.feedback_dir.glob("*.jsonl"):
        for line in f.read_text().splitlines():
            if not line.strip(): continue
            entry = json.loads(line)
            await self.submit_feedback(...)
        f.unlink()
```

**删除的文件:** `src/skill_evolution.py` 中的 JSONL 读写逻辑也改为调用 `SkillFeedbackManager` 的 DB 方法。

---

## 3. Phase 2: DB-backed 分析方法 (MEDIUM)

### 2.1 添加 DB-backed feedback 分析方法

**文件:** `src/skill_feedback.py`

在 Phase 1 的 DB 基础上，添加：

```python
async def get_all_analytics(self) -> dict[str, dict]:
    """一次性获取所有 skill 的分析数据"""
    async with self.db.connection() as conn:
        cursor = await conn.execute(
            """SELECT skill_name, COUNT(*), AVG(rating), ...
               FROM skill_feedback GROUP BY skill_name"""
        )
        ...

async def get_feedback_for_session(self, session_id: str) -> list[dict]:
    """查询特定 session 的反馈"""
    ...

async def get_recent_feedback(self, limit: int = 50) -> list[dict]:
    """最近的反馈（管理仪表盘用）"""
    ...
```

### 2.2 添加用户反馈查询端点

**文件:** `main_server.py`

新增 `GET /api/users/{user_id}/feedback`:

```python
@app.get("/api/users/{user_id}/feedback")
async def get_user_feedback(user_id: str) -> dict:
    """获取用户的所有反馈记录和统计"""
    async with _db.connection() as conn:
        # 统计：按 skill 分组
        cursor = await conn.execute(
            """SELECT skill_name, COUNT(*) as count, AVG(rating) as avg_rating, ...
               FROM skill_feedback WHERE user_id = ? GROUP BY skill_name""",
            (user_id,),
        )
        stats = [{"skill_name": r[0], "count": r[1], "avg_rating": r[2], ...} for r in rows]

        # 明细：按时间倒序
        cursor = await conn.execute(
            """SELECT skill_name, rating, comment, session_id, created_at
               FROM skill_feedback WHERE user_id = ? ORDER BY created_at DESC""",
            (user_id,),
        )
        items = [...]

        return {"stats": stats, "items": items}
```

### 2.3 添加自动进化检查端点

**文件:** `main_server.py`

新增 `POST /api/skills/{skill_name}/evolve-check`:

```python
@app.post("/api/skills/{skill_name}/evolve-check")
async def check_skill_evolution(skill_name: str) -> dict:
    mgr = SkillEvolutionManager()
    return {
        "skill_name": skill_name,
        "should_evolve": mgr.should_evolve(skill_name),
        "stats": { ... },
    }
```

**注意:** 仅返回建议，不自动应用 LLM 改写。

---

## 4. Phase 3: UX 打磨 (LOW)

### 3.1 前端错误处理与重试

**文件:** `frontend/src/components/SkillFeedbackWidget.tsx`

- 添加 `error` state
- catch 块中设置错误信息
- 显示错误 + Retry 按钮
- 用户修改评分/评论时清除错误

### 3.2 收集 user_edits 字段

**涉及文件:**
- `src/models.py` — 模型已有 `user_edits` 字段
- `main_server.py` — SkillFeedbackRequest 新增 `user_edits: str = ""`
- `src/skill_feedback.py` — submit_feedback 参数新增 `user_edits`
- `SkillFeedbackWidget.tsx` — 添加折叠的 "What did you change?" textarea

### 3.3 反馈管理页面

**入口：** UserMenu 下拉菜单新增"反馈管理"按钮

```
👤 yguo
  ├─ Settings
  ├─ 反馈管理        ← 新增
  └─ Logout
```

**涉及文件：**

| 文件 | 修改类型 | 说明 |
|------|----------|------|
| `frontend/src/components/UserMenu.tsx` | 修改 | 新增 `onOpenFeedback` prop 和菜单项 |
| `frontend/src/components/FeedbackPage.tsx` | 新建 | 反馈管理页面 |
| `frontend/src/App.tsx` | 修改 | 新增 `showFeedback` 状态 + 条件渲染 |
| `frontend/src/components/Header.tsx` | 修改 | 新增 `onOpenFeedback` prop 传递 |
| `main_server.py` | 修改 | 新增 `GET /api/users/{user_id}/feedback` 端点 |

**页面布局：**

```
┌──────────────────────────────────────────────────┐
│  反馈管理                                   [← 返回] │
├──────────────────────────────────────────────────┤
│                                                  │
│  ┌─ 反馈统计 ───────────────────────────────┐   │
│  │ 共提交 12 条反馈                          │   │
│  │                                            │   │
│  │ Skill      │ 平均分 │ 次数 │ 评分分布     │   │
│  │────────────┼────────┼──────┼──────────────│   │
│  │ general    │ ★★★★ 4.2│  8  │ ████░      │   │
│  │ audit-pdf  │ ★★★  3.5│  4  │ ███░░      │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  ┌─ 反馈明细 ───────────────────────────────┐   │
│  │ ┌─ [general] ★★★★  2026-04-18 16:30 ──┐│   │
│  │ │ "整体好用，但有时输出格式不对"          ││   │
│  │ │ Session: session_xxx...              ││   │
│  │ └──────────────────────────────────────┘│   │
│  │ ┌─ [audit-pdf] ★★★  2026-04-17 10:20 ─┐│   │
│  │ │ "PDF提取表格时漏了几列"               ││   │
│  │ │ Session: session_xxx...              ││   │
│  │ └──────────────────────────────────────┘│   │
│  └──────────────────────────────────────────┘   │
│                                                  │
└──────────────────────────────────────────────────┘
```

**两个区块：**
1. **反馈统计** — 按 skill 分组的评分汇总（平均、次数、分布条）
2. **反馈明细** — 每条反馈的详细信息（skill、评分、评论、session_id、时间）

**路由方式：** 条件渲染，不引入 react-router

```tsx
// App.tsx
const [showFeedback, setShowFeedback] = useState(false)

if (showFeedback) {
  return <FeedbackPage onBack={() => setShowFeedback(false)} userId={userId} authToken={authToken} />
}
```

**后端新端点：** `GET /api/users/{user_id}/feedback`

```python
@app.get("/api/users/{user_id}/feedback")
async def get_user_feedback(user_id: str) -> dict[str, Any]:
    """获取用户的所有反馈记录和统计"""
    async with _db.connection() as conn:
        # 统计：按 skill 分组
        cursor = await conn.execute(
            """SELECT skill_name, COUNT(*) as count,
                      AVG(rating) as avg_rating,
                      SUM(CASE WHEN rating=1 THEN 1 ELSE 0 END) as dist_1,
                      ...
               FROM skill_feedback WHERE user_id = ?
               GROUP BY skill_name""",
            (user_id,),
        )
        stats = [...]

        # 明细：按时间倒序
        cursor = await conn.execute(
            """SELECT skill_name, rating, comment, session_id, created_at
               FROM skill_feedback WHERE user_id = ?
               ORDER BY created_at DESC""",
            (user_id,),
        )
        items = [...]

        return {"stats": stats, "items": items}
```

**不做权限控制** — 登录用户即可查看自己的反馈。

---

## 5. 依赖关系

```
Phase 1:
  1.1 (auth headers) --> 1.2 (frontend sends token)
  1.3 (skill name) -----> 独立

Phase 2:
  2.1 (DB 分析方法) ----> 独立
  2.2 (用户反馈端点) --> 依赖 Phase 1.4（DB 写入就绪）
  2.3 (evolve-check) --> 依赖 Phase 1.4（DB 写入就绪）

Phase 3:
  3.1, 3.2, 3.3 互相独立

跨阶段:
  Phase 3.3 (反馈管理页面) 依赖 Phase 2.2（用户反馈端点就绪）
  Phase 2 依赖 Phase 1.4（DB 写入就绪）
  Phase 1.2 依赖 Phase 1.1
```

---

## 6. 风险与缓解

| 风险 | 级别 | 缓解措施 |
|------|------|----------|
| tool name 与 skill 目录名不匹配 | 中 | 添加映射层：检查已知 skill 名称列表 |
| JSONL 迁移丢失数据 | 中 | 先导入 SQLite 再删除 JSONL；验证条目数；记录迁移日志 |
| 自动进化触发过于激进 | 中 | 仅返回建议，不自动应用；记录每次触发决策 |

---

## 7. 测试策略

### 后端单元测试

**`tests/unit/test_main_server.py`:**
- 带 Bearer token 的 feedback 请求正确提取 user_id
- 无 token 时 ENFORCE_AUTH=false fallback 到 "default"
- 带 user_edits 的请求正确存储
- evolve-check 返回正确推荐

**`tests/unit/test_skill_feedback.py` (重写):**
- `submit_feedback` 正确写入 SQLite
- `get_analytics` 返回正确的聚合数据
- `get_user_feedback` 按用户筛选
- `get_evolution_candidates` 正确识别低评分 skill
- JSONL 迁移方法正确导入数据到 DB

**`tests/unit/test_skill_evolution.py`:**
- `should_evolve` 从 DB 读取数据
- `collect_feedback` 调用 `SkillFeedbackManager` 的 DB 方法

### 前端测试

**`FeedbackPage.test.tsx` (新建):**
- 正确渲染两个区块（统计 + 明细）
- 空状态时显示友好提示
- 网络请求失败时显示错误
- 返回按钮工作正常

**`SkillFeedbackWidget.test.tsx` (新建):**
- 星评渲染正确
- 提交 payload 正确
- 网络失败显示错误状态
- Retry 按钮工作正常
- 折叠/展开切换

**更新 ChatArea 测试:**
- 从 tool_use 消息提取 skill 名称
- fallback 到 "general"
- 请求头包含 auth token

---

## 8. 成功标准

- [ ] feedback 端点从 JWT 提取 user_id（非 "anonymous"）
- [ ] skill 名称来自会话上下文（非硬编码 "general"）
- [ ] feedback 数据写入 SQLite `skill_feedback` 表（不再写 JSONL）
- [ ] 聚合分析用 SQL 查询（`AVG`, `COUNT`, `GROUP BY`）
- [ ] 已有 JSONL 数据成功迁移到 DB
- [ ] evolve-check 端点返回准确推荐
- [ ] 前端提交失败显示错误 + Retry
- [ ] user_edits 被后端接收并存储到 DB
- [ ] 反馈管理页面展示统计 + 明细
- [ ] 新增 `GET /api/users/{user_id}/feedback` 端点
- [ ] 所有测试通过，覆盖率 80%+

---

## 9. 估算工作量

| 阶段 | 工作量 | 复杂度 |
|------|--------|--------|
| Phase 1 (HIGH) | 2-3 小时 | 中 — 认证集成 + skill 推导 + DB 迁移 |
| Phase 2 (MEDIUM) | 1-2 小时 | 低 — DB-backed 分析方法 |
| Phase 3 (LOW) | 3-4 小时 | 低-中 — UI 扩展 + 字段扩展 |
| **合计** | **6-9 小时** | **中** |

---

## 10. 涉及文件清单

| 文件 | 修改类型 |
|------|----------|
| `frontend/src/components/SkillFeedbackWidget.tsx` | 修改 — 错误处理、user_edits、skill 名称 |
| `frontend/src/components/ChatArea.tsx` | 修改 — 传递 authToken、推导 skill 名称 |
| `frontend/src/App.tsx` | 修改 — 传递 authToken + 反馈页面条件渲染 |
| `frontend/src/components/FeedbackPage.tsx` | 新建 — 反馈管理页面 |
| `frontend/src/components/UserMenu.tsx` | 修改 — 新增"反馈管理"菜单项 |
| `frontend/src/components/Header.tsx` | 修改 — 传递 onOpenFeedback |
| `main_server.py` | 修改 — 认证头解析、evolve-check 端点、注入 db、用户反馈端点 |
| `src/skill_feedback.py` | **重写** — 从 JSONL 改为 SQLite 操作 |
| `src/skill_evolution.py` | 修改 — 调用 DB 方法替代 JSONL 读取 |
| `src/database.py` | **保留** — 已有 `skill_feedback` 表定义 |
| `src/models.py` | 检查 — user_edits 字段 |
| `tests/unit/test_main_server.py` | 新增测试 |
| `tests/unit/test_skill_feedback.py` | **重写** — DB 操作测试 |
| `tests/unit/test_skill_evolution.py` | 修改测试 |
