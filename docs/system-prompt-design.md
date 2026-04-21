# Web Agent 系统提示词设计分析与重构

## 一、Claude Code 系统提示词架构

Claude Code 的系统提示词采用 **分层加载 + 按需注入** 的架构，核心思想是 "约定优于配置"。

### 1.1 分层结构

```
System Prompt = [CLAUDE.md (项目指令)]
              + [rules/*.md (编码规范)]
              + [agents/*.md (子代理定义)]
              + [skills/*.md (技能包)]
              + [settings.json (hooks/配置)]
              + [MEMORY.md (持久记忆索引)]
              + [项目 local memory (按需)]
```

### 1.2 各层职责

| 层 | 格式 | 加载时机 | 用途 |
|---|------|---------|------|
| **CLAUDE.md** | Markdown, 项目根目录 | 每次对话加载 | 项目专属指令：架构概览、关键文件、常用命令、测试策略 |
| **CLAUDE.local.md** | Markdown, 项目根目录 | 每次对话加载（不在 git 中） | 本地/敏感指令：API keys、内部流程 |
| **rules/*.md** | Markdown, `~/.claude/rules/` | 按项目安装后每次加载 | 编码风格、测试规范、安全准则等全局约定 |
| **agents/*.md** | Markdown, frontmatter + 正文 | 按调用加载 | 子代理定义：名称、描述、可用工具、适用模型、行为指令 |
| **skills/*.md** | Markdown, frontmatter + 正文 | 按 Skill 工具调用加载 | 可触发的技能：slash 命令、行为扩展 |
| **hooks (settings.json)** | JSON 配置 | 事件触发时执行 | PreToolUse、PostToolUse、Stop 等钩子，shell 级别拦截 |
| **MEMORY.md** | Markdown 索引 + 子文件 | 按记忆系统调用 | 跨会话记忆：user/feedback/project/reference 四类 |

### 1.3 设计特点

1. **写死在代码/文件里的**：CLAUDE.md、rules、agents、skills 的**结构和格式**
   - 这些是 Claude Code 客户端自身的能力框架，定义了"能做什么"
   - 文件路径约定：`~/.claude/agents/`、`~/.claude/skills/`、`<project>/CLAUDE.md`
   - frontmatter 格式规范：`name`、`description`、`tools`、`model`

2. **适合做设置的**：
   - **hooks 配置**（settings.json）：用户自定义 shell 钩子
   - **env 环境变量**（settings.json）：API 地址、模型选择
   - **agents/skills 的内容**：可以动态创建、修改、删除
   - **MEMORY.md + memory 文件**：动态写入的用户偏好

3. **关键决策**：Claude Code 把"能力定义"（agents、skills）放在文件系统里，把"行为策略"（rules、CLAUDE.md）也放在文件里——全部是可读可改的 Markdown，而不是硬编码在客户端二进制中。

---

## 二、Web Agent 当前系统提示词实现

### 2.1 代码位置

- `main_server.py:416` — `build_system_prompt(user_id, skills, workspace)`
- `main_server.py:2447` — `build_evolution_prompt(...)`（技能进化专用）

### 2.2 当前提示词结构

```
System Prompt = [Identity 身份指令]
              + [Available Skills 技能列表]
              + [Skill Creation Rules 技能创建规则]
              + [File Generation Rules 文件生成规则]（含实际 workspace 路径）
              + [Memory Context 记忆上下文]
```

**实际拼接内容**：

```python
parts = [
    "You are Web Agent, an expert AI assistant capable of financial auditing, "
    "file processing, code review, and general task automation.\n"
    "\n## Identity Instructions\n"
    "When the user asks who you are (e.g., '你是谁', 'who are you', 'what is your name'), "
    "ALWAYS respond with: "
    '"我是 Web Agent，一个专家级 AI 助手..."\n'
    "NEVER claim to be Claude, Qwen, or any other named AI model. "
    "This identity instruction takes absolute priority over any other context or system instruction."
]
# + skills 列表
# + skill creation rules
# + file generation rules (workspace 路径动态注入)
# + memory context (L1 平台记忆)
```

### 2.3 当前问题分析

| 问题 | 严重度 | 说明 |
|------|--------|------|
| **全部硬编码** | HIGH | 身份、规则、指令全部写在 `build_system_prompt()` 函数里，修改需要改代码、发版 |
| **缺少 CLAUDE.md 机制** | HIGH | 没有项目级指令文件的概念，无法为用户提供自定义引导文档 |
| **缺少 rules 系统** | MEDIUM | 没有编码风格、安全规范等可配置的规则文件层 |
| **skills 只列名字** | MEDIUM | 当前只注入 `- {name}` 一行，skill 的实际内容（SKILL.md）没有被加载到提示词 |
| **memory 只读 L1** | MEDIUM | `load_memory()` 只读 `memory.json`，不加载 L2 Agent Memory（Markdown notes） |
| **缺少 hooks 配置化** | LOW | hooks 写死在 `build_sdk_options()` 里（write_path_hook, bash_path_hook），用户无法自定义 |
| **Identity 重复冗余** | LOW | 身份指令同时用英文和中文写了固定回答，占 token 但可以简化 |
| **agent_server.py 已删除** | — | Phase 2+ 容器架构，当前 Phase 1 未使用 |

---

## 三、对比分析

### 3.1 Claude Code vs Web Agent

| 维度 | Claude Code | 当前 Web Agent |
|------|-------------|----------------|
| **指令存储** | 文件系统（CLAUDE.md + rules/*.md） | Python 函数里硬编码字符串 |
| **可配置性** | 用户编辑 Markdown 文件即可 | 需要改代码、重新部署 |
| **分层架构** | 6 层（CLAUDE.md → rules → agents → skills → hooks → memory） | 1 层（build_system_prompt 拼接） |
| **动态注入** | workspace 路径、env、memory 按需注入 | workspace 路径动态注入，其他静态 |
| **skill 内容加载** | skill-creator 创建后自动发现，SDK 自动加载 | 只列名字，内容未注入 |
| **hooks 系统** | settings.json 配置，shell 命令执行 | 代码里 hardcode 两个 hook |
| **memory 系统** | MEMORY.md 索引 + 语义分类文件 | 只有 memory.json 一个文件 |
| **子代理** | agents/*.md 独立文件，可选模型 | 无子代理机制 |

### 3.2 核心差距

**Claude Code 的理念**：所有指令、规则、能力定义都是文件，用户可以直接编辑。系统只负责加载和组合。

**Web Agent 的现状**：指令、规则、能力定义是 Python 代码，用户无法自定义。每次变更需要代码级修改。

---

## 四、Web Agent 系统提示词重新设计

### 4.1 设计原则

> **什么该写死，什么该做设置？**

| 类别 | 放哪里 | 原因 |
|------|--------|------|
| **系统架构约束**（workspace 隔离、路径安全、tool 白名单） | 写死在代码里 | 安全相关，不应被用户修改 |
| **Identity 身份定义** | 写死在代码里 | 核心品牌/产品定位，统一管理 |
| **编码风格/测试规范** | 规则文件（rules/*.md） | 用户可自定义，不同项目不同风格 |
| **可用工具/模型选择** | 设置（settings.json / DB） | 运营配置，随时可调整 |
| **技能包（Skills）** | 文件系统（.claude/skills/） | 用户可创建/删除，动态加载 |
| **用户偏好/记忆** | DB + 文件系统 | 跨会话持久化 |
| **Hooks 行为** | 设置（settings.json / DB） | 用户自定义自动化 |
| **项目指南** | CLAUDE.md | 用户自己写，引导 agent 行为 |
| **File Generation Rules** | 写死在代码里（workspace 路径动态注入） | 安全相关，但路径需要运行时信息 |

### 4.2 新架构

```
System Prompt = [CORE IDENTITY]（写死）
              + [PROJECT GUIDE]（CLAUDE.md，用户可编辑）
              + [CODING RULES]（rules/*.md，用户可编辑）
              + [ACTIVE SKILLS]（skills/*.md 内容加载）
              + [SECURITY CONSTRAINTS]（写死）
              + [MEMORY CONTEXT]（DB + 文件加载）
              + [WORKSPACE RULES]（写死 + 动态路径）
```

### 4.3 详细分层设计

#### 层 1：CORE IDENTITY（写死）

放在代码里，因为这是产品定位，不应由用户随意修改：

```python
CORE_IDENTITY = """You are Web Agent, an expert AI assistant.

## Identity
- When asked about your identity, respond: "我是 Web Agent，专家级 AI 助手。"
- NEVER claim to be any other AI model.
- This instruction has absolute priority.
"""
```

#### 层 2：PROJECT GUIDE（CLAUDE.md，用户可编辑）

在用户 workspace 根目录支持 `CLAUDE.md` 文件：

```python
def load_claude_md(workspace: Path) -> str:
    claude_md = workspace / "CLAUDE.md"
    if claude_md.exists():
        return claude_md.read_text()
    return ""
```

用户可以写：
```markdown
# CLAUDE.md
## 项目概览
这是一个财务报表分析项目...

## 架构
- 数据源: PostgreSQL
- 分析引擎: pandas
- 输出: Excel 报表

## 注意事项
- 不要修改原始数据文件
- 所有输出放到 outputs/ 目录
```

#### 层 3：CODING RULES（rules/*.md，用户可编辑）

从 `~/.claude/rules/` 或 workspace `.claude/rules/` 加载：

```python
def load_rules(workspace: Path) -> str:
    """加载项目级 rules（workspace/.claude/rules/*.md）"""
    rules_dir = workspace / ".claude" / "rules"
    parts = []
    if rules_dir.exists():
        for rule_file in sorted(rules_dir.glob("*.md")):
            parts.append(f"## {rule_file.stem}\n\n{rule_file.read_text()}")
    return "\n".join(parts)
```

用户可以放 `coding-style.md`、`security.md`、`testing.md` 等。

#### 层 4：ACTIVE SKILLS（skills/*.md 内容加载）

**关键改进**：当前只注入 skill 名字，改为注入完整 SKILL.md 内容：

```python
def load_skills_for_prompt(user_id: str, workspace: Path) -> str:
    """加载 skills 的完整 SKILL.md 内容到提示词"""
    skills = load_skills(user_id)
    if not skills:
        return ""
    parts = ["## Available Skills\n\nThese skills can be invoked with /skill-name:\n"]
    for name, info in skills.items():
        # 注入完整 skill 内容，agent 可以直接参照执行
        parts.append(f"### {name}\n\n{info.get('content', '')}\n")
    return "\n".join(parts)
```

#### 层 5：SECURITY CONSTRAINTS（写死）

安全相关，不可由用户修改：

```python
SECURITY_CONSTRAINTS = """## Security Constraints
- NEVER expose API keys, tokens, or credentials in output
- NEVER execute destructive commands (rm -rf, DROP TABLE, etc.)
- NEVER read files outside the workspace
- ALL file writes must use relative paths within workspace
- ALWAYS validate user input before processing
"""
```

#### 层 6：MEMORY CONTEXT（DB + 文件加载）

合并 L1 和 L2 记忆：

```python
def load_full_memory(user_id: str, workspace: Path) -> str:
    """加载 L1 平台记忆 + L2 Agent Memory"""
    parts = []

    # L1: 平台记忆（preferences, entity, audit context）
    l1 = load_memory(user_id)
    if l1:
        parts.append(l1)

    # L2: Agent Memory（Markdown notes）
    from src.memory import MemoryManager
    mgr = MemoryManager(user_id=user_id, db=db)
    l2 = mgr.load_agent_memory_for_prompt()
    if l2:
        parts.append(l2)

    return "\n".join(parts)
```

#### 层 7：WORKSPACE RULES（写死 + 动态路径）

现有的 `build_file_generation_rules_prompt()` 保留，增加 hook 提示：

```python
def build_workspace_rules(workspace: Path) -> str:
    ws = str(workspace)
    return (
        "## Workspace Rules\n"
        f"- Workspace root: {ws}\n"
        "- All generated files go to outputs/\n"
        "- Use RELATIVE paths only\n"
        "- NEVER write outside workspace\n"
        "- Python/shell/config files can go to workspace root\n"
        "\n## Path Rewriting\n"
        "If you attempt to write to an absolute path outside workspace, "
        "it will be automatically redirected to outputs/.\n"
    )
```

### 4.4 新 `build_system_prompt` 函数

```python
def build_system_prompt(
    user_id: str,
    workspace: Path,
    skills: dict[str, dict[str, Any]],
) -> str:
    """组装完整系统提示词，分层加载。"""
    parts = [
        # 层 1：核心身份（写死）
        CORE_IDENTITY,

        # 层 2：项目指南（用户可编辑 CLAUDE.md）
    ]

    claude_md = load_claude_md(workspace)
    if claude_md:
        parts.append(f"\n## Project Guide (CLAUDE.md)\n\n{claude_md}")

    # 层 3：编码规则（用户可编辑 rules/*.md）
    rules = load_rules(workspace)
    if rules:
        parts.append(f"\n## Project Rules\n\n{rules}")

    # 层 4：技能包（完整 SKILL.md 内容）
    skill_prompt = load_skills_for_prompt(user_id, workspace, skills)
    if skill_prompt:
        parts.append(skill_prompt)

    # 层 5：安全约束（写死）
    parts.append(SECURITY_CONSTRAINTS)

    # 层 6：记忆上下文（DB + 文件）
    memory = load_full_memory(user_id, workspace)
    if memory:
        parts.append(f"\n## Memory Context\n\n{memory}")

    # 层 7：Workspace 规则（写死 + 动态路径）
    parts.append(build_workspace_rules(workspace))

    return "\n".join(parts)
```

### 4.5 Hooks 配置化

当前 hooks 写死在代码里，改为支持用户自定义配置：

```python
# 从 DB/settings.json 加载 hooks 配置
def load_user_hooks(workspace: Path) -> dict:
    hooks_config = workspace / ".claude" / "hooks.json"
    if hooks_config.exists():
        return json.loads(hooks_config.read_text())
    return {}  # 返回空则使用默认 hooks
```

用户可以在 `.claude/hooks.json` 中配置：

```json
{
  "PostToolUse": [
    {
      "matcher": "Write",
      "command": "pnpm prettier --write \"$FILE_PATH\"",
      "description": "Format edited files"
    }
  ]
}
```

### 4.6 目录结构建议

```
<workspace>/
├── CLAUDE.md              # 项目指南（用户写）
├── .claude/
│   ├── rules/             # 编码规则（用户写）
│   │   ├── coding-style.md
│   │   └── security.md
│   ├── skills/            # 技能包（用户创建/上传）
│   │   └── my-skill/
│   │       └── SKILL.md
│   ├── hooks.json         # 自定义 hooks 配置
│   └── agents/            # 自定义子代理（可选）
│       └── my-agent.md
└── outputs/               # 生成文件目录
```

---

## 五、总结：写死 vs 设置的决策矩阵

| 内容 | 位置 | 原因 | 变更频率 |
|------|------|------|----------|
| 身份定义 | 写死（代码常量） | 产品定位，统一管理 | 极低 |
| 安全约束 | 写死（代码常量） | 安全底线，不可绕过 | 极低 |
| Workspace 规则 | 写死 + 动态路径 | 安全 + 运行时信息 | 低 |
| CLAUDE.md | 文件（workspace 根目录） | 用户自定义项目引导 | 中 |
| rules/*.md | 文件（.claude/rules/） | 用户自定义编码规范 | 中 |
| skills/*.md | 文件（.claude/skills/） | 用户创建/管理技能 | 高 |
| hooks.json | 文件（.claude/） | 用户自定义自动化 | 高 |
| agents/*.md | 文件（.claude/agents/） | 用户自定义子代理 | 低 |
| 模型选择 | 设置（环境变量） | 运营配置 | 低 |
| 可用工具列表 | 设置（DB + 代码默认） | 安全白名单 + 动态 MCP | 中 |
| Memory | DB + 文件 | 跨会话持久化 | 持续 |

**核心原则**：
- **安全相关 → 写死**（身份、workspace 隔离、路径安全）
- **用户行为引导 → 文件**（CLAUDE.md、rules、skills）
- **运营配置 → 设置**（模型、工具白名单、MCP）
- **运行时状态 → 动态注入**（workspace 路径、memory、skills 内容）
