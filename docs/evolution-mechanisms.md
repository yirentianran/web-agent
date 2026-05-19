# Web Agent 进化机制

系统通过用户反馈驱动技能的自动改进、晋升和知识积累，形成闭环进化系统。

## 一、总体架构

```
用户请求 → 使用技能 → 提交反馈
                        ↓
              数据存储 (skill_feedback, skill_usage)
                        ↓
         ┌──────────────┼──────────────┐
         ↓              ↓              ↓
    自动进化       自动晋升        Wiki 生成
   (auto_evolve)  (promotion)    (wiki_gen)
         ↓              ↓              ↓
    技能版本更新    个人→共享技能    知识库积累
         ↓              ↓              ↓
    系统提示注入 ← 共享技能加载 ← 相关 Wiki 注入
                        ↓
              下一轮用户请求受益
```

## 二、四个后台循环

**文件:** `src/collective_intelligence.py`
**启动:** `main_server.py` 行 6009-6017，`CollectiveIntelligenceEngine.start_background_jobs()`

| 循环 | 方法 | 间隔 | 作用 |
|------|------|------|------|
| Wiki 挖掘 | `_wiki_mining_loop` (行 41) | 6h | 从对话反馈中生成知识页面 |
| 模式提取 | `_pattern_extraction_loop` (行 51) | 12h | 提取工具共现和成功率 |
| 自动晋升 | `_auto_promotion_loop` (行 62) | 2h | 检查个人技能晋升条件 + 清理过期 |
| 自动进化 | `_auto_evolve_loop` (行 80) | 4h | 分析候选技能并执行安全修复 |

所有循环均带 `try/except` 异常隔离，单个循环失败不影响其他循环。

## 三、技能进化 (Auto-Evolve)

**核心文件:** `src/auto_evolve.py`（分类器）、`src/skill_feedback.py`（版本管理）

### 3.1 触发条件

```python
# src/auto_evolve.py
SHOULD_EVOLVE_MIN_COUNT = 5       # 最少反馈条数
SHOULD_EVOLVE_MAX_RATING = 4.0    # 平均分低于此值才考虑进化
HIGH_QUALITY_MIN_RATING = 4       # 高质量反馈阈值
```

SQL 查询在 `DBSkillFeedbackManager.get_evolution_candidates()`:
`GROUP BY skill_name HAVING cnt >= 5 AND avg_r < 4.0`

### 3.2 五级动作分类器

`AutoEvolvePolicy.analyze_skill()` (行 79) 按安全等级从高到低决策：

| 优先级 | 条件 | 动作 | 描述 |
|--------|------|------|------|
| 1 | `has_user_edits` | `APPLY_EDITS` | 用户提供了已知正确的编辑，直接合并 |
| 2 | `has_specific_bugs` | `AUTO_FIX` | 检测到具体 bug 关键词，调用 Haiku 生成修复 |
| 3 | `uses_count >= 50` | `REQUIRE_REVIEW` | 高使用量技能，强制人工审核 |
| 4 | `is_vague` | `PROPOSE` | 反馈模糊，生成改进建议供参考 |
| 5 | 默认 | `SKIP` | 信号不足，跳过 |

**bug 关键词** (行 59-70): `hardcod`, `missing`, `timeout`, `crash`, `error`, `fail`, `broken`, `null`, `wrong path`, `incorrect`, `doesn't handle`, `no validation`, `unhandled`, `exception`, `traceback`, `overflow`, `memory`, `leak`

**模糊关键词** (行 71): `slow`, `bad`, `not good`, `confusing`, `doesn't work`, `useless`, `poor`, `terrible`, `annoying`

### 3.3 版本管理

所有版本管理逻辑集中在 `DBSkillFeedbackManager` (`src/skill_feedback.py`):

- `create_version()`: 版本备份、目录创建、DB 记录（核心方法）
- `next_version_number()`: 计算下一版本号
- `apply_user_edits()`: 直接合并用户编辑 → 调用 `create_version()`
- `auto_fix_skill()`: Haiku LLM 生成修复 → 调用 `apply_user_edits()`
- `activate_version()`: 激活待定版本
- `rollback_version()`: 回滚到最近备份
- `activate_directory_version()`: 激活目录版本（复制文件 + 更新 meta）
- `list_versions()`: 列出所有版本

版本管理流程（`create_version()`）:
1. 从 `skills` 表读取技能路径
2. `next_version_number()` 计算新版本号
3. 备份当前 SKILL.md 为 `SKILL_backup_v{N}.md`
4. 创建版本目录 `versions/v{N}/`
5. 写入新 SKILL.md
6. 记录到 `skill_versions` 表
7. 更新 `skills.version` 字段

### 3.4 进化 Agent (LLM 自主改进)

**文件:** `main_server.py` 行 4797-5069

与 `auto_evolve.py` 的自动修复不同，进化 Agent 是完整的 Claude SDK 会话：

1. 收集反馈：高质量/低评分/用户编辑分类
2. 读取当前 SKILL.md 和所有技能文件
3. 在 `versions/v{N}/` 目录中工作
4. `permission_mode="acceptEdits"` 自主改进
5. 收集生成的文件

**API:**
- `POST /api/skills/{skill_name}/evolve-agent` — 启动
- `GET /api/skills/{skill_name}/evolve-status/{task_id}` — 轮询状态
- `POST /api/skills/{skill_name}/activate-version` — 激活新版本

## 四、自动晋升 (Auto-Promotion)

**核心文件:** `src/skill_manager.py`

### 4.1 阈值

| 指标 | 阈值 | 说明 |
|------|------|------|
| 反馈数 | >= 5 | `skill_feedback` 表中的条目数 |
| 独立用户 | >= 3 | 至少 3 个不同用户给过反馈 |
| 平均评分 | >= 4.0 | `skill_feedback` 中的 avg(rating) |
| 时间窗口 | 30 天 | 仅统计最近 30 天 |

### 4.2 实现

`check_auto_promotion()` (行 235):
- SQL 查询 `skills JOIN skill_feedback`
- 筛选 `source='personal' AND status='active'`
- `GROUP BY` + `HAVING` 应用三个阈值
- 结果插入 `skill_promotion_queue` 表 (`INSERT OR IGNORE`)

`execute_promotion()` (行 580):
1. 复制个人技能目录到 `DATA_ROOT/shared-skills/{skill_name}/`
2. 更新 `skill-meta.json` 记录 `promoted_by`, `promoted_at`, `original_owner`
3. DB 中插入/更新为 `source='shared'`
4. 队列状态标记为 `'approved'`

`reject_promotion()` (行 669): 标记为 `'rejected'`，可选填原因。

`cleanup_expired_promotions()` (行 681): 30 天未处理的标记为 `'expired'`。

### 4.3 状态机

```
pending → approved (管理员批准)
pending → rejected (管理员拒绝)
pending → expired (30 天超时)
```

### 4.4 管理员 API

| 端点 | 方法 | 作用 |
|------|------|------|
| `/api/skills/promotion/pending` | GET | 列出待晋升技能 |
| `/api/skills/promotion/{name}/approve` | PUT | 批准晋升 |
| `/api/skills/promotion/{name}/reject` | PUT | 拒绝晋升 |
| `/api/skills/promotion/cleanup` | POST | 清理过期项 |

## 五、Wiki 自动生成

**核心文件:** `src/wiki_generator.py`

### 5.1 生成流程

`mine_and_generate()` (行 36):
1. 查询最近 N 小时 (默认 24h) 的 `skill_feedback JOIN messages`
2. 按 `skill_name` 聚类主题
3. 每个主题至少 2 条反馈
4. 根据 `avg_rating` 生成三种页面：

| avg_rating | 页面类型 | 状态 | confidence | 类别 |
|------------|----------|------|------------|------|
| >= 4.0 | 正常 Wiki | `draft` | 0.75 | skills |
| 2.0 - 4.0 | 警告页 | `warning` | 0.6 | skills |
| < 2.0 | 反模式页 | `anti-pattern` | 0.9 | anti-patterns |

5. 同主题不重复创建，仅增加 `validation_count`

### 5.2 内容生成

- `_generate_page()` (行 201): 正常页面，使用会话片段
- `_generate_warning_page()` (行 94): 提取已知问题和用户建议修复
- `_generate_antipattern_page()` (行 126): 关键词频率分析提取常见抱怨，严重问题列表，社区修复

### 5.3 脱敏

`src/semantic_search.py` 中的 `anonymize_summary()`:
- 移除具体文件路径
- 移除用户名/用户 ID
- 保留问题类型、解决方案、工具使用模式

## 六、模式学习

**核心文件:** `src/pattern_learner.py`

`extract_tool_patterns()` (行 21):
1. 查询最近 24 小时 `messages` 表中 `type='tool_use'` 的消息
2. 按 `session_id` 分组，分析工具共现
3. 计算工具对的共现次数，返回 top 20
4. `_calculate_tool_success_rates()` (行 89): 匹配 `tool_use` 与 `tool_result` (通过 `tool_use_id`)
5. 计算每个工具的成功/失败率
6. 存储到 `learned_patterns` 表，`pattern_type='tool_cooccurrence'`, `confidence=0.8`

### 6.1 数据结构

**`skill_feedback` 表** (`database.py` 行 125):
```
id, skill_name, user_id, session_id, rating (1-5), comment, user_edits,
skill_version, conversation_snippet, created_at
```

**`skill_usage` 表** (`database.py` 行 209):
```
id, skill_name, user_id, session_id, version_number, action, created_at
```

**`skill_versions` 表** (`database.py` 行 224):
```
id, skill_name, version_number, path, change_summary, status (pending/active/rolled_back),
created_by, file_count, created_at
```

**`learned_patterns` 表** (`database.py` 行 598):
```
id, pattern_type, pattern_data (JSON), confidence, created_at, updated_at
```

**`wiki_pages` 表** (`database.py` 行 553):
```
id, title, body, category, tags (JSON), status (draft/published/rejected),
source, confidence, validation_count, created_at, updated_at
```

## 七、管理员控制面

### 进化相关

| 端点 | 方法 | 作用 |
|------|------|------|
| `/api/admin/skills/evolution-candidates` | GET | 列出进化候选技能 |
| `/api/admin/skills/evolution/trigger` | POST | 手动触发进化 |
| `/api/admin/skills/evolution/status` | GET | 查询进化状态 |
| `/api/admin/skills/evolution/reset` | POST | 重置进化状态 |
| `/api/admin/skills/auto-evolve` | POST | 执行自动进化 |
| `/api/admin/skills/analytics` | GET | 技能分析数据 |

### 反馈管理

| 端点 | 方法 | 作用 |
|------|------|------|
| `/api/admin/feedback` | GET | 全部反馈列表 + 统计 |

### 版本管理

| 端点 | 方法 | 作用 |
|------|------|------|
| `/api/skills/{name}/version` | GET | 列出所有版本 |
| `/api/skills/{name}/activate-version` | POST | 激活待定版本 |
| `next_version_number()` | 行 4722 | 计算下一版本号 |

## 八、启动引导

**文件:** `scripts/bootstrap_collective_intelligence.py`

一次性从历史数据中挖掘初始知识：
1. `WikiGenerator.mine_and_generate(lookback_hours=8760)` — 1 年历史生成 Wiki
2. `PatternLearner.extract_tool_patterns()` — 全量工具模式分析

## 九、数据流完整路径

### 会话启动时（读取）

```
用户打开会话
  → load_skills(user_id)              ← L2: 共享 + 个人技能
  → load_memory(user_id)              ← L1: 用户记忆
  → load_agent_memory_for_prompt()    ← L1: Markdown notes
  → _load_wiki_context()              ← L3: Wiki 相关片段
  → _load_semantic_context()          ← L4: 相似历史对话
  → _load_pattern_context()           ← L5: 活跃模式提示
  → build_system_prompt()             ← 组装完整系统提示
  → 传递给 Agent SDK
```

### 会话进行中（写入）

```
用户发送消息
  → WebSocket → message_buffer
  → Agent 处理并回复
  → 会话结束时：
    → 消息批量写入 messages 表
    → sessions.status = 'completed'
    → 触发 skill_feedback 提交
```

### 后台周期（异步）

```
每 2 小时:  技能晋升检查
每 4 小时:  自动进化分析
每 6 小时:  Wiki 挖掘
每 12 小时: 工具模式提取
```
