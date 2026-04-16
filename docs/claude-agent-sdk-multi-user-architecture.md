# 基于 Claude Agent SDK 的多用户 Web Agent 平台架构设计

## 1. 项目背景

### 1.1 需求概述

| 需求项 | 说明 |
|--------|------|
| 访问方式 | Web 浏览器访问 |
| 用户模型 | 多人同时使用，彼此隔离 |
| 核心能力 | 完整的 Agent 能力（文件读写、命令执行、工具调用） |
| 业务场景 | 财务审计业务（根据会计准则、审计准则进行财务审计作业。需要处理 Office、PDF、图片等类型文件） |
| 文件管理 | 支持用户上传文件，生成结果文件供用户下载 |
| Skills 管理 | 支持 SKILL.md 格式，分**公共 Skill** 和**用户个人 Skill** |
| Tool/MCP 管理 | 管理员统一管理 MCP Server 和内置 Tool，控制用户可用工具范围 |

### 1.2 技术选型

| 组件 | 技术 | 说明 |
|------|------|------|
| Agent 引擎 | Claude Agent SDK (Python) | 将 Claude Code 能力程序化为可调用的 Agent。**注意**：SDK 内部通过 `subprocess` 启动 `claude` CLI 二进制，由 CLI 内部维护 Agent Loop、Session 和 Hooks。SDK 不直接调用 Anthropic API——它管理 CLI 子进程的生命周期、输入输出和消息流式返回。 |
| Web 框架 | FastAPI | 高性能异步 Python Web 框架，原生支持 WebSocket |
| 用户隔离 | Docker Container / 沙箱 | 每个用户独立容器，文件系统、进程、网络完全隔离 |
| 前端 | 自定义 Web UI（待选型：React/Vue） | 通过 WebSocket 与 Agent 实时通信 |
| 沙箱备选 | Daytona / Blaxel / PPIO | 第三方隔离方案，替代自建 Docker 编排 |


## 目录

| 章节 | 内容 |
|------|------|
| [1. 项目背景](#1-项目背景) | 需求概述、技术选型 |
| [2. 总体架构](#2-总体架构) | 接入层、管理服务、容器编排 |
| [3. Web 前端页面设计](#3-web-前端页面设计) | 页面布局、Session 列表、Chat 区域、文件操作 |
| [4. Skills 双层架构设计](#4-skills-双层架构设计) | 文件系统、容器挂载、权限矩阵 |
| [5. 记忆机制](#5-记忆机制) | L1 平台记忆、L2 Agent 自主记忆、Skill 反馈与进化 |
| [6. MCP Server 与 Tool 管理](#6-mcp-server-与-tool-管理) | 架构设计、权限模型、注册中心 |
| [7. Claude Agent SDK 核心能力](#7-claude-agent-sdk-核心能力) | Agent Loop、内置工具、Hooks、Session、子代理、成本控制、Task 管理、断连续传、Spinner |
| [8. 核心代码实现](#8-核心代码实现) | 主服务器、容器内 Agent、Dockerfile |
| [9. 部署方案](#9-部署方案) | Docker Compose、生产环境、第三方沙箱 |
| [10. 审计场景 Skills 示例](#10-审计场景-skills-示例) | PDF/Excel/图片审计、文件存储 |
| [11. 已知限制与注意事项](#11-已知限制与注意事项) | 多用户限制、资源消耗、安全考虑 |
| [12. 总结](#12-总结) | 需求与方案对照表 |

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        接入层                                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Nginx / API Gateway                                      │  │
│  │  · 用户认证 (JWT / OAuth)                                 │  │
│  │  · 请求路由                                               │  │
│  │  · WebSocket 代理                                         │  │
│  └───────────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                      管理服务 (FastAPI)                          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │  用户认证服务    │  │  Skills 管理 API │  │  会话管理 API    │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
│  ┌─────────────────┐  ┌─────────────────┐                        │
│  │  MCP/Tool 管理   │  │  文件管理 API    │                        │
│  └─────────────────┘  └─────────────────┘                        │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │                 容器编排层 (Docker SDK)                    │    │
│  │  · 用户登录 → 创建/获取容器                               │    │
│  │  · 用户退出 → 休眠/销毁容器                               │    │
│  │  · 资源管控 (CPU / 内存 / 磁盘)                          │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────┬──────────────────────────────────────┘
                           │
    ┌──────────────────────┼──────────────────────┐
    ▼                      ▼                      ▼
┌─────────┐          ┌─────────┐          ┌─────────┐
│ User A  │          │ User B  │          │ User C  │
│ Container│         │ Container│         │ Container│
│          │          │          │          │          │
│ ┌──────┐│          │ ┌──────┐│          │ ┌──────┐│
│ │Web   ││          │ │Web   ││          │ │Web   ││
│ │Server││          │ │Server││          │ │Server││
│ └──┬───┘│          │ └──┬───┘│          │ └──┬───┘│
│    │    │          │    │    │          │    │    │
│ ┌──▼───┐│          │ ┌──▼───┐│          │ ┌──▼───┐│
│ │SDK   ││          │ │SDK   ││          │ │SDK   ││
│ │Agent ││          │ │Agent ││          │ │Agent ││
│ └──┬───┘│          │ └──┬───┘│          │ └──┬───┘│
│    │    │          │    │    │          │    │    │
│ ┌──▼───────┐│      │ ┌──▼───────┐│      │ ┌──▼───────┐│
│ │Skills    ││      │ │Skills     ││      │ │Skills    ││
│ │(shared + ││      │ │(shared +  ││      │ │(shared + ││
│ │personal) ││      │ │personal)  ││      │ │personal) ││
│ └──────────┘│      │ └───────────┘│      │ └──────────┘│
│ ┌──────────┐│      │ ┌──────────┐│      │ ┌──────────┐│
│ │MCP       ││      │ │MCP       ││      │ │MCP       ││
│ │Server    ││      │ │Server    ││      │ │Server    ││
│ └──────────┘│      │ └──────────┘│      │ └──────────┘│
└─────────────┘      └──────────────┘      └──────────────┘
```

---

## 3. Web 前端页面设计

### 3.1 页面布局

```
┌─────────────────────────────────────────────────────────────────┐
│  Header: Logo  │  当前用户: Alice  │  设置  │  退出登录          │
├──────────────┬──────────────────────────────────────────────────┤
│              │                                                  │
│  Session 列表 │                  Chat 区域                       │
│              │                                                  │
│  ┌────────┐  │  ┌────────────────────────────────────────────┐  │
│  │+ 新会话 │  │  │                                          │  │
│  ├────────┤  │  │  [Agent] 你好！有什么可以帮你的吗？         │  │
│  │        │  │  │                                          │  │
│  │ ● 审计报│  │  │  [用户] 请帮我审计这份2025年度财务报表     │  │
│  │   告_041│  │  │                                          │  │
│  │   12:30 │  │  │  ┌──────────────────────────────────┐    │  │
│  │        │  │  │  │ 📎 上传的文件                     │    │  │
│  │ ○ 凭证 │  │  │  │ • annual_report_2025.pdf  [✕]    │    │  │
│  │   抽查 │  │  │  │ • ledger.xlsx             [✕]    │    │  │
│  │   0411│  │  │  │ • bank_statement.jpg        [✕]    │  │
│  │        │  │  │  └──────────────────────────────────┘    │  │
│  │ ○ 收入 │  │  │                                          │  │
│  │   确认 │  │  │  [Agent] 已收到 3 个文件，开始分析...    │  │
│  │   检查 │  │  │  ●●● 正在处理 annual_report_2025.pdf    │  │
│  │   0410│  │  │  ●●● 正在执行收入截止测试                 │  │
│  │        │  │  │                                          │  │
│  │        │  │  │  [Agent] 审计分析已完成。                │  │
│  │        │  │  │                                          │  │
│  │        │  │  │  ┌──────────────────────────────────┐    │  │
│  │        │  │  │  │ 📄 生成的结果文件                │    │  │
│  │        │  │  │  │ • audit_report_2025.md  [下载]  │    │  │
│  │        │  │  │  │ • exceptions.xlsx       [下载]  │    │  │
│  │        │  │  │  │ • working_paper.txt     [下载]  │    │  │
│  │        │  │  │  └──────────────────────────────────┘    │  │
│  │        │  │  │                                          │  │
│  │        │  │  └────────────────────────────────────────────┘  │
│              │                                                  │
│              │  ┌────────────────────────────────────────────┐  │
│              │  │  [ 输入审计任务...               ]  [发送]  │  │
│              │  │  [📎 上传文件]                              │  │
│              │  └────────────────────────────────────────────┘  │
├──────────────┴──────────────────────────────────────────────────┤
│  状态栏: ● 已连接  │  Session: abc-123  │  本轮消耗: $0.12     │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 左侧面板：Session 列表

```
┌─────────────────────────────┐
│  [+ 新建会话]               │  ← 点击创建新 session
├─────────────────────────────┤
│  ● 审计报告_2025     12:30  │  ← 当前活跃会话（● 蓝色圆点）
│    生成审计报告中...         │  ← 最后一句 agent 消息摘要
├─────────────────────────────┤
│  ○ 凭证抽查          昨天   │  ← 历史会话（○ 灰色圆点）
│    抽查完成，3 笔异常        │
├─────────────────────────────┤
│  ○ 收入确认检查     3天前   │
│    收入确认政策符合准则      │
├─────────────────────────────┤
│  ○ 固定资产盘点     上周    │
│    盘点差异报告已生成        │
└─────────────────────────────┘
```

**交互规则**：

| 操作 | 行为 |
|------|------|
| 点击会话 | 加载该 session 的历史消息，恢复聊天上下文 |
| 新建会话 | 调用 `POST /api/sessions`，创建空 session，切换到新会话 |
| 会话标题 | 首次 Agent 回复后自动生成（取首条用户消息前 20 字 + 时间戳） |
| 排序 | 按 `last_active_at` 倒序，活跃会话置顶 |
| 删除会话 | 右键/长按菜单 → 确认 → `DELETE /api/sessions/{id}`（同时清理容器 session） |

**API**：

```typescript
interface SessionItem {
  session_id: string;
  title: string;            // 自动生成，如 "审计报告_2025"
  last_message: string;     // 最后一条 agent 消息摘要（截断 40 字）
  last_active_at: string;   // ISO 8601 时间
  status: "active" | "completed" | "error";
  file_count: number;       // 关联文件数
}

GET    /api/users/{user_id}/sessions          → SessionItem[]
POST   /api/users/{user_id}/sessions          → { session_id, title }
DELETE /api/users/{user_id}/sessions/{id}      → { status: "ok" }
```

### 3.3 中间主体：Chat 区域

**消息类型**：

| 类型 | 渲染 | 示例 |
|------|------|------|
| user | 右对齐气泡，蓝色背景 | 用户输入的审计指令 |
| assistant | 左对齐，支持 Markdown 渲染 | Agent 分析结果、审计结论 |
| system | 居中，灰色小字 | "会话已恢复"、"Agent 正在思考..." |
| tool_use | 可折叠面板，显示工具名+参数 | `mcp__pdf_server__extract_text` |
| file_upload | 文件卡片列表 | 用户上传的附件 |
| file_result | 文件卡片列表，带下载按钮 | Agent 生成的结果文件 |

**文件卡片组件**：

```tsx
// 用户上传的文件卡片
<div className="file-upload-card">
  <FileIcon type="pdf" />
  <span className="filename">annual_report_2025.pdf</span>
  <span className="filesize">2.4 MB</span>
  <button className="delete-btn" title="删除文件">✕</button>
</div>

// Agent 生成的结果文件卡片
<div className="file-result-card">
  <FileIcon type="md" />
  <span className="filename">audit_report_2025.md</span>
  <span className="filesize">15 KB</span>
  <a href="/api/download/..." download className="download-btn">
    [下载]
  </a>
</div>
```

### 3.4 底部：输入区 + 文件操作

```
┌──────────────────────────────────────────────────────────┐
│  已上传文件预览区（可选显示）                              │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐           │
│  │ report.pdf │ │ data.xlsx  │ │ scan.jpg   │           │
│  │  2.4 MB ✕  │ │  1.1 MB ✕  │ │  800 KB ✕  │           │
│  └────────────┘ └────────────┘ └────────────┘           │
├──────────────────────────────────────────────────────────┤
│  [📎]  输入审计指令...                      [发送 ▶]     │
└──────────────────────────────────────────────────────────┘
```

**交互规则**：

| 操作 | 行为 |
|------|------|
| 点击 📎 上传文件 | 打开文件选择器 → `POST /api/upload` → 显示文件卡片 + ✕ 按钮 |
| 点击 ✕ 删除文件 | `DELETE /api/files/{filename}` → 移除卡片 → 刷新文件列表 |
| 输入文本 + 发送 | 通过 WebSocket 发送 `{ type: "chat", session_id, message, files }` |
| 文件 + 文字一起发 | 同一条消息包含文本指令和文件列表，Agent 一并处理 |
| 拖拽上传 | 拖拽文件到页面任意位置 → 自动上传并显示卡片 |

### 3.5 文件管理完整流程

```
用户点击上传 / 拖拽文件
        │
        ▼
POST /api/users/{user_id}/upload
        │
        ▼
FastAPI → 写入 /data/users/{user_id}/workspace/uploads/
        │
        ▼
前端收到 { status: "ok", filename, size }
        │
        ├── 在输入区显示文件卡片（带 ✕ 删除按钮）
        └── 刷新侧边栏文件列表（若有独立文件面板）

用户在 Chat 中发送消息（附带已上传文件列表）
        │
        ▼
WebSocket → Agent 处理文件（通过 MCP Server）
        │
        ▼
Agent 产出结果文件 → 写入 /data/users/{user_id}/workspace/reports/
        │
        ▼
Agent 消息中包含结果文件列表 → Chat 区域渲染文件卡片（带 [下载] 按钮）
        │
        ▼
用户点击 [下载] → GET /api/users/{user_id}/download/{path}
        │
        ▼
浏览器下载文件
```

### 3.6 响应式布局

| 断点 | 布局 |
|------|------|
| ≥ 1024px | 三栏布局（Session 列表 240px + Chat 自适应） |
| 768px - 1023px | Session 列表可折叠（抽屉式） |
| < 768px | 全屏 Chat，Session 列表通过汉堡菜单弹出 |

---

## 4. Skills 双层架构设计

### 4.1 文件系统结构

```
/data/
│
├── shared-skills/                          ← 公共 Skills（所有用户可用）
├── training/                               ← 反馈与训练数据（Section 5.5）
│   ├── qa/                                 ← prompt-response 对
│   ├── preferences/                        ← 审计结论 + 人工修正
│   ├── skill_outcomes/                     ← 多版本 Skill 效果对比
│   └── corrections/                        ← 用户对回答的修改
├── mcp-registry.json                       ← MCP 注册中心
│   ├── audit-pdf/
│   │   └── SKILL.md                        ← YAML frontmatter + Markdown 指令
│   ├── audit-excel/
│   │   └── SKILL.md
│   ├── report-gen/
│   │   └── SKILL.md
│   └── compliance-check/
│       └── SKILL.md
│
└── users/
    ├── alice/
    │   ├── workspace/                       ← 工作目录（审计文件、产出报告）
    │   └── skills/                          ← 个人 Skills（仅 alice 可用）
    │       ├── custom-review/
    │       │   └── SKILL.md
    │       └── internal-template/
    │           └── SKILL.md
    ├── bob/
    │   ├── workspace/
    │   └── skills/
    │       └── special-audit/
    │           └── SKILL.md
    └── charlie/
        ├── workspace/
        └── skills/                          ← 可以没有个人 Skills
```

### 4.2 容器挂载策略

每个用户容器同时挂载两层 Skills：

> **注意**：`ro`（只读）仅针对**用户容器内部**。管理员（Web 应用中的角色）通过浏览器 → API → FastAPI 后端写入宿主机路径 `/data/shared-skills/`，整个流程不经过用户容器。

```
┌──────────────────────────────────────────────────┐
│  管理员（Web 用户角色）                            │
│  ┌────────────────────────────────────────────┐  │
│  │ 浏览器 → 管理后台 UI                        │  │
│  │  · 创建/编辑/删除公共 Skill                 │  │
│  │  · 注册/注销 MCP Server                    │  │
│  │  · 启用/禁用 Tool                          │  │
│  └─────────────────┬──────────────────────────┘  │
└────────────────────┼──────────────────────────────┘
                     │ HTTPS
                     ▼
┌──────────────────────────────────────────────────┐
│  FastAPI 后端进程（应用级权限）                    │
│  · 校验管理员角色（JWT role=admin）               │
│  · 写入 /data/shared-skills/{name}/SKILL.md      │
│  · 更新 /data/mcp-registry.json                  │
│  · 触发受影响容器热加载                           │
└──────────────┬───────────────────────────────────┘
               │ Docker Volume 自动同步
               ▼
┌──────────────────────────────┐
│  用户容器内挂载（ro 只读）    │
│  /home/agent/.claude/        │
│     shared-skills/           │ ← 文件立即可见，不可修改
└──────────────────────────────┘
```

```python
volumes = {
    # 1. 公共 Skills — 只读挂载到用户容器，所有用户共享
    #    管理员通过宿主机路径 /data/shared-skills/ 直接管理（不走容器）
    "/data/shared-skills": {
        "bind": "/home/agent/.claude/shared-skills",
        "mode": "ro"  # 容器内只读，普通用户不可修改
    },
    # 2. 用户个人 Skills — 读写挂载
    f"/data/users/{user_id}/skills": {
        "bind": "/home/agent/.claude/personal-skills",
        "mode": "rw"
    },
    # 3. 用户工作目录
    f"/data/users/{user_id}/workspace": {
        "bind": "/workspace",
        "mode": "rw"
    },
    # 4. Claude 数据持久化（Session、配置、缓存、prompt cache）
    #    容器重启/销毁后 session 不丢失
    f"/data/users/{user_id}/claude-data": {
        "bind": "/home/agent/.claude",
        "mode": "rw"
    },
}

environment = {
    "ANTHROPIC_API_KEY": get_user_api_key(user_id),
    # 告诉 Agent Skills 的搜索路径
    "CLAUDE_SKILLS_DIRS": (
        "/home/agent/.claude/shared-skills,"
        "/home/agent/.claude/personal-skills"
    ),
}
```

### 4.3 SKILL.md 格式规范

与 Claude Code 100% 兼容，每个 Skill 是一个目录，包含一个 `SKILL.md` 文件：

```markdown
---
name: audit-pdf
description: Use when the user asks to audit, review, or analyze PDF files for financial or compliance purposes
---

# PDF 审计技能

## 触发条件

当用户要求审计 PDF 文件时自动激活。

## 执行步骤

1. 使用 `pdfplumber` 提取 PDF 中的文本和表格数据
2. 检查以下审计要点：
   - 金额一致性（借贷平衡）
   - 日期逻辑（凭证日期 vs 业务日期）
   - 签章完整性
   - 页码连续性
3. 生成审计报告到 `/workspace/reports/` 目录

## 输出格式


审计报告 - {文件名}
==================
发现 N 个问题：
1. ...
2. ...


## 依赖工具

- `pdfplumber` (Python)
- `pandas` (Python)
```

### 4.4 Skill 加载与合并

```python
def load_skills():
    """从公共 + 个人目录加载所有 Skills"""
    skills_dirs = os.getenv(
        "CLAUDE_SKILLS_DIRS",
        "/home/agent/.claude/shared-skills,/home/agent/.claude/personal-skills"
    ).split(",")

    all_skills = {}
    for skills_dir in skills_dirs:
        path = Path(skills_dir)
        if not path.exists():
            continue
        for skill_dir in path.iterdir():
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                name = skill_dir.name
                all_skills[name] = {
                    "path": str(skill_dir),
                    "source": "shared" if "shared" in str(skill_dir) else "personal",
                    "content": skill_file.read_text(),
                }

    # 同名 Skill：个人优先覆盖公共
    return all_skills
```

### 4.5 权限矩阵

| 操作 | 公共 Skills | 个人 Skills |
|------|-----------|-----------|
| 查看 | ✅ 所有用户 | ✅ 仅本人 |
| 创建 | ❌ 仅管理员（Web 管理后台） | ✅ 用户自己 |
| 编辑/更新 | ❌ 仅管理员（Web 管理后台） | ✅ 用户自己 |
| 删除 | ❌ 仅管理员 | ✅ 用户自己 |
| Agent 自动调用 | ✅ 匹配 description 时 | ✅ 匹配 description 时 |
| 同名冲突 | 被个人覆盖 | 覆盖公共 |

---

---

## 5. 记忆机制

审计业务天然需要跨会话的实体上下文——公司名、会计期间、历史审计结论等不应每次新会话都重新输入。SDK 自带的会话恢复仅解决**单会话内**的消息连续性，不解决**跨会话记忆**。

### 5.1 记忆分层

```
┌─────────────────────────────────────────────────┐
│              记忆机制（Memory）                    │
├──────────────────┬──────────────────────────────┤
│  L1: 平台记忆     │  L2: Agent 自主记忆          │
│  (MVP 阶段)       │  (后续迭代)                  │
├──────────────────┼──────────────────────────────┤
│ settings.json    │  memory/*.md                 │
│ + entity_memory  │  Agent 通过 Write 工具维护    │
│ 结构化数据         │ Markdown 自然语言            │
│ 新会话自动加载    │ Agent 自主决定何时更新        │
└──────────────────┴──────────────────────────────┘
```

**L1: 平台记忆**——由 Web 平台管理，用户通过 UI 编辑，以结构化 JSON 存储。新会话启动时注入 Agent 的 system prompt。

**L2: Agent 自主记忆**——由 Agent 在对话中自主维护，通过 SDK 的 `Write` 工具写入 Markdown 文件，下次会话时自动读取。

### 5.2 L1: 平台记忆（MVP）

#### 5.2.1 存储结构

```
/data/users/{user_id}/
├── workspace/                ← 工作目录
├── skills/                   ← 个人 Skills
├── claude-data/              ← SDK 会话数据（已存在）
│   ├── sessions/
│   └── prompt_cache/
└── memory.json               ← 平台记忆（新增）
```

```jsonc
{
  "user_id": "alice",
  // 用户偏好（UI 可编辑）
  "preferences": {
    "model": "claude-sonnet-4-6",
    "max_budget_usd": 2.0,
    "language": "zh",
    "audit_detail_level": "standard"    // standard | detailed
  },
  // 实体记忆（跨会话共享的企业信息）
  "entity_memory": {
    "company_name": "某某科技有限公司",
    "credit_code": "91110000XXXXXXXXXX",
    "fiscal_year": "2025",
    "accounting_standard": "CAS",       // CAS | IFRS | US GAAP
    "industry": "软件和信息技术服务业",
    "last_audit_date": "2025-03-15",
    "key_contacts": [
      { "name": "张三", "role": "财务总监" },
      { "name": "李四", "role": "会计主管" }
    ]
  },
  // 审计上下文（自动积累，跨会话延续）
  "audit_context": {
    "prior_findings": [
      {
        "session": "session_abc",
        "date": "2025-03-15",
        "item": "2笔跨期收入，合计 ¥1,250,000",
        "standard": "CAS 14 第五条",
        "status": "待整改跟踪"
      },
      {
        "session": "session_def",
        "date": "2025-03-20",
        "item": "3家未披露关联方，交易总额 ¥3,800,000",
        "standard": "CAS 36 第四条",
        "status": "待整改跟踪"
      }
    ],
    "risk_areas": ["收入确认", "关联交易披露", "银行未达账项"],
    "prior_sessions": ["session_abc", "session_def"]
  },
  // 文件记忆（高频文件引用）
  "file_memory": [
    { "filename": "annual_report_2025.pdf", "path": "uploads/annual_report_2025.pdf", "last_used": "2025-04-12" },
    { "filename": "ledger.xlsx", "path": "uploads/ledger.xlsx", "last_used": "2025-04-12" }
  ]
}
```

#### 5.2.2 新会话自动注入

每次新会话启动时，Agent 读取 `memory.json` 并构建增强的 system prompt：

```
你是审计 AI 助手。以下是当前工作上下文：

【企业信息】
- 公司名称: 某某科技有限公司
- 会计标准: CAS（企业会计准则）
- 财年: 2025
- 行业: 软件和信息技术服务业

【上次审计发现】
1. 2笔跨期收入 ¥1,250,000（CAS 14，状态: 待整改跟踪）
2. 3家未披露关联方 ¥3,800,000（CAS 36，状态: 待整改跟踪）

【重点关注】
请特别关注以上问题的整改情况。

【常用文件】
- annual_report_2025.pdf（上次使用: 2025-04-12）
- ledger.xlsx（上次使用: 2025-04-12）
```

#### 5.2.3 更新机制

| 触发时机 | 更新内容 | 方式 |
|---------|---------|------|
| 用户通过 UI 编辑 | 企业信息、偏好设置 | 前端 `PUT /api/memory/{user_id}` → 写入 `memory.json` |
| Agent 审计完成 | 新增 prior_findings | Agent 通过 `Bash` 工具调用平台 API 更新 |
| 新会话结束 | 更新 prior_sessions | 后端自动追加 |
| 文件首次使用 | 更新 file_memory | Agent 处理文件时自动记录 |

```python
@app.put("/api/users/{user_id}/memory")
async def update_memory(user_id: str, update: MemoryUpdate):
    """更新用户记忆（用户通过 UI 编辑）"""
    mem_file = Path(f"/data/users/{user_id}/memory.json")
    if mem_file.exists():
        memory = json.loads(mem_file.read_text())
    else:
        memory = {"user_id": user_id, "preferences": {}, "entity_memory": {},
                  "audit_context": {"prior_findings": [], "risk_areas": [], "prior_sessions": []},
                  "file_memory": []}
    # 递归合并
    deep_merge(memory, update.model_dump(exclude_unset=True))
    memory["updated_at"] = datetime.utcnow().isoformat()
    mem_file.write_text(json.dumps(memory, ensure_ascii=False, indent=2))
    return {"status": "ok"}
```

### 5.3 L2: Agent 自主记忆（后续迭代）

#### 5.3.1 记忆文件结构

Agent 在对话过程中通过 SDK 的 `Write` / `Edit` 工具自主维护 Markdown 记忆文件：

```
/data/users/{user_id}/claude-data/
└── memory/
    ├── entity.md        # 企业基本信息（Agent 自动补充）
    ├── findings.md      # 审计发现汇总（Agent 实时记录）
    ├── preferences.md   # 用户偏好（Agent 学习记录）
    └── audit-plan.md    # 审计计划与进度（Agent 自主规划）
```

```markdown
<!-- findings.md 示例 -->
# 审计发现记录

## 2025-04-12 收入审计
- 跨期收入 2 笔，¥1,250,000，违反 CAS 14 第五条
- 已通知用户，等待确认
- 下一步: 追踪整改情况

## 2025-04-12 关联方审查
- 发现 3 家未披露关联方
- 通过股权穿透识别
- 交易总额 ¥3,800,000
```

#### 5.3.2 与 L1 的区别

| 维度 | L1 平台记忆 | L2 Agent 自主记忆 |
|------|------------|-------------------|
| 维护者 | Web 平台 + 用户 | Agent 自主决定 |
| 格式 | JSON 结构化 | Markdown 自然语言 |
| 更新方式 | UI 编辑 + API | Agent 通过 Write/Edit 工具 |
| 读取方式 | 新会话注入 | Agent 自主读取（通过 Read 工具） |
| 灵活性 | 低（固定字段） | 高（Agent 自由组织） |
| 适合场景 | 企业信息、偏好、历史结论 | 审计过程记录、临时备注 |

#### 5.3.3 实现方式

在 `agent_server.py` 中，启动 Agent 时自动将 `memory/` 目录加载到 Skills 加载链中：

```python
def load_memory() -> str:
    """加载 Agent 自主记忆文件"""
    mem_dir = Path("/home/agent/.claude/memory")
    if not mem_dir.exists():
        return ""
    parts = []
    for f in mem_dir.glob("*.md"):
        parts.append(f"# {f.name}\n{f.read_text()}")
    return "\n---\n".join(parts)

# 注入到 system prompt 末尾
system_prompt = build_system_prompt(skills)
memory_context = load_memory()
if memory_context:
    system_prompt += f"\n\n# 记忆上下文\n\n{memory_context}\n\n请根据实际情况读取和更新这些文件。"
```

### 5.4 记忆与 Session 的关系

```
┌───────────────────────────────────────────────┐
│                                               │
│  记忆 (memory.json + memory/)                  │
│  ┌─────────────────────────────────────────┐  │
│  │ 跨会话、长期存在、持续积累               │  │
│  │ 用户信息、偏好、历史审计结论              │  │
│  └─────────────────────────────────────────┘  │
│                                               │
│  Session A          Session B          Session C │
│  ┌───────┐          ┌───────┐          ┌───────┐│
│  │ 会话级 │          │ 会话级 │          │ 会话级 ││
│  │ 消息   │          │ 消息   │          │ 消息   ││
│  │ 上下文 │          │ 上下文 │          │ 上下文 ││
│  └───────┘          └───────┘          └───────┘│
│     │                  │                  │     │
│     └──────────────────┼──────────────────┘     │
│                        │                        │
│              都读取同一份记忆                     │
└───────────────────────────────────────────────┘
```

**关键原则**：记忆是会话之间的共享层，Session 是短暂的（可删除、可恢复），记忆是持久的。

### 5.5 Skill 反馈与进化机制（平台级 RLHF）

> Claude Agent SDK 底层调用 API 模型，**不支持模型权重微调（RLHF）**。
> 但可以通过**人类反馈驱动的 Skill prompt 迭代**实现同等目标——持续优化 Skill 质量和审计效果。

#### 5.5.1 反馈收集闭环

```
用户使用 Skill 执行审计
        │
        ▼
审计任务完成 → 触发反馈界面
        │
        ├── 👍 有效 / 👎 需要改进
        ├── 用户实际修改了哪些内容
        ├── 补充的审计要点（文本备注）
        └── 标记"已采纳到公共 Skill"
        │
        ▼
feedback.jsonl 记录
        │
        ▼
积累到阈值（10+ 条同 Skill 反馈）
        │
        ▼
LLM 分析反馈 → 生成 Skill 改进版
        │
        ▼
管理员审核通过 → 替换公共 Skill
```

**为什么这是可行的 RLHF**：

| 传统 RLHF | 平台级 Skill RLHF |
|-----------|------------------|
| 人类标注员对模型输出打分 | 用户对 Skill 审计结果打分 |
| 训练奖励模型 | 反馈积累到 `feedback.jsonl` |
| PPO 更新模型权重 | LLM 分析反馈生成改进版 prompt |
| 权重更新后全局生效 | Skill 版本更新后所有用户生效 |
| 成本高、周期长 | 轻量级、实时生效 |

#### 5.5.2 训练数据收集

为未来迁移到可微调模型做准备，高质量交互沉淀为训练数据：

```
/data/training/
├── qa/                              # prompt-response 对
│   ├── {timestamp}_{session_id}.jsonl
│   │   {"prompt": "请审计这份年报...", "response": "...",
│   │    "skill_used": "audit-pdf", "rating": 5,
│   │    "user_edits": "修改了第3段的结论", "model": "claude-sonnet-4-6"}
│   └── ...
├── preferences/                     # 审计结论 + 人工修正
│   ├── {timestamp}_{session_id}.jsonl
│   │   {"original": "未发现异常", "corrected": "发现跨期收入...",
│   │    "context": "收入确认审计", "skill": "revenue-check"}
│   └── ...
├── skill_outcomes/                  # 多版本 Skill 效果对比
│   ├── audit-pdf_v2.json            # {"avg_rating": 4.2, "usage_count": 28}
│   ├── audit-pdf_v3.json            # {"avg_rating": 4.7, "usage_count": 15}
│   └── ...
└── corrections/                     # 用户对回答的修改
    └── {timestamp}_{session_id}.diff
```

**数据质量标准**：

| 标准 | 阈值 |
|------|------|
| 最低评分 | ≥ 4/5 才纳入训练数据 |
| 用户修改率 | < 20% 视为高质量输出 |
| Skill 采纳率 | 被标记"已采纳"的 Skill 版本优先 |
| 模型多样性 | 同一 prompt 至少收集 3 个模型的响应 |

#### 5.5.3 Skill 自动进化

```python
import json
import time
from pathlib import Path
from dataclasses import dataclass, field, asdict

import anthropic  # 用于 Skill 改进版生成

@dataclass
class SkillFeedback:
    skill_name: str
    session_id: str
    rating: int              # 1-5
    user_edits: str          # 用户实际修改内容
    comments: str            # 备注
    skill_version: str       # 当时使用的 Skill 版本
    timestamp: str = field(default_factory=lambda: time.time().__str__())

class SkillEvolution:
    """基于用户反馈自动进化 Skill"""

    FEEDBACK_FILE = "/data/training/qa/feedback.jsonl"
    SKILLS_DIR = "/data/shared-skills"
    MIN_FEEDBACK = 10          # 最低反馈数
    MIN_AVG_RATING = 3.5       # 触发改进的平均分阈值

    def collect_feedback(self, feedback: SkillFeedback):
        """收集用户对 Skill 产出的反馈"""
        with open(self.FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(feedback), ensure_ascii=False) + "\n")

    def get_feedback_stats(self, skill_name: str) -> dict:
        """统计 Skill 的反馈数据"""
        all_fb = []
        with open(self.FEEDBACK_FILE, "r", encoding="utf-8") as f:
            for line in f:
                item = json.loads(line)
                if item["skill_name"] == skill_name:
                    all_fb.append(item)
        if not all_fb:
            return {"count": 0}
        ratings = [fb["rating"] for fb in all_fb]
        return {
            "count": len(all_fb),
            "avg_rating": sum(ratings) / len(ratings),
            "high_quality": sum(1 for r in ratings if r >= 4),
            "versions": set(fb["skill_version"] for fb in all_fb),
        }

    def should_evolve(self, skill_name: str) -> bool:
        """判断是否需要进化该 Skill"""
        stats = self.get_feedback_stats(skill_name)
        return (
            stats.get("count", 0) >= self.MIN_FEEDBACK
            and stats.get("avg_rating", 0) < 4.5   # 还有提升空间
        )

    def generate_improved_skill(self, skill_name: str) -> str:
        """用高质量交互生成改进版 Skill prompt

        流程：
        1. 取 top-10 高评分交互（rating ≥ 4）
        2. 取用户修改最多的片段
        3. 调用 LLM 总结规律并生成新 prompt
        4. 输出新 Skill 内容（不直接替换，需审核）
        """
        stats = self.get_feedback_stats(skill_name)
        if stats["count"] < self.MIN_FEEDBACK:
            raise ValueError(f"反馈不足: {stats['count']} 条")

        # 1. 收集高质量交互
        high_quality = []
        with open(self.FEEDBACK_FILE, "r", encoding="utf-8") as f:
            for line in f:
                fb = json.loads(line)
                if fb["skill_name"] == skill_name and fb["rating"] >= 4:
                    high_quality.append(fb)

        # 2. 构建改进指令
        improvement_prompt = f"""你是一个 Skill 优化专家。基于以下用户反馈，
改进名为 {skill_name} 的审计 Skill。

高质量交互示例（用户满意的）：
{json.dumps(high_quality[:5], ensure_ascii=False, indent=2)}

用户常见的修改内容：
{chr(10).join(fb['user_edits'] for fb in high_quality if fb['user_edits'])}

请生成改进后的 Skill 内容，保持 SKILL.md 格式（YAML frontmatter + Markdown 指令）。
重点改进：用户经常手动修正的部分、评分较低的环节。"""

        # 3. 调用 LLM 生成（管理端后台任务，不经过用户容器）
        #    注意：此处使用原始 API 而非 SDK，因为 Skill 进化是后台批处理任务，
        #    不需要工具调用、Session 管理等 SDK 能力。
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-6-20260317",
            max_tokens=4096,
            system="你是一个 Skill prompt 优化专家。",
            messages=[{"role": "user", "content": improvement_prompt}]
        )
        improved_content = response.content[0].text

        # 4. 保存为待审核版本
        review_path = Path(f"/data/shared-skills/{skill_name}/SKILL_v_next.md")
        review_path.write_text(improved_content, encoding="utf-8")
        return str(review_path)

    def get_evolution_candidates(self) -> list[dict]:
        """获取所有需要进化的 Skill 列表"""
        candidates = []
        skills_dir = Path(self.SKILLS_DIR)
        for skill_dir in skills_dir.iterdir():
            if (skill_dir / "SKILL.md").exists():
                name = skill_dir.name
                if self.should_evolve(name):
                    candidates.append({
                        "name": name,
                        "stats": self.get_feedback_stats(name),
                    })
        return sorted(candidates, key=lambda x: x["stats"]["avg_rating"])


# ── Skill A/B 测试 ──────────────────────────────────────

class SkillABTest:
    """Skill 版本的 A/B 测试"""

    def __init__(self, skill_name: str, version_a: str, version_b: str):
        self.skill_name = skill_name
        self.version_a = version_a   # 当前版本内容
        self.version_b = version_b   # 新版本内容
        self.results_a: list[int] = []   # A 版本评分
        self.results_b: list[int] = []   # B 版本评分

    def assign_version(self, user_id: str) -> str:
        """根据 user_id 哈希分配版本"""
        return "a" if hash(user_id) % 2 == 0 else "b"

    def record_result(self, user_id: str, rating: int):
        version = self.assign_version(user_id)
        if version == "a":
            self.results_a.append(rating)
        else:
            self.results_b.append(rating)

    def is_winner(self) -> str | None:
        """判断是否有胜出版本（t-test 显著性）"""
        if len(self.results_a) < 5 or len(self.results_b) < 5:
            return None
        avg_a = sum(self.results_a) / len(self.results_a)
        avg_b = sum(self.results_b) / len(self.results_b)
        # 简单阈值：差距 > 0.3 且样本足够
        if abs(avg_b - avg_a) > 0.3:
            return "b" if avg_b > avg_a else "a"
        return None
```

#### 5.5.4 反馈 UI 设计

审计任务完成后，在 Chat 区域底部显示：

```
┌─────────────────────────────────────────────────┐
│  本次审计质量如何？                              │
│  [⭐⭐⭐⭐⭐]  非常满意                           │
│  [⭐⭐⭐⭐  ]  满意                                │
│  [⭐⭐⭐    ]  一般                                │
│  [⭐⭐      ]  需要改进                          │
│  [⭐        ]  不满意                            │
│                                                 │
│  您做了哪些修改？                                │
│  ┌───────────────────────────────────────────┐  │
│  │ 补充了对关联交易的穿透审查...              │  │
│  └───────────────────────────────────────────┘  │
│                                                 │
│  [提交反馈]        [跳过]                        │
└─────────────────────────────────────────────────┘
```

#### 5.5.5 进化流程总结

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ 1. 收集  │────▶│ 2. 评估  │────▶│ 3. 进化  │────▶│ 4. 审核  │
│ 反馈     │     │ 统计指标  │     │ 生成新版  │     │ 管理员    │
│ (实时)   │     │ (阈值)   │     │ (LLM)    │     │ (人工)   │
└──────────┘     └──────────┘     └──────────┘     └─────┬────┘
                                                         │
                                    ┌────────────────────▼────┐
                                    │ 5. A/B 测试              │
                                    │ 灰度发布 → 对比效果       │
                                    └──────────────┬──────────┘
                                                   │
                                    ┌──────────────▼──────────┐
                                    │ 6. 全量替换              │
                                    │ 胜出版本成为新公共 Skill │
                                    └─────────────────────────┘
```

**关键决策点**：

| 阶段 | 自动 / 人工 | 触发条件 |
|------|------------|---------|
| 收集反馈 | 自动 | 每次审计完成 |
| 评估指标 | 自动 | 反馈数 ≥ 10 |
| 生成新版 | 自动 | 平均分 < 4.5 |
| 审核通过 | **人工** | 管理员在管理后台确认 |
| A/B 测试 | 自动 | 审核通过后灰度 20% 用户 |
| 全量替换 | 自动 | A/B 测试胜出版本 |

## 6. MCP Server 与 Tool 管理

文件处理（Office/PDF/图片/OCR）通过 MCP Server 实现，Agent SDK 本身不直接处理文件。管理员统一管理 MCP Server 的注册、启用、禁用和权限分配。

### 6.1 架构设计

```
┌──────────────────────────────────────────────────────────┐
│  管理员控制台                                              │
│  · 注册/注销 MCP Server                                  │
│  · 启用/禁用特定 Tool                                     │
│  · 配置 Tool 权限（哪些用户/角色可用）                      │
│  · 健康检查 & 监控                                        │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│  MCP 注册中心 (MCP Registry)                              │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │  MCP Server 列表                                  │    │
│  │  · mcp-pdf: stdio 模式, 状态: running             │    │
│  │  · mcp-excel: stdio 模式, 状态: running           │    │
│  │  · mcp-ocr: http 模式, 状态: running              │    │
│  │  · mcp-image: http 模式, 状态: running            │    │
│  │  · mcp-docx: stdio 模式, 状态: running            │    │
│  └──────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────┐    │
│  │  Tool 权限矩阵                                    │    │
│  │  · pdf_extract: all users                        │    │
│  │  · excel_audit: all users                        │    │
│  │  · ocr_scan: all users                           │    │
│  │  · custom_export: admin only                     │    │
│  └──────────────────────────────────────────────────┘    │
└────────────────────────┬─────────────────────────────────┘
                         │ 注入到容器环境变量
                         ▼
┌──────────────────────────────────────────────────────────┐
│  用户容器                                                  │
│                                                          │
│  MCP_CONFIG_JSON = {                                     │
│    "mcpServers": {                                       │
│      "mcp-pdf": {                                        │
│        "command": "python",                               │
│        "args": ["-m", "mcp_pdf_server"],                  │
│        "enabled_tools": ["extract_text",                  │
│                          "extract_tables",                │
│                          "audit_document"]                │
│      },                                                  │
│      "mcp-ocr": {                                        │
│        "url": "http://mcp-ocr:8000/mcp",                  │
│        "enabled_tools": ["scan_image",                   │
│                          "extract_invoice"]               │
│      }                                                   │
│    }                                                     │
│  }                                                       │
└──────────────────────────────────────────────────────────┘
```

### 6.2 MCP 配置文件结构

```
/data/
│
├── mcp-servers/                           ← MCP Server 代码/配置
│   ├── mcp-pdf/
│   │   ├── pyproject.toml
│   │   └── src/
│   ├── mcp-excel/
│   │   ├── pyproject.toml
│   │   └── src/
│   ├── mcp-ocr/
│   │   ├── pyproject.toml
│   │   └── src/
│   └── mcp-docx/
│       ├── pyproject.toml
│       └── src/
│
├── mcp-registry.json                      ← MCP 注册中心配置
│
└── users/
    └── {user_id}/
        └── mcp-config.json                ← 用户级 MCP 配置（权限裁剪后）
```

`mcp-registry.json` 示例：

```json
{
  "mcpServers": {
    "mcp-pdf": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_pdf_server"],
      "tools": ["extract_text", "extract_tables", "audit_document"],
      "description": "PDF 文件提取和审计工具",
      "enabled": true,
      "access": "all"
    },
    "mcp-excel": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_excel_server"],
      "tools": ["read_sheet", "audit_ledger", "export_report"],
      "description": "Excel 数据读取和审计工具",
      "enabled": true,
      "access": "all"
    },
    "mcp-ocr": {
      "type": "http",
      "url": "http://mcp-ocr-service:8000/mcp",
      "tools": ["scan_image", "extract_invoice", "detect_tampering"],
      "description": "图片 OCR 和发票识别工具",
      "enabled": true,
      "access": "all"
    },
    "mcp-custom-export": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_custom_export"],
      "tools": ["generate_custom_report"],
      "description": "自定义审计报告导出（仅管理员）",
      "enabled": true,
      "access": "admin"
    }
  }
}
```

### 6.3 权限模型

| 操作 | 公共 MCP Server | 用户可见 Tool |
|------|----------------|--------------|
| 注册/注销 | ❌ 仅管理员 | — |
| 启用/禁用 | ❌ 仅管理员 | — |
| 启用/禁用单个 Tool | ❌ 仅管理员 | — |
| 查看可用 Tool | ✅ 所有用户 | 仅本人有权限的 |
| 调用 Tool | — | 仅 enabled 且有权限的 |

### 6.4 容器内 MCP 注入逻辑

```python
def build_user_mcp_config(user_id: str, registry: dict, user_role: str) -> dict:
    """根据用户角色裁剪 MCP 配置"""
    mcp_config = {"mcpServers": {}}
    for server_name, server_config in registry["mcpServers"].items():
        # 跳过已禁用的 Server
        if not server_config.get("enabled", False):
            continue
        # 检查访问权限
        if server_config.get("access") == "admin" and user_role != "admin":
            continue
        mcp_config["mcpServers"][server_name] = {
            "command": server_config.get("command"),
            "args": server_config.get("args"),
            "url": server_config.get("url"),
        }
    return mcp_config
```

### 6.5 管理员 MCP 管理 API

```python
class McpServerConfig(BaseModel):
    name: str
    type: str          # "stdio" | "http"
    command: Optional[str] = None
    args: Optional[list[str]] = None
    url: Optional[str] = None
    tools: list[str]
    description: str
    enabled: bool = True
    access: str = "all"  # "all" | "admin"

class ToolToggle(BaseModel):
    server: str
    tool: str
    enabled: bool

@app.get("/api/admin/mcp-servers", response_model=dict)
async def list_mcp_servers():
    """管理员查看已注册的 MCP Server 列表"""
    registry = json.loads(Path("/data/mcp-registry.json").read_text())
    return registry["mcpServers"]

@app.post("/api/admin/mcp-servers")
async def register_mcp_server(server: McpServerConfig):
    """管理员注册新的 MCP Server"""
    registry = json.loads(Path("/data/mcp-registry.json").read_text())
    registry["mcpServers"][server.name] = server.model_dump()
    Path("/data/mcp-registry.json").write_text(json.dumps(registry, indent=2))
    # 重启受影响的用户容器以加载新 MCP 配置
    restart_all_containers_with_mcp()
    return {"status": "ok"}

@app.delete("/api/admin/mcp-servers/{server_name}")
async def unregister_mcp_server(server_name: str):
    """管理员注销 MCP Server"""
    registry = json.loads(Path("/data/mcp-registry.json").read_text())
    registry["mcpServers"].pop(server_name, None)
    Path("/data/mcp-registry.json").write_text(json.dumps(registry, indent=2))
    restart_all_containers_with_mcp()
    return {"status": "ok"}

@app.patch("/api/admin/mcp-servers/{server_name}/toggle")
async def toggle_mcp_server(server_name: str, enabled: bool):
    """管理员启用/禁用 MCP Server"""
    registry = json.loads(Path("/data/mcp-registry.json").read_text())
    if server_name in registry["mcpServers"]:
        registry["mcpServers"][server_name]["enabled"] = enabled
        Path("/data/mcp-registry.json").write_text(json.dumps(registry, indent=2))
        restart_all_containers_with_mcp()
    return {"status": "ok"}

@app.patch("/api/admin/mcp-servers/{server_name}/tools/{tool_name}/toggle")
async def toggle_tool(server_name: str, tool_name: str, enabled: bool):
    """管理员启用/禁用 MCP Server 中的单个 Tool"""
    registry = json.loads(Path("/data/mcp-registry.json").read_text())
    if server_name in registry["mcpServers"]:
        tools = registry["mcpServers"][server_name].get("tools", [])
        if enabled and tool_name not in tools:
            tools.append(tool_name)
        elif not enabled and tool_name in tools:
            tools.remove(tool_name)
        Path("/data/mcp-registry.json").write_text(json.dumps(registry, indent=2))
        restart_all_containers_with_mcp()
    return {"status": "ok"}
```

### 6.6 容器启动时加载 MCP

```python
# agent_server.py 中容器启动时
def load_mcp_config() -> dict:
    """从环境变量加载 MCP 配置"""
    mcp_config_json = os.getenv("MCP_CONFIG_JSON", "{}")
    return json.loads(mcp_config_json)

@app.websocket("/ws")
async def handle_agent(websocket: WebSocket):
    await websocket.accept()

    skills = load_skills()
    mcp_config = load_mcp_config()

    sdk = ClaudeSDK(
        model="claude-sonnet-4-6-20260317",
        work_dir="/workspace",
        system_prompt=build_system_prompt(skills),
        mcp_config=mcp_config,  # 注入 MCP 配置
    )

    while True:
        # ... 处理消息
```

容器环境变量由主服务器注入：

```python
def get_user_role(user_id: str) -> str:
    """获取用户角色（实际应从数据库/认证服务获取）"""
    roles_file = Path("/data/users.json")
    if roles_file.exists():
        users = json.loads(roles_file.read_text())
        return users.get(user_id, {}).get("role", "user")
    return "user"

def get_user_env(user_id: str) -> dict:
    registry = json.loads(Path("/data/mcp-registry.json").read_text())
    user_role = get_user_role(user_id)  # "user" | "admin"
    mcp_config = build_user_mcp_config(user_id, registry, user_role)

    return {
        "ANTHROPIC_API_KEY": os.getenv(f"API_KEY_{user_id.upper()}"),
        "CLAUDE_SKILLS_DIRS": (
            "/home/agent/.claude/shared-skills,"
            "/home/agent/.claude/personal-skills"
        ),
        "MCP_CONFIG_JSON": json.dumps(mcp_config),
        "USER_ID": user_id,
    }
```

---

## 7. Claude Agent SDK 核心能力

基于 [Claude Agent SDK TypeScript 源码分析](./CLAUDE_AGENT_SDK_ANALYSIS.md)，SDK 提供以下核心能力。

### 7.1 Agent Loop 运行机制

```
用户消息
   │
   ▼
┌─────────────────────────────────────────────────────┐
│  SDK query({ prompt, options })                     │
│                                                     │
│  1. 启动 `claude` CLI 子进程（非 API 直调）         │
│     - SDK 内部通过 subprocess 管理 CLI              │
│     - CLI 内部维护 Agent Loop、Session、Hooks        │
│     - 所有工具执行、Hook 拦截由 CLI 处理             │
│  2. 通过 stdin/stdout JSONL 与 CLI 通信             │
│  3. CLI 内部 Agentic Loop:                          │
│     ┌─────────────────────┐                         │
│     │ 发送用户消息         │                         │
│     │       ↓             │                         │
│     │ 接收 assistant 消息  │                         │
│     │       ↓             │                         │
│     │ 是否需要工具调用？   │                         │
│     │   是 → 执行工具      │                         │
│     │       ↓             │                         │
│     │ AskUserQuestion?    │── 是 → 暂停，等待用户响应 │
│     │       ↓ 否           │                         │
│     │ 返回 result/end      │                         │
│     └─────────────────────┘                         │
│                                                     │
│  4. SDK 通过 AsyncGenerator 流式返回消息给 Python    │
│  5. close() 终止子进程，清理资源                      │
└─────────────────────────────────────────────────────┘
```

SDK 通过 **AsyncGenerator** 流式返回消息，每条消息类型不同：

| 消息类型 | 说明 | 前端处理 |
|----------|------|----------|
| `assistant` | Claude 思考/回复 | 流式显示 |
| `tool_use` | 工具调用请求 | 显示工具执行中 |
| `tool_result` | 工具执行结果 | 折叠显示 |
| `result` | 对话结束 | 显示完成状态 |
| `system` | 系统事件（init/进度/通知） | 内部处理 |

### 7.2 内置工具列表

SDK 自动提供以下内置工具（无需额外配置）：

| 类别 | 工具 | 说明 |
|------|------|------|
| **文件操作** | `Read` | 读取文件、图片、PDF |
| | `Edit` | 字符串替换编辑 |
| | `Write` | 文件创建/覆写 |
| | `Glob` | 文件模式匹配搜索 |
| | `Grep` | 文件内容搜索 |
| **命令执行** | `Bash` | Shell 命令执行 |
| | `NotebookEdit` | Jupyter 笔记本编辑 |
| **Web** | `WebFetch` | URL 内容获取 |
| | `WebSearch` | Web 搜索 |
| **代理协作** | `Agent` | 子代理调用 |
| | `Skill` | 技能执行 |
| **交互** | `AskUserQuestion` | 向用户提问 |
| **规划** | `EnterPlanMode` / `ExitPlanMode` | 规划模式切换 |
| **任务管理** | `TaskCreate` / `TaskUpdate` / `TaskList` | 任务跟踪 |

**权限控制**：通过 `allowedTools` 和 `disallowedTools` 精确控制：

```python
sdk = ClaudeSDK(
    model="claude-sonnet-4-6-20260317",
    work_dir="/workspace",
    allowed_tools=["Read", "Grep", "Glob", "Bash", "mcp__pdf__extract_text"],
    disallowed_tools=["WebSearch", "WebFetch"],
)
```

### 7.2.1 AskUserQuestion 跨 WebSocket 交互

SDK 的 `AskUserQuestion` 工具会暂停 Agent Loop，等待用户回答。在多用户 WebSocket 架构下，这需要一个**双向通信机制**：

```
Agent (CLI 子进程)              SDK                  WebSocket                  前端
      │                         │                       │                        │
      │  tool_use: AskUser     │                       │                        │
      │ ───────────────────→  │ ─────────────────────→ │ ─────────────────────→ │
      │  (暂停，等待答案)       │                       │  type: "question"      │
      │                         │                       │  {                     │
      │                         │                       │    questions: [...],   │
      │                         │                       │    session_id          │
      │                         │                       │  }                     │
      │                         │                       │                        │
      │                         │                       │  ←── 用户选择选项 ─── │
      │                         │                       │                        │
      │                         │ ←──────────────────── │ type: "answer"         │
      │ ←── resume with ────── │ ←──────────────────── │ { session_id,          │
      │    tool_result         │                       │   answers: {...} }     │
      │                         │                       │                        │
      │ 继续执行...             │                       │                        │
```

**前端实现**：

```typescript
// 接收到 question 类型消息时渲染交互组件
function QuestionCard({ questions, onAnswer }: { questions: any[], onAnswer: (answers: Record<string, string>) => void }) {
  const [selected, setSelected] = useState<Record<string, string>>({});

  return (
    <div className="question-card">
      {questions.map((q, i) => (
        <div key={i} className="question-block">
          <p>{q.question}</p>
          {q.options.map((opt: any) => (
            <button
              key={opt.label}
              onClick={() => setSelected(prev => ({ ...prev, [q.question]: opt.label }))}
              className={selected[q.question] === opt.label ? 'selected' : ''}
            >
              {opt.label}
            </button>
          ))}
        </div>
      ))}
      <button onClick={() => onAnswer(selected)}>提交回答</button>
    </div>
  );
}
```

**服务端实现**（WebSocket 端点补充）：

```python
# 在 handle_agent 中增加 answer 分支
@app.websocket("/ws")
async def handle_agent(websocket: WebSocket):
    await websocket.accept()

    while True:
        msg = await websocket.receive_text()
        data = json.loads(msg)

        if data.get("type") == "answer":
            # 用户回答 → 写入 answer_queue
            session_id = data["session_id"]
            answer = data["answers"]
            answer_queue = pending_answers.get(session_id)
            if answer_queue:
                answer_queue.set_result(answer)
            continue

        # ... 原有 chat 消息处理
```

**Agent 任务中的 answer 等待逻辑**：

```python
pending_answers: dict[str, asyncio.Future] = {}

async def run_agent_task(session_id: str, user_message: str, sdk: ClaudeSDK):
    """独立 Agent 任务，处理 AskUserQuestion 暂停"""
    answer_event: asyncio.Future = asyncio.get_event_loop().create_future()
    pending_answers[session_id] = answer_event

    try:
        # 监听消息队列，检测 AskUserQuestion
        async for event in sdk.run(user_message):
            buffer.add_message(session_id, event)

            if event["type"] == "tool_use" and event["name"] == "AskUserQuestion":
                # Agent 暂停，等待用户回答
                answer = await asyncio.wait_for(answer_event, timeout=300)
                # 将答案注入回 CLI 子进程（通过 SDK 的 tool_result 机制）
                sdk.inject_tool_result(event["id"], json.dumps(answer))

        buffer.mark_done(session_id)
    finally:
        pending_answers.pop(session_id, None)
```

### 7.3 MCP 工具命名规则

MCP 工具在 SDK 中的命名格式：`mcp__<serverName>__<toolName>`

```python
allowed_tools = [
    "mcp__pdf__extract_text",
    "mcp__pdf__extract_tables",
    "mcp__pdf__audit_document",
    "mcp__excel__read_sheet",
    "mcp__excel__audit_ledger",
    "mcp__ocr__scan_image",
    "mcp__utils__calculate",
]
```

> **注意**：SDK **不支持** `mcp__utils__*` 通配符。每个 MCP Tool 必须精确列出全名。
> 在 `build_sdk_for_user` 中，需要从 MCP 配置动态展开所有 tool 名称：
>
> ```python
> def expand_allowed_tools(mcp_config: dict) -> list[str]:
>     """从 MCP 配置展开所有 tool 的全名列表"""
>     tools = []
>     for server_name in mcp_config.get("mcpServers", {}):
>         server_cfg = mcp_config["mcpServers"][server_name]
>         for tool_name in server_cfg.get("enabled_tools", []):
>             tools.append(f"mcp__{server_name}__{tool_name}")
>     return tools
> ```

### 7.4 权限模式

| 模式 | 行为 | 适用场景 |
|------|------|----------|
| `default` | 未批准工具显示权限对话框 | 交互式使用 |
| `dontAsk` | 未批准工具自动拒绝 | CI/CD、无人值守 |
| `acceptEdits` | 自动批准 Edit/Write | 自动化文件处理 |
| `bypassPermissions` | 绕过所有权限检查 | 可信环境 |
| `plan` | 进入规划模式 | 复杂任务规划 |

**本平台使用**：用户容器内使用 `bypassPermissions`（容器已隔离，用户操作在沙箱内），管理员可通过 `allowedTools` 限制可用工具范围。

### 7.5 钩子系统（Hooks）

SDK 提供事件钩子，可在关键节点拦截：

| 钩子 | 触发时机 | 可用操作 |
|------|----------|----------|
| `PreToolUse` | 工具执行前 | 允许/拒绝/修改参数 |
| `PostToolUse` | 工具执行后 | 审计日志/二次验证 |
| `PostToolUseFailure` | 工具失败后 | 重试/降级处理 |
| `Stop` | 会话停止时 | 资源清理/最终验证 |
| `UserPromptSubmit` | 用户提交提示前 | 内容过滤 |

**本平台应用**：Hooks 配置写入 `settings.json`，由 CLI 子进程在工具执行时调用外部脚本：

```jsonc
// ~/.claude/settings.json（容器内）
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "command": "python /hooks/pre_tool_use.py"
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "command": "python /hooks/post_tool_use.py"
      }
    ],
    "Stop": [
      {
        "command": "python /hooks/on_stop.py"
      }
    ]
  }
}
```

```python
# /hooks/pre_tool_use.py — 从 stdin 读取 JSON，输出决策到 stdout
import json
import sys

input_data = json.load(sys.stdin)
cmd = input_data.get("tool_input", {}).get("command", "")

DANGEROUS = ["rm -rf /", "curl", "wget", "nc ", "chmod 777", "mkfs"]
if any(x in cmd for x in DANGEROUS):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"危险命令已拦截: {cmd}"
        }
    }))
    sys.exit(0)  # CLI 拒绝该工具执行

# 允许执行 — 不输出任何内容，CLI 继续使用默认权限逻辑
```

```python
# /hooks/post_tool_use.py — 工具执行后记录审计日志
import json
import sys
from datetime import datetime

input_data = json.load(sys.stdin)
tool_name = input_data.get("tool_name", "unknown")
timestamp = datetime.utcnow().isoformat()

# 追加到审计日志
with open(f"/workspace/.audit-{tool_name}.log", "a") as f:
    f.write(json.dumps({"tool": tool_name, "timestamp": timestamp,
                        "session": sys.argv[1] if len(sys.argv) > 1 else "unknown"}) + "\n")
```

> **注意**：容器启动时，主服务器将 `settings.json` 和 Hook 脚本一起注入到用户容器的 `/home/agent/.claude/` 和 `/hooks/` 目录中。

### 7.5.1 工具输出截断策略

MCP Server 返回的工具输出可能非常大（例如 50 页 PDF 的全文提取），直接注入对话会导致：
- 上下文窗口溢出
- Token 成本暴增
- Agent 推理质量下降

**处理策略**：

```python
MAX_TOOL_OUTPUT_CHARS = 10_000  # 单次工具输出最大字符数
TRUNCATION_MESSAGE = "\n\n[... 输出已截断，已保留前 {n} 字符。如需更多内容，请分段读取 ...]"

def truncate_tool_output(raw_output: str) -> str:
    """截断过长的工具输出，保留头部和摘要"""
    if len(raw_output) <= MAX_TOOL_OUTPUT_CHARS:
        return raw_output

    head = raw_output[:MAX_TOOL_OUTPUT_CHARS]
    # 可选：尾部追加结构摘要（表格行数、金额合计等）
    summary = summarize_output(raw_output[MAX_TOOL_OUTPUT_CHARS:])
    return head + TRUNCATION_MESSAGE.format(n=MAX_TOOL_OUTPUT_CHARS) + (summary or "")

def summarize_output(tail: str) -> str:
    """从截断部分提取关键统计信息"""
    lines = tail.strip().split("\n")
    return f"\n\n[截断部分统计: 剩余 {len(lines)} 行]"
```

**在 SDK 消息消费者中应用**：

```python
async def run_agent_task(session_id: str, user_message: str, sdk: ClaudeSDK):
    try:
        async for event in sdk.run(user_message):
            # 对 tool_result 进行截断处理
            if event["type"] == "tool_result" and event.get("content"):
                content = event["content"]
                if isinstance(content, str) and len(content) > MAX_TOOL_OUTPUT_CHARS:
                    event["content"] = truncate_tool_output(content)
            buffer.add_message(session_id, event)
        buffer.mark_done(session_id)
    except Exception as e:
        buffer.add_message(session_id, {"type": "error", "message": str(e)})
        buffer.mark_done(session_id)
```

### 7.6 会话管理

| 能力 | 说明 | 应用场景 |
|------|------|----------|
| `resume` | 恢复指定 session_id 的对话 | 用户刷新页面后继续审计工作 |
| `forkSession` | 从当前会话分支出新 session | 同一份材料尝试不同审计方案 |
| `listSessions` | 列出历史会话 | 用户查看历史审计记录 |
| `getSessionMessages` | 获取历史消息 | 审计追溯/回放 |

```python
# 恢复会话
sdk = ClaudeSDK(
    resume="session-uuid-xxx",   # 从上次中断处继续
    work_dir="/workspace",
)

# 分支会话
sdk = ClaudeSDK(
    resume="session-uuid-xxx",
    fork_session=True,            # 新分支，不影响原会话
    work_dir="/workspace",
)
```

### 7.7 子代理（Sub-Agents）

SDK 支持定义子代理，主代理可自动调度：

```python
agents = {
    "code-reviewer": {
        "description": "代码和安全审查专家",
        "prompt": "你是安全审查专家，检查财务系统代码的数据泄露风险。",
        "tools": ["Read", "Grep", "Glob"],     # 受限工具集
        "model": "sonnet",
        "max_turns": 10,
    },
    "data-analyst": {
        "description": "数据分析和统计验证",
        "prompt": "你是数据分析专家，负责财务数据的统计分析和异常检测。",
        "tools": ["Read", "Bash", "mcp__excel__*"],
        "model": "haiku",                        # 简单任务用更快模型
        "max_turns": 15,
    },
}
```

**在财务审计中的应用**：
- 主代理负责整体审计流程协调
- `data-analyst` 子代理专门处理 Excel/CSV 数据分析
- `report-writer` 子代理专门负责审计报告撰写
- 各子代理使用不同模型和工具集，优化成本和性能

### 7.8 成本控制

| 参数 | 说明 | 推荐值 |
|------|------|--------|
| `max_turns` | 最大代理轮次（API 往返次数） | 30-50 |
| `max_budget_usd` | 单次查询最大预算（美元） | 1.0-5.0 |
| `task_budget` | Token 预算 | 替代 max_budget_usd |
| `thinking` | 思考模式配置 | `enabled/disabled` |
| `effort` | 执行努力级别 | `low/medium/high/max` |

```python
sdk = ClaudeSDK(
    model="claude-sonnet-4-6-20260317",
    max_turns=30,
    max_budget_usd=2.0,
    thinking="enabled",
    effort="high",
)
```

### 7.8.1 任务管理与 Todo List

SDK 提供 **TaskCreate / TaskUpdate / TaskList** 工具，功能等价甚至强于 CLI 的 TodoWrite。

**SDK vs CLI 对比**：

| 功能 | CLI 工具 | SDK 工具 | 区别 |
|------|---------|---------|------|
| 创建任务 | TodoWrite | TaskCreate | SDK 支持 `activeForm` 自定义 spinner 文字 |
| 更新任务 | TodoWrite | TaskUpdate | SDK 支持依赖关系（blocks/blockedBy）、状态机 |
| 查看任务 | TodoWrite | TaskList | 相同 |
| 任务删除 | ❌ | TaskUpdate(status=deleted) | SDK 额外支持 |
| 任务激活 | ❌ | ✅ | SDK 额外支持 |

Agent 在对话中自动使用这些工具跟踪进度，前端通过监听消息渲染 Todo UI：

```typescript
// 前端监听 task 相关消息更新 Todo 面板
if (msg.type === "tool_use" && msg.name === "TaskCreate") {
  addTodoItem(msg.input.subject, msg.input.activeForm);
}
if (msg.type === "tool_use" && msg.name === "TaskUpdate") {
  updateTodoItem(msg.input.taskId, msg.input.status, msg.input.activeForm);
}
if (msg.type === "tool_result" && msg.input?.tool_name === "TaskList") {
  refreshTodoPanel(JSON.parse(msg.content));
}
```

**Agent 实际工作流**：

```
用户: "帮我做一个完整的财务审计系统"
  │
  ▼
Agent 自动调用 TaskCreate:
  Task 1: "设计数据库 schema"           → pending
  Task 2: "实现用户认证"                → pending
  Task 3: "开发审计引擎"                → pending
  Task 4: "编写测试"                    → pending
  │
  ▼
Agent 开始执行:
  TaskUpdate(1, status=in_progress, activeForm="设计数据库 schema")
  TaskCreate("定义 User 表", activeForm="定义 User 表", blockedBy=[1])
  TaskCreate("定义 Session 表", activeForm="定义 Session 表", blockedBy=[1])
  │
  ▼
Agent 完成子任务:
  TaskUpdate("定义 User 表", status=completed)
  TaskUpdate("定义 Session 表", status=completed)
  TaskUpdate(1, status=completed)
  TaskUpdate(2, status=in_progress, activeForm="实现用户认证")
  ...
```

```python
# 前端 Todo 面板组件（React 示例）
function TodoPanel({ messages }) {
  const [tasks, setTasks] = useState([]);

  useEffect(() => {
    messages.forEach(msg => {
      if (msg.type === "tool_use" && msg.name === "TaskCreate") {
        setTasks(prev => [...prev, {
          id: msg.input?.subject,
          subject: msg.input?.subject,
          status: "pending",
          activeForm: msg.input?.activeForm,
        }]);
      }
      if (msg.type === "tool_use" && msg.name === "TaskUpdate") {
        setTasks(prev => prev.map(t =>
          t.id === msg.input?.taskId
            ? { ...t, status: msg.input?.status, activeForm: msg.input?.activeForm }
            : t
        ));
      }
    });
  }, [messages]);

  return (
    <div className="todo-panel">
      <h3>任务进度</h3>
      {tasks.map(task => (
        <div key={task.id} className={`task ${task.status}`}>
          <span className="task-status">{task.status}</span>
          <span className="task-name">{task.subject}</span>
          {task.activeForm && task.status === "in_progress" && (
            <span className="spinner">●●● {task.activeForm}</span>
          )}
        </div>
      ))}
    </div>
  );
}
```

**在审计场景中的应用**：

| 审计阶段 | 自动生成任务 |
|---------|-------------|
| 年报审计 | 提取文本 → 提取表格 → 识别类型 → 执行检查 → 生成报告 |
| Excel 审计 | 读取数据 → 必填检查 → 借贷平衡 → 异常检测 → 输出报告 |
| 关联方审查 | 股权穿透 → 交易汇总 → 合规检查 → 风险提示 |
| 图片 OCR | 文字识别 → 发票提取 → 交叉验证 → 伪造检测 → 生成报告 |

### 7.9 容器内 Session 管理

#### 7.9.1 问题

Claude Code CLI 的 session 数据存储在 `~/.claude/` 目录内：

```
~/.claude/
├── sessions/          ← 会话历史（session_id.jsonl）
├── settings.json      ← 用户设置
├── claude.json        ← 账户信息、prompt 缓存
└── shared-skills/     ← 公共 Skills（只读挂载）
└── personal-skills/   ← 个人 Skills（读写挂载）
```

**不持久化的后果**：容器重启 → `~/.claude/sessions/` 清空 → 所有对话历史丢失 → 用户无法恢复会话、无法继续上次审计工作。

#### 7.9.2 架构设计

```
┌──────────────────────────────────────────────────────────────┐
│  宿主机路径                                                   │
│  /data/users/{user_id}/claude-data/                          │
│  ├── sessions/         ← 所有 session 历史                    │
│  ├── settings.json     ← 用户个性化设置                       │
│  └── claude.json       ← prompt 缓存（跨容器重启保留）         │
└──────────────────────┬───────────────────────────────────────┘
                       │ Docker Volume (rw)
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  用户容器内                                                   │
│  /home/agent/.claude/                                        │
│  ├── shared-skills/    (ro, 来自 /data/shared-skills)         │
│  ├── personal-skills/  (rw, 来自 /data/users/{id}/skills)     │
│  ├── sessions/         (rw, 持久化到宿主机)                   │
│  └── claude.json       (rw, 持久化到宿主机)                   │
└──────────────────────────────────────────────────────────────┘
```

**关键设计**：`/home/agent/.claude` 目录**不作为一个整体 volume**，而是**拆分为多个 mount**：

```python
volumes = {
    # 公共 Skills — 只读
    "/data/shared-skills": {
        "bind": "/home/agent/.claude/shared-skills",
        "mode": "ro"
    },
    # 个人 Skills — 读写
    f"/data/users/{user_id}/skills": {
        "bind": "/home/agent/.claude/personal-skills",
        "mode": "rw"
    },
    # Claude 数据（session + settings + cache）— 持久化
    f"/data/users/{user_id}/claude-data": {
        "bind": "/home/agent/.claude",
        "mode": "rw"
    },
}
```

#### 7.9.3 Session 生命周期

```
用户登录 → 主服务器确保容器运行 → 浏览器建立 WebSocket
                                      ↓
发送消息  → agent_server.py 调用 SDK.run(message)
                                      ↓
                          Claude CLI 子进程创建/恢复 session
                                      ↓
                          session_id 存储到 ~/.claude/sessions/
                          (持久化到宿主机 /data/users/{id}/claude-data/sessions/)
                                      ↓
                          SDK 通过 AsyncGenerator 流式返回消息
                                      ↓
                          前端记录 session_id ← 存入 localStorage

用户刷新页面 → 从 localStorage 读取 session_id
              → 重新建立 WebSocket
              → 发送 { message: "继续", session_id: "xxx" }
              → SDK.resume(session_id) → 恢复完整上下文

用户关闭浏览器 → WebSocket 断开
              → 容器继续运行（不销毁）
              → session 数据持久在宿主机

用户登出/长时间不活跃 → 容器休眠 (docker pause)
              → session 数据仍在宿主机
              → 用户下次登录 → docker unpause → 恢复会话
```

#### 7.9.4 前端 Session 管理

```typescript
// 浏览器端 Session 状态管理
// 注意：此为简化示意，完整实现见 7.10.4 节
class SessionManager {
  // 发送消息并跟踪 session_id
  async sendMessage(message: string, userId: string) {
    // session_id 以服务端 API 为准（跨浏览器一致）
    const sessions = await fetch(`/api/users/${userId}/sessions`).then(r => r.json());
    const active = sessions.find((s: SessionItem) => s.status === "active");
    const sessionId = active?.session_id || localStorage.getItem(`session_${userId}`);

    const ws = new WebSocket(`ws://host/ws/${userId}`);

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'system' && data.subtype === 'init') {
        localStorage.setItem(`session_${userId}`, data.session_id);
      }

      if (data.type === 'result') {
        this.onComplete(data);
      }

      if (data.type === 'assistant') {
        this.onStream(data.content);
      }
    };

    ws.onopen = () => {
      ws.send(JSON.stringify({
        message,
        user_id: userId,
        session_id: sessionId,
      }));
    };
  }

  // 列出用户所有历史 session
  async listSessions(userId: string) {
    const resp = await fetch(`/api/users/${userId}/sessions`);
    return resp.json();
  }

  // 切换到指定 session
  switchSession(userId: string, sessionId: string) {
    localStorage.setItem(`session_${userId}`, sessionId);
    this.connect(userId);
  }
}
```

#### 7.9.5 Session 管理 API

```python
@app.get("/api/users/{user_id}/sessions")
async def list_sessions(user_id: str):
    """列出用户所有历史会话"""
    sessions_dir = Path(f"/data/users/{user_id}/claude-data/sessions")
    if not sessions_dir.exists():
        return []

    sessions = []
    for session_file in sorted(sessions_dir.glob("*.jsonl"), reverse=True):
        # 读取首行获取 session 元信息
        first_line = session_file.read_text().split("\n")[0]
        data = json.loads(first_line)
        sessions.append({
            "session_id": session_file.stem,
            "created_at": data.get("timestamp"),
            "title": data.get("message", {}).get("content", "")[:100],
            "size_mb": session_file.stat().st_size / (1024 * 1024),
        })
    return sessions

@app.delete("/api/users/{user_id}/sessions/{session_id}")
async def delete_session(user_id: str, session_id: str):
    """删除指定会话历史（释放磁盘空间）"""
    session_file = Path(f"/data/users/{user_id}/claude-data/sessions/{session_id}.jsonl")
    if session_file.exists():
        session_file.unlink()
    return {"status": "ok"}

@app.post("/api/users/{user_id}/sessions/{session_id}/cancel")
async def cancel_session(user_id: str, session_id: str):
    """取消正在运行的 Agent 任务（用户主动中断）"""
    task_key = f"task_{session_id}"
    task = active_tasks.get(task_key)
    if task and not task.done():
        task.cancel()
        buffer.cancel(session_id)
        # 同时终止 CLI 子进程（SDK 内部清理）
        sdk_instances.get(session_id)?.close()
    return {"status": "ok"}

@app.get("/api/users/{user_id}/sessions/{session_id}/status")
async def session_status(user_id: str, session_id: str):
    """查询 session 当前状态（含进度和实时成本）"""
    state = buffer.get_session_state(session_id)
    return {
        "session_id": session_id,
        "state": state["state"],            # idle | running | completed | error | waiting_user | cancelled
        "cost_usd": state["cost_usd"],
        "last_active": state["last_active"],
    }
```

#### 7.9.6 磁盘空间管理

Session 文件随对话增长可能较大。需要控制策略：

```python
# 容器启动时自动清理
def cleanup_old_sessions(user_id: str, max_age_days: int = 30, max_total_mb: int = 500):
    """清理超过 N 天或总大小超过 M MB 的旧 session"""
    sessions_dir = Path(f"/data/users/{user_id}/claude-data/sessions")
    if not sessions_dir.exists():
        return

    import time
    cutoff = time.time() - (max_age_days * 86400)

    # 按时间排序，清理最旧的
    for f in sorted(sessions_dir.glob("*.jsonl")):
        if f.stat().st_mtime < cutoff:
            f.unlink()

    # 如果总大小仍超限，继续清理最大的
    total_size = sum(f.stat().st_size for f in sessions_dir.glob("*.jsonl"))
    if total_size > max_total_mb * 1024 * 1024:
        for f in sorted(sessions_dir.glob("*.jsonl"),
                        key=lambda x: x.stat().st_size, reverse=True):
            f.unlink()
            total_size -= f.stat().st_size
            if total_size <= max_total_mb * 1024 * 1024:
                break
```

### 7.10 断连续传：页面刷新保持 Agent 工作

#### 7.10.1 问题场景

```
用户在页面输入"请审计这份 Excel 文件"
         ↓
    ClaudeSDK.run() 开始执行
         ↓
    Claude CLI 子进程 → 调用 mcp-excel:read_sheet → 正在分析...
         ↓
    用户刷新页面（F5）
         ↓
    ❌ WebSocket 断开 → agent_to_browser 协程退出
    ✅ Claude CLI 子进程继续运行（不受 WebSocket 影响）
    ❌ 但后续消息无人消费 → 用户看不到进度和结果
```

**核心矛盾**：SDK 的 `AsyncGenerator` 是**推模式**——消息产生后立即推送给消费者，消费者断开则消息丢失。

#### 7.10.2 解决方案：服务端消息缓存 + 客户端重连

```
┌─────────────────────────────────────────────────────────────┐
│  容器内 agent_server.py                                      │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  MessageBuffer (内存)                                 │   │
│  │                                                      │   │
│  │  session_abc123:                                     │   │
│  │    [{type: "assistant", content: "正在读取Excel..."}]│   │
│  │    [{type: "tool_use", tool: "mcp__excel__read"}]    │   │
│  │    [{type: "tool_result", ...}]                      │   │
│  │    [{type: "result", result: "审计完成"}]             │   │
│  │                                                      │   │
│  │  session_def456:                                     │   │
│  │    [{type: "assistant", content: "好的，开始审计"}]   │   │
│  │    ...                                               │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─────────────────┐      ┌──────────────────────────┐      │
│  │  WebSocket #1    │      │  WebSocket #2 (新连接)    │      │
│  │  (旧连接,已断开)  │      │  (页面刷新后)             │      │
│  └────────┬─────────┘      └──────────┬───────────────┘      │
│           │                           │                       │
│           │  消费者死亡                │  新消费者加入          │
│           │  消息继续缓存              │  先收历史再收实时      │
│           ▼                           ▼                       │
│  ┌────────────────────────────────────────────────────┐      │
│  │  SDK AsyncGenerator (持续产出消息)                  │      │
│  │  → 消息写入 MessageBuffer                          │      │
│  │  → 推送给所有活跃消费者                             │      │
│  └────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

#### 7.10.3 实现方案

```python
import asyncio
import json
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

# ── 磁盘+内存双层消息缓存 ────────────────────────────────
# 内存层用于实时推送，磁盘层用于断线恢复和容器重启恢复。

class MessageBuffer:
    """每个 session 的消息缓存 + 多消费者支持 + 磁盘持久化"""

    MAX_HISTORY = 500        # 内存中最多缓存消息数
    BUFFER_TIMEOUT = 3600    # 消息保留 1 小时
    BASE_DIR = Path("/workspace/.msg-buffer")  # 磁盘缓存目录

    def __init__(self):
        self.sessions: dict = defaultdict(lambda: {
            "messages": deque(maxlen=self.MAX_HISTORY),
            "consumers": set(),
            "done": False,
            "state": "idle",         # idle | running | completed | error | waiting_user
            "last_active": time.time(),
            "cost_usd": 0.0,         # 累计成本
        })
        self.BASE_DIR.mkdir(parents=True, exist_ok=True)

    def _disk_path(self, session_id: str) -> Path:
        return self.BASE_DIR / f"{session_id}.jsonl"

    def _write_disk(self, session_id: str, message: dict):
        """追加消息到磁盘（断线恢复 + 容器重启恢复）"""
        path = self._disk_path(session_id)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    def _read_disk(self, session_id: str, after_index: int = 0) -> list:
        """从磁盘读取历史消息"""
        path = self._disk_path(session_id)
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return [json.loads(line) for i, line in enumerate(lines) if i >= after_index]

    def add_message(self, session_id: str, message: dict):
        """SDK 产出消息时调用（写内存 + 写磁盘）"""
        buf = self.sessions[session_id]
        buf["messages"].append(message)
        buf["last_active"] = time.time()
        self._write_disk(session_id, message)

        # 更新 session 状态
        if message["type"] == "system" and message.get("subtype") == "progress":
            buf["state"] = "running"
        elif message["type"] == "tool_use" and message.get("name") == "AskUserQuestion":
            buf["state"] = "waiting_user"
        elif message["type"] == "result":
            buf["state"] = "completed"

        # 提取 token 用量并估算成本
        if message.get("usage"):
            cost = estimate_cost(
                message["usage"].get("input_tokens", 0),
                message["usage"].get("output_tokens", 0),
            )
            buf["cost_usd"] += cost

        # 通知所有等待的消费者
        for event in list(buf["consumers"]):
            event.set()

    def get_history(self, session_id: str, after_index: int = 0) -> list:
        """获取历史消息（优先内存，不足时读磁盘）"""
        buf = self.sessions[session_id]
        messages = list(buf["messages"])
        if len(messages) > after_index:
            return messages[after_index:]
        # 内存中没有 → 读磁盘
        disk_msgs = self._read_disk(session_id, after_index)
        for msg in disk_msgs:
            buf["messages"].append(msg)
        return list(buf["messages"])[after_index:]

    def get_session_state(self, session_id: str) -> dict:
        """获取 session 当前状态（含进度和成本）"""
        buf = self.sessions.get(session_id, {})
        return {
            "state": buf.get("state", "idle"),
            "cost_usd": round(buf.get("cost_usd", 0), 4),
            "last_active": buf.get("last_active", 0),
        }

    def mark_done(self, session_id: str):
        self.sessions[session_id]["done"] = True
        self.sessions[session_id]["state"] = "completed"

    def is_done(self, session_id: str) -> bool:
        return self.sessions[session_id].get("done", False)

    def cancel(self, session_id: str):
        """取消运行中的 Agent 任务"""
        self.sessions[session_id]["state"] = "cancelled"
        self.sessions[session_id]["done"] = True
        self.sessions[session_id]["messages"].append({
            "type": "system",
            "subtype": "session_cancelled",
            "message": "Agent 任务已被用户取消"
        })

    def cleanup_expired(self):
        now = time.time()
        expired = [
            sid for sid, buf in self.sessions.items()
            if now - buf["last_active"] > self.BUFFER_TIMEOUT
        ]
        for sid in expired:
            del self.sessions[sid]
            # 磁盘文件保留，不清理（用户可能仍需在 Web 查看历史）

buffer = MessageBuffer()

# 全局任务跟踪（session_id -> asyncio.Task）
active_tasks: dict[str, asyncio.Task] = {}

# SDK 实例跟踪（用于 cancel 时清理子进程）
sdk_instances: dict[str, "ClaudeSDK"] = {}

# AskUserQuestion 等待队列（session_id -> Future）
pending_answers: dict[str, asyncio.Future] = {}


def estimate_cost(input_tokens: int, output_tokens: int, model: str = "claude-sonnet-4-6") -> float:
    """估算单次 API 调用的美元成本（2025-04 价格）"""
    prices = {
        "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},   # per 1M tokens
        "claude-haiku-4-5-20251001": {"input": 0.8, "output": 4.0},
    }
    p = prices.get(model, prices["claude-sonnet-4-6"])
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000


# 后台清理任务
async def cleanup_loop():
    while True:
        await asyncio.sleep(300)
        buffer.cleanup_expired()

@app.on_event("startup")
async def startup():
    asyncio.create_task(cleanup_loop())
```

**SDK 消息消费者（独立于 WebSocket 连接）**：

```python
async def run_agent_task(
    session_id: str,
    user_message: str,
    sdk: ClaudeSDK,
):
    """独立的 Agent 任务，不依赖 WebSocket 连接"""
    sdk_instances[session_id] = sdk
    try:
        async for event in sdk.run(user_message):
            # 检测 AskUserQuestion → 暂停等待用户回答
            if event["type"] == "tool_use" and event.get("name") == "AskUserQuestion":
                buffer.add_message(session_id, event)
                answer_event = asyncio.get_event_loop().create_future()
                pending_answers[session_id] = answer_event
                answer = await asyncio.wait_for(answer_event, timeout=300)
                sdk.inject_tool_result(event["id"], json.dumps(answer))
                pending_answers.pop(session_id, None)
                continue

            # 对 tool_result 进行截断处理
            if event["type"] == "tool_result" and event.get("content"):
                content = event["content"]
                if isinstance(content, str) and len(content) > MAX_TOOL_OUTPUT_CHARS:
                    event["content"] = truncate_tool_output(content)

            buffer.add_message(session_id, event)

        buffer.mark_done(session_id)
        buffer.add_message(session_id, {
            "type": "system",
            "subtype": "session_state_changed",
            "state": "completed"
        })
    except asyncio.CancelledError:
        buffer.add_message(session_id, {
            "type": "system",
            "subtype": "session_cancelled",
            "message": "Agent 任务已被用户取消"
        })
        buffer.mark_done(session_id)
    except Exception as e:
        buffer.add_message(session_id, {
            "type": "error",
            "message": str(e)
        })
        buffer.mark_done(session_id)
    finally:
        sdk_instances.pop(session_id, None)
        pending_answers.pop(session_id, None)
```

**WebSocket 端点改造（支持断连续传）**：

```python
@app.websocket("/ws")
async def handle_agent(websocket: WebSocket):
    await websocket.accept()

    try:
        # 1. 接收客户端消息
        msg = await websocket.receive_text()
        data = json.loads(msg)

        user_id = data.get("user_id", "default")
        user_message = data.get("message", "")
        session_id = data.get("session_id")     # 前端传来的 session_id
        last_index = data.get("last_index", 0)  # 前端已知消息索引

        # 2. 如果没有 session_id，生成新的
        if not session_id:
            session_id = f"session_{user_id}_{int(time.time())}_{uuid4().hex[:8]}"

        # 3. 先发送历史消息（断连续传）
        history = buffer.get_history(session_id, after_index=last_index)
        for i, h in enumerate(history):
            await websocket.send_text(json.dumps({
                **h,
                "index": last_index + i,       # 客户端记录消费进度
                "replay": True,                # 标记为重放消息
            }))

        # 4. 如果没有运行中的任务，启动新的
        task_key = f"task_{session_id}"
        if task_key not in active_tasks or active_tasks[task_key].done():
            sdk = build_sdk_for_user(user_id, session_id)
            task = asyncio.create_task(
                run_agent_task(session_id, user_message, sdk)
            )
            active_tasks[task_key] = task

# 辅助函数：为用户构建 SDK 实例（在 agent_server.py 中定义）
def build_sdk_for_user(user_id: str, session_id: str) -> ClaudeSDK:
    """根据用户配置构建 ClaudeSDK 实例"""
    skills = load_skills()
    mcp_config = load_mcp_config()
    settings = load_user_settings(user_id)

    # 内置工具 + MCP 工具（精确列出，不使用通配符）
    allowed_tools = ["Read", "Edit", "Write", "Glob", "Grep", "Bash",
                     "WebFetch", "WebSearch", "Agent", "Skill"]
    allowed_tools.extend(expand_allowed_tools(mcp_config))

    return ClaudeSDK(
        model=settings["model"],
        work_dir="/workspace",
        system_prompt=build_system_prompt(skills),
        mcp_config=mcp_config,              # 正确参数名（非 mcp_servers）
        allowed_tools=allowed_tools,
        permission_mode="bypassPermissions", # 容器已隔离，用户操作在沙箱内
        max_turns=settings.get("max_turns", 30),
        max_budget_usd=settings.get("max_budget_usd", 2.0),
        effort=settings.get("effort", "high"),
        resume=session_id,
    )

        # 5. 订阅实时消息（while 循环消费）
        buf = buffer.sessions[session_id]
        last_seen = last_index + len(history)
        event = asyncio.Event()
        buf["consumers"].add(event)

        try:
            while not buffer.is_done(session_id):
                event.clear()
                # 有新消息时唤醒
                await asyncio.wait_for(event.wait(), timeout=30)
                new_messages = buffer.get_history(session_id, after_index=last_seen)
                for i, h in enumerate(new_messages):
                    await websocket.send_text(json.dumps({
                        **h,
                        "index": last_seen + i,
                        "replay": False,
                    }))
                last_seen += len(new_messages)

            # session 完成，发送最后的标记
            if buffer.is_done(session_id):
                remaining = buffer.get_history(session_id, after_index=last_seen)
                for i, h in enumerate(remaining):
                    await websocket.send_text(json.dumps({
                        **h, "index": last_seen + i, "replay": True,
                    }))
        finally:
            buf["consumers"].discard(event)

    except WebSocketDisconnect:
        pass  # WebSocket 断开，Agent 任务继续在后台运行
    except Exception as e:
        await websocket.send_text(json.dumps({
            "type": "error", "message": str(e)
        }))
```

#### 7.10.4 前端 Session 管理

```typescript
class SessionManager {
  private ws: WebSocket | null = null;
  private messageBuffer: any[] = [];
  private lastIndex: number = 0;
  private sessionId: string | null = null;

  /**
   * 会话 ID 以服务端 API 为准，localStorage 仅作为页面刷新时的临时缓存。
   * 用户换浏览器/设备时，通过 GET /api/users/{id}/sessions 恢复。
   */
  async sendMessage(message: string, userId: string) {
    // 1. 先从服务端加载最近一个活跃 session（跨浏览器一致）
    const sessions = await fetch(`/api/users/${userId}/sessions`).then(r => r.json());
    const activeSession = sessions.find((s: any) => s.status === "active");

    if (activeSession) {
      this.sessionId = activeSession.session_id;
    }

    // 2. localStorage 兜底（网络不可达时页面刷新）
    if (!this.sessionId) {
      const saved = localStorage.getItem(`session_${userId}`);
      if (saved) {
        this.sessionId = JSON.parse(saved).sessionId;
      }
    }

    this.connect(userId, message);
  }

  private connect(userId: string, message?: string) {
    this.ws = new WebSocket(`ws://host/ws/${userId}`);

    this.ws.onopen = () => {
      this.ws!.send(JSON.stringify({
        message: message || "",
        user_id: userId,
        session_id: this.sessionId,
        last_index: this.lastIndex,
      }));
    };

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'system' && data.subtype === 'init') {
        this.sessionId = data.session_id;
        // 仅缓存当前页面 session 引用，不作为权威来源
        localStorage.setItem(`session_${userId}`, JSON.stringify({
          sessionId: data.session_id,
          lastIndex: 0,
        }));
      }

      // 实时更新本地成本显示
      if (data.type === 'system' && data.subtype === 'progress') {
        updateCostDisplay(data.cost_usd);
      }

      if (!data.replay || data.index >= this.lastIndex) {
        this.messageBuffer.push(data);
        this.lastIndex = Math.max(this.lastIndex, data.index + 1);
        localStorage.setItem(`session_${userId}`, JSON.stringify({
          sessionId: this.sessionId,
          lastIndex: this.lastIndex,
        }));
        this.onMessage(data);
      }
    };

    this.ws.onclose = () => {
      console.log('WebSocket 断开，刷新页面可恢复');
    };
  }

  // 页面加载时自动恢复
  autoRecover(userId: string) {
    this.connect(userId);  // 不发新消息，依赖服务端 session 列表恢复
  }

  // 取消正在运行的 Agent 任务
  async cancelSession(userId: string) {
    if (!this.sessionId) return;
    await fetch(`/api/users/${userId}/sessions/${this.sessionId}/cancel`, {
      method: 'POST',
    });
    this.sessionId = null;
  }
}
```
}
```

#### 7.10.5 完整流程

```
时间线:

T0: 用户输入"审计这份 Excel"
    → 创建 session_abc, lastIndex=0
    → SDK.run() 开始执行
    → 消息 0-5 流式推送到页面

T1: 用户刷新页面 (F5)
    → WebSocket 断开
    → SDK.run() 继续执行 ✅
    → 消息 6-15 写入 MessageBuffer（无人消费）

T2: 页面加载完成
    → 从 localStorage 读取: session_id=session_abc, lastIndex=6
    → 重新连接 WebSocket
    → 发送 { session_id: "session_abc", last_index: 6 }

T3: 服务端收到重连
    → 先发送历史消息 6-15 (replay=true)
    → 前端渲染：补上刷新期间丢失的进度
    → 订阅实时消息，继续接收 16+

T4: 审计完成
    → MessageBuffer.mark_done("session_abc")
    → 前端显示"完成"状态
    → 用户可查看完整审计报告
```

### 7.11 Agent 工作指示器（Spinner）

Spinner 是**前端 UI 行为**，SDK 本身不直接控制。通过系统事件驱动 Spinner 的显示、文字更新和隐藏。

#### 7.11.1 设计思路

```
用户发送消息 → 显示 Spinner ("正在思考...")
    │
    ▼
收到 tool_use 消息 → 更新 Spinner 文字 ("正在执行: mcp__pdf__extract_text")
    │
    ▼
收到 result 消息 → 隐藏 Spinner
    │
    ▼
WebSocket 断开但任务仍在运行 → 显示 "后台处理中..."
```

#### 7.11.2 服务端：发送 Spinner 信号

在 `agent_server.py` 的 WebSocket 端点中，在关键节点发送系统事件：

```python
@app.websocket("/ws")
async def handle_agent(websocket: WebSocket):
    await websocket.accept()
    try:
        msg = await websocket.receive_text()
        data = json.loads(msg)

        user_id = data.get("user_id", "default")
        user_message = data.get("message", "")
        session_id = data.get("session_id")
        last_index = data.get("last_index", 0)

        if not session_id:
            session_id = f"session_{user_id}_{int(time.time())}_{uuid4().hex[:8]}"

        # 1. 发送 Agent 启动信号（前端显示 Spinner）
        await websocket.send_text(json.dumps({
            "type": "system",
            "subtype": "agent_started",
            "session_id": session_id,
            "timestamp": time.time(),
        }))

        # 2. 先发送历史消息（断连续传）
        history = buffer.get_history(session_id, after_index=last_index)
        for i, h in enumerate(history):
            await websocket.send_text(json.dumps({
                **h, "index": last_index + i, "replay": True,
            }))

        # 3. 启动/复用 Agent 任务
        task_key = f"task_{session_id}"
        if task_key not in active_tasks or active_tasks[task_key].done():
            sdk = build_sdk_for_user(user_id, session_id)
            task = asyncio.create_task(
                run_agent_task(session_id, user_message, sdk)
            )
            active_tasks[task_key] = task

        # 4. 订阅实时消息
        buf = buffer.sessions[session_id]
        last_seen = last_index + len(history)
        event = asyncio.Event()
        buf["consumers"].add(event)

        try:
            while not buffer.is_done(session_id):
                event.clear()
                await asyncio.wait_for(event.wait(), timeout=30)
                new_messages = buffer.get_history(session_id, after_index=last_seen)
                for i, h in enumerate(new_messages):
                    msg_payload = {**h, "index": last_seen + i, "replay": False}
                    await websocket.send_text(json.dumps(msg_payload))

                    # 工具调用时更新 Spinner 文字
                    if h.get("type") == "tool_use":
                        tool_name = h.get("name", h.get("tool_name", ""))
                        await websocket.send_text(json.dumps({
                            "type": "system",
                            "subtype": "spinner_update",
                            "text": f"正在执行: {tool_name}",
                        }))

                last_seen += len(new_messages)

            # 剩余消息
            remaining = buffer.get_history(session_id, after_index=last_seen)
            for i, h in enumerate(remaining):
                await websocket.send_text(json.dumps({
                    **h, "index": last_seen + i, "replay": True,
                }))

        finally:
            buf["consumers"].discard(event)

        # 5. 发送完成信号（前端隐藏 Spinner）
        await websocket.send_text(json.dumps({
            "type": "system",
            "subtype": "agent_completed",
            "timestamp": time.time(),
        }))

    except WebSocketDisconnect:
        pass  # Agent 任务继续在后台运行
    except Exception as e:
        await websocket.send_text(json.dumps({
            "type": "error", "message": str(e),
        }))
```

#### 7.11.3 前端：Spinner 状态管理

```typescript
class SpinnerManager {
  private spinner: HTMLElement;
  private ws: WebSocket | null = null;

  constructor(spinnerEl: HTMLElement) {
    this.spinner = spinnerEl;
  }

  // 连接到 Agent，管理 Spinner 生命周期
  connect(userId: string, sessionId: string, lastIndex: number) {
    this.show("正在思考...");

    this.ws = new WebSocket(`ws://host/ws/${userId}`);

    this.ws.onopen = () => {
      this.ws!.send(JSON.stringify({
        message: "",
        user_id: userId,
        session_id: sessionId,
        last_index: lastIndex,
      }));
    };

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      switch (data.type) {
        case "system":
          if (data.subtype === "agent_started") {
            this.show("正在思考...");
          }
          if (data.subtype === "spinner_update") {
            this.update(data.text);
          }
          if (data.subtype === "agent_completed") {
            this.hide();
          }
          break;
        case "result":
          this.hide();
          break;
        case "error":
          this.hide();
          showError(data.message);
          break;
        default:
          renderMessage(data);
      }
    };

    this.ws.onclose = () => {
      // 连接断开但 Agent 可能仍在后台运行
      this.show("后台处理中，刷新页面可恢复...");
    };
  }

  private show(text: string) {
    this.spinner.textContent = text;
    this.spinner.style.display = "flex";
    this.spinner.setAttribute("data-state", "active");
  }

  private update(text: string) {
    this.spinner.textContent = text;
    this.spinner.setAttribute("data-tool", text);
  }

  hide() {
    this.spinner.style.display = "none";
    this.spinner.setAttribute("data-state", "hidden");
  }
}
```

#### 7.11.4 Spinner 事件汇总

| 系统事件 | 前端显示 | 触发条件 |
|----------|---------|---------|
| `agent_started` | "正在思考..." | 用户发消息后首次收到信号 |
| `spinner_update` | "正在执行: {tool_name}" | 每次工具调用前 |
| `agent_completed` | 隐藏 | SDK.run() 正常结束 |
| WebSocket close | "后台处理中..." | 连接断开，Agent 任务可能仍在运行 |
| `error` | 隐藏 + 错误提示 | Agent 执行出错 |

### 7.12 日志系统

财务审计平台对日志的需求高于普通应用——**既要可观测性（运维），也要审计追溯（合规）**。

#### 7.12.1 分层设计

```
┌─────────────────────────────────────────────────────────────┐
│  L1: 审计日志 (Audit Log)                                    │
│  · 谁、何时、做了什么、结果如何                                │
│  · 不可篡改、长期存储、可追溯                                  │
│  · 合规需求（等保、SOC 2）                                    │
├─────────────────────────────────────────────────────────────┤
│  L2: 应用日志 (Application Log)                              │
│  · FastAPI 请求日志、错误日志、性能指标                        │
│  · 结构化 JSON，按 service/user 索引                          │
│  · 保留 30 天                                                │
├─────────────────────────────────────────────────────────────┤
│  L3: Agent 执行日志 (Agent Log)                              │
│  · 每次 tool_use 的输入输出                                   │
│  · API 调用详情、token 消耗、费用                              │
│  · 保留 90 天                                                │
├─────────────────────────────────────────────────────────────┤
│  L4: 容器日志 (Container Log)                                │
│  · docker logs 输出（stdout/stderr）                          │
│  · uvicorn 日志、异常堆栈                                     │
│  · 保留 7 天（自动轮转）                                      │
└─────────────────────────────────────────────────────────────┘
```

#### 7.12.2 文件结构

```
/data/logs/
├── audit/                    # L1 审计日志（长期保留）
│   ├── 2025-04-12_auth.jsonl    # 登录/登出/权限变更
│   ├── 2025-04-12_skills.jsonl  # Skill 创建/删除/修改
│   ├── 2025-04-12_mcp.jsonl     # MCP 注册/启用/禁用
│   └── 2025-04-12_files.jsonl   # 文件上传/下载/删除
├── application/              # L2 应用日志（30 天）
│   ├── 2025-04-12_api.jsonl     # API 请求/响应
│   └── 2025-04-12_errors.jsonl  # 错误堆栈
├── agent/                    # L3 Agent 执行日志（90 天）
│   └── {user_id}/
│       └── {session_id}.jsonl     # 完整的 tool_use → result 链
└── container/                # L4 容器日志（7 天）
    └── {user_id}.log              # docker logs 输出
```

#### 7.12.3 L1: 审计日志

审计日志记录**谁在什么时间做了什么**，不可篡改，满足合规要求：

```jsonl
{"timestamp": "2025-04-12T10:30:00Z", "actor": "alice", "action": "login", "ip": "192.168.1.100", "user_agent": "Mozilla/5.0", "result": "ok"}
{"timestamp": "2025-04-12T10:31:00Z", "actor": "alice", "action": "skill.create", "resource": "custom-review", "ip": "192.168.1.100", "result": "ok"}
{"timestamp": "2025-04-12T10:32:00Z", "actor": "alice", "action": "session.start", "resource": "session_abc", "ip": "192.168.1.100", "result": "ok"}
{"timestamp": "2025-04-12T10:33:00Z", "actor": "alice", "action": "tool.use", "resource": "mcp__pdf__extract_text", "session": "session_abc", "file": "annual_report_2025.pdf", "result": "ok"}
{"timestamp": "2025-04-12T10:35:00Z", "actor": "alice", "action": "file.upload", "resource": "annual_report_2025.pdf", "size": 2516582, "ip": "192.168.1.100", "result": "ok"}
{"timestamp": "2025-04-12T10:40:00Z", "actor": "admin", "action": "skill.update", "resource": "audit-pdf", "ip": "192.168.1.50", "result": "ok", "detail": "版本从 v2 升级到 v3"}
```

```python
import json
import time
from pathlib import Path
from datetime import datetime

class AuditLogger:
    """不可篡改的审计日志"""

    BASE_DIR = Path("/data/logs/audit")

    def __init__(self):
        self.BASE_DIR.mkdir(parents=True, exist_ok=True)

    def log(self, actor: str, action: str, resource: str,
            result: str = "ok", ip: str = "", **extra):
        """写入一条审计日志"""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        # 按日期拆分文件
        log_file = self.BASE_DIR / f"{today}_{action.split('.')[0]}.jsonl"
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "actor": actor,
            "action": action,
            "resource": resource,
            "ip": ip,
            "result": result,
            **extra,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def query(self, actor: str = None, action: str = None,
              start: str = None, end: str = None) -> list[dict]:
        """查询审计日志（支持过滤）"""
        results = []
        for log_file in sorted(self.BASE_DIR.glob("*.jsonl")):
            for line in log_file.read_text().splitlines():
                entry = json.loads(line)
                if actor and entry.get("actor") != actor:
                    continue
                if action and entry.get("action") != action:
                    continue
                if start and entry.get("timestamp", "") < start:
                    continue
                if end and entry.get("timestamp", "") > end:
                    continue
                results.append(entry)
        return results

audit_log = AuditLogger()

# API 中使用示例
@app.post("/api/users/{user_id}/skills")
async def create_user_skill(user_id: str, skill: SkillCreate):
    skill_dir = Path(f"/data/users/{user_id}/skills/{skill.name}")
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill.content)

    # 写入审计日志
    audit_log.log(
        actor=user_id,
        action="skill.create",
        resource=skill.name,
        ip="192.168.1.100",  # 实际从请求头获取
    )
    return {"status": "ok", "source": "personal"}

@app.get("/api/admin/audit-logs")
async def get_audit_logs(actor: str = None, action: str = None,
                         start: str = None, end: str = None):
    """管理员查询审计日志"""
    return audit_log.query(actor=actor, action=action, start=start, end=end)
```

#### 7.12.4 L3: Agent 执行日志

Agent 执行日志记录每次 tool_use 的完整输入输出链，用于问题排查和费用追溯：

```python
class AgentLogBuffer:
    """Agent 执行日志缓冲，写入文件 + 可选发送到外部系统"""

    BASE_DIR = Path("/data/logs/agent")

    def __init__(self):
        self.BASE_DIR.mkdir(parents=True, exist_ok=True)

    def log_tool_use(self, user_id: str, session_id: str,
                     tool_name: str, tool_input: dict,
                     tool_output: str, duration_ms: int,
                     token_usage: dict = None):
        """记录一次工具调用"""
        user_dir = self.BASE_DIR / user_id
        user_dir.mkdir(exist_ok=True)
        log_file = user_dir / f"{session_id}.jsonl"

        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "session_id": session_id,
            "tool": tool_name,
            "input": tool_input,
            "output": tool_output[:5000],  # 截断超长输出
            "duration_ms": duration_ms,
            "tokens": token_usage,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

agent_log = AgentLogBuffer()

# 在 run_agent_task 中记录
async def run_agent_task(session_id: str, user_message: str,
                         user_id: str, sdk: ClaudeSDK):
    start_time = time.time()
    async for event in sdk.run(user_message):
        buffer.add_message(session_id, event)

        # 记录工具调用
        if event.get("type") == "tool_use":
            # tool_result 会在后续消息中返回
            pending_tools[event.get("tool_use_id")] = {
                "tool_name": event.get("name"),
                "tool_input": event.get("input", {}),
                "start_time": time.time(),
            }

        if event.get("type") == "tool_result":
            tool_id = event.get("tool_use_id")
            if tool_id in pending_tools:
                info = pending_tools.pop(tool_id)
                duration_ms = int((time.time() - info["start_time"]) * 1000)
                agent_log.log_tool_use(
                    user_id=user_id,
                    session_id=session_id,
                    tool_name=info["tool_name"],
                    tool_input=info["tool_input"],
                    tool_output=str(event.get("output", ""))[:5000],
                    duration_ms=duration_ms,
                )

    buffer.mark_done(session_id)
```

#### 7.12.5 日志与审计追溯的关系

```
用户 Alice 发现审计结果异常
        │
        ▼
管理员查询审计日志:
  audit_log.query(actor="alice", action="tool.use",
                  start="2025-04-12T10:00", end="2025-04-12T12:00")
        │
        ▼
发现:
  10:32 tool.use mcp__pdf__extract_text  ✓
  10:33 file.upload annual_report_2025.pdf  ✓
  10:35 tool.use mcp__excel__read_sheet  ✗ 文件不存在
        │
        ▼
查看 Agent 执行日志:
  /data/logs/agent/alice/session_abc.jsonl
        │
        ▼
发现: mcp__excel__read_sheet 的输入参数中
  文件名拼写错误 ("ledgr.xlsx" vs "ledger.xlsx")
        │
        ▼
问题定位完成，可追溯、可复现
```

**关键原则**：

| 日志层级 | 作用 | 保留期 | 不可篡改 |
|---------|------|--------|---------|
| L1 审计日志 | 合规追溯 | 3 年+ | ✅（追加写入，无删除/修改） |
| L2 应用日志 | 运维排障 | 30 天 | ❌ |
| L3 Agent 日志 | Agent 行为追溯 | 90 天 | ❌（但可 hash 校验） |
| L4 容器日志 | 运行时排障 | 7 天 | ❌ |

---

## 8. 核心代码实现

### 8.1 管理服务（主服务器）

```python
"""
main_server.py — 用户管理 + 容器编排 + Skills CRUD
"""
import os
import shutil
from pathlib import Path
from typing import Optional

import docker
import json
from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Multi-User Claude Agent Platform")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"])  # 生产环境应限制域名

client = docker.from_env()

# ──────────────────────────────────────
# 数据模型
# ──────────────────────────────────────

class SkillCreate(BaseModel):
    name: str
    content: str  # 完整的 SKILL.md 内容

class SkillInfo(BaseModel):
    name: str
    source: str       # "shared" | "personal"
    description: str  # 从 YAML frontmatter 提取

class Message(BaseModel):
    message: str

# ──────────────────────────────────────
# 容器管理
# ──────────────────────────────────────

def ensure_container(user_id: str) -> docker.models.containers.Container:
    """为用户获取或创建独立容器"""
    container_name = f"claude-agent-{user_id}"
    try:
        container = client.containers.get(container_name)
        if container.status == "exited":
            container.start()
        return container
    except docker.errors.NotFound:
        cleanup_old_sessions(user_id)  # 创建容器前清理旧 session
        container = client.containers.run(
            image="claude-agent-sdk:latest",
            name=container_name,
            detach=True,
            volumes=get_user_volumes(user_id),
            environment=get_user_env(user_id),
            mem_limit="4g",
            cpu_quota=200000,
            network_mode="agent-net",
        )
        return container

def get_user_volumes(user_id: str) -> dict:
    return {
        "/data/shared-skills": {
            "bind": "/home/agent/.claude/shared-skills",
            "mode": "ro",
        },
        f"/data/users/{user_id}/skills": {
            "bind": "/home/agent/.claude/personal-skills",
            "mode": "rw",
        },
        f"/data/users/{user_id}/workspace": {
            "bind": "/workspace",
            "mode": "rw",
        },
        f"/data/users/{user_id}/claude-data": {
            "bind": "/home/agent/.claude",
            "mode": "rw",  # Session + settings + prompt cache 持久化
        },
    }

def get_user_role(user_id: str) -> str:
    """获取用户角色（实际应从数据库/认证服务获取）"""
    roles_file = Path("/data/users.json")
    if roles_file.exists():
        users = json.loads(roles_file.read_text())
        return users.get(user_id, {}).get("role", "user")
    return "user"

def get_user_env(user_id: str) -> dict:
    registry = json.loads(Path("/data/mcp-registry.json").read_text())
    user_role = get_user_role(user_id)  # "user" | "admin"
    mcp_config = build_user_mcp_config(user_id, registry, user_role)
    return {
        "ANTHROPIC_API_KEY": os.getenv(f"API_KEY_{user_id.upper()}"),
        "CLAUDE_SKILLS_DIRS": (
            "/home/agent/.claude/shared-skills,"
            "/home/agent/.claude/personal-skills"
        ),
        "MCP_CONFIG_JSON": json.dumps(mcp_config),
        "USER_ID": user_id,
    }

# ──────────────────────────────────────
# WebSocket 通信
# ──────────────────────────────────────

@app.websocket("/ws/{user_id}")
async def agent_websocket(websocket: WebSocket, user_id: str):
    """WebSocket 桥接：浏览器 ↔ 主服务器 ↔ 容器内 agent_server.py
    注：这是简化版桥接。生产环境应使用 Section 7.10 的 MessageBuffer + 断连续传架构。"""
    await websocket.accept()

    container = ensure_container(user_id)
    container_port = 8000

    # 获取容器的 WebSocket 端点
    container_ws_url = (
        f"ws://{container.attrs['NetworkSettings']['IPAddress']}:"
        f"{container_port}/ws"
    )

    # 桥接：浏览器 ↔ 主服务器 ↔ 容器内 Agent
    import websockets
    async with websockets.connect(container_ws_url) as agent_ws:
        import asyncio

        async def browser_to_agent():
            async for msg in websocket.iter_text():
                await agent_ws.send(msg)

        async def agent_to_browser():
            async for msg in agent_ws:
                await websocket.send_text(msg)

        await asyncio.gather(browser_to_agent(), agent_to_browser())

# ──────────────────────────────────────
# Skills 管理 API
# ──────────────────────────────────────

def list_skills_at_path(base: Path, source: str) -> list[SkillInfo]:
    """扫描目录，返回 Skills 列表"""
    if not base.exists():
        return []
    skills = []
    for d in base.iterdir():
        skill_file = d / "SKILL.md"
        if skill_file.exists():
            content = skill_file.read_text()
            desc = extract_description(content)
            skills.append(SkillInfo(name=d.name, source=source, description=desc))
    return skills

def extract_description(content: str) -> str:
    """从 YAML frontmatter 中提取 description"""
    import re
    match = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
    return match.group(1) if match else ""

@app.get("/api/shared-skills", response_model=list[SkillInfo])
async def list_shared_skills():
    """公共 Skills（所有用户可见）"""
    return list_skills_at_path(Path("/data/shared-skills"), "shared")

@app.get("/api/users/{user_id}/skills")
async def list_user_skills(user_id: str):
    """用户的完整 Skills 列表（公共 + 个人）"""
    return {
        "shared": list_skills_at_path(Path("/data/shared-skills"), "shared"),
        "personal": list_skills_at_path(
            Path(f"/data/users/{user_id}/skills"), "personal"
        ),
    }

@app.post("/api/users/{user_id}/skills")
async def create_user_skill(user_id: str, skill: SkillCreate):
    """用户创建/更新个人 Skill"""
    skill_dir = Path(f"/data/users/{user_id}/skills/{skill.name}")
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill.content)
    return {"status": "ok", "source": "personal"}

@app.delete("/api/users/{user_id}/skills/{skill_name}")
async def delete_user_skill(user_id: str, skill_name: str):
    """用户删除个人 Skill"""
    skill_dir = Path(f"/data/users/{user_id}/skills/{skill_name}")
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    return {"status": "ok"}

# ──────────────────────────────────────
# 文件上传 / 下载
# ──────────────────────────────────────

from fastapi import UploadFile, File
from fastapi.responses import FileResponse

@app.post("/api/users/{user_id}/upload")
async def upload_file(user_id: str, file: UploadFile = File(...)):
    """用户上传文件到个人工作区"""
    workspace = Path(f"/data/users/{user_id}/workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    dest = workspace / file.filename
    with open(dest, "wb") as f:
        f.write(await file.read())
    return {"status": "ok", "filename": file.filename, "path": str(dest)}

@app.get("/api/users/{user_id}/files")
async def list_files(user_id: str):
    """列出用户工作区所有文件"""
    workspace = Path(f"/data/users/{user_id}/workspace")
    if not workspace.exists():
        return []
    files = []
    for f in workspace.rglob("*"):
        if f.is_file():
            files.append({
                "name": f.name,
                "path": str(f.relative_to(workspace)),
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
            })
    return files

@app.get("/api/users/{user_id}/download/{file_path:path}")
async def download_file(user_id: str, file_path: str):
    """下载工作区中的文件（路径安全检查）"""
    workspace = Path(f"/data/users/{user_id}/workspace")
    target = (workspace / file_path).resolve()
    # 防止路径遍历攻击
    if not str(target).startswith(str(workspace.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(target), filename=target.name)

# ──────────────────────────────────────
# 管理员 API（公共 Skills）
# ──────────────────────────────────────

@app.post("/api/admin/shared-skills")
async def create_shared_skill(skill: SkillCreate):
    """管理员创建/更新公共 Skill"""
    skill_dir = Path(f"/data/shared-skills/{skill.name}")
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill.content)
    return {"status": "ok", "source": "shared"}

@app.delete("/api/admin/shared-skills/{skill_name}")
async def delete_shared_skill(skill_name: str):
    """管理员删除公共 Skill"""
    skill_dir = Path(f"/data/shared-skills/{skill_name}")
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    return {"status": "ok"}
```

### 8.2 容器内 Agent 服务

```python
"""
agent_server.py — 运行在用户容器内，处理 Agent 请求
集成：Skills 加载 + MCP 注入 + Hooks + 成本控制 + 会话恢复
"""
import os
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect
from claude_code_sdk import ClaudeSDK

app = FastAPI()

def load_skills() -> dict:
    """加载所有 Skills（公共 + 个人，个人优先覆盖）"""
    skills_dirs = os.getenv(
        "CLAUDE_SKILLS_DIRS",
        "/home/agent/.claude/shared-skills,/home/agent/.claude/personal-skills",
    ).split(",")

    all_skills = {}
    for skills_dir in skills_dirs:
        path = Path(skills_dir)
        if not path.exists():
            continue
        for skill_dir in path.iterdir():
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                name = skill_dir.name
                # 后加载优先（个人覆盖公共）
                all_skills[name] = {
                    "path": str(skill_dir),
                    "source": "shared" if "shared" in str(skill_dir) else "personal",
                    "content": skill_file.read_text(),
                }
    return all_skills

def build_system_prompt(skills: dict) -> str:
    """将 Skills 描述注入 System Prompt"""
    skill_descriptions = []
    for name, info in skills.items():
        import re
        match = re.search(r"^description:\s*(.+)$", info["content"], re.MULTILINE)
        desc = match.group(1) if match else ""
        source = info["source"]
        skill_descriptions.append(f"- [{source}] {name}: {desc}")

    skills_text = "\n".join(skill_descriptions)
    return f"""你是一个专业的财务审计 AI 助手。你熟悉《企业会计准则》（CAS）、
《中国注册会计师审计准则》和 IFRS。你可以访问以下 Skills：

{skills_text}

当用户请求匹配某个 Skill 的 description 时，加载该 Skill 的完整指令并执行。
"""

def load_mcp_config() -> dict:
    """从环境变量加载 MCP 配置"""
    mcp_config_json = os.getenv("MCP_CONFIG_JSON", "{}")
    return json.loads(mcp_config_json)

def load_user_settings(user_id: str) -> dict:
    """加载用户个性化设置（模型偏好、预算等）"""
    settings_file = Path(f"/data/users/{user_id}/settings.json")
    if settings_file.exists():
        return json.loads(settings_file.read_text())
    return {
        "model": "claude-sonnet-4-6-20260317",
        "max_turns": 30,
        "max_budget_usd": 2.0,
        "effort": "high",
    }

@app.websocket("/ws")
async def handle_agent(websocket: WebSocket):
    await websocket.accept()

    try:
        msg = await websocket.receive_text()
        data = json.loads(msg)

        user_id = data.get("user_id", "default")
        user_message = data.get("message", "")
        session_id = data.get("session_id")          # 恢复会话
        fork_session = data.get("fork_session", False)

        skills = load_skills()
        mcp_config = load_mcp_config()
        settings = load_user_settings(user_id)

        # 构建 allowed_tools（内置 + MCP 通配符）
        allowed_tools = [
            "Read", "Edit", "Write", "Glob", "Grep", "Bash",
            "WebFetch", "WebSearch", "Agent", "Skill",
            "TaskCreate", "TaskUpdate", "TaskList",
        ]
        for server_name in mcp_config.get("mcpServers", {}):
            allowed_tools.append(f"mcp__{server_name}__*")

        # PreToolUse Hook：拦截危险命令
        async def pre_tool_use_hook(input_data, tool_use_id, context):
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})
            if tool_name == "Bash":
                cmd = tool_input.get("command", "")
                if any(x in cmd for x in ["rm -rf /", "curl", "wget", "nc "]):
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "危险命令已拦截"
                        }
                    }
            return {}

        sdk = ClaudeSDK(
            model=settings["model"],
            work_dir="/workspace",
            system_prompt=build_system_prompt(skills),
            mcp_servers=mcp_config.get("mcpServers", {}),
            allowed_tools=allowed_tools,
            permission_mode="bypassPermissions",  # 容器已隔离，无需二次确认
            hooks={
                "PreToolUse": [{"hooks": [pre_tool_use_hook]}],
            },
            max_turns=settings["max_turns"],
            max_budget_usd=settings["max_budget_usd"],
            effort=settings["effort"],
            resume=session_id,
            fork_session=fork_session,
        )

        async for event in sdk.run(user_message):
            await websocket.send_text(json.dumps(event, default=str))

    except Exception as e:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": str(e),
        }))
```

### 8.3 Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# 安装依赖（文件处理由 MCP Server 提供，容器内只需 SDK 和 Web 框架）
RUN pip install --no-cache-dir \
    fastapi uvicorn websockets \
    claude-code-sdk \
    python-multipart \
    aiohttp

# 复制 Agent 服务代码
COPY agent_server.py .

# 创建 Skills 和工作目录
RUN mkdir -p /home/agent/.claude/shared-skills \
             /home/agent/.claude/personal-skills \
             /workspace

# 暴露 WebSocket 端口
EXPOSE 8000

# 启动
CMD ["uvicorn", "agent_server:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 9. 部署方案

### 9.1 Docker Compose（开发/小团队）

```yaml
version: "3.9"

services:
  # 主管理服务
  main-server:
    build: .
    image: agent-platform:latest
    ports:
      - "8080:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./data:/data
    environment:
      - API_KEY_ALICE=sk-ant-xxx
      - API_KEY_BOB=sk-ant-yyy
    networks:
      - agent-net

  # 公共 Skills 数据卷
  shared-skills:
    image: busybox
    volumes:
      - ./data/shared-skills:/data/shared-skills

  # MCP Server：PDF 处理
  mcp-pdf:
    build: ./mcp-servers/mcp-pdf
    networks:
      - agent-net
    read_only: true

  # MCP Server：Excel 处理
  mcp-excel:
    build: ./mcp-servers/mcp-excel
    networks:
      - agent-net
    read_only: true

  # MCP Server：OCR 图片识别
  mcp-ocr:
    build: ./mcp-servers/mcp-ocr
    networks:
      - agent-net
    volumes:
      - ./tesseract-data:/usr/share/tesseract-ocr

networks:
  agent-net:
    driver: bridge
```

### 9.2 生产环境部署

```
┌─────────────────────────────────────────────────────────┐
│  Kubernetes / Docker Swarm                              │
│                                                         │
│  ┌───────────────┐    ┌───────────────────────────────┐ │
│  │  Ingress/Nginx│    │    Main Server (2 replicas)   │ │
│  │  + TLS + Auth │───▶│    (无状态, 共享 PostgreSQL)  │ │
│  └───────────────┘    └───────────────┬───────────────┘ │
│                                      │                  │
│                   ┌──────────────────┼───────────────┐  │
│                   ▼                  ▼               ▼  │
│            ┌──────────┐      ┌──────────┐     ┌──────────┐
│            │ User A   │      │ User B   │     │ User C   │
│            │ Container│      │ Container│     │ Container│
│            │ (1核/4G) │      │ (1核/4G) │     │ (2核/8G) │
│            └──────────┘      └──────────┘     └──────────┘
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │  MCP Services (共享)                               │  │
│  │  mcp-pdf  mcp-excel  mcp-ocr  mcp-docx            │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  持久化存储: NFS / Ceph (共享 Skills + 用户数据 + MCP)   │
└─────────────────────────────────────────────────────────┘
```

### 9.3 第三方沙箱方案（替代自建 Docker 编排）

| 方案 | 优势 | 集成方式 |
|------|------|---------|
| **[Daytona](https://www.daytona.io/docs/en/guides/claude/claude-agent-sdk-interactive-terminal-sandbox/)** | 专为 AI Agent 设计 | 改 `baseURL` 即可 |
| **[Blaxel](https://blaxel.ai/blog/run-claude-code-safely-with-blaxel-sandboxes/)** | 远程安全沙箱 | SDK 内置集成 |
| **[PPIO](https://zhuanlan.zhihu.com/p/1992908640672301785)** | 三步接入 | 改 `baseURL` 即可 |
| **[Rivet Sandbox SDK](https://rivet.dev/changelog/2026-01-28-sandbox-agent-sdk/)** | 统一 API 抽象层 | 标准化接入 |

示例（Daytona）：

```python
from daytona_sdk import Daytona

daytona = Daytona(api_key="...")

# 为每个用户创建独立沙箱
sandbox = daytona.create(
    image="claude-agent-sdk:latest",
    resources={"cpu": 2, "memory": "4g"},
    env={
        "ANTHROPIC_API_KEY": user_api_key,
        "CLAUDE_SKILLS_DIRS": "/home/agent/.claude/shared-skills,/home/agent/.claude/personal-skills",
    },
    volumes=[
        {"source": "/data/shared-skills", "target": "/home/agent/.claude/shared-skills", "readOnly": True},
        {"source": f"/data/users/{user_id}/skills", "target": "/home/agent/.claude/personal-skills"},
        {"source": f"/data/users/{user_id}/workspace", "target": "/workspace"},
    ],
)

# 通过沙箱端点调用 Agent
sdk = ClaudeSDK(base_url=sandbox.get_endpoint(), api_key=sandbox.api_key)
```

---

## 10. 审计场景 Skills 示例

### 10.1 公共 Skill：PDF 审计 (`audit-pdf/SKILL.md`)

```markdown
---
name: audit-pdf
description: Use when the user asks to audit, review, or analyze PDF documents for financial statements, audit reports, or compliance documents
---

# PDF 文档审计

## 适用范围

- 财务报表 PDF（资产负债表、利润表、现金流量表、所有者权益变动表）
- 审计报告 PDF（标准无保留意见、保留意见、否定意见、无法表示意见）
- 合规性文件 PDF（合同、协议、银行对账单）

## 执行流程

1. 调用 MCP Tool `mcp-pdf:extract_text` 提取 PDF 内容
2. 调用 MCP TOOL `mcp-pdf:extract_tables` 提取表格数据
3. 识别文档类型（财务报表 / 审计报告 / 合同 / 银行对账单）
4. 执行对应类型的审计检查
5. 输出结构化审计发现

## 审计检查项

### 财务报表
- **会计准则依据**：按照《企业会计准则》（CAS）或 IFRS 检查报表格式和披露
- 资产负债表平衡：资产 = 负债 + 所有者权益
- 利润表勾稽关系：净利润与资产负债表中未分配利润变动一致
- 现金流量表：经营活动现金流与净利润的合理性
- 数据合理性：毛利率、净利率、资产负债率在行业合理区间内
- 附注披露完整性：会计政策、估计变更、关联方交易

### 审计报告
- **审计准则依据**：按照《中国注册会计师审计准则》检查审计意见类型
- 审计意见类型与报告内容一致性
- 关键审计事项是否得到充分披露
- 期后事项是否已考虑
- 持续经营假设是否恰当

### 合同与银行对账单
- 合同金额与账务记录一致性
- 银行对账单余额与账面余额调节
- 大额异常交易识别

## 输出

生成 Markdown 格式审计报告，保存到 `/workspace/reports/`
报告需注明所引用的会计准则/审计准则条款
```

### 10.2 公共 Skill：Excel 审计 (`audit-excel/SKILL.md`)

```markdown
---
name: audit-excel
description: Use when the user asks to audit Excel files, spreadsheets, financial data tables, or accounting ledgers
---

# Excel 数据审计

## 执行流程

1. 调用 MCP TOOL `mcp-excel:read_sheet` 读取 Excel
2. 调用 MCP TOOL `mcp-excel:audit_ledger` 执行审计规则验证
3. 调用 MCP TOOL `mcp-excel:export_report` 生成异常报告

## 审计规则

- **会计准则合规**：会计科目符合《企业会计准则》科目体系
- 必填字段检查（凭证号、日期、科目、金额）
- 金额字段非负检查（除特殊科目如红字冲销）
- 日期范围合理性检查（不超出会计期间）
- 重复记录检查（凭证号唯一性）
- 借贷平衡检查（借方合计 = 贷方合计）
- 会计分录完整性（有借必有贷，借贷必相等）
- 大额异常交易识别（Benford 定律、离群值检测）

## 输出

生成 Excel 异常报告，标注违反的审计规则，保存到 `/workspace/reports/`
```

### 10.3 公共 Skill：图片/OCR 审计 (`audit-image/SKILL.md`)

```markdown
---
name: audit-image
description: Use when the user uploads images or scanned documents containing financial records, receipts, invoices, or contracts requiring OCR extraction and audit analysis
---

# 图片/扫描件审计

## 适用范围

- 扫描的纸质发票、收据
- 银行对账单扫描件
- 手写或打印的凭证照片
- 合同照片

## 执行流程

1. 调用 MCP TOOL `mcp-ocr:scan_image` 进行 OCR 文字识别（支持中英文）
2. 调用 MCP TOOL `mcp-ocr:extract_invoice` 提取关键财务数据
3. 与账务记录进行交叉验证
4. 调用 MCP TOOL `mcp-ocr:detect_tampering` 识别伪造、涂改痕迹

## 审计检查项

- 发票真伪验证（发票代码、号码、校验码）
- 金额一致性：发票金额 vs 入账金额
- 日期逻辑：发票日期不晚于记账日期
- 单位名称与合同对手方一致
- 连号发票检测（可能为虚开）

## 输出

生成 OCR 提取结果和审计发现，保存到 `/workspace/reports/`
附带提取的原始 OCR 文本便于追溯
```

### 10.4 文件存储架构

```
文件流转全链路：

┌──────────┐      POST /api/users/{id}/upload      ┌──────────────────┐
│ 浏览器    │ ──────────────────────────────────────▶  主服务器          │
│ 上传文件  │                                        │ 写入 workspace    │
└──────────┘                                        └────────┬─────────┘
                                                              │
                                                              ▼
                                                  /data/users/{id}/workspace/
                                                  ├── uploads/          ← 用户上传
                                                  │   ├── invoice.pdf
                                                  │   ├── ledger.xlsx
                                                  │   └── receipt.jpg
                                                  ├── reports/          ← Agent 产出
                                                  │   ├── audit_report.md
                                                  │   ├── exceptions.xlsx
                                                  │   └── ocr_results.txt
                                                  └── temp/             ← 中间产物

┌──────────┐      GET /api/users/{id}/files      ┌──────────────────┐
│ 浏览器    │ ◀──────────────────────────────────  主服务器          │
│ 文件列表  │   返回 workspace 文件树             │ 扫描 workspace    │
└─────┬────┘                                      └──────────────────┘
      │
      │  GET /api/users/{id}/download/{path}
      ▼
┌──────────┐
│ 浏览器    │  下载审计报表、分析结果
│ 下载文件  │
└──────────┘
```

安全要点：
- 下载接口做**路径遍历检查**（`resolve().startswith(workspace)`）
- 上传文件**大小限制**（默认 50MB，可在 Nginx 层配置）
- 文件类型白名单：`.pdf .xlsx .xls .docx .doc .jpg .jpeg .png .csv .txt`
- 用户上传与 Agent 产出**分目录存储**，便于追溯

### 10.5 用户个人 Skill 示例 (`custom-review/SKILL.md`)

```markdown
---
name: custom-review
description: Use when the user requests a custom internal review following the company's 2026 audit checklist
---

# 公司内部审查流程（2026版）

> 此 Skill 仅对个人用户可用，包含公司内部保密的审计清单。

## 审查步骤

1. ...（内部流程）
```

---

## 11. 已知限制与注意事项

### 11.1 Claude Agent SDK 的多用户限制

- SDK **不是为多租户设计的**，底层通过子进程启动 Claude Code CLI
- [Issue #51](https://github.com/anthropics/claude-agent-sdk-python/issues/51) — 单进程多用户会出现 session 混淆
- [Issue #54](https://github.com/anthropics/claude-agent-sdk-python/issues/54) — `session_id` 无法完全隔离上下文
- [Issue #333](https://github.com/anthropics/claude-agent-sdk-python/issues/333) — 服务端多实例性能问题

**结论**：必须通过容器/沙箱隔离来避免这些问题，单进程多用户方案不可行。

### 11.2 资源消耗

| 指标 | 单用户 | 10 用户 | 50 用户 |
|------|--------|---------|---------|
| CPU | ~1 核 | ~10 核 | ~50 核 |
| 内存 | ~2-4 GB | ~20-40 GB | ~100-200 GB |
| 磁盘 | ~500 MB + 工作区 | ~5 GB + 工作区 | ~25 GB + 工作区 |

建议对空闲容器实施休眠策略（`docker pause`）以节省资源。

### 11.3 安全考虑

- 容器以**非 root 用户**运行
- 公共 Skills 目录**只读挂载**
- 每个用户独立的 API Key
- 容器间**网络隔离**（Docker network）
- 工作目录**权限隔离**（用户只能访问自己的目录）

---

## 12. 总结

| 需求 | 方案 |
|------|------|
| Web 访问 | FastAPI + WebSocket 桥接容器内 Agent，自定义 UI（Session 列表 + Chat + 文件管理） |
| 多人并发隔离 | 每用户一个 Docker 容器 / 沙箱（SDK 非多租户设计） |
| 公共 Skills | 只读挂载到 `/home/agent/.claude/shared-skills`，管理员（Web 角色）通过 API 管理 |
| 个人 Skills | 读写挂载到 `/home/agent/.claude/personal-skills`，用户自主管理，同名覆盖公共 |
| SKILL.md 兼容 | 100% 兼容 Claude Code 格式 |
| 财务审计业务 | Skills 内置审计流程（PDF/Excel/图片），引用《企业会计准则》和《中国注册会计师审计准则》 |
| 文件管理 | 用户上传 → workspace → Agent 处理（MCP Server） → 生成结果 → 下载 |
| 图片/OCR | MCP Server (`mcp-ocr`) 支持中英文 OCR，处理扫描件和发票 |
| Tool/MCP 管理 | 管理员通过注册中心统一管理，`allowedTools` + 通配符控制粒度，容器启动时注入裁剪配置 |
| 安全控制 | PreToolUse Hook 拦截危险命令 + 容器隔离 + 权限隔离 + 路径遍历检查 |
| 成本控制 | `max_turns` + `max_budget_usd` 限制单次查询，子代理可用 haiku 优化简单任务成本 |
| 会话管理 | `resume` 恢复中断会话，`forkSession` 分支探索不同审计方案 |
| 页面刷新恢复 | MessageBuffer 服务端缓存 + 客户端 localStorage + WebSocket 重连 + 消息去重 |
| 记忆机制 | L1 平台记忆（memory.json，跨会话共享企业信息）+ L2 Agent 自主记忆（Markdown 文件） |
| Skill 反馈进化 | 用户反馈 → 积累反馈数据 → LLM 生成改进版 → 管理员审核 → A/B 测试 → 全量替换 |
| 数据持久化 | Volume 挂载 `/home/agent/.claude`（sessions/settings/cache），容器销毁不丢失 |
| Spinner 指示 | 系统事件驱动（agent_started → spinner_update → agent_completed），WebSocket 断开显示"后台处理中..." |
| 任务管理 | TaskCreate/TaskUpdate/TaskList 替代 CLI 的 TodoWrite，支持任务依赖、状态机、前端实时渲染 |
