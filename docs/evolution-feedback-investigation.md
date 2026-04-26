# Web Agent 自进化机制与反馈机制调查报告

> 调查日期：2026-04-26 | 分支：`optimize-code` | 仅调查分析，未修改任何代码

---

## 一、总体结论

**Web Agent 已有自进化机制，且反馈机制是其核心数据来源，两者是上下游依赖关系，反馈机制仍然至关重要。**

系统共定义了 **7 种技能生命周期机制**（见 `docs/skill-creation-mechanisms.md`），其中反馈机制（#7）是进化机制（#5）的**唯一数据源**。没有反馈，进化代理 (Evolution Agent) 将失去判断"改什么、怎么改"的用户信号。

---

## 二、七种技能生命周期机制一览

| # | 机制 | 触发方式 | 产出 | 状态 |
|---|------|----------|------|------|
| 1 | ZIP 上传（个人） | 用户在 SkillsPanel 上传 | 个人技能写入 workspace | 已实现 |
| 2 | ZIP 上传（共享） | 用户在 SkillsPanel 上传（Shared 标签） | 共享技能写入 `shared-skills/` | 已实现 |
| 3 | Agent 自主创建技能 | Agent 在对话中检测可复用模式 | 个人技能（经用户确认后写入 `.claude/skills/`） | 已实现 |
| 4 | 提升（个人→共享） | 用户点击或 Agent 调用 API | 复制个人技能到 `shared-skills/` | 已实现 |
| **5** | **进化改进** | **用户通过 EvolutionPanel 触发** | **Agent 会话生成 `versions/v{N}/`，用户手动激活** | **已实现** |
| 6 | 共享技能同步 | 每次会话前自动执行 | 符号链接(Unix)或复制(Windows)到 workspace | 已实现 |
| **7** | **反馈提交** | **会话完成后用户通过 SkillFeedbackWidget 提交** | **反馈记录写入 SQLite `skill_feedback` 表** | **已实现** |

---

## 三、反馈机制详解

### 3.1 存储层

**两套管理器并存：**

| 管理器 | 位置 | 存储方式 | 状态 |
|--------|------|----------|------|
| `SkillFeedbackManager` | `src/skill_feedback.py:32` | 文件系统 (`data/training/skill-feedback/*.jsonl`) | 遗留，仅 fallback |
| `DBSkillFeedbackManager` | `src/skill_feedback.py:149` | SQLite (`data/web-agent.db`, 表 `skill_feedback`) | **当前主力** |

**数据库表结构**（`src/database.py:109-122`）：

```sql
skill_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT,
    rating INTEGER NOT NULL,          -- 1-5 星
    comment TEXT NOT NULL DEFAULT '',  -- 用户文字反馈
    user_edits TEXT NOT NULL DEFAULT '', -- 用户手动修改内容
    skill_version TEXT NOT NULL DEFAULT '',
    conversation_snippet TEXT NOT NULL DEFAULT '', -- 对话片段(≤2000字符)
    created_at REAL NOT NULL
)
```

### 3.2 API 端点

| 端点 | 方法 | 位置 (`main_server.py`) | 权限 |
|------|------|--------------------------|------|
| `/api/skills/{name}/feedback` | POST | :2886 | 登录用户 |
| `/api/skills/{name}/analytics` | GET | :2924 | 登录用户 |
| `/api/admin/skills/analytics` | GET | :2939 | **管理员** |
| `/api/skills/{name}/suggestions` | GET | :2951 | 登录用户 |
| `/api/admin/feedback` | GET | :3461 | **管理员** |
| `/api/users/{uid}/feedback` | GET | :3985 | 登录用户 |

### 3.3 前端组件

| 组件 | 文件 | 功能 |
|------|------|------|
| `SkillFeedbackWidget` | `frontend/src/components/SkillFeedbackWidget.tsx` | 会话结束后展示星级评分+评论+修改内容，可折叠 |
| `FeedbackPage` | `frontend/src/components/FeedbackPage.tsx` | 管理员查看所有反馈：统计表 + 详细条目列表 |

### 3.4 用户反馈流程

```
用户完成会话
  → ChatArea.tsx 检测 sessionState === "completed"
  → 从 tool_use 消息中提取技能名称
  → 渲染 SkillFeedbackWidget
  → 用户选择 1-5 星 + 可选评论 + 可选"你改了什么？"
  → POST /api/skills/{name}/feedback (JWT 认证)
  → DBSkillFeedbackManager.submit_feedback()
  → INSERT INTO skill_feedback
```

---

## 四、自进化机制详解

### 4.1 知识抽取规则

**文件**：`src/learn-extraction.md`（注入到 Agent 系统提示词中）

**用户请求摘要**（Agent 被动响应）：
1. 锁定范围（识别"this"指什么）
2. 仅从锁定范围提取（排除系统组件）
3. 确认后再写入

**主动提取工作流**（Agent 自主触发）：
1. **Review** — 识别核心可复用洞察
2. **Classify** — 标签：`error-resolution` / `debugging-technique` / `workaround` / `project-convention`
3. **Check duplicates** — 搜索已有技能
4. **Quality gate** — 必须具体、可操作、未被覆盖、可复用
5. **Confirm** — 写入前询问用户
6. **Write** — 使用 skill-creator 创建 `.claude/skills/<name>/SKILL.md` + `skill-meta.json`
7. **Promote**（如果是共享候选）— 调用 `POST /api/users/{uid}/skills/{name}/promote`

### 4.2 进化代理引擎

**核心函数**：`run_evolution_agent()` (`main_server.py:3112`)

**执行流程**：

```
1. 收集反馈 → mgr.db_get_feedback_for_evolution(skill_name)
   ├── high_quality (≥4 星): 用户喜欢什么
   ├── low_rated (≤2 星且有评论): 用户不满意什么
   └── user_edits (有手动修改): 用户实际改了什么

2. 解析技能目录 → shared-skills/{skill_name}/
   列出所有现有文件

3. 创建版本输出目录 → versions/v{N}/

4. 构建进化提示词 → build_evolution_prompt()
   包含: 当前 SKILL.md 全文 + 技能目录结构 + 三类反馈数据

5. 构建 SDK 选项 → _build_evolution_sdk_options()
   ├── 同步共享+个人技能到版本目录
   ├── 提供全部工具 (Read,Write,Edit,Bash,Glob,Grep,WebFetch,WebSearch,Agent,Skill)
   └── permission_mode="acceptEdits"

6. 启动完整 Agent 会话 → ClaudeSDKClient
   Agent 自主重写 SKILL.md 和/或创建新文件

7. 流式输出进度 → message buffer (供前端轮询)

8. 返回结果 → task_id, status, 生成的文件列表, 摘要
```

### 4.3 进化判定阈值

| 参数 | 当前代码值 | 所在位置 |
|------|-----------|----------|
| 最少反馈数 | `SHOULD_EVOLVE_MIN_COUNT = 10` | `src/skill_evolution.py:19` |
| 最高平均评分 | `SHOULD_EVOLVE_MAX_RATING = 4.5` | `src/skill_evolution.py:20` |

判定逻辑：`count >= 10 AND avg_rating < 4.5` → 该技能需要进化

### 4.4 进化相关 API

| 端点 | 方法 | 位置 | 权限 |
|------|------|------|------|
| `/api/skills/{name}/evolve-agent` | POST | :3278 | **登录用户**（已去管理员限制） |
| `/api/skills/{name}/evolve-status/{task_id}` | GET | :3337 | **仍要求管理员**（不一致） |
| `/api/skills/{name}/version-files/{vn}` | GET | :3381 | **仍要求管理员** |
| `/api/skills/{name}/version-file/{vn}` | GET | :3412 | **仍要求管理员** |
| `/api/skills/{name}/activate-version` | POST | :3067 | 登录用户 |
| `/api/skills/{name}/rollback` | POST | :3089 | 登录用户 |
| `/api/admin/skills/evolution-candidates` | GET | :3434 | **仍要求管理员** |

### 4.5 前端进化面板

**组件**：`EvolutionPanel` (`frontend/src/components/EvolutionPanel.tsx`)

三个标签页：
- **Candidates**：展示待进化技能列表，提供 Preview / Versions / Rollback
- **Versions**：版本管理
- **Review**：查看 Agent 生成的文件树，逐文件预览，提供 Activate / Rollback / Cancel

**轮询机制**：`evolveAgent()` 获取 `task_id` 后，每 3 秒轮询 `getEvolveStatus()` 直到状态变为 "complete" 或 "failed"

---

## 五、反馈 → 进化 数据流（关键路径）

```
skill_feedback 表 (SQLite)
        │
        ▼
get_evolution_candidates()
  SELECT skill_name, COUNT(*) cnt, AVG(rating) avg_r
  FROM skill_feedback
  GROUP BY skill_name
  HAVING cnt >= 10 AND avg_r < 4.5    ← 筛选待进化技能
        │
        ▼
db_get_feedback_for_evolution(skill_name)
  分桶:
  ├── high_quality: rating >= 4     → "用户认为好的部分"
  ├── low_rated: rating <= 2 + 有评论 → "用户认为差的部分"
  └── user_edits: 有手动修改内容    → "用户实际改了什么"
        │
        ▼
build_evolution_prompt()
  将上述数据格式化后注入进化 Agent 的系统提示词
        │
        ▼
run_evolution_agent()
  Agent 根据反馈数据自主重写技能，输出到 versions/v{N}/
        │
        ▼
用户审核 → 点击 Activate → 替换当前技能文件
```

**结论：反馈是进化机制的唯一数据源。没有反馈，进化 Agent 将失去判断依据。**

---

## 六、已实现 vs 未实现（设计文档对比）

### 6.1 设计意图 vs 实际代码差异

| 设计文档要求 | 实际代码状态 | 风险等级 |
|-------------|-------------|----------|
| **去掉所有管理员限制** (P0) | `evolve-status`、`version-files`、`evolution-candidates` 仍要求管理员 | 中 — 用户触发进化后无法查看状态/结果 |
| **降低进化阈值：≥5 条反馈，均分 <4.0** | 代码仍为 ≥10 条，均分 <4.5 | 中 — 进化触发门槛偏高，反馈积累慢 |
| **去掉 `.pending/` 中间目录** (P1) | 代码仍引用 `_pending_dir` | 低 — 提升流程有冗余步骤 |
| **移除 A/B 测试** | `src/ab_testing.py` 及端点仍存在 | 低 — 死代码残留 |
| **统一 `data/skills/` 和 `shared-skills/`** (P0) | `skill_evolution.py` 仍引用 `data_root / "skills"` | 中 — 双路径容易混淆 |
| **进化闭环验证** (P3) | 未实现 — 进化激活后无跟踪新版本评分改善 | 低 — 无法量化进化效果 |

### 6.2 关键缺失功能

| 缺失项 | 影响 |
|--------|------|
| **无自动进化触发** | 虽有 `should_evolve()` 判定函数，但无后台进程/cron/事件处理器自动触发进化。100% 依赖用户手动操作 EvolutionPanel |
| **无进化后验证** | 技能激活新版本后，无机制对比新旧版本评分变化，无法量化"进化是否有效" |
| **Agent 自主创建技能无自动反馈关联** | Agent 通过 learn-extraction.md 创建技能后，无自动为其建立反馈收集上下文 |

---

## 七、反馈机制仍然有用的证据

### 7.1 反馈是进化不可替代的输入

- `run_evolution_agent()` 第一行就是 `feedback = await mgr.db_get_feedback_for_evolution(skill_name)`
- 若无反馈数据，`build_evolution_prompt()` 生成的提示词将不包含任何用户洞察
- `get_evolution_candidates()` 的 SQL 查询直接依赖 `skill_feedback` 表：`HAVING cnt >= 10 AND avg_r < 4.5`

### 7.2 反馈提供三类不可替代的信息

| 信息类型 | 作用 | 替代来源 |
|----------|------|----------|
| 用户满意度评分 (1-5) | 量化技能质量，判定是否需要进化 | **无替代** |
| 用户评论文本 | 指出具体问题/亮点 | **无替代** |
| 用户手动修改内容 | 揭示用户实际需求与技能输出的差距 | **无替代** |

### 7.3 反馈的独立价值

即使不考虑进化用途，反馈机制还承担：
- **管理员质量监控**：通过 `/api/admin/feedback` 全局了解技能使用情况
- **用户改进建议**：`suggest_improvements()` 提供启发式建议
- **技能质量仪表盘**：前端 `FeedbackPage` 展示评分分布

---

## 八、风险评估

| 风险 | 严重程度 | 说明 |
|------|----------|------|
| 反馈数据过少 | **高** | 进化阈值要求 ≥10 条反馈。若用户不主动提交反馈，进化永远无法触发。当前无任何引导或激励机制 |
| 管理员权限不一致 | **中** | 用户可触发进化但查看结果需管理员权限，造成流程断裂 |
| 双版本系统并存 | **中** | 扁平文件版本和目录结构版本共存，增加维护复杂度 |
| 两套反馈管理器并存 | **低** | DB 版已稳定运行，文件版仅作 fallback，但代码重复 |
| 技能名称匹配脆弱 | **中** | 前端从 `tool_use` 消息名推导技能名，可能与实际目录名不匹配 |

---

## 九、建议路线（仅供决策参考，未实施）

1. **立即可做**：移除 `evolve-status`、`version-files`、`evolution-candidates` 的 `require_admin()`，使非管理员用户能完整体验进化流程
2. **短期**：降低进化阈值至 ≥5 条反馈 / 均分 <4.0，降低启动门槛
3. **中期**：增加自动进化触发器（cron 或事件驱动），减少对手动操作的依赖
4. **长期**：实现进化闭环验证，量化和追踪每次进化的实际效果

---

## 十、关键文件索引

| 文件 | 作用 |
|------|------|
| `src/skill_feedback.py` | 反馈管理器（DB + 文件双实现）、版本管理、进化候选查询 |
| `src/skill_evolution.py` | 进化管理器：`should_evolve()`、候选逻辑 |
| `src/learn-extraction.md` | 知识抽取规则（注入 Agent 系统提示词） |
| `src/database.py:109-122` | `skill_feedback` 表定义 |
| `src/ab_testing.py` | A/B 测试（设计文档已标记移除但代码仍存） |
| `main_server.py:2862-2960` | 反馈 API 端点 |
| `main_server.py:2959-3458` | 进化 API 端点 + `run_evolution_agent()` + `build_evolution_prompt()` |
| `docs/skill-creation-mechanisms.md` | 七种机制的设计文档 |
| `docs/plans/feedback-improvement-plan.md` | 反馈系统改进计划（三阶段） |
| `frontend/src/components/ChatArea.tsx:336-406` | 会话结束触发反馈控件 |
| `frontend/src/components/SkillFeedbackWidget.tsx` | 星级评分控件 |
| `frontend/src/components/EvolutionPanel.tsx` | 进化面板（三标签页+轮询） |
| `frontend/src/components/FeedbackPage.tsx` | 管理员反馈管理页 |
| `frontend/src/hooks/useSkillEvolutionApi.ts` | 进化相关前端 API 封装 |
