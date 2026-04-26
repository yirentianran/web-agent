# 对话内容总结成 Skill — 基于现有 Skill 的实现方案

## 1. 目标定义

**用户期望**：将当前对话内容提取并总结为可复用的 SKILL.md 文件。

**首选策略**：使用已有的 skill 组合实现，不创建新 skill，除非现有 skill 确实无法覆盖。

---

## 2. 可用的现有 Skill

### 2.1 直接相关（核心路径）

| Skill | 类型 | 用途 | 在本方案中的角色 |
|-------|------|------|------------------|
| **`learn-eval`** | Command | 从会话提取可复用模式 → SKILL.md，含质量门禁和位置决策 | **主角** — 直接实现"对话→skill" |
| **`learn`** | Command | 简化版 learn-eval，无质量门禁 | 快速轻量提取 |
| **`save-session`** | Command | 保存完整会话上下文到文件 | 前置步骤 — 持久化对话内容 |

### 2.2 辅助支撑

| Skill | 类型 | 用途 | 在本方案中的角色 |
|-------|------|------|------------------|
| **`writing-skills`** | Plugin Skill | TDD 方法编写高质量 skill | 质量保障 — 验证输出 skill 质量 |
| **`continuous-learning-v2`** | Skill (auto) | 自动观测 + 提取原子级 instinct | 持续学习 — 后续强化 |
| **`instinct-export`** | Command | 导出 instinct YAML | 分享和迁移 |

### 2.3 不相关（排除）

| Skill | 原因 |
|-------|------|
| `skill-create` | 数据源是 git 历史，不是对话内容 |

---

## 3. 组合使用方案

### 3.1 方案 A：直接使用 `/learn-eval`（推荐）

最简单有效的路径。在对话结束时执行：

```
用户: /learn-eval
```

`learn-eval` 的工作流：
1. 回顾当前会话，寻找四类可提取模式：
   - 错误解决方案（根因 + 修复 + 可复用性）
   - 调试技巧（非显而易见的步骤、工具组合）
   - 变通方案（库的怪癖、API 限制、版本特定修复）
   - 项目特定模式（约定、架构决策、集成模式）
2. 识别最有价值的可复用洞察
3. 确定存储位置（global `~/.claude/skills/learned/` vs project `.claude/skills/learned/`）
4. 起草 SKILL.md（含 YAML frontmatter）
5. **质量门禁**：
   - grep 已有 skill 目录检查去重
   - 检查 MEMORY.md
   - 判断是否合并到已有 skill
   - 输出整体裁决：Save / Improve then Save / Absorb into [X] / Drop
6. 根据裁决走对应的确认流程

### 3.2 方案 B：`save-session` → `/learn-eval`（长对话/跨会话）

适合需要跨会话保留上下文的场景：

```
步骤 1: /save-session          → 保存会话到 ~/.claude/session-data/YYYY-MM-DD-xxx.tmp
步骤 2: 新会话中 /resume-session → 恢复上下文
步骤 3: /learn-eval            → 从恢复的上下文中提取 skill
```

### 3.3 方案 C：`/learn-eval` + `writing-skills` TDD 验证（高质量要求）

适合对 skill 质量有严格要求的场景：

```
步骤 1: /learn-eval 提取 skill → 生成初始 SKILL.md
步骤 2: 用 writing-skills 的 RED-GREEN-REFACTOR 循环验证：
         RED   — 不用 skill，用子 agent 跑典型场景，记录基线行为
         GREEN — 装上 skill，验证 agent 行为改善
         REFACTOR — 发现新漏洞，补充反制措施
步骤 3: 迭代直到 skill "防弹"
```

### 3.4 方案 D：四步完整流水线

```
对话结束
  │
  ├─ 1. /save-session          保存完整上下文
  │     └→ ~/.claude/session-data/YYYY-MM-DD-xxx.tmp
  │
  ├─ 2. /learn-eval             提取可复用模式
  │     ├→ 去重检查（grep 已有 skill）
  │     ├→ 位置决策（global vs project）
  │     └→ 生成 SKILL.md
  │
  ├─ 3. writing-skills          质量验证
  │     └→ TDD 循环确保 skill 有效
  │
  └─ 4. continuous-learning-v2  持续强化
        └→ 自动观测后续使用，提升置信度
```

---

## 4. 已验证的工作原理

### 4.1 `learn-eval` 的质量门禁细节

从 `learn-eval` 命令文件中提取的关键机制：

| 检查项 | 方法 | 强制性 |
|--------|------|--------|
| 与已有 skill 重叠 | `grep` 扫描 global + project skill 目录 | 强制 |
| 与 MEMORY.md 重叠 | 检查 project 和 global 的 MEMORY.md | 强制 |
| 是否应合并到已有 skill | 人工判断 | 建议 |
| 是否真正可复用 | 排除一次性修复、简单拼写错误 | 强制 |

裁决类型：
- **Save** — 通过所有检查，直接保存
- **Improve then Save** — 内容有价值但格式需调整
- **Absorb into [X]** — 应合并到已有 skill
- **Drop** — 不值得保存

### 4.2 `learn-eval` 的位置决策

| 条件 | 存放位置 |
|------|----------|
| 通用模式，跨 2+ 项目可用 | Global: `~/.claude/skills/learned/` |
| 项目特定知识 | Project: `.claude/skills/learned/` |
| 不确定时 | 选 Global |

生成的 SKILL.md 格式：
```yaml
---
name: <skill-name>
description: <when to use, max 1024 chars>
user-invocable: false
origin: auto-extracted
---
# <Skill Title>

## Overview
...

## When to Use
...

## Example
...
```

---

## 5. 上次失败的根因分析

上次用户说"总结成 skill"时的失败路径：

```
Agent 的理解（错误）:
  "总结成 skill" → 把 150+ 个已有 skill 总结成一个分类文件

Agent 的行为:
  1. ls ~/.claude/skills/         ← 150+ 个目录
  2. 逐个读取 skill.md             ← 上下文溢出
  3. grep 匹配模式脆弱             ← 拿到空描述
  4. 陷入循环无法完成              ← 超时/上下文超限

正确的理解:
  "总结成 skill" → 从当前对话中提取可复用知识，生成新的 SKILL.md

正确的行为:
  /learn-eval → 自动分析对话 → 提取 1 个最有价值的模式 → 生成 SKILL.md
```

**结论**：不是工具不够，是 Agent 当时没有走 `/learn-eval` 路径，而是试图手动做一件超出上下文能力的事。`/learn-eval` 的 prompt 已经包含了分批、去重、质量门禁等关键设计。

---

## 6. 现有 Skill 覆盖度评估

| 需求 | 覆盖工具 | 覆盖程度 |
|------|----------|----------|
| 从对话提取知识 | `learn-eval` / `learn` | 完全覆盖 |
| 生成结构化 SKILL.md | `learn-eval`（内置） | 完全覆盖 |
| 去重检查 | `learn-eval`（grep 检查） | 基本覆盖 |
| 位置决策 global/project | `learn-eval`（内置） | 完全覆盖 |
| 质量验证 | `writing-skills` | 完全覆盖 |
| 批量提取多个知识点 | **无** — 只能逐个提取 | **差距** |
| 自动触发（无需手动 /） | `continuous-learning-v2` | 部分覆盖（粒度是 instinct 不是 skill） |
| 对话级别语义理解 | `learn-eval` | 完全覆盖 |
| Skill 版本管理 | **无** | **差距** |
| 跨会话上下文保留 | `save-session` + `resume-session` | 完全覆盖 |

### 真正的差距（需要新建的只有这些）

1. **批量提取**：`learn-eval` 每次只提取 1 个模式。如果需要从一段对话中提取多个 skill，需要多次调用
2. **Skill 版本管理**：没有自动追踪 skill 变更历史的机制

这两个差距可以通过工作流约定来解决，不一定需要新建 skill：
- 批量提取 → 多次运行 `/learn-eval`，每次聚焦不同类别
- 版本管理 → 在 SKILL.md frontmatter 中手动维护 version 字段

---

## 7. 推荐操作流程

### 日常使用（90% 场景）

```
对话快结束时 → /learn-eval → 按提示确认 → 完成
```

### 长对话/重要讨论

```
阶段 1: /save-session     ← 中场保存
阶段 2: 继续讨论
阶段 3: /learn-eval       ← 提取 skill
```

### 高质量 skill 产出

```
/learn-eval → 产出初稿 → 用 writing-skills TDD 循环验证 → 定稿
```

---

## 8. 下一步行动

1. **立即验证**：执行 `/learn-eval`，从本次对话（探索代码库、分析差距）中提取一个 skill
2. **评估效果**：如果 `/learn-eval` 产出质量好，说明现有 skill 完全够用
3. **仅当不够用时**：才考虑创建新的 command/skill 来弥补"批量提取"或"版本管理"的差距
