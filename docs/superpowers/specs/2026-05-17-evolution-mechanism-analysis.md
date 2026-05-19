# 进化机制分析与优化报告

> 生成日期: 2026-05-17
> 分析范围: `src/skill_manager.py`, `src/skill_evolution.py`, `src/skill_feedback.py`, `src/collective_intelligence.py`, `src/pattern_learner.py`, `src/wiki_generator.py`, `src/auto_evolve.py`

---

## 一、现有机制诊断

Web agent 实现了六层记忆架构（L0-L5），包含技能注册、反馈收集、自动晋升、Wiki 生成、模式学习等进化机制。代码分析发现：**骨架完整，但多处关键路径未闭环**。

### 1.1 合理的部分

| 组件 | 评价 |
|------|------|
| **L1 用户记忆** (`memory.py`) | SQLite + 文件双存储，`_deep_merge` 支持增量知识积累，设计清晰 |
| **技能版本管理** (`skill_manager.py`) | 扁平 `{skill}@vN/` 目录约定 + DB 状态追踪，备份/回滚逻辑完整 |
| **反馈系统** (`skill_feedback.py`) | 1-5 星评分 + 评论 + user_edits + conversation_snippet，数据结构合理 |
| **Hooks 审计链** (`hooks/post_tool_use.py`) | tool 调用写入 `.audit/tools.jsonl`，为模式学习提供数据源 |
| **安全钩子** (`hooks/pre_tool_use.py`) | 阻止危险命令，防止学习数据被污染 |

### 1.2 发现的 6 个问题

| 优先级 | 问题 | 文件 | 影响 |
|--------|------|------|------|
| **P0** | 自动晋升没有闭环 | `collective_intelligence.py:59-70` | `_auto_promotion_loop` 写入 `skill_promotion_queue` 但永远停留在 `pending`，优质技能无法推广 |
| **P1** | 使用统计失真 | `skill_manager.py:152-170` | `record_usage` 在技能加载时调用，不在实际使用时，晋升阈值基于错误数据 |
| **P1** | Wiki 只收录高质量内容 | `wiki_generator.py:68-70` | `avg_rating < 4.0` 被跳过，反模式和踩坑经验永远缺失 |
| **P2** | 模式学习者名不副实 | `pattern_learner.py:72-77` | `tool_success_rates` 只有 count，没有成功/失败区分 |
| **P2** | 技能进化缺乏自动执行 | `skill_evolution.py` | `should_evolve()` 能识别但不能自动修复，低质量技能持续伤害用户 |
| **P2** | 晋升队列无过期机制 | `skill_promotion_queue` 表 | 未审批记录永久堆积 |

---

## 二、优化方案与实施

### Phase 1: 自动晋升闭环（P0）

**目标**: 让自动晋升真正执行，有审批 → 执行 → 过期清理的完整生命周期。

#### 新增方法 (`src/skill_manager.py`)

| 方法 | 作用 |
|------|------|
| `get_pending_promotions()` | 查询所有 pending 状态的晋升队列 |
| `execute_promotion(skill_name, reviewed_by)` | 复制个人技能到 `shared-skills/` + 更新 DB `source='shared'` + 标记 `approved` |
| `reject_promotion(skill_name, reason, reviewed_by)` | 拒绝晋升，记录原因 |
| `cleanup_expired_promotions(days)` | 清理 30 天前仍为 pending 的记录（auto-reject） |

#### 新增 API 端点 (`main_server.py`)

| 端点 | 方法 | 作用 |
|------|------|------|
| `/api/skills/promotion/pending` | GET | 查询待审批队列 |
| `/api/skills/promotion/{skill_name}/approve` | POST | 审批通过并执行晋升 |
| `/api/skills/promotion/{skill_name}/reject` | POST | 拒绝晋升 |
| `/api/skills/promotion/cleanup` | POST | 手动触发过期清理 |

#### 后台循环增强 (`src/collective_intelligence.py`)

`_auto_promotion_loop` 每次扫描后调用 `cleanup_expired_promotions()` 清理过期记录。

---

### Phase 2: 修复使用统计（P1）

**目标**: usage 记录从"加载时"改为"反馈驱动"。

#### 修改点

1. **移除加载时记录** (`main_server.py`):
   - `build_sdk_options()` 中的 `record_usage(action="load")` 已移除
   - 添加注释说明原因

2. **改写晋升检测逻辑** (`src/skill_manager.py`):
   - `check_auto_promotion()` 从 JOIN `skill_usage` 改为 JOIN `skill_feedback`
   - 常量 `AUTO_PROMOTE_MIN_USES=10` → `AUTO_PROMOTE_MIN_FEEDBACK=5`
   - 现在基于"有多少用户主动反馈"而非"加载了多少次"判断技能质量

#### 为什么改用反馈计数

- 加载时计数包含所有被加载但未使用的技能，数据严重膨胀
- 反馈是用户主动行为，更能反映技能的真实影响力
- 结合 avg_rating 和 unique_users，形成更可靠的质量信号

---

### Phase 3: Wiki 反模式收录（P1）

**目标**: 让 Wiki 记录"不要这样做"的警示知识。

#### 分级策略

| avg_rating | 页面类型 | status | 内容 |
|------------|----------|--------|------|
| >= 4.0 | 正常技能页 | `draft` | 现有逻辑不变 |
| 2.0 ~ 3.9 | 警告页 | `warning` | 报告问题 + 用户编辑的正确做法 |
| < 2.0 | 反模式页 | `anti-pattern` | 常见问题汇总 + 投诉关键词统计 |

#### 新增方法 (`src/wiki_generator.py`)

| 方法 | 作用 |
|------|------|
| `_generate_warning_page()` | 生成警告型 Wiki 页，包含问题列表和用户建议修复 |
| `_generate_antipattern_page()` | 生成反模式页，包含常见投诉关键词和低分反馈汇总 |
| `_extract_issues()` | 从低分反馈中提取格式化问题列表 |
| `_find_common_complaints()` | 使用 Counter 统计低分评论中的高频投诉关键词 |

---

### Phase 4: 模式学习增强（P2）

**目标**: 让 PatternLearner 真正学习"成功/失败"模式。

#### 新增方法 (`src/pattern_learner.py`)

| 方法 | 作用 |
|------|------|
| `_calculate_tool_success_rates()` | 关联 `tool_use` 和 `tool_result` 消息，计算真实成功率 |

#### 工作原理

1. 查询最近 24 小时的 `tool_use` 和 `tool_result` 消息
2. 通过 `tool_use_id` 关联两者
3. 检查 `tool_result.is_error` 判断成功/失败
4. 输出: `{"total": N, "success": N, "failure": N, "rate": 95.0}`

#### 输出格式对比

| 字段 | 修改前 | 修改后 |
|------|--------|--------|
| `tool_success_rates.Read` | `{"count": 45200}` | `{"total": 45200, "success": 44800, "failure": 400, "rate": 99.1}` |

---

### Phase 5: 自动进化分级策略（P2）

**目标**: 实现反馈驱动的分级自动进化，不同风险等级用不同策略。

#### 新增文件 `src/auto_evolve.py`

```
AutoEvolvePolicy
├── EvolveAction (枚举)
│   ├── APPLY_EDITS    — 用户已知正确答案 → 直接合并（最安全）
│   ├── AUTO_FIX       — 明确 bug 描述 → Agent 自动修复
│   ├── PROPOSE        — 模糊反馈 → 生成改进建议待审批
│   ├── REQUIRE_REVIEW — 高频技能评分下降 → 强制人工审查
│   └── SKIP           — 信号不足
├── analyze_skill(skill_name) → EvolveDecision
├── analyze_all_candidates() → list[EvolveDecision]
├── apply_user_edits(skill_name, user_edits) → 创建新版本
└── _summarize_feedback(skill_name) → FeedbackSummary
```

#### 分类逻辑

| 条件 | 策略 | 风险等级 |
|------|------|----------|
| 用户提供了 `user_edits` | `APPLY_EDITS` — 直接合并 | 最低 |
| 反馈包含明确 bug 关键词 | `AUTO_FIX` — 生成修复版本 | 低 |
| 反馈模糊，无具体描述 | `PROPOSE` — 生成建议 | 中 |
| 使用次数 >= 50 + 评分下降 | `REQUIRE_REVIEW` — 强制人工 | 高 |
| 其他 | `SKIP` — 跳过 | — |

#### Bug 关键词库

`hardcod`, `missing`, `timeout`, `crash`, `error`, `fail`, `broken`, `null`, `none`, `empty`, `wrong path`, `incorrect`, `doesn't handle`, `no validation`, `unhandled`, `exception`, `traceback`, `overflow`, `memory`, `leak`

#### 模糊关键词库

`slow`, `bad`, `not good`, `confusing`, `doesn't work`, `useless`, `poor`, `terrible`, `annoying`, `frustrating`

#### 后台循环 (`src/collective_intelligence.py`)

新增 `_auto_evolve_loop()`，每 4 小时运行：
- 扫描所有进化候选技能
- `APPLY_EDITS` → 直接执行 `apply_user_edits()`
- 其他策略 → 记录日志供 admin 审查

---

## 三、代码变更清单

| 文件 | 变更 | 行数变化 |
|------|------|----------|
| `src/skill_manager.py` | 新增晋升队列管理、改写自动晋升检测逻辑 | +124, -8 |
| `main_server.py` | 移除加载时 usage 记录、新增 4 个审批 API | +76, -14 |
| `src/collective_intelligence.py` | 新增 `_auto_evolve_loop`、集成过期清理 | +46, -0 |
| `src/wiki_generator.py` | 新增警告页/反模式页生成、投诉关键词提取 | +120, -3 |
| `src/pattern_learner.py` | 新增 `tool_result` 关联、真实成功率计算 | +83, -7 |
| `src/auto_evolve.py` | **新文件** — 分级自动进化策略 | +230, +0 |
| **总计** | | **+679, -32** |

---

## 四、验证

### 现有测试（通过 42/42）

```bash
uv run pytest tests/unit/test_collective_intelligence.py \
                 tests/unit/test_skill_evolution.py \
                 tests/unit/test_skill_feedback_db.py \
                 tests/unit/test_semiauto_evolution.py -v
```

### 预存失败（9 个，与本次修改无关）

`test_skill_manager.py` 和 `test_skills_api.py` 中的 9 个失败在修改前就存在，原因是 `skill_usage` 表在测试 fixture 中未初始化和外键约束问题。

### 建议新增测试

1. **晋升审批流程** — approve → 文件复制 → DB 更新 → 队列标记 approved
2. **使用统计准确性** — 验证反馈驱动的检测逻辑
3. **Wiki 反模式页生成** — 低评分技能生成 warning/anti-pattern 页
4. **模式学习成功率** — 验证 tool_result 关联计算
5. **AutoEvolvePolicy 分类** — 5 种策略的分支覆盖
6. **过期清理** — 30 天后 pending 记录自动 expired

---

## 五、后续建议

| 事项 | 优先级 | 说明 |
|------|--------|------|
| Embedding 支持（Phase 2） | P1 | `src/embedding.py` 当前全是 `NotImplementedError`，配置 `EMBEDDING_API_URL` + `EMBEDDING_API_KEY` 后可启用语义搜索 |
| AUTO_FIX 实际 LLM 调用 | P1 | 当前 `AUTO_FIX` 策略只记录日志，需要接入 Agent SDK 实际生成修复代码 |
| 前端审批 UI | P2 | admin 面板展示待审批队列、一键 approve/reject |
| 技能进化效果度量 | P2 | 追踪进化前后的评分变化，验证自动进化是否有效 |
| 多语言 Wiki | P3 | Wiki 页按语言隔离，避免中英文技能知识混淆 |
