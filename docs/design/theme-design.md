# 明暗模式支持 - 设计方案

## 背景

Web Agent 前端目前仅有浅色模式设计。所有 CSS 变量令牌在 `:root` 中硬编码为浅色值，且 `global.css`（4618 行）中大量颜色直接硬编码，未使用变量。需要支持深色模式，尊重系统偏好，允许手动切换，并在会话间持久化。

## 架构决策

**`data-theme` 属性挂载在 `<html>` 上 + React Context**，配合 `index.html` 中的内联防闪烁脚本。

- 通过 `[data-theme="dark"]` 选择器交换 CSS 变量 — 无运行时重计算，与现有原生 CSS 架构兼容
- React Context（`ThemeContext`）管理状态、localStorage 持久化和 `matchMedia` 监听
- `<head>` 中的防 FOUC 脚本在首帧绘制前设置 `data-theme`
- 主题来源追踪（`"system"` | `"manual"`）：仅在用户未手动覆盖时响应系统主题变更

### 决策流程

1. 首次访问：读取 localStorage 键 `"theme-preference"`
2. 若存在（`"light"` 或 `"dark"`），直接使用
3. 若不存在，检查 `window.matchMedia("(prefers-color-scheme: dark)")`
4. 将结果应用为 `data-theme` 属性
5. 用户手动切换时，写入 localStorage 并更新属性

## 新增文件

| 文件 | 用途 |
|------|------|
| `frontend/src/contexts/ThemeContext.tsx` | Context Provider：`theme`、`setTheme`、`toggleTheme` |
| `frontend/src/hooks/useTheme.ts` | 便捷 Hook，包装 `useContext(ThemeContext)` |
| `frontend/src/components/ThemeToggle.tsx` | 切换按钮（太阳/月亮 SVG 图标，带旋转动画） |
| `frontend/src/components/ThemeToggle.test.tsx` | 单元测试 |

## 修改文件

| 文件 | 变更内容 |
|------|---------|
| `frontend/index.html` | `<head>` 中添加内联防 FOUC 脚本 |
| `frontend/src/main.tsx` | 用 `<ThemeProvider>` 包裹 `<App />` |
| `frontend/src/styles/global.css` | 添加 `[data-theme="dark"]` 令牌块、约 60 个新语义令牌、硬编码颜色替换为 `var(--color-*)` |
| `frontend/src/components/Header.tsx` | 在 `<LanguageSwitcher />` 和 `<SettingsMenu />` 之间插入 `<ThemeToggle />` |
| `frontend/src/i18n/en.json` | 添加 `theme.switchToDark` / `theme.switchToLight` |
| `frontend/src/i18n/zh.json` | 添加 `theme.switchToDark` / `theme.switchToLight` |

## CSS 令牌映射

### 现有令牌（在 `[data-theme="dark"]` 中覆盖）

| 令牌 | 浅色值 | 深色值 |
|------|--------|--------|
| `--color-bg` | `#f5f5f5` | `#111827` |
| `--color-surface` | `#ffffff` | `#1f2937` |
| `--color-text` | `#1a1a1a` | `#f3f4f6` |
| `--color-text-secondary` | `#6b7280` | `#9ca3af` |
| `--color-primary` | `#3b82f6` | `#60a5fa` |
| `--color-primary-hover` | `#2563eb` | `#3b82f6` |
| `--color-primary-light` | `rgba(59,130,246,0.08)` | `rgba(96,165,250,0.12)` |
| `--color-accent` | `#475569` | `#94a3b8` |
| `--color-border` | `#e5e7eb` | `#374151` |
| `--color-border-soft` | `#f3f4f6` | `#2d3748` |
| `--color-surface-hover` | `#f9fafb` | `#283142` |
| `--color-system-text` | `#6b7280` | `#9ca3af` |
| `--color-success` | `#22c55e` | `#4ade80` |
| `--color-success-light` | `#f0fdf4` | `rgba(74,222,128,0.1)` |
| `--color-success-border` | `#86efac` | `#22c55e` |
| `--color-success-text` | `#166534` | `#86efac` |
| `--color-error` | `#dc2626` | `#f87171` |
| `--color-error-light` | `#fef2f2` | `rgba(248,113,113,0.1)` |
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.05)` | `0 1px 2px rgba(0,0,0,0.3)` |
| `--shadow-md` | `0 4px 12px rgba(0,0,0,0.08)` | `0 4px 12px rgba(0,0,0,0.4)` |

### 新增令牌（同时在 `:root` 和 `[data-theme="dark"]` 中定义）

#### 代码块（两种主题下均为深色背景，深色模式下略深）

| 令牌 | 浅色值 | 深色值 |
|------|--------|--------|
| `--color-code-bg` | `#1e1e1e` | `#0d1117` |
| `--color-code-text` | `#d4d4d4` | `#e6edf3` |
| `--color-code-header-bg` | `#2d2d2d` | `#161b22` |
| `--color-code-header-border` | `#3a3a3a` | `#30363d` |
| `--color-code-lang` | `#a1a1aa` | `#8b949e` |
| `--color-code-copy-border` | `#52525b` | `#484f58` |
| `--color-code-copy-hover-bg` | `#3f3f46` | `#30363d` |
| `--color-code-copy-hover-text` | `#e4e4e7` | `#c9d1d9` |
| `--color-inline-code-bg` | `#f3f4f6` | `#2d3748` |

#### 侧边栏

| 令牌 | 浅色值 | 深色值 |
|------|--------|--------|
| `--color-sidebar-hover` | `#f5f5f5` | `#283142` |
| `--color-sidebar-active` | `#e2e8f0` | `#374151` |
| `--color-sidebar-delete-color` | `#71717a` | `#9ca3af` |
| `--color-sidebar-delete-hover-bg` | `#fef2f2` | `rgba(248,113,113,0.15)` |
| `--color-sidebar-delete-hover-color` | `#ef4444` | `#f87171` |
| `--color-sidebar-empty` | `#71717a` | `#9ca3af` |

#### 用户消息气泡

| 令牌 | 浅色值 | 深色值 |
|------|--------|--------|
| `--color-user-bubble-bg` | `#ffffff` | `#3b82f6` |
| `--color-user-bubble-text` | `#1a1a1a` | `#ffffff` |
| `--color-user-bubble-code-bg` | `rgba(0,0,0,0.08)` | `rgba(255,255,255,0.15)` |

#### 头部栏

| 令牌 | 浅色值 | 深色值 |
|------|--------|--------|
| `--color-header-logo` | `#475569` | `#94a3b8` |
| `--color-btn-new-session-text` | `#ffffff` | `#ffffff` |
| `--color-connection-bg` | `rgba(34,197,94,0.1)` | `rgba(74,222,128,0.12)` |
| `--color-connection-text` | `#15803d` | `#4ade80` |
| `--color-connection-reconnect-bg` | `rgba(245,158,11,0.1)` | `rgba(251,191,36,0.12)` |
| `--color-connection-reconnect-text` | `#b45309` | `#fbbf24` |
| `--color-connection-failed-bg` | `rgba(239,68,68,0.1)` | `rgba(248,113,113,0.12)` |
| `--color-connection-failed-text` | `#dc2626` | `#f87171` |

#### 错误和横幅

| 令牌 | 浅色值 | 深色值 |
|------|--------|--------|
| `--color-error-bubble-bg` | `#fef2f2` | `rgba(248,113,113,0.1)` |
| `--color-error-bubble-border` | `#fecaca` | `rgba(248,113,113,0.25)` |
| `--color-error-bubble-text` | `#991b1b` | `#fca5a5` |
| `--color-resolved-error-bg` | `#f9fafb` | `#1f2937` |
| `--color-resolved-error-border` | `#d1d5db` | `#374151` |
| `--color-resolved-error-text` | `#9ca3af` | `#6b7280` |
| `--color-resolved-badge-bg` | `#e5e7eb` | `#374151` |
| `--color-resolved-badge-text` | `#6b7280` | `#9ca3af` |
| `--color-banner-reconnect-bg` | `#fef3c7` | `rgba(251,191,36,0.12)` |
| `--color-banner-reconnect-text` | `#92400e` | `#fbbf24` |
| `--color-banner-failed-bg` | `#fee2e2` | `rgba(248,113,113,0.12)` |
| `--color-banner-failed-text` | `#991b1b` | `#fca5a5` |
| `--color-banner-btn-bg` | `#ef4444` | `#dc2626` |
| `--color-banner-btn-hover` | `#dc2626` | `#b91c1c` |

#### 工具消息

| 令牌 | 浅色值 | 深色值 |
|------|--------|--------|
| `--color-tool-edit-old-border` | `#ef4444` | `#f87171` |
| `--color-tool-edit-new-border` | `#22c55e` | `#4ade80` |
| `--color-tool-edit-old-label` | `#ef4444` | `#f87171` |
| `--color-tool-edit-new-label` | `#22c55e` | `#4ade80` |
| `--color-tool-error-border` | `#fca5a5` | `#f87171` |
| `--color-tool-error-summary` | `#dc2626` | `#f87171` |
| `--color-analysis-border` | `#f59e0b` | `#fbbf24` |
| `--color-analysis-text` | `#92400e` | `#fbbf24` |
| `--color-summary-border` | `#14b8a6` | `#2dd4bf` |
| `--color-summary-text` | `#0f766e` | `#2dd4bf` |

#### 技能页面

| 令牌 | 浅色值 | 深色值 |
|------|--------|--------|
| `--color-skill-row-bg` | `#ffffff` | `#1f2937` |
| `--color-skill-row-border` | `#e5e5e5` | `#374151` |
| `--color-skill-badge-personal-bg` | `#dbeafe` | `#1e3a5f` |
| `--color-skill-badge-personal-text` | `#3b82f6` | `#60a5fa` |
| `--color-skill-badge-shared-bg` | `#dcfce7` | `#14532d` |
| `--color-skill-badge-shared-text` | `#15803d` | `#4ade80` |
| `--color-skill-badge-invalid-bg` | `#fef3c7` | `#713f12` |
| `--color-skill-badge-invalid-text` | `#a16207` | `#fbbf24` |
| `--color-skill-invalid-border` | `#f59e0b` | `#fbbf24` |
| `--color-skill-invalid-bg` | `#fffbeb` | `rgba(251,191,36,0.08)` |
| `--color-skill-meta-text` | `#71717a` | `#9ca3af` |

#### 标签页

| 令牌 | 浅色值 | 深色值 |
|------|--------|--------|
| `--color-tab-bg` | `#f5f5f5` | `#283142` |
| `--color-tab-text` | `#71717a` | `#9ca3af` |
| `--color-tab-hover-bg` | `#e4e4e7` | `#374151` |
| `--color-tab-hover-text` | `#52525b` | `#d1d5db` |
| `--color-tab-active-bg` | `#dbeafe` | `#1e3a5f` |
| `--color-tab-active-text` | `#3b82f6` | `#60a5fa` |
| `--color-tab-active-border` | `#3b82f6` | `#60a5fa` |
| `--color-sp-tabs-border` | `#e5e5e5` | `#374151` |

#### 按钮

| 令牌 | 浅色值 | 深色值 |
|------|--------|--------|
| `--color-btn-border` | `#d4d4d4` | `#4b5563` |
| `--color-btn-text` | `#52525b` | `#d1d5db` |
| `--color-btn-view-hover-border` | `#60a5fa` | `#3b82f6` |
| `--color-btn-delete-hover-border` | `#ef4444` | `#f87171` |
| `--color-btn-promote-hover-border` | `#22c55e` | `#4ade80` |
| `--color-btn-promote-hover-text` | `#16a34a` | `#4ade80` |

#### 其他

| 令牌 | 浅色值 | 深色值 |
|------|--------|--------|
| `--color-overlay-bg` | `rgba(0,0,0,0.1)` | `rgba(0,0,0,0.5)` |
| `--color-files-panel-bg` | `#ffffff` | `#1f2937` |
| `--color-files-panel-border` | `#e5e5e5` | `#374151` |
| `--color-setting-item-bg` | `#f5f5f5` | `#283142` |
| `--color-setting-value` | `#71717a` | `#9ca3af` |
| `--color-rating-star` | `#f59e0b` | `#fbbf24` |
| `--color-login-error` | `#dc2626` | `#f87171` |
| `--color-dot-error` | `#ef4444` | `#f87171` |
| `--color-dot-warning` | `#f59e0b` | `#fbbf24` |

## 核心代码设计

### ThemeContext.tsx

```typescript
type Theme = 'light' | 'dark';

interface ThemeContextValue {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
}
```

关键实现要点：

1. **初始化**：localStorage → matchMedia → `"light"` 回退
2. **同步**：主题变更时更新 `document.documentElement.dataset.theme`
3. **来源追踪**：`"theme-source"` 键区分 `"system"` 和 `"manual"`
4. **系统监听**：仅当来源为 `"system"` 时响应 `matchMedia` 变更事件
5. **持久化**：同时写入 `"theme-preference"` 和 `"theme-source"` 到 localStorage

### useTheme.ts

```typescript
import { useContext } from 'react';
import { ThemeContext } from '../contexts/ThemeContext';

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
}
```

### main.tsx 变更

```tsx
// 变更前
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)

// 变更后
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <ThemeProvider>
        <App />
      </ThemeProvider>
    </BrowserRouter>
  </StrictMode>,
)
```

### 防 FOUC 内联脚本（index.html `<head>` 中）

```html
<script>
  (function() {
    var pref = localStorage.getItem('theme-preference');
    var theme = pref || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);
  })();
</script>
```

### ThemeToggle.tsx

- 渲染带 class `theme-toggle` 的 `<button>`
- 浅色模式显示月亮图标（提示可切换至深色），深色模式显示太阳图标
- `aria-label` 来自 i18n（如 "切换到深色模式" / "切换到浅色模式"）
- 点击时图标旋转 180° + 缩放的 CSS 动画（仅使用 `transform` 和 `opacity`，符合合成器友好原则）
- 悬停状态：背景使用 `--color-surface-hover`
- 尺寸和视觉风格与 `LanguageSwitcher` 保持一致（紧凑图标按钮）

### Header.tsx 变更

```tsx
// 在 .app-header-actions 中
<LanguageSwitcher />
<ThemeToggle />           {/* 新增 */}
<SettingsMenu ... />
```

### i18n 新增键

```json
// en.json
"theme": {
  "switchToDark": "Switch to dark mode",
  "switchToLight": "Switch to light mode"
}

// zh.json
"theme": {
  "switchToDark": "切换到深色模式",
  "switchToLight": "切换到浅色模式"
}
```

## CSS 迁移策略

`global.css` 中有约 110 处硬编码颜色，按组件区域分组替换：

| CSS 区域 | 行号范围 | 硬编码数量 | 使用的新令牌 |
|----------|---------|-----------|-------------|
| `:root` 令牌块 | 1-34 | 0 | +60 个新令牌 |
| App 布局 | 43-121 | 0 | 现有令牌 |
| 侧边栏 | 123-277 | ~8 | `--color-sidebar-*` |
| 聊天区/消息 | 279-347 | 0 | 现有令牌 |
| 消息气泡 | 349-470 | ~6 | `--color-user-bubble-*`、`--color-code-*` |
| 代码块 | 471-570 | ~28 | `--color-code-*` |
| 工具消息 | 694-1033 | ~12 | `--color-tool-*`、`--color-error-*` |
| 登录页 | 1076-1143 | 1 | `--color-login-error` |
| 问题卡片 | 1145-1199 | 1 | `--color-primary` |
| 头部栏 | 2360-2623 | ~12 | `--color-header-*`、`--color-connection-*` |
| 标签页 | 2950-3016 | ~10 | `--color-tab-*` |
| 技能面板 | 3020-3178 | ~18 | `--color-skill-*` |
| 文件面板 | 3180-3239 | 4 | `--color-overlay-bg`、`--color-files-panel-*` |
| 反馈页 | 3240-3360 | 2 | `--color-rating-star` |
| MCP 页 | 3700-3900 | ~6 | 混合现有 + 新令牌 |
| 连接横幅 | 4297-4354 | ~8 | `--color-banner-*`、`--color-dot-*` |
| 错误状态 | 4450-4617 | ~4 | `--color-error-*` |

语法高亮（highlight.js "Atom One Dark" 主题）的硬编码颜色保持不变，因为代码块在两种主题下都是深色背景。

## 实施步骤

### Step 1: 防 FOUC 脚本
在 `index.html` 的 `<head>` 中添加内联脚本（在任何 CSS/JS 加载之前）。
**重要性：最高** — 否则用户在深色模式下会看到白色闪烁。

### Step 2: 创建 ThemeContext 和 useTheme Hook
纯逻辑，无 CSS 依赖。包含完整的 localStorage 读写、matchMedia 监听、data-theme 属性管理。

### Step 3: 在 main.tsx 中接入 Provider
包裹 `<App />`。验证应用仍正常加载（默认浅色，视觉无变化）。

### Step 4: CSS 令牌迁移
1. 在 `:root` 中添加所有新令牌（仅浅色值）— 视觉无变化
2. 添加 `[data-theme="dark"]` 块（所有深色值）
3. 添加过渡令牌：`--theme-transition: background-color 300ms ease, color 300ms ease, border-color 300ms ease`
4. 按区域逐步替换硬编码颜色，每步验证浅色模式是否与原来一致
5. 全部替换完成后，浅色模式应与原来像素级一致

### Step 5: 应用过渡动画
将 `transition: var(--theme-transition)` 应用到 `body` 和主要容器，避免对布局属性使用 `transition: all`。

### Step 6: 创建 ThemeToggle 组件
内联 SVG 图标（继承 text color），接入 `useTheme()`，添加 CSS 样式。

### Step 7: 在 Header 中接入切换按钮
在 `.app-header-actions` 中 `<LanguageSwitcher />` 和 `<SettingsMenu />` 之间插入。

### Step 8: 添加 i18n 翻译
`en.json` 和 `zh.json` 中添加 `theme` 命名空间。

### Step 9: 视觉打磨
在两种主题下测试所有视图：
- 登录页、空欢迎页、活跃聊天、技能页、反馈页、MCP 页、进化面板、记忆面板、设置预览
- 验证所有悬停、聚焦、活跃、禁用状态
- 检查文本对比度（尤其是 `--color-text-secondary` 在深色背景上）
- 验证代码块可区分
- 验证用户消息气泡对比度（浅色白底 vs 深色蓝底）
- 验证深色模式下阴影可见

### Step 10: 测试
- `ThemeToggle.test.tsx`：渲染测试、点击行为、正确图标
- 更新现有测试包装器（如果它们渲染了现在需要 `ThemeProvider` 的组件树）
- 运行 `npm test` 确保全部通过
- 运行 `npx tsc --noEmit` 确保类型检查通过

## 风险和缓解

| 风险 | 缓解措施 |
|------|---------|
| 主题切换时 FOUC | `<head>` 中内联脚本解决首次加载 FOUC；运行时切换时 CSS 变量即时交换，300ms 过渡平滑视觉变化 |
| 遗漏硬编码颜色 | 迁移后运行 `grep -n '#[0-9a-fA-F]\{3,6\}'` 扫描残留硬编码色值 |
| highlight.js 主题冲突 | 语法高亮使用为深色背景设计的硬编码颜色，代码块在两种模式下都保持深色，仅区块容器使用新令牌 |
| 深色模式下阴影不可见 | 深色表面上的阴影需要比浅色表面更强的透明度，令牌交换已处理 |
| 登录页主题支持 | 登录页在 `MainApp` 内部、`MainLayout` 之前渲染，继承 `ThemeProvider` 上下文 |
| 测试失败 | 任何渲染包含 Header 的组件树的测试都需要 `ThemeProvider`，可添加测试工具包装函数 |
| 组件内联样式 | `MainLayout` 中有硬编码颜色的内联 `style={}`，需替换为使用令牌的 CSS 类 |

## 验证方案

1. **开发服务器**：`cd frontend && npm run dev` — 切换主题，验证所有页面正常渲染
2. **系统偏好**：切换操作系统主题设置，验证应用跟随（无手动覆盖时）
3. **localStorage 持久化**：切换主题，刷新页面，验证偏好保留
4. **无 FOUC**：硬刷新（Cmd+Shift+R），验证深色模式下无白色闪烁
5. **类型检查**：`npx tsc --noEmit` 必须通过
6. **现有测试**：`npm test` 必须全部通过
7. **浅色模式不变**：并排对比，确认浅色模式与当前版本像素级一致
