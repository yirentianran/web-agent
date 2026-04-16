# Web Agent — 需求文档

> **版本**: v1.0
> **日期**: 2026-04-14
> **范围**: 文件交互 + 消息渲染全面优化

---

## 需求概览

| 需求 | 主题 | 复杂度 |
|------|------|--------|
| 需求 1 | 文件上传行为优化 | 中 |
| 需求 2 | Agent 生成文件展示 | 中 |
| 需求 3 | ChatArea 消息渲染统一优化 | 中 |

---

## 需求 1: 文件上传行为优化

### 目标
统一文件上传的交互逻辑，确保文件上传后能被用户引用、被 Agent 识别，并在页面刷新后持久显示。

### 功能场景

#### 场景 1: 仅上传文件（无文字）
- 用户选择文件后点击发送，**自动发送一条纯文件消息气泡**
- 一次上传多个文件 → **每个文件一个独立消息气泡**
- 文件在气泡中以 FileCard 卡片形式展示（含文件名、大小）

#### 场景 2: 文件 + 文字同时发送
- 先上传文件，再执行文字指令
- 文字消息**自动引用文件名**，格式为 `@文件名 文字内容`
- 示例：上传 `report.pdf` 后发送 "分析这份文档" → 实际发送 `@report.pdf 分析这份文档`

#### 场景 3: 点击文件引用到输入框
- 点击任何消息气泡中的文件卡片
- 自动在输入框中插入 `@文件名 ` 引用文本
- 光标定位到引用文本之后，用户可继续输入

### 技术实现要点

| 组件 | 改动 |
|------|------|
| `InputBar.tsx` | 文件无文字时允许发送；有文字时自动 prepend `@filename` 引用；暴露 `insertText` 方法 |
| `App.tsx` | 连接 `onFileClick` 回调到 InputBar 的 `insertText` |
| `MessageBubble.tsx` | 文件气泡支持空文字渲染；传递 `onFileClick` 给 FileCards |
| `FileCards.tsx` | 新增 `onFileClick` 回调，点击文件卡片触发 |
| `ChatArea.tsx` | 透传 `onFileClick` 到 MessageBubble |
| `types.ts` | Message 接口保持 `data` 字段传递文件元信息 |

### 数据流

```
用户选择文件 → HTTP 上传到 uploads/ → 前端 WS 发送 { message, files: [...] }
→ 后端持久化用户消息 (含 data: [{filename}]) → 刷新页面时从 disk reload
→ 前端渲染 FileCardList → 点击触发 onFileClick → 插入 @filename 到输入框
```

---

## 需求 2: Agent 生成文件展示

### 目标
Agent 在会话中生成的文件能够在消息气泡中展示，并可通过侧边栏文件列表统一查看和下载。

### 功能场景

#### 场景 1: 消息气泡中展示生成文件
- Agent 使用 Write 工具创建文件后，自动在对话中显示 **文件结果卡片**
- 用户可点击下载文件
- 用户可点击文件引用到输入框（与需求 1 一致）

#### 场景 2: 侧边栏文件列表
- 点击左下角 **Files** 按钮，打开文件列表面板
- 显示该会话中所有 Agent 生成的文件
- **按生成时间倒序排列**（最新的在上面）
- 每个文件显示：文件名、大小、生成时间
- 点击可下载

### 技术实现要点

| 组件 | 改动 |
|------|------|
| `main_server.py` | 监听 Write tool_use，收集 generated_files，发送 `file_result` 消息 |
| `main_server.py` | 新增 API: `GET /api/users/{user_id}/sessions/{session_id}/files` |
| `main_server.py` | 新增 API: `GET /api/users/{user_id}/generated-files` |
| `MessageBubble.tsx` | 处理 `file_result` 消息类型，渲染 FileCardList (带下载链接) |
| `types.ts` | Message 接口新增 `user_id` 字段用于下载 URL 构建 |
| `SettingsPanel.tsx` | 添加 Skills/Files 标签页切换，Files 页展示生成文件列表 |
| `App.tsx` | 设置面板支持 `initialTab` 参数，控制默认打开的标签 |

### 数据流

```
Agent Write tool → run_agent_task 检测 tool_use.name === "Write"
→ 提取 file_path、content → 记录到 generated_files 列表
→ 会话结束前发送 file_result 消息到 buffer
→ 前端接收 file_result → MessageBubble 渲染为可下载文件卡片
→ 侧边栏 Files 面板调用 /api/users/{user_id}/generated-files → 按时间倒序展示
```

---

## 需求 3: ChatArea 消息渲染统一优化

### 目标
统一所有消息类型的 Markdown 渲染与视觉样式，代码块支持语法高亮、语言标签显示、一键复制。

### 功能场景

#### 场景 1: 用户消息 Markdown 渲染
- 用户发送的消息支持完整 Markdown 语法：标题、列表、链接、代码块、表格等
- 用户消息中的代码块同样支持语法高亮

#### 场景 2: Agent 消息代码高亮 + 复制
- Agent 回复中的代码块自动识别语言并**语法高亮**
- 代码块顶部显示**语言标签**（如 `typescript`、`python`）
- 代码块顶部显示**复制按钮**，点击复制到剪贴板

#### 场景 3: 思考块 Markdown 渲染
- `[thinking]...[/thinking]` 内容中的 Markdown 语法正常渲染
- 支持代码块、列表、链接等格式化

#### 场景 4: 工具结果格式化
- Tool result 内容检测：JSON 内容自动格式化处理
- 非 JSON 内容按 Markdown 渲染
- 超长内容支持折叠/截断

### 技术实现要点

| 组件 | 改动 |
|------|------|
| `package.json` | 新增依赖：`rehype-highlight` + `highlight.js` |
| `MarkdownRenderer.tsx` | 添加 `rehypeHighlight` 插件；自定义 CodeBlock 组件（语言标签 + 复制按钮） |
| `MessageBubble.tsx` | 用户消息改用 MarkdownRenderer；思考块内容改用 MarkdownRenderer；工具结果改用 MarkdownRenderer + JSON 检测 |
| `global.css` | Markdown 样式从 `.assistant-message .bubble` 提取为 `.message .bubble` 共享；用户气泡代码适配（浅色代码文字）；highlight.js 主题样式；代码块头部栏样式 |

### 当前 vs 目标状态

| 消息类型 | 当前渲染 | 目标渲染 |
|---------|---------|---------|
| assistant | Markdown ✅ / 代码无高亮 | Markdown + 语法高亮 + 复制按钮 |
| user | 纯文本 ❌ | Markdown + 语法高亮 |
| thinking | 纯文本 ❌ | Markdown 渲染 |
| tool_result | `<pre>` 纯文本 ❌ | Markdown + JSON 格式化 |
| system | 纯文本灰色 ✅ | 保持不变 |
| file_upload | FileCard ✅ | 保持不变 |
| file_result | FileCard ✅ | 保持不变 |

---

## 整体架构关系

```
┌─────────────────────────────────────────────────────┐
│                    ChatArea                          │
│  ┌─────────────────────────────────────────────────┐│
│  │              MessageBubble                       ││
│  │  ┌───────────────────────────────────────────┐  ││
│  │  │         MarkdownRenderer                  │  ││
│  │  │  (GFM + rehype-highlight + CodeBlock)     │  ││
│  │  └───────────────────────────────────────────┘  ││
│  │                                                  ││
│  │  User Message: Markdown + @file refs            ││
│  │  Assistant Message: Markdown + code highlight   ││
│  │  FileCard: uploaded / result, click to ref      ││
│  │  Thinking: Markdown                              ││
│  │  Tool Result: Markdown / JSON formatted          ││
│  └─────────────────────────────────────────────────┘│
│                                                      │
│  StatusSpinner (hook / agent working indicator)     │
│  SkillFeedbackWidget                                │
└─────────────────────────────────────────────────────┘
         ▲                                             
         │ onFileClick                                 
┌────────┴──────────┐          ┌──────────────────────┐
│    InputBar       │          │   SettingsPanel      │
│  @filename insert │          │  Skills | Files tab  │
│  insertText API   │          │  generated file list  │
└───────────────────┘          └──────────────────────┘
```

---

## 修改文件清单

### 前端 (TypeScript/React)

| 文件 | 需求 1 | 需求 2 | 需求 3 |
|------|:------:|:------:|:------:|
| `InputBar.tsx` | ✅ ref 暴露 insertText, 自动引用 | - | - |
| `App.tsx` | ✅ onFileClick 回调, settingsTab | ✅ initialTab | - |
| `ChatArea.tsx` | ✅ onFileClick 透传 | - | - |
| `MessageBubble.tsx` | ✅ 空文字+文件渲染 | ✅ file_result 渲染 | ✅ 全类型 Markdown |
| `FileCards.tsx` | ✅ onFileClick 回调 | - | - |
| `SettingsPanel.tsx` | - | ✅ Skills/Files 标签页 | - |
| `MarkdownRenderer.tsx` | - | - | ✅ 语法高亮 + CodeBlock |
| `types.ts` | - | ✅ user_id 字段 | - |
| `global.css` | - | ✅ files-empty | ✅ 统一 Markdown 样式 |
| `MessageBubble.test.tsx` | ✅ 测试 | ✅ 测试 | ✅ 测试 |
| `package.json` | - | - | ✅ rehype-highlight |

### 后端 (Python)

| 文件 | 需求 1 | 需求 2 | 需求 3 |
|------|:------:|:------:|:------:|
| `main_server.py` | - | ✅ file_result + 新 API | - |
| `message_buffer.py` | ✅ 已支持 file metadata 持久化 | - | - |

---

## 测试计划

### 前端测试 (Vitest)
1. 用户消息文件-only 气泡渲染
2. 用户消息 Markdown 渲染（标题、代码、列表、链接）
3. 代码块语法高亮 class 应用
4. 代码块复制按钮存在
5. file_result 消息渲染 + 下载链接
6. onFileClick 回调触发

### 后端测试 (pytest)
1. Write tool 检测收集生成文件
2. file_result 消息发送到 buffer
3. `/generated-files` API 返回正确排序的文件列表
4. `/sessions/{id}/files` API 按会话过滤
