# 技能进化机制优化方案

> 生成日期: 2026-05-17
> 来源: 代码分析 + 架构讨论

## 现状分析

当前系统有两条技能进化路径，**互补而非替代**：

| | 手动进化 (`skill_evolution.py`) | 自动机制（新方案） |
|---|---|---|
| **触发条件** | admin 主动触发 | 后台定时 |
| **适用场景** | 低质量技能的修复路径（avg_rating < 4.0） | 优质个人技能的推广路径（avg_rating ≥ 4.0） |
| **执行方式** | 启动完整 Agent SDK 会话重写 SKILL.md | 提取统计、生成 Wiki、加入晋升队列 |
| **目标** | **修复问题** | **积累知识** |
| **保留** | **必须保留** | 新增 |

## 发现的 3 个关键问题

### 问题一：自动晋升没有闭环（P0）

`_auto_promotion_loop`（`src/collective_intelligence.py`）只识别候选者并写入 `skill_promotion_queue`，但**不会真正晋升**。队列中的记录永远停留在 `status='pending'`，没有审批/拒绝/执行的逻辑。

**修复**: 加 admin 审批端点（approve/reject），approve 后执行文件复制 + DB 更新 `source='shared'`。

### 问题二：使用统计失真（P1）

`skill_usage` 在 `build_sdk_options()` 中记录（每次会话加载技能时），不是在实际使用时。如果技能被加载但 Agent 没调用它，计数仍然增加。auto_promotion 的阈值（≥10 uses）因此失真。

**修复**: 将 usage 记录时机从 skill load 改为实际 tool_use 事件发生时。

### 问题三：Wiki 只收录高质量内容（P1）

Wiki 生成要求 avg_rating ≥ 4.0。反模式、常见错误、踩坑经验**永远不会被记录**。

**修复**: Wiki 增加"反模式"收录，低评分技能生成警告型 Wiki 页（`status='warning'`）。

## 自动进化分级方案

手动进化的瓶颈是：技能重写是**破坏性变更**，一改所有用户都受影响，所以需要 admin 审批。但审批也带来了延迟——低质量技能在等待期间继续伤害用户。

### 分级策略

```
反馈数据分析
  │
  ├── 用户提供了 user_edits
  │   └── → 用户已知正确答案 → 自动合并（最安全）
  │
  ├── 明确的 bug 描述（"文件路径硬编码"、"缺少超时"）
  │   └── → 确定性修复 → Agent 自动修复 → 生成版本 → 通知用户
  │
  ├── 模糊抱怨（"不好用"、"太慢了"）
  │   └── → 不确定怎么修 → 生成改进版 → 等待审批
  │
  └── 高频使用技能突然评分下降
      └── → 影响面大 → 必须人工审查
```

### 代码设计

```python
class AutoEvolvePolicy:
    """根据反馈类型决定进化策略。"""

    async def classify(self, skill_name: str) -> EvolveAction:
        feedback = await self._get_feedback(skill_name)

        # 策略 1：用户提供了可合并的编辑
        if feedback.has_user_edits():
            return EvolveAction.APPLY_EDITS  # 直接应用用户编辑

        # 策略 2：明确的 bug 描述
        if feedback.has_specific_bugs():
            return EvolveAction.AUTO_FIX  # Agent 自动修复 → 生成版本 → 通知用户

        # 策略 3：模糊反馈
        if feedback.is_vague():
            return EvolveAction.PROPOSE  # Agent 生成改进版 → 等待审批

        # 策略 4：高频使用技能
        if feedback.uses_count > 50:
            return EvolveAction.REQUIRE_REVIEW  # 影响面大，必须人工

        # 策略 5：其他
        return EvolveAction.SKIP
```

### 策略矩阵

| 维度 | 规则 |
|------|------|
| 用户已给出正确代码 | 自动合并（最安全的自动进化） |
| 问题是明确的 bug | 自动修复，生成新版本不直接激活 |
| 问题是体验类（"太慢了"） | 只生成建议，不自动应用 |
| 影响用户多 | 提高审批门槛 |
| 新技能/使用少 | 降低审批门槛，允许快速迭代 |

## 待补充

- `skill_promotion_queue` 加过期机制：30 天后自动清理未审批记录
- `src/wiki_generator.py` 增加低评分技能收录逻辑
- `build_sdk_options()` 中 usage 记录改到实际 tool_use 时触发
