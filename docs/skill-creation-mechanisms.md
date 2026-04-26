# Skill 创建机制 — 优化设计方案

> 更新日期：2026-04-26
>
> **设计前提**：暂不区分普通用户与管理员，所有用户拥有同等权限。

---

## 一、简化机制全景图

```
                       ┌──────────────────────────────────────┐
                       │        web-agent Skill 生命周期        │
                       └──────────────────────────────────────┘
                                        │
        ┌───────────────┬───────────────┼───────────────┬────────────────┐
        ▼               ▼               ▼               ▼                ▼
    用户上传          Agent 创建       反馈驱动         演化改进         Sync 同步
  (SkillsPanel)    (会话中自动)     (会话后评分)    (用户触发)       (系统内部)
        │               │               │               │                │
   ┌────┴────┐          ▼               ▼               ▼                ▼
   ▼         ▼     skill-creator   SkillFeedback     evolve-agent   _sync_shared
personal   shared   工具 + 规则      Widget          + activate     _skills()
   │         │         │               │               │                │
   │         │         ▼               ▼               ▼                ▼
   │         │    workspace 的    反馈存入 SQLite   versions/vN/    workspace
   │         │   .claude/skills       │           → 覆盖 shared-    中的软链
   │         │         │             ▼              skills/         /副本
   │         │    ┌────┴────┐   演化候选列表
   │         │    ▼         ▼   (>= 5 条低分
   │         │  personal  promote   或手动触发)
   │         │    │         │         │
   │         │    │         ▼         ▼
   │         │    │    直接写入    用户触发
   │         │    │   shared-skills  演化
   │         │    │         │         │
   └─────────┴────┴─────────┴─────────┘
```

---

## 二、全部 7 种机制（从 12 种精简）

| # | 机制 | 端点 / 代码 | 触发方式 | 产出 |
|---|------|------------|---------|------|
| 1 | ZIP 上传 (个人) | `POST /api/users/{uid}/skills/upload` | 用户在 SkillsPanel 上传 | Personal skill |
| 2 | ZIP 上传 (共享) | `POST /api/shared-skills/upload` | 用户在 SkillsPanel (Shared tab) 上传 | Shared skill |
| 3 | Agent skill-creator | 系统规则 + `Skill` 工具 + learn-extraction.md | Agent 检测到可复用模式 | Personal skill (workspace) |
| 4 | Promote (个人 → 共享) | `POST /api/users/{uid}/skills/{name}/promote` | 用户点击或 Agent 调用 | 直接写入 shared-skills/ |
| 5 | 演化改进 | `POST /api/skills/{name}/evolve-agent` + `activate-version` | 用户触发 | 新版本覆盖 shared-skills/ |
| 6 | 共享 Skill 同步 | `_sync_shared_skills()` | 每次会话前自动 | workspace 中的软链/副本 |
| 7 | 反馈提交 | `POST /api/skills/{name}/feedback` | 用户通过 Widget 提交 | 反馈记录 (SQLite) |

### 已移除的机制

| 原机制 | 移除原因 |
|--------|---------|
| Admin 批准 / 拒绝 | 无 admin 角色，promote 直接生效 |
| A/B 测试 | 独立管线，暂不纳入（后续按需恢复） |
| 回滚 | 合并到演化改进中，activate 时自动备份 |

---

## 三、详细说明

### 1. ZIP 上传 — Personal Skill

**端点** `POST /api/users/{user_id}/skills/upload`

用户在 SkillsPanel (Personal tab) 选择 `.zip` → 后端解压到 `data/users/{uid}/workspace/.claude/skills/{name}/`。

**安全检查**：.zip 格式、文件名校验、50MB 压缩 / 100MB 解压上限、100 文件上限、拒绝软链、拒绝路径穿越。

### 2. ZIP 上传 — Shared Skill

**端点** `POST /api/shared-skills/upload`

用户在 SkillsPanel (Shared tab) 上传 `.zip` → 直接解压到 `data/shared-skills/{name}/`。

**说明**：不区分用户角色，任何用户都可以直接贡献共享 skill。如果要避免低质量 skill 污染共享库，可通过**反馈评分**自然淘汰（评分持续低的 skill 被演化或手动清理）。

### 3. Agent skill-creator 工具

Agent 通过 `Skill` 工具调用内置 skill-creator，配合 `src/learn-extraction.md` 中的提取规则：

```
识别 → 分类 → 查重 → 质量门 → 用户确认 → 写入 workspace
                                                │
                                        判断是否可共享？
                                           │         │
                                          Yes        No
                                           │         │
                                          ▼          ▼
                                       promote    保留为
                                      (机制 #4)   personal
```

### 4. Promote (个人 → 共享)

**端点** `POST /api/users/{user_id}/skills/{skill_name}/promote`

**触发方式**：
- 用户在 SkillsPanel 中点击 personal skill 的 "Promote to Shared" 按钮
- Agent 判断 skill 可共享后调用 API

**流程**：直接复制到 `data/shared-skills/{name}/`，无需中间审核。

**冲突处理**：shared 已存在同名 skill → 返回 409 + 已有 skill 的描述，用户可选择改名或手动合并。

### 5. 演化改进

**端点** `POST /api/skills/{name}/evolve-agent` → `activate-version`

**触发条件**（满足任一即可）：
- 自动：反馈 `>= 5` 条且均值 `< 4.0`（降低阈值，提高灵敏度）
- 手动：用户在 EvolutionPanel 直接触发

**流程**：
```
用户触发 evolve-agent
  │
  ▼
后台 Agent 会话启动
  │  输入：当前 SKILL.md + 反馈评论 + 会话上下文
  ▼
生成改进版本 → versions/v{N}/
  │
  ▼
用户预览 → 满意则 activate-version
  │  自动备份旧版本为 SKILL_backup_v{N-1}.md
  ▼
覆盖 shared-skills/{name}/SKILL.md
```

**与旧设计的区别**：
- 不再需要 admin 审核，用户自己决定是否激活
- 演化直接操作 `shared-skills/`，消除 `data/skills/` 目录
- 激活时自动备份，替代独立的 rollback 端点

### 6. 共享 Skill 同步

`_sync_shared_skills()` 在每次 `build_sdk_options()` 时调用：

| 平台 | 方式 | 说明 |
|------|------|------|
| Unix | 符号链接 | 即时，零存储开销 |
| Windows | 复制 + `.shared_skill_source` 标记 | mtime 增量更新 |

### 7. 反馈提交

**端点** `POST /api/skills/{name}/feedback`

用户通过 SkillFeedbackWidget 提交 1-5 星评分 + 可选评论 → 存入 SQLite。

**改进点**：反馈增加 `conversation_snippet` 字段，为演化 Agent 提供上下文。

---

## 四、目录结构（简化后）

```
data/
├── shared-skills/              # 唯一的共享 skill 目录
│   ├── my-skill/
│   │   ├── SKILL.md
│   │   ├── SKILL_backup_v1.md  # activate 时自动备份
│   │   ├── skill-meta.json
│   │   └── versions/           # 演化产生的候选版本
│   │       └── v2/
│   └── ...
│
└── users/{uid}/
    └── workspace/.claude/skills/  # 用户个人 skill + 共享 skill 的同步副本
        ├── my-personal-skill/     # 个人 skill（真实目录）
        └── my-shared-skill/       # 共享 skill（软链 或 Windows 复制 + .shared_skill_source）
```

**关键变化**：不再有独立的 `data/skills/` 目录，演化和激活直接操作 `shared-skills/`。

---

## 五、改进优先级（新版）

| 优先级 | 改动 | 说明 |
|--------|------|------|
| **P0** | 移除 admin 守卫 | 所有 `/api/admin/*` 端点合并到普通用户端点 |
| **P0** | `data/skills/` → `shared-skills/` | 演化直接读写 shared-skills，消除目录分裂 |
| **P1** | Promote 简化 | 去掉 `.pending/`，直接复制到 shared |
| **P1** | 前端加 Promote 按钮 | SkillsPanel personal skill 列表增加 "Promote" 操作 |
| **P1** | 反馈增加上下文 | `conversation_snippet` + `skill_version` 字段 |
| **P2** | 演化阈值优化 | 5 条 + 时间衰减 + 手动触发 |
| **P2** | skill 名推导修复 | Agent 主动声明使用的 skill；`general` 单独处理 |
| **P3** | skill-meta 格式统一 | ZIP 上传和 Agent 创建使用一致的 meta 格式 |
| **P3** | 演化闭环验证 | 激活后自动追踪评分，退化告警 |

---

## 六、与旧设计对照

| 维度 | 旧设计 | 新设计 |
|------|--------|--------|
| 机制数量 | 12 | 7 |
| 角色 | 普通用户 + Admin | 统一用户 |
| 审核流程 | promote → .pending/ → admin approve/reject | promote → 直接写入 shared |
| 存储目录 | `shared-skills/` + `data/skills/` (分裂) | 仅 `shared-skills/` |
| 演化权限 | Admin only | 所有用户 |
| A/B 测试 | Admin 独立管线 | 暂移除，后续按需 |
| 回滚 | 独立端点 | 合入 activate 时自动备份 |
| 演化阈值 | 10 条 + 均值 < 4.5 | 5 条 + 均值 < 4.0 + 时间衰减 |
