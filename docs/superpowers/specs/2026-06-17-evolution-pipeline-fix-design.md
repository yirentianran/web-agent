---
name: 进化管道修复与简化
description: 修复 Evolution 页面的数据管道，启用 agent 行为反馈闭环，简化评分模型，清理失效组件
type: design
status: draft
created: 2026-06-17
---

# 进化管道修复与简化

## 1. 问题陈述

Evolution 模块（集体智能 L5 层）的 UI 框架和基础数据采集工作正常，但存在三个层面的问题：

### 1.1 数据管道 Bug（HIGH）

| Bug | 影响 |
|-----|------|
| 快照日期类型不匹配（`evolution_signals.py:63-76`）—— SQL 比较 UNIX 时间戳与日期字符串，所有比较结果为 0 | 所有每日快照 `total`/`usage_count` 恒为 0，趋势图、信号分解无数据 |
| 版本 diff 路径错误（`main_server.py:6456-6462`）—— 查找 `SKILL_v{n}.md`，实际路径为 `versions/v{n}/SKILL.md` | 自动应用的进化无法查看 diff |
| RollbackTimeline 调用不存在的 API `/api/admin/skills/evolution-timeline/{id}` | 回滚时间线始终为空 |
| `avg_rating` 硬编码为 0（`evolution_signals.py:115`） | 评分信号无意义 |
| `unique_user_count` 从未递增（`instinct_extractor.py:79-87`） | 用户数列始终为 1 |

### 1.2 反馈闭环断裂

`build_system_prompt()` L4 Semantic Context 段（`main_server.py:1085`）被注释，注释说明 "disabled, re-enable when data pipeline is ready"。提取出的 instinct 和 SKILL.md 变更不会注入到 agent 的 system prompt 中，进化数据采了、本能提取了，但 agent 行为不受影响。

### 1.3 设计过重

- 每日快照预计算（`skill_eval_snapshots` 表 + `EvolutionSignals.daily_eval`）增加了维护复杂度，但评分模型本身缺乏验证
- `PipelineFunnel`、`RollbackTimeline` 等展示组件没有对应的可靠数据源

---

## 2. 设计目标

1. **反馈闭环启用**：L4 context 注入，让本能数据真正影响 agent 行为
2. **数据展示可验证**：趋势图、信号分解展示真实聚合数据，而非空快照
3. **减少维护负担**：删除不可靠的预计算管道，改为实时聚合

---

## 3. 架构变更

### 3.1 删除

| 删除项 | 原因 |
|--------|------|
| `skill_eval_snapshots` 表 | 日期类型匹配 bug 导致快照全为空，且预计算设计本身过重 |
| `src/evolution_signals.py` | 每日评估、退化检测、自动回滚——全部依赖空快照 |
| `CollectiveIntelligenceEngine._daily_eval_loop()` | 后台每日评估任务 |
| 前端 `RollbackTimeline.tsx` | 无服务器端点 |
| 前端 `PipelineFunnel.tsx` | 依赖快照数据 |
| `GET /api/admin/evolution/{id}/evaluation-snapshots` 端点 | 不再需要 |

### 3.2 修改

| 修改项 | 说明 |
|--------|------|
| `ScoreTrendChart.tsx` | 数据源改为新 API `GET /api/admin/evolution/{id}/trend`，从 observations 实时聚合 |
| `SignalBreakdown.tsx` | 只展示 `success_rate` 和 `usage_count`，去掉 `avg_rating` |
| `EvolutionDetail.tsx` | 删除 RollbackTimeline 引用，更新趋势/信号数据获取 |
| `useEvolutionApi.ts` | 新增 `useEvolutionTrend`，去掉 snapshot/timeline hooks |
| `build_system_prompt()` L4 段 | 启用：为当前查询匹配 top-3 活跃本能，注入 prompt |

### 3.3 新增

| 新增 | 说明 |
|------|------|
| `GET /api/admin/evolution/{id}/trend` | 实时聚合该进化关联 observations 的 tool 成功率和使用量，按天分桶 |
| `GET /api/admin/evolution/{id}/signals` | 返回最新周期成功率、使用量 vs 基线 delta |

### 3.4 保留不变

- `ObservationsStore` + `ToolObserver` — 数据采集工作正常
- `InstinctExtractor` + 10 分钟定时提取 — 工作正常
- `InstinctStore` + `evolution_log` 表 — 工作正常
- 前端 `StatsCards`、`OverviewTable`、`InstinctList`、`ObservationBrowser`、`VersionDiff` — 工作正常

---

## 4. 数据层：评分模型简化

### 4.1 核心指标

仅保留两个信号，从 `observations` 表实时聚合：

| 指标 | SQL 来源 | 聚合方式 |
|------|----------|----------|
| `tool_success_rate` | `observations` WHERE `event_type = 'tool_call_end'`, 按 instinct 关联 session 的时间范围 | `COUNT(success=true) / COUNT(*)` |
| `usage_count` | 该 evolution 关联的 session 数 | `COUNT(DISTINCT session_id)` WHERE instinct 有命中 |

### 4.2 趋势 API

```
GET /api/admin/evolution/{id}/trend?days=30
→ [{date: "2026-06-17", success_rate: 0.85, usage_count: 12}, ...]
```

按天分桶，SQL 直接对 observations 时间范围 GROUP BY date。

### 4.3 信号 API

```
GET /api/admin/evolution/{id}/signals
→ {
    success_rate: {current: 0.85, baseline: 0.72, delta: +0.13},
    usage_count:  {current: 12,   baseline: 8,    delta: +4}
  }
```

Baseline 为进化创建后前 7 天的平均值，current 为最近 7 天的平均值。

---

## 5. 应用层：L4 Context 注入

### 5.1 匹配逻辑

在 `build_system_prompt()` 的 L4 段中：

1. 查询 `instincts` 表 `WHERE status='active' AND confidence >= 0.5`
2. 用当前用户消息做关键词匹配（`normalized_trigger` 字段），取 top 3
3. 格式化为 markdown 追加到 system prompt

### 5.2 Prompt 格式

```markdown
## Learned Patterns

The following patterns have been identified from past experience:

- {instinct.guidance}
- {instinct.guidance}
- {instinct.guidance}
```

### 5.3 容错

- 无匹配本能 → 静默跳过，不追加 L4 段
- DB 查询失败 → 静默降级，不影响正常流程
- instinct 是增强，不是必需品

---

## 6. Bug 修复清单

| Bug | 修复方式 |
|-----|----------|
| 快照日期类型不匹配 | 不修复——直接删除 `EvolutionSignals` 和 `skill_eval_snapshots` 表 |
| 版本 diff 路径错误 | `main_server.py:6456` 改为 `versions/v{from_ver}/SKILL.md` |
| RollbackTimeline 无端点 | 删除前端组件 |
| `avg_rating` 硬编码 0 | 从信号模型中删除 |
| `unique_user_count` 不递增 | `instinct_extractor.py` upsert 时增加 `unique_user_count += 1`（当不同用户触发时） |
| 大小写导入不匹配 | `App.tsx` 导入改为 `./pages/evolutionpage` |

---

## 7. 文件变更总览

| 操作 | 文件 |
|------|------|
| 删除 | `src/evolution_signals.py` |
| 修改 | `main_server.py` — 启用 L4 context、新增 trend/signals API、删除 snapshot API、修 diff 路径 |
| 修改 | `src/instinct_extractor.py` — 修 unique_user_count 不递增 |
| 修改 | `src/collective_intelligence.py` — 删除 daily eval loop |
| 修改 | `src/database.py` — DROP skill_eval_snapshots 表（迁移） |
| 修改 | `frontend/src/App.tsx` — 修导入大小写 |
| 修改 | `frontend/src/pages/evolution/EvolutionDetail.tsx` — 删 RollbackTimeline、更新数据获取 |
| 修改 | `frontend/src/pages/evolution/ScoreTrendChart.tsx` — 新数据源 |
| 修改 | `frontend/src/pages/evolution/SignalBreakdown.tsx` — 两个信号 |
| 修改 | `frontend/src/hooks/useEvolutionApi.ts` — 新增/删除 hooks |
| 删除 | `frontend/src/pages/evolution/RollbackTimeline.tsx` |
| 删除 | `frontend/src/pages/evolution/PipelineFunnel.tsx` |

---

## 8. 测试计划

- [ ] 趋势 API 返回正确的按天聚合数据
- [ ] 信号 API 返回正确的最新 vs 基线 delta
- [ ] L4 context 注入在 system prompt 中可见（单元测试 assert 包含 `## Learned Patterns`）
- [ ] L4 context 在无匹配本能时静默跳过
- [ ] diff API 返回正确路径的版本对比
- [ ] 前端 ScoreTrendChart 渲染趋势数据
- [ ] 前端 SignalBreakdown 展示 success_rate + usage_count
- [ ] 前端 EvolutionDetail 无 RollbackTimeline 引用
- [ ] 手动触发提取后，instinct 正常 upsert（含 unique_user_count）
