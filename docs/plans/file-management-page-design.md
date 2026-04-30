# Session File Panel 设计方案

## 现状

- 上传和生成的文件只在消息流中以内联卡片出现，散落在对话里，不易查找
- `FilesPanel` 是一个模态弹窗，只能看生成文件，不能删除、不能看上传文件
- 用户想在 session 视图中方便地查看和管理文件

## 设计思路

**一个面板完成所有文件操作。** 在 session 聊天视图右侧，通过 `📁` 按钮打开面板。面板内可切换范围。

---

## 布局

```
┌──────────┬──────────────────────────────────────────────┐
│ Sidebar  │ Header (session title, user menu)            │
│          ├──────────────────────────────┬───────────────┤
│ Sessions │ Chat Area                    │ 📁 Files      │
│          │                              │               │
│          │ Messages...                  │ [All][Session]│ ← 范围切换
│          │                              │               │
│          │                              │ ▼ Uploads (2) │
│          │                              │   report.pdf  │
│          │                              │   [↓] [✕]     │
│          │                              │   data.csv    │
│          │                              │   [↓] [✕]     │
│          │                              │               │
│          │                              │ ▼ Generated(3)│
│          │                              │   chart.png   │
│          │                              │   [↓] [✕]     │
│          │                              │   output.xlsx │
│          │                              │   [↓] [✕]     │
│          │                              │   result.json │
│          │                              │   [↓] [✕]     │
│          ├──────────────────────────────┴───────────────┤
│          │ Input Bar                                    │
└──────────┴──────────────────────────────────────────────┘
```

---

## 交互

- **打开/关闭**：InputBar 旁边 `📁` 按钮，点击切换面板开关
- **范围切换**：面板顶部 `[All]` `[Session]` 两个 tab
  - All：显示所有会话的文件
  - Session：只显示当前会话文件
- **面板宽度**：280px ~ 320px
- **文件分组**：Uploads 和 Generated 两个折叠组，分别显示文件数
- **文件卡片**：文件名、大小、下载按钮(↓)、删除按钮(✕)
- **点击文件名** → 在输入框中 `@` 引用该文件（如 `@data.csv`）
- **下载**：`<a>` 标签直接链接到下载端点
- **删除**：点击 ✕ → confirm 确认 → 调用 DELETE API → 刷新列表
- **空状态**：
  - 无上传文件：Uploads 分组显示 "No uploads"
  - 无生成文件：Generated 分组显示 "No generated files"

---

## 数据获取

| 范围 | API |
|------|-----|
| All | `GET /api/users/{user_id}/files` |
| Session | `GET /api/users/{user_id}/sessions/{session_id}/files` |
| 删除 | `DELETE /api/users/{user_id}/files/{filename}` |
| 下载 | `GET /api/users/{user_id}/download/{file_path}` |

---

## 文件修改清单

| 文件 | 变更 |
|------|------|
| `frontend/src/components/SessionFilePanel.tsx` | **新建** — 右侧文件面板 |
| `frontend/src/App.tsx` | 添加面板到 session 视图；传入 `activeSessionId`；移除 `FilesPanel` 引用 |
| `frontend/src/styles/global.css` | 添加 `.session-file-panel` 等样式 |

### 清理

| 文件 | 说明 |
|------|------|
| `FilesPanel.tsx` | 被 SessionFilePanel 替代 |
| `SettingsPanel.tsx` | 已被 SkillsPage 替代，无引用 |

---

## 文件引用到输入框

点击面板中的文件名，触发回调将 `@filename` 插入到 InputBar 的输入框中：

```
Props:
  onFileClick: (filename: string) => void

App.tsx 中:
  const inputBarRef = useRef<InputBarHandle>(null)
  <SessionFilePanel onFileClick={(name) => inputBarRef.current?.insertMention(name)} />
```

> 需要在 InputBar 中暴露 `insertMention` 方法，将 `@filename ` 插入到光标位置。

---

## 状态处理

```
Loading     →  面板内显示 "Loading..."
Error       →  面板内显示红色错误信息
Empty       →  "No files found"
有文件      →  按 Uploads / Generated 分组显示
```

---

## CSS 要点

- `.session-file-panel` — 固定宽度右侧面板，flex column，带左边框
- `.session-file-toggle` — 触发按钮，带文件计数 badge
- `.session-file-scope` — 范围切换 tabs
- `.session-file-group` — 折叠分组标题
- `.session-file-item` — 文件名（可点击）+ 大小 + 操作按钮
- `.session-file-empty` — 空状态

---

## 实现顺序

1. **SessionFilePanel 组件** — 面板 UI + 数据获取 + 范围切换
2. **InputBar insertMention** — 暴露插入 `@` 引用的方法
3. **App.tsx 集成** — 面板嵌入 + 回调连接
4. **清理旧组件** — 删除 FilesPanel.tsx、SettingsPanel.tsx

---

## 验证

1. `npx tsc --noEmit` — 类型检查通过
2. `npm run build` — 构建成功
3. 手动测试：
   - 点击 📁 → 面板打开，默认显示当前 session 文件
   - 切换到 All → 显示所有会话文件
   - Uploads / Generated 分组正确折叠展开
   - 点击文件名 → 输入框出现 `@filename`
   - 下载按钮触发浏览器下载
   - 删除按钮确认后文件移除，列表刷新
   - 切换 session → Session 范围自动刷新
