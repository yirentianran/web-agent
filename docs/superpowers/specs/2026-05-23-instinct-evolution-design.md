# Instinct Evolution — Web Agent 进化系统设计

## 概述

参考 ECC（Enterprise Code Companion）的 continuous-learning-v2 技能进化机制，为 Web Agent 重新设计进化系统。替换现有的 session_learner + evolution_evaluator 体系。

核心闭环：**事件捕获 → 本能提取 → 聚类生成 → 自动应用 → 信号跟踪 → 退化回滚**。

---

## 关键架构决策

| 决策 | 选择 |
|------|------|
| 进化作用域 | 系统级 — 所有用户会话共同贡献，进化结果共享 |
| 进化粒度 | 细粒度原子本能模型（参考 ECC instinct） |
| 数据捕获 | 事件驱动流 — 在 agent 执行循环中埋点 |
| 存储方式 | 数据库（SQLite）存储事件和本能，文件系统存储 SKILL.md |
| 提取时机 | 批量定时（每 10 分钟，≥ 30 条新事件才触发） |
| 进化领域 | tool_usage（工具使用模式）+ task_orchestration（任务编排） |
| 信心评分 | 出现次数 + 独立用户数，新本能从 0.3 起步 |
| 聚类方式 | Haiku 输出 normalized_trigger 标签，同名标签字符串匹配 |
| 应用策略 | confidence ≥ 0.7 自动应用 |
| 评估方式 | 成功率 + 失败趋势 → 退化检测 → 回滚 |
| 文件范围 | 默认仅 SKILL.md，按需扩展至 scripts/ 和 references/ |

---

## 数据模型

### observations（事件记录表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| session_id | TEXT | 关联会话 |
| user_id | TEXT | 关联用户 |
| event_type | TEXT | tool_call_start / tool_call_end / user_correct / user_retry / user_interrupt / session_complete / session_error |
| tool_name | TEXT | 仅 tool_call_* 类事件有值 |
| tool_input_summary | TEXT | 输入摘要 |
| tool_output_summary | TEXT | 输出摘要 |
| success | BOOLEAN | 工具调用是否成功 |
| error_message | TEXT | 失败原因 |
| duration_ms | INTEGER | 执行耗时 |
| created_at | REAL | |

### instincts（原子本能表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| domain | TEXT | tool_usage / task_orchestration |
| normalized_trigger | TEXT | Haiku 输出的归一化标签，用于聚类匹配 |
| trigger | TEXT | 触发条件描述 |
| action | TEXT | 建议行为 |
| confidence | REAL | 0.1-0.9，新本能从 0.3 起步 |
| source_count | INTEGER | 观察到该 pattern 的独立事件数 |
| unique_user_count | INTEGER | 去重用户数 |
| scope | TEXT | active / deprecated |
| source_evolution_id | INTEGER | 关联到产生此本能的 evolution_log |
| evidence_json | TEXT | 来源事件 ID 范围 |
| created_at | REAL | |
| updated_at | REAL | |

### 保留的表

- **evolution_log** — 不变（记录每次技能版本变更）
- **skill_eval_snapshots** — 不变（每日快照）
- **skills** — 不变（技能元数据）

### 废弃

- `src/session_learner.py` — 由 instinct_extractor 替代
- `src/evolution_evaluator.py` — 由 evolution_signals 替代
- `src/auto_evolve.py` — 移除
- `src/skill_feedback.py` + `skill_feedback` 表 — 由纯隐式信号替代
- `src/collective_intelligence.py` 中旧 eval 循环和 wiki mining 等 — 精简为仅驱动定时提取
- 前端 EvolutionPanel — 移除
- 旧 agent-driven evolution API（evolve-agent、evolve-status 等端点）

---

## 核心流程

### 阶段一：事件捕获

在 agent 执行循环中埋点，写入 `observations` 表：

**埋点位置：**

- `agent_server.py:_CliRunner.run()` — tool_call_start / tool_call_end
- `main_server.py:run_agent_task()` / `run_agent_task_container()` — session_complete / session_error
- 用户消息处理 — user_correct（用户重新描述任务）、user_retry（重复发类似指令）
- 取消处理 — user_interrupt

**事件类型：**

| 事件 | 触发时机 |
|------|----------|
| tool_call_start | 工具调用开始 |
| tool_call_end | 工具调用结束（含 success/error_message/duration_ms） |
| user_correct | 用户重新描述任务 |
| user_retry | 用户重复发类似指令 |
| user_interrupt | 用户点击停止 |
| session_complete | 会话正常结束 |
| session_error | 会话异常结束 |

### 阶段二：本能提取 + 聚类生成

单个后台定时任务（每 10 分钟，新事件 ≥ 30 条才触发）：

**提取：**

1. 扫描上次提取后新增的 observations，按 session 分组
2. 规则筛选有信号的片段：
   - 同 session 内同工具连续失败 2 次 → 提取反模式
   - user_correct 出现 → 提取纠正前的 tool call 序列
   - 同一 tool call 序列出现在 3+ 个 session → 提取为有效模式
3. 将筛选后的事件段喂给 Haiku，生成 instinct 候选：
   - trigger — 触发条件描述
   - action — 建议行为
   - domain — tool_usage 或 task_orchestration
   - **normalized_trigger** — 简短归一化标签（中英文均可），用于跨批次聚类匹配
   - initial_confidence — 0.3
4. 去重合并：相同 normalized_trigger+action 的候选合并，累加 source_count 和 unique_user_count

**聚类：**

5. 按 domain 分组，对 normalized_trigger 做字符串匹配，相同的归为一组
6. 聚类判定：
   - 2+ instinct、平均 confidence ≥ 0.5 → 生成技能改动
   - 仅 1 个 → 继续积累

**生成与应用：**

7. 用 Haiku 将聚类编译为 SKILL.md 变更：
   - input: instinct 列表 + 目标技能现有 SKILL.md + 文件清单
   - output: 变更后的 SKILL.md 内容（及按需的 scripts/references 文件）
8. 平均 confidence ≥ 0.7 → 自动写入文件系统 + 旧版本归档 → evolution_log（status=active）
9. 平均 confidence < 0.7 → 写入 evolution_log（status=proposed），管理面板人工审核

**信心动态调整（每次提取时）：**

- 同一 pattern 继续出现 → confidence += 0.05（上限 0.9）
- 新事件与该本能矛盾 → confidence -= 0.05（下限 0.1）
- user_correct 直接关联到该本能 → confidence -= 0.1
- 低于 0.3 → scope = deprecated

### 阶段三：信号跟踪与退化回滚

每日评估所有 active 状态的 evolution：

- 跟踪指标：**技能调用成功率**（tool_success_rate）和 **失败趋势**（连续下降天数）
- 失败趋势连续 7 天恶化 → under_review → 48h 后自动回滚
- 回滚：恢复 `.versions/` 中上一版本 → evolution_log（status=rolled_back）

---

## 技能存储与分发

- SKILL.md 存储于文件系统 `shared-skills/<name>/SKILL.md`
- 元数据存储于数据库 `skills` 表
- SDK 发现：每次启动 agent 会话时 `load_skills()` 扫描文件系统 → 注入 system prompt
- 进化生效：新版本写入后，下次会话启动自动获取。已运行会话不受影响

---

## 模块清单

| 模块 | 路径 | 职责 |
|------|------|------|
| observation | `src/observation.py` | 事件捕获 API，写入 observations 表 |
| instinct_extractor | `src/instinct_extractor.py` | 定时扫描 + 规则筛选 + Haiku 提取本能（含 normalized_trigger）+ 标签匹配聚类 + 生成 SKILL.md 改动 + 信心调整 |
| evolution_signals | `src/evolution_signals.py` | 每日成功率 + 失败趋势跟踪 + 退化检测 + 回滚 |
| evolution_log | `src/evolution_log.py` | 保留，evolution_log + skill_eval_snapshots CRUD |
| database | `src/database.py` | 新增 observations、instincts 表 |
| CI engine | `src/collective_intelligence.py` | 精简，仅驱动定时提取和每日评估 |

### 前端 — 进化管理面板

**整体布局：**

```
┌──────────────────────────────────────────────────────────┐
│  指标卡片行                                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ 今日事件  │ │ 活跃本能  │ │ 待审核进化 │ │ 本周自动  │   │
│  │          │ │          │ │          │ │ 应用数    │   │
│  │  1,247   │ │   43     │ │    3     │ │   12     │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
├──────────────────────────────────────────────────────────┤
│  流水线漏斗                                                │
│  observations ──→ instincts ──→ clusters ──→ evolutions  │
│     (1,247)        (43)          (8)       (3 applied,   │
│                                              2 proposed) │
├──────────────────────────────────────────────────────────┤
│  Tab: [进化列表] [本能列表] [事件浏览]                       │
│                                                          │
│  (主内容区 — 表格 / 详情)                                   │
└──────────────────────────────────────────────────────────┘
```

**指标卡片** — 调用 `GET /api/admin/evolution/stats` 获取：

| 指标 | 说明 |
|------|------|
| 今日事件数 | 当日新增 observations |
| 活跃本能数 | scope=active 的 instinct 数 |
| 待审核进化数 | status=proposed 的 evolution 数 |
| 本周自动应用数 | 近 7 天自动应用的 evolution 数 |

**流水线漏斗** — 实时反映各阶段转化量，每个阶段可点击跳转到对应 Tab。

**三个 Tab：**

1. **进化列表** — 改造现有 OverviewTable，每行展示：
   - 技能名称、版本范围、关联 instinct 数、状态（active/proposed/under_review/rolled_back）
   - 点击行 → EvolutionDetail（增加关联 instinct 列表展示）
   - 操作按钮：proposed → keep/discard；under_review → keep/rollback

2. **本能列表** — 新增，可筛选（domain/confidence 范围），展示：
   - normalized_trigger、trigger、action、confidence、source_count、unique_user_count
   - 点击行展开来源事件摘要

3. **事件浏览** — 只读，可筛选（session_id / event_type），用于排查验证

**组件清单：**

| 组件 | 路径 | 改动 |
|------|------|------|
| EvolutionPage | `frontend/src/pages/EvolutionPage.tsx` | 重构为指标卡片 + 漏斗 + Tab 布局 |
| StatsCards | `frontend/src/pages/evolution/StatsCards.tsx` | 新增，4 个指标卡片 |
| PipelineFunnel | `frontend/src/pages/evolution/PipelineFunnel.tsx` | 新增，流水线漏斗图 |
| EvolutionDetail | `frontend/src/pages/evolution/EvolutionDetail.tsx` | 增加关联 instinct 列表 |
| InstinctList | `frontend/src/pages/evolution/InstinctList.tsx` | 新增，本能列表 + 筛选 |
| ObservationBrowser | `frontend/src/pages/evolution/ObservationBrowser.tsx` | 新增，事件只读浏览 |
| EvolutionPanel | `frontend/src/components/EvolutionPanel.tsx` | 移除 |

### API 端点

**保留并改造：**
- `GET /api/admin/evolution/overview` — 增加关联 instinct 数量
- `GET /api/admin/evolution/{id}` — 增加关联 instinct 列表
- `GET /api/admin/evolution/{id}/diff` — 不变
- `POST /api/admin/evolution/{id}/review` — 审核 proposed 的进化（keep/discard）

**新增：**
- `GET /api/admin/evolution/stats` — 仪表板指标（今日事件数、活跃本能数、待审核数、本周自动应用数、漏斗各阶段数量）
- `GET /api/admin/instincts` — 本能列表，按 domain/scope/confidence 筛选
- `GET /api/admin/instincts/{id}` — 本能详情 + 来源事件
- `GET /api/admin/observations` — 事件浏览，按 session_id/event_type 筛选，分页

**废弃：**
- `POST /api/skills/{skill_name}/evolve-agent` + 相关端点
- `GET /api/admin/skills/evolution-candidates`

---

## 验证计划

1. **单元测试** — observation 写入、instinct 去重合并、confidence 调整、标签匹配聚类、退化检测
2. **集成测试** — 事件写入 → 定时提取 → 聚类 → skill 文件写入 全链路
3. **E2E 测试** — agent 执行会话 → 产生 observations → 提取 instinct → 自动应用 → 新会话使用进化后技能
4. **回滚测试** — 退化检测触发 → 自动回滚 → 技能恢复旧版本
5. **覆盖目标** — 80%+

---

## 与现有系统的关系

| 保留 | 废弃 |
|------|------|
| evolution_log 表 | session_learner.py |
| skill_eval_snapshots 表 | evolution_evaluator.py |
| skills 表 | auto_evolve.py |
| skill versioning + rollback | skill_feedback.py + skill_feedback 表 |
| EvolutionPage + EvolutionDetail（改造） | collective_intelligence.py 旧循环 |
| skill_manager.py | EvolutionPanel |
| collective_intelligence.py（精简） | 旧 agent-driven evolution API |
