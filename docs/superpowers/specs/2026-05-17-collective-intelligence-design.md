---
name: 集体智能架构设计
description: 六层记忆模型（L0-L5），让多用户 Web Agent 系统随使用量增长而变聪明：自动 LLM Wiki、sqlite-vec 语义索引、技能自动晋升、隐式模式学习
type: design
status: draft
created: 2026-05-17
---

# 集体智能架构设计

## 1. 问题陈述

### 当前局限

Web Agent 系统已有 25 个用户、248 个会话、107,880 条消息、9 个共享技能。但存在以下结构性问题：

1. **知识孤岛**：每个用户的经验完全隔离。用户 A 解决过的错误、沉淀的技巧、验证过的方案，用户 B 遇到时系统一无所知。
2. **技能进化靠手动**：`src/skill_evolution.py` 的进化流程需要管理员手动触发（admin trigger），没有自动机制将高频高质技能提升为共享技能。
3. **系统提示静态化**：`build_system_prompt()` 在 `main_server.py` 第 848 行组装，内容由 L1 用户记忆（`src/memory.py`）+ L2 技能文件（`load_skills()` 第 330 行）+ 固定的知识提取规则（`src/learn-extraction.md`）组成。系统无法自动注入跨用户的集体知识。
4. **107K 消息未被利用**：`messages` 表是知识金矿，但没有任何机制从中挖掘模式、常见问题和解决方案。

### 目标

构建一个六层记忆模型（L0-L5），让系统：
- 自动从对话中生成知识文章（LLM Wiki）
- 用语义搜索找到相似的历史对话
- 自动将验证过的个人技能提升为共享技能
- 从用户行为中提取隐式反馈信号
- 在每次会话启动时，将最相关的集体知识注入系统提示

---

## 2. 架构总览：六层记忆模型

```
L0: 系统提示（固定）              ← build_system_prompt() 的前半部分，身份、安全规则
L1: 用户记忆（每用户）             ← 已有 src/memory.py，增强跨会话上下文
L2: 技能库（跨用户）               ← 已有 src/skill_manager.py + src/skill_evolution.py，增强自动晋升
L3: LLM Wiki（跨用户）             ← NEW: data/wiki/ 自动生成的知识文章
L4: 语义索引（跨用户）             ← NEW: sqlite-vec 向量检索
L5: 模式学习（跨用户）             ← NEW: 隐式信号提取 + 周期性分析
```

### 系统提示组装流程

`build_system_prompt()` 在 `main_server.py:848` 的组装顺序扩展为：

```
build_system_prompt(user_id, skills, workspace, language)
  |
  +-- L0: 固定的身份/安全/规则部分（现有）
  +-- L0: Available Skills 列表（现有，load_skills()）
  +-- L0: 知识提取规则（现有，src/learn-extraction.md）
  +-- L0: API 大小限制 + 文件生成规则（现有）
  |
  +-- L1: 用户记忆（现有，load_memory() → MemoryManager.read()）
  +-- L1: L2 Agent Memory notes（现有，load_agent_memory_for_prompt()）
  |
  +-- L3: LLM Wiki 相关片段（NEW: _load_wiki_context()）
  +-- L4: 相似历史对话摘要（NEW: _load_semantic_context()）
  +-- L5: 活跃模式提示（NEW: _load_pattern_context()）
  |
  +-- L0: 最终语言检查（现有）
```

新增的三个上下文加载函数将在 `main_server.py` 中实现，每个函数带预算限制（总 token 预算不超过 3000 tokens），确保不膨胀系统提示。

---

## 3. LLM Wiki 设计（L3）

### 3.1 存储结构

**Wiki 内容只存在数据库中（方案 A：单一数据源）。**

不使用文件系统存储，避免文件/数据库双重存储的一致性问题。所有 Wiki 页面存储在 `wiki_pages` 表：

```sql
CREATE TABLE wiki_pages (
    id TEXT PRIMARY KEY,              -- 例: "sqlite-database-locked"
    title TEXT NOT NULL,              -- 例: "SQLite Database Locked 常见原因及解决方案"
    body TEXT NOT NULL,               -- 完整 Markdown 正文
    category TEXT NOT NULL,           -- skills / patterns / common-errors / domain-knowledge
    tags TEXT NOT NULL DEFAULT '[]',  -- JSON 数组
    status TEXT NOT NULL DEFAULT 'draft',  -- draft / published / rejected
    source TEXT NOT NULL DEFAULT 'auto-generated',
    confidence REAL NOT NULL DEFAULT 0.5,
    validation_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);
```

FTS5 全文索引直接建立在 `wiki_pages` 表上，无需额外同步步骤。

LLM 检索 Wiki 时，从数据库读取，而非文件系统。

### 3.2 自动生流程

新建 `src/wiki_generator.py`：

```python
class WikiGenerator:
    def __init__(self, db: Database):
        self.db = db

    async def mine_and_generate(self, lookback_hours: int = 24) -> list[str]:
        """主循环：挖掘对话 → 生成 Wiki 页面。

        1. 从 messages 表提取最近 lookback_hours 的对话
        2. 筛选有 skill_feedback 的会话（已有质量信号）
        3. 按主题聚类（基于 conversation_snippet + feedback comment）
        4. 对每个聚类调用 LLM 生成 Wiki 页面
        5. 写入 wiki_pages 表，FTS5 自动索引
        """
```

### 3.3 触发条件

| 触发类型 | 条件 | 动作 |
|---------|------|------|
| 问题重复出现 | 同一类错误/问题在 3+ 个不同用户的会话中出现 | 生成 common-errors Wiki 页面 |
| 解决方案被验证 | 同一方案被 2+ 个用户通过 skill_feedback 确认（rating >= 4） | 提升 confidence，标记 validated |
| 新模式被发现 | `src/learn-extraction.md` 提取规则触发新技能创建 ≥ 2 次 | 生成 patterns Wiki 页面 |
| 领域知识积累 | 同一领域的对话累计 5+ 次 | 生成 domain-knowledge Wiki 页面 |

### 3.4 人工审核流程

Wiki 页面初始状态为 `draft`，存储在 `wiki_pages` 表：

```sql
SELECT id, title, status, confidence FROM wiki_pages WHERE status = 'draft';
```

管理员通过新增的 REST API 审核：

- `GET /api/admin/wiki` — 列出所有待审核页面
- `GET /api/admin/wiki/{page_id}` — 查看页面内容
- `POST /api/admin/wiki/{page_id}/approve` — 批准发布（status → published）
- `POST /api/admin/wiki/{page_id}/reject` — 拒绝
- `PUT /api/admin/wiki/{page_id}` — 编辑后发布

批准后页面状态变为 `published`，才会被 `_load_wiki_context()` 加载到系统提示中。

### 3.5 系统提示注入

在 `main_server.py` 中新增函数：

```python
def _load_wiki_context(user_id: str, current_message: str, max_tokens: int = 1000) -> str:
    """根据当前会话上下文，检索最相关的已发布 Wiki 页面，注入系统提示。

    1. 从当前用户消息提取关键词
    2. FTS5 全文搜索 wiki_pages 表
    3. 读取最相关的 1-2 个页面的摘要部分（非全文）
    4. 组装为 "## Collective Knowledge" 段落
    """
```

注入格式：

```markdown
## Collective Knowledge

基于当前对话上下文，以下集体知识可能相关：

### SQLite Database Locked
- 常见原因：并发写入、WAL checkpoint 冲突
- 解决：使用 busy_timeout + PASSIVE checkpoint
- 验证：2 位用户确认有效
```

---

## 4. 语义索引设计（L4）

### 4.1 sqlite-vec 集成方案

sqlite-vec 作为 SQLite 扩展加载，与现有 `data/web-agent.db` 共用同一个数据库文件。

在 `src/database.py` 的 `_CREATE_TABLES` 中新增：

```sql
-- Wiki 页面嵌入向量
CREATE TABLE IF NOT EXISTS wiki_embeddings (
    page_id TEXT PRIMARY KEY REFERENCES wiki_pages(id),
    embedding BLOB NOT NULL,  -- sqlite-vec f32 vector
    content_hash TEXT NOT NULL,
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 对话摘要嵌入向量
CREATE TABLE IF NOT EXISTS session_summary_embeddings (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    summary TEXT NOT NULL,
    embedding BLOB NOT NULL,
    user_id TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- 技能描述嵌入向量
CREATE TABLE IF NOT EXISTS skill_embeddings (
    skill_name TEXT PRIMARY KEY REFERENCES skills(skill_name),
    embedding BLOB NOT NULL,
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- FTS5 全文索引（用于混合搜索）
CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
    title, body, tags,
    content='wiki_pages',
    content_rowid='rowid'
);

CREATE VIRTUAL TABLE IF NOT EXISTS session_summary_fts USING fts5(
    summary, user_id,
    content='session_summary_embeddings',
    content_rowid='rowid'
);
```

### 4.2 Embedding 策略（第一阶段不引入）

**决策：第一阶段采用 Karpathy 纯 LLM Wiki 模式，不使用 Embedding。**

#### 为什么不引入 Embedding

Karpathy 的 LLM Wiki 设计核心：**LLM 自己判断需要查阅哪篇 Wiki，不需要向量索引。**

| 方法 | 成本 | 效果 | 适用阶段 |
|------|------|------|---------|
| **纯 LLM 判断（Karpathy 模式）** | 无额外开销 | Wiki 文章少（<50 篇）时足够精准 | **第一阶段** |
| BM25 全文索引 | 免费，零依赖 | 字面匹配，跨语言搜索差 | 第一阶段补充 |
| Embedding 向量搜索 | 需配置 API key / 部署模型 | 语义匹配，跨语言 | 第二阶段可选 |

#### Karpathy 模式工作流程

```
1. 系统提示中注入 Wiki 目录索引（标题 + 标签）：
   ## Collective Knowledge Index
   - common-errors/sqlite-database-locked.md
   - patterns/chunked-file-processing.md
   - skills/python-async-patterns.md

2. Agent 处理用户请求时，自己判断是否需要查阅某篇 Wiki
   - 标题足够清晰时，Agent 自主决定是否读取
   - 例如看到 "sqlite database locked"，Agent 知道去查对应 Wiki

3. 如果 Agent 判断需要，读取完整 Markdown 文件注入上下文
```

#### 搜索降级策略

```
第一优先：LLM 自主判断（零成本，零依赖）
第二优先：FTS5 BM25 全文搜索（免费，SQLite 内置）
第三阶段可选：Embedding + sqlite-vec 向量搜索（需额外配置）
```

#### 什么时候考虑引入 Embedding

满足以下条件时，再评估是否引入：

1. Wiki 文章 ≥ 50 篇，LLM 自主判断开始出现遗漏
2. 需要跨语言语义匹配（中文搜英文场景）
3. 历史会话量足够大（≥ 1000 条），需要向量搜索相似会话
4. 有明确的 Embedding API 可用（无需新配置）

#### 预留 Embedding 接口

代码结构上预留 `embed_text()` 接口位置，但第一阶段不实现、不调用。
后续如果要引入，只需实现 `src/embedding.py` 并在 `src/semantic_search.py` 中启用向量路径。

### 4.3 索引内容（第一阶段：仅 FTS5）

| 索引类型 | 内容 | 阶段 |
|---------|------|------|
| `wiki_fts` (FTS5) | Wiki 页面的 title + body | **第一阶段** |
| `session_summary_fts` (FTS5) | 会话摘要全文 | **第一阶段** |
| `wiki_embeddings` (sqlite-vec) | Wiki 向量 | 第二阶段可选 |
| `session_summary_embeddings` | 会话向量 | 第二阶段可选 |
| `skill_embeddings` | 技能向量 | 第二阶段可选 |

### 4.4 查询流程（第一阶段）

**Wiki 检索**：LLM 自主判断 + FTS5 全文索引辅助

```python
class SemanticSearch:
    def __init__(self, db: Database):
        self.db = db

    def find_relevant_wiki(self, query: str, top_k: int = 3) -> list[dict]:
        """第一阶段：FTS5 全文索引搜索（无向量）。

        1. 用 query 在 wiki_fts 中做全文搜索
        2. 返回 top_k 篇已发布 Wiki 页面
        """
```

**混合搜索**（后续可选）：BM25 + 向量相似度

```
score = 0.4 * bm25_score + 0.6 * cosine_similarity
```

第一阶段不引入 Embedding，仅用 BM25/FTS5。

### 4.5 系统提示注入

```python
def _load_semantic_context(user_id: str, current_message: str, max_tokens: int = 1000) -> str:
    """语义搜索相似历史对话，注入系统提示。

    1. 用当前用户消息做 query，搜索 session_summary_embeddings
    2. 过滤掉当前用户的会话（避免自我引用）
    3. 返回 top-2 相似对话的摘要 + 关键结论
    """
```

注入格式：

```markdown
## Similar Past Conversations

以下是其他用户遇到过的相似问题：

### 会话 sess_xyz789（用户 bob，2026-05-15）
**问题**：SQLite 并发写入导致 locked 错误
**解决**：设置 busy_timeout=30000 + 使用 PASSIVE checkpoint
**结果**：成功，后续 3 天无类似报错
```

---

## 5. 技能自动晋升（L2 增强）

### 5.1 晋升阈值

| 指标 | 阈值 | 说明 |
|------|------|------|
| 使用次数 | >= 10 | `skill_usage` 表中该技能被使用的总次数 |
| 平均评分 | >= 4.0 | `skill_feedback` 表中该技能的平均 rating |
| 使用用户数 | >= 3 | 至少 3 个不同用户使用过 |
| 时间窗口 | 30 天 | 避免偶发高频使用误判 |

### 5.2 实现

在 `src/skill_manager.py` 中新增方法：

```python
async def check_auto_promotion(self) -> list[dict]:
    """扫描符合晋升条件的个人技能，标记为 pending_promotion。

    查询逻辑：
    1. 找到所有 source='personal' 且 status='active' 的技能
    2. JOIN skill_usage 统计使用次数和独立用户数
    3. JOIN skill_feedback 计算平均评分
    4. 筛选满足阈值的技能
    5. 更新 status='pending_promotion'
    """
```

在 `src/skill_evolution.py` 中调整现有阈值：

```python
# 现有阈值（src/skill_evolution.py:16-17）
SHOULD_EVOLVE_MIN_COUNT = 5          # 降低到 3（有 L5 隐式信号辅助）
SHOULD_EVOLVE_MAX_RATING = 4.0       # 保持不变

# 新增晋升阈值
AUTO_PROMOTE_MIN_USES = 10
AUTO_PROMOTE_MIN_USERS = 3
AUTO_PROMOTE_MIN_AVG_RATING = 4.0
AUTO_PROMOTE_WINDOW_DAYS = 30
```

### 5.3 晋升流程

```
个人技能使用 ≥ 10 次
    → 平均评分 ≥ 4.0
    → 独立用户 ≥ 3
    → status 变更为 'pending_promotion'
    → 通知管理员（日志 + 可选 webhook）
    → 管理员审核（approve / reject / edit_then_approve）
    → approve：复制 SKILL.md 到 data/shared-skills/{skill_name}/
               更新 source='shared'
               保留原始 owner_id 用于归属
    → reject：status 恢复为 'active'，记录拒绝原因
```

管理员 API 新增：

- `GET /api/admin/skills/pending` — 列出待晋升技能
- `POST /api/admin/skills/{skill_name}/promote` — 批准晋升
- `POST /api/admin/skills/{skill_name}/reject` — 拒绝（需填写原因）

---

## 6. 模式学习（L5）

### 6.1 隐式信号定义

不需要用户显式评分，从行为中提取信号：

| 信号 | 提取逻辑 | 含义 | 权重 |
|------|---------|------|------|
| follow-up refinement | 同一会话中用户连续发送 2+ 条相关请求 | 首次回答不完整 | -0.3 |
| explicit correction | 用户消息包含 "不对"、"错了"、"not right" 等否定词 | 回答错误 | -0.5 |
| session abandonment | 会话 status='active' 但 24h 无新消息 | 用户放弃该会话 | -0.2 |
| re-invocation | 同一问题在 7 天内被同一用户重复提问 | 未解决根本问题 | -0.4 |
| tool success pattern | 某工具组合在该场景下成功率高 | 有效模式 | +0.3 |
| session completion | 会话正常结束且用户有正向反馈 | 成功会话 | +0.2 |

### 6.2 工具使用模式

```python
# src/pattern_learner.py
class PatternLearner:
    def __init__(self, db: Database):
        self.db = db

    async def extract_tool_patterns(self) -> list[dict]:
        """从 messages 表提取工具使用模式。

        1. 提取所有 type='tool_use' 的消息
        2. 按 session_id 分组，分析工具调用序列
        3. 找出频繁共现的工具对（support / confidence）
        4. 计算每个工具在不同场景下的成功率
        """
```

示例输出：

```json
{
    "tool_pairs": [
        {
            "tools": ["Read", "Edit"],
            "co_occurrence": 15230,
            "confidence": 0.89,
            "avg_session_messages": 24
        }
    ],
    "tool_success_rates": {
        "Read": {"success": 0.97, "count": 45200},
        "Edit": {"success": 0.88, "count": 12800},
        "Bash": {"success": 0.76, "count": 8900}
    }
}
```

### 6.3 背景分析任务

新建 `src/collective_intelligence.py`，包含定时任务调度：

```python
class CollectiveIntelligenceEngine:
    """集体智能引擎，协调 L3/L4/L5 的所有后台任务。"""

    def __init__(self, db: Database):
        self.db = db
        self.wiki_generator = WikiGenerator(db, ...)
        self.semantic_search = SemanticSearch(db)
        self.pattern_learner = PatternLearner(db)

    async def start_background_jobs(self):
        """启动所有后台定时任务。"""
        asyncio.create_task(self._wiki_mining_loop())          # 每 6 小时
        asyncio.create_task(self._embedding_update_loop())     # 每 1 小时
        asyncio.create_task(self._pattern_extraction_loop())   # 每 12 小时
        asyncio.create_task(self._auto_promotion_check())      # 每 2 小时

    async def _wiki_mining_loop(self):
        while True:
            await self.wiki_generator.mine_and_generate(lookback_hours=6)
            await asyncio.sleep(6 * 3600)

    async def _embedding_update_loop(self):
        while True:
            await self._embed_new_sessions()
            await self._embed_new_wiki_pages()
            await asyncio.sleep(3600)

    async def _pattern_extraction_loop(self):
        while True:
            await self.pattern_learner.extract_tool_patterns()
            await asyncio.sleep(12 * 3600)

    async def _auto_promotion_check(self):
        while True:
            candidates = await self.skill_manager.check_auto_promotion()
            if candidates:
                logger.info(f"Auto-promotion candidates: {[c['skill_name'] for c in candidates]}")
            await asyncio.sleep(2 * 3600)
```

### 6.4 在 main_server.py 中集成

在 FastAPI 的 lifespan 中启动集体智能引擎：

```python
# main_server.py 的 lifespan 函数中
@app.on_event("startup")
async def startup():
    ...
    # 启动集体智能后台任务
    ci_engine = CollectiveIntelligenceEngine(db=_db)
    asyncio.create_task(ci_engine.start_background_jobs())
```

---

## 7. 数据流：一次会话的读写路径

### 7.1 会话启动时（读取路径）

```
用户打开会话
  → POST /api/users/{user_id}/sessions
    → load_skills(user_id)             ← L2: 加载共享 + 个人技能
    → load_memory(user_id)            ← L1: 加载用户记忆
    → MemoryManager.load_agent_memory_for_prompt()  ← L1: 加载 Markdown notes
    → _load_wiki_context(user_id, ...)   ← L3: 检索相关 Wiki
    → _load_semantic_context(...)        ← L4: 检索相似历史对话
    → _load_pattern_context(...)         ← L5: 加载活跃模式提示
    → build_system_prompt(...)           ← 组装完整系统提示
    → 传递给 Agent SDK
```

### 7.2 会话进行中（写入路径）

```
用户发送消息
  → WebSocket 消息存入 message_buffer
  → Agent 处理并回复
  → 会话结束时：
    → 消息批量写入 messages 表（已有）
    → 更新 sessions.status = 'completed'
    → 触发后台任务：
      → 生成会话摘要 → session_summary_embeddings 表
      → 如果有 skill_feedback → 更新 skill_feedback 表
      → 如果触发知识提取规则 → 创建个人技能
```

### 7.3 后台周期性（异步写入路径）

```
每 6 小时：  Wiki 挖掘 → data/wiki/ 新页面
每 1 小时：  新会话/新 Wiki 页面 → 计算 embedding → 写入向量表
每 12 小时： 消息模式分析 → 工具共现/成功率统计
每 2 小时：  技能晋升检查 → 更新 skill.status
```

---

## 8. 隐私与隔离

### 8.1 原则

- **L1 用户记忆完全隔离**：不跨用户共享。`MemoryManager` 已经是 per-user 设计，保持不变。
- **L2/L3/L4/L5 知识去标识化**：共享的知识不包含 `user_id` 或任何可追溯到个人的信息。
- **会话摘要脱敏**：生成摘要时剥离个人信息（文件名、路径、业务实体名称等）。

### 8.2 脱敏规则

在 `src/semantic_search.py` 中新增脱敏函数：

```python
def anonymize_summary(summary: str) -> str:
    """去除会话摘要中的个人可识别信息。"""
    # 1. 移除具体文件路径（保留文件类型）
    summary = re.sub(r'/[\w/.-]+\.(py|md|txt|xlsx|pdf)', '<path>', summary)
    # 2. 移除用户名/用户 ID
    summary = re.sub(r'(user_id|user)["\s:=]+["\w-]+', 'user: <anonymized>', summary)
    # 3. 移除具体业务实体名称（从 entity_memory 中提取的实体）
    # 4. 保留问题类型、解决方案、工具使用模式等通用知识
    return summary
```

### 8.3 Wiki 页面中的归属

Wiki 页面记录 `related_sessions` 但仅存储 session_id，不存储 user_id。在系统提示中注入时，显示为：

```markdown
### SQLite Database Locked
- 验证：2 位用户确认有效（不显示具体用户名）
```

### 8.4 技能晋升中的隐私

个人技能晋升为共享技能时：

```python
async def promote_skill(self, skill_name: str, owner_id: str) -> None:
    """晋升个人技能为共享技能。

    1. 复制 SKILL.md 到 shared-skills/
    2. 扫描内容，脱敏个人项目相关引用
    3. 保留 owner_id 作为 created_by（用于归属）
    4. 在 skill 表中新建 source='shared' 的记录
    """
```

---

## 9. 迁移路径：从 107K 消息启动

### 9.1 阶段 0：数据库迁移

新建 `src/database.py` 中的 `migrate_collective_intelligence()` 方法：

```python
async def migrate_collective_intelligence(self) -> None:
    """添加集体智能相关的新表。安全可重入。"""
    async with self.connection() as conn:
        # 1. Wiki 页面表
        await conn.execute("""CREATE TABLE IF NOT EXISTS wiki_pages (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            category TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'draft',
            source TEXT NOT NULL DEFAULT 'auto-generated',
            confidence REAL NOT NULL DEFAULT 0.5,
            validation_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
        )""")

        # 2. 向量嵌入表（见 4.1 节）
        # 3. FTS5 索引（见 4.1 节）
        # 4. 技能晋升审核表
        await conn.execute("""CREATE TABLE IF NOT EXISTS skill_promotion_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT NOT NULL REFERENCES skills(skill_name),
            original_owner_id TEXT NOT NULL,
            uses_count INTEGER NOT NULL,
            unique_users_count INTEGER NOT NULL,
            avg_rating REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            admin_review_comment TEXT,
            reviewed_at REAL,
            reviewed_by TEXT,
            created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
        )""")

        # 5. 模式存储表
        await conn.execute("""CREATE TABLE IF NOT EXISTS learned_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_type TEXT NOT NULL,  -- 'tool_cooccurrence', 'success_rate', 'error_pattern'
            pattern_data TEXT NOT NULL,   -- JSON
            confidence REAL NOT NULL,
            created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
        )""")

        await conn.commit()
```

### 9.2 阶段 1：Bootstrapping（历史数据挖掘）

新建一次性启动脚本 `scripts/bootstrap_collective_intelligence.py`：

```python
async def bootstrap():
    """从历史 107K 消息中挖掘初始知识。"""
    # 1. 提取所有有 skill_feedback 的会话 → 生成首批 Wiki 页面
    # 2. 对所有已有会话生成摘要 → 填充 session_summary_embeddings
    # 3. 对所有已有技能计算 embedding → 填充 skill_embeddings
    # 4. 分析全部 messages 表的工具使用模式 → 初始 learned_patterns
    # 5. 检查现有技能是否满足晋升条件 → 填充 skill_promotion_queue
```

执行策略：
- 分批处理，每次处理 5000 条消息，避免内存溢出
- 总耗时预计 30-60 分钟（取决于 embedding API 速率）
- 支持断点续传（记录已处理的 message id）

### 9.3 阶段 2：渐进式上线

| 时间 | 动作 |
|------|------|
| 第 1 周 | 仅运行 L4 embedding 更新，不注入系统提示 |
| 第 2 周 | L3 Wiki 生成（draft 模式），管理员审核 |
| 第 3 周 | 注入 L3 Wiki 到系统提示（只读，预算 500 tokens） |
| 第 4 周 | 注入 L4 相似对话到系统提示（只读，预算 800 tokens） |
| 第 5 周 | 启用 L5 隐式信号收集 |
| 第 6 周 | 启用 L2 自动晋升检查 |
| 第 8 周 | 全量运行，评估效果 |

---

## 10. 技术栈决策

### 10.1 向量存储：sqlite-vec（第一阶段不引入）

**第一阶段仅使用 FTS5 全文索引，不引入 sqlite-vec。**

FTS5 是 SQLite 内置功能，零额外依赖，已满足当前需求：
- Wiki 文章少（<50 篇），FTS5 全文搜索足够
- LLM 自主判断需要哪篇 Wiki（Karpathy 模式）
- 不需要向量相似度匹配

**sqlite-vec 作为第二阶段可选**，当满足以下条件时再评估引入：
- Wiki ≥ 50 篇且 FTS5 搜索结果不够精准
- 有 Embedding API 可用
- 需要跨语言语义搜索

**安装方式**（预留，第二阶段）：
```bash
pip install sqlite-vec
```

### 10.2 Embedding 模型（第一阶段不引入）

第一阶段采用 Karpathy 纯 LLM Wiki 模式，不使用 Embedding。详见第 4.2 节。

如果后续引入 Embedding，可选方案：

| 维度 | 模型 | 适用场景 |
|------|------|---------|
| 1536 | OpenAI text-embedding-3-small | 通用英文 |
| 512 | BAAI/bge-small-zh-v1.5 | 中文优化 |
| 本地 | sentence-transformers | 零 API 成本，需 GPU |

### 10.3 后台任务框架

复用项目已有的 `asyncio` 模式。`main_server.py` 已有 `_checkpoint_loop()` 的 asyncio 定时任务模式，集体智能的后台任务遵循相同模式。

不引入 Celery/RQ 等外部队列框架，理由：
- 当前任务量小（4 个定时任务）
- 已有 asyncio 基础设施
- 避免额外依赖和运维复杂度

如果后续任务量增长到需要队列/重试/分布式，再考虑迁移到 Celery。

### 10.4 新增文件清单

| 文件 | 职责 |
|------|------|
| `src/wiki_generator.py` | Wiki 自动挖掘和生成 |
| `src/semantic_search.py` | FTS5 全文索引搜索（预留 Embedding 接口） |
| `src/pattern_learner.py` | 隐式信号提取与分析 |
| `src/collective_intelligence.py` | 总调度引擎，协调所有后台任务 |
| `src/embedding.py` | Embedding API 封装（第二阶段可选） |
| `scripts/bootstrap_collective_intelligence.py` | 一次性历史数据迁移脚本 |
| `docs/superpowers/specs/2026-05-17-collective-intelligence-design.md` | 本设计文档 |

### 10.5 对现有文件的修改

| 文件 | 修改内容 |
|------|---------|
| `src/database.py` | 新增 `migrate_collective_intelligence()` 方法，添加新表 + FTS5 索引 |
| `main_server.py` | 在 `build_system_prompt()` 中新增 L3/L4/L5 上下文加载调用；在 lifespan 中启动 `CollectiveIntelligenceEngine` |
| `src/skill_manager.py` | 新增 `check_auto_promotion()` 方法 |
| `src/skill_evolution.py` | 新增自动晋升阈值常量 |
| `src/memory.py` | 不变（L1 已足够） |

---

## 附录 A：关键函数签名参考

```python
# main_server.py
def build_system_prompt(user_id, skills, workspace, language) -> str:
    # 现有函数，扩展 L3/L4/L5 注入

def _load_wiki_context(user_id: str, session_history: list, max_tokens: int = 1000) -> str:
    # 新增

def _load_semantic_context(user_id: str, current_message: str, max_tokens: int = 1000) -> str:
    # 新增

def _load_pattern_context(user_id: str, max_tokens: int = 500) -> str:
    # 新增

# src/database.py
class Database:
    async def migrate_collective_intelligence(self) -> None:
        # 新增

# src/embedding.py
async def embed_text(text: str) -> np.ndarray:
    # 新增

# src/semantic_search.py
class SemanticSearch:
    async def search_similar_sessions(query: str, top_k: int = 3) -> list[dict]:
        # 新增

# src/wiki_generator.py
class WikiGenerator:
    async def mine_and_generate(self, lookback_hours: int = 24) -> list[str]:
        # 新增

# src/pattern_learner.py
class PatternLearner:
    async def extract_tool_patterns(self) -> list[dict]:
        # 新增

# src/collective_intelligence.py
class CollectiveIntelligenceEngine:
    async def start_background_jobs(self):
        # 新增
```

## 附录 B：Token 预算分配

系统提示中集体智能部分总预算：**3000 tokens**

| 层 | 内容 | 预算 |
|---|------|------|
| L3 Wiki | 1-2 个相关 Wiki 页面的摘要 | ~1000 tokens |
| L4 相似对话 | top-2 相似会话摘要 | ~1000 tokens |
| L5 模式提示 | 活跃模式/工具建议 | ~500 tokens |
| 缓冲 | 安全余量 | ~500 tokens |

超过预算时按优先级截断：L3 > L4 > L5。

## 附录 C：风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 低质量 Wiki 注入系统提示 | Agent 输出错误建议 | 人工审核 + confidence 阈值 + 定期清理 |
| Embedding API 延迟影响会话启动 | 用户体验下降 | 异步预加载 + 本地缓存 + 超时降级 |
| sqlite-vec 扩展加载失败 | 向量搜索不可用 | 降级为纯 BM25 FTS5 搜索 |
| 历史数据 bootstrap 耗时过长 | 延迟上线 | 分批处理 + 断点续传 + 并行 embedding |
| 隐私泄露（跨用户信息暴露） | 信任风险 | 严格脱敏规则 + 代码审查 + 审计日志 |
| 系统提示膨胀超出模型上下文 | 性能下降 | 硬预算限制 + 优先级截断 |
