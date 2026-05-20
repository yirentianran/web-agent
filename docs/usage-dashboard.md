# 使用统计 (Usage Dashboard)

管理后台 `/_/dashboard` 的使用统计页面，提供 Token 消耗、用户活跃度、会话数、容器资源等维度的数据可视化和排行。

## 页面结构

`DashboardPage.tsx` 从上到下渲染：

1. 返回按钮 → 导航到 `/`
2. 标题 `<h2>` + `TimeRangeSelector` 时间范围选择器
3. `OverviewCards` — 五项指标概览卡片，含环比变化
4. `TokenTrendChart` — Token 消耗趋势折线图
5. `ActivityTrendChart` — 活跃用户与会话数趋势折线图
6. `UserRankingTable` + `SkillRankingTable` — 左右并排排行表
7. `ResourcePanel` — 容器资源使用面板（独立请求，不受时间范围影响）

默认展示近 30 天数据，同时请求等长历史周期用于环比计算。

## 时间范围选择器

`TimeRangeSelector.tsx` 支持两种模式：

### 预设

| 预设 | 范围 | 粒度 |
|------|------|------|
| 今天 | 当日 00:00 ~ 23:59 | 5 分钟 |
| 7 天 | 7 天前 ~ 今日 | 小时 |
| 30 天 | 30 天前 ~ 今日（默认） | 天 |

粒度由 `autoInterval()` 自动推断（`useDashboardApi.ts:95-102`）：
- ≤0 天 → `5min`
- ≤3 天 → `hour`
- \>3 天 → `day`

### 自定义范围

点击「自定义」展开 `datetime-local` 输入框，可选择到分钟级别的时间范围（如 `2026-05-20T10:00` 至 `2026-05-20T14:00`），实现日内 5 分钟粒度查询。

日期时间格式为 `YYYY-MM-DDTHH:MM`，后端通过 `datetime.fromisoformat()` 解析。当字符串不含 `T`（即仅日期格式）时，维持原有的 `00:00:00` / `23:59:59` 边界行为，向后兼容预设按钮。

## 概览卡片

`OverviewCards.tsx` 展示五项指标：

| 指标 | 数据来源 | 环比 |
|------|---------|------|
| 活跃用户 | `sessions` 表 DISTINCT user_id | 有（↑/↓ N%） |
| 总用户数 | `users` 表 COUNT | 无 |
| 新增用户 | `users` 表 created_at 在范围内 | 有（+N） |
| 总会话数 | `sessions` 表 COUNT | 有（↑/↓ N%） |
| Token 用量 | `messages` 表 usage JSON 求和（input + output + cache_read + cache_write） | 有（↑/↓ N%） |

环比变化通过同时请求前一个等长周期数据计算。格式化：绝对数值用 K/M 后缀，Token 子行显示 `I {input} O {output}`。

## 图表

### Token 消耗趋势 (`TokenTrendChart.tsx`)

Recharts 动态导入的 `LineChart`，X 轴为日期/时间桶，四条折线：

| 系列 | 颜色 | 线型 | 说明 |
|------|------|------|------|
| Input | indigo | 实线 | 输入 Token |
| Output | green | 实线 | 输出 Token |
| Cache Read | amber | 虚线 `4 4` | 缓存读取 |
| Cache Write | red | 虚线 `2 2` | 缓存创建 |

Y 轴刻度后缀 `K`。

### 活跃用户与会话 (`ActivityTrendChart.tsx`)

将 `dauData`（日活用户）和 `sessionsData`（会话数）按日期合并为 `MergedPoint`。两条 `Line`：

| 系列 | 颜色 | 说明 |
|------|------|------|
| DAU | indigo | 日活跃用户数 |
| Sessions | green | 会话数 |

Y 轴仅显示整数。

## 排行榜

| 表格 | 数据 | 排序 | 列 |
|------|------|------|-----|
| 用户排行 | `TopUser[]` | total_tokens 降序 | #, 用户, Token, 会话数 |
| 技能排行 | `TopSkill[]` | use_count 降序 | #, 技能, 使用次数, 用户数 |

均限制前 10 名。

## 容器资源面板

`ResourcePanel.tsx` 独立调用 `GET /api/admin/resources`，不受时间范围影响。后端委托给 `src.resource_manager.get_all_resources()`。

展示内容：
- 摘要栏：运行中容器数、总 CPU %、总内存 GB、总磁盘使用/配额
- 用户表：用户、容器名 (`web-agent-{userId}`)、CPU %、内存 MB、磁盘 GB、状态（`●` 正常 / `○` 空闲 CPU<0.5% / `⚠` 高负载 CPU>80%）

## 后端 API

所有仪表盘端点均需 `require_admin` 认证，使用 `PROJECT_TZ`（UTC+8）时区，范围超过 365 天返回 422。

| 端点 | Query 参数 | 返回数据 |
|------|-----------|---------|
| `GET /api/admin/dashboard/overview` | `from_date`, `to_date` | active_users, total_users, new_users, total_sessions, token 四项求和 |
| `GET /api/admin/dashboard/trends` | `from_date`, `to_date`, `interval` | 按桶分组的 active_users[], sessions[], tokens[]（含 input/output/cache_read/cache_write） |
| `GET /api/admin/dashboard/rankings` | `from_date`, `to_date` | top_users[user_id, total_tokens, session_count], top_skills[skill_name, use_count, unique_users] |
| `GET /api/admin/resources` | — | 每个用户的 cpu_percent, memory_usage_mb, status, disk used_gb/total_gb, quota |

SQL 查询使用 `strftime` + `localtime` 按桶分组，Token 数据从 `messages.usage` JSON 字段解析。

## 关键文件

| 文件 | 职责 |
|------|------|
| `frontend/src/components/DashboardPage.tsx` | 页面容器，时间状态管理，协调 API 调用 |
| `frontend/src/components/dashboard/TimeRangeSelector.tsx` | 时间范围选择器（预设 + 自定义 datetime-local） |
| `frontend/src/components/dashboard/OverviewCards.tsx` | 五项指标概览卡片 |
| `frontend/src/components/dashboard/TokenTrendChart.tsx` | Token 消耗趋势折线图 |
| `frontend/src/components/dashboard/ActivityTrendChart.tsx` | 活跃用户与会话趋势图 |
| `frontend/src/components/dashboard/UserRankingTable.tsx` | 用户 Token 消耗排行 |
| `frontend/src/components/dashboard/SkillRankingTable.tsx` | 技能使用排行 |
| `frontend/src/components/dashboard/ResourcePanel.tsx` | 容器资源面板 |
| `frontend/src/hooks/useDashboardApi.ts` | 仪表盘 API Hook，自动粒度推断，并行请求 |
| `frontend/src/lib/dates.ts` | 日期工具函数（formatDate / formatDatetime / todayStr / nowStr / daysAgoStr） |
| `frontend/src/i18n/en.json` / `zh.json` (~L253-314) | 国际化字符串 |
| `main_server.py` (~L4715-4970) | 后端仪表盘三个 REST 端点 |
| `main_server.py` (~L6241) | 容器资源端点 |
| `src/resource_manager.py` | 容器资源查询逻辑 |
