# Plan: stored_name 格式设计

## Context

并发 session 共享 workspace/outputs 目录，多个 session 可能产生同名的物理文件（如 `report.pdf`）。之前的方案是纯 UUID 物理名（`a1b2c3d4.pdf`），可读性差。需要改进 `stored_name` 格式。

## 方案对比

| 方案 | 格式 | 示例 | 长度 | 优点 | 缺点 |
|------|------|------|------|------|------|
| A | `{name}-{uuid8}{ext}` | `report-a1b2c3d4.pdf` | 原始名+8位+连字符 | 可读性好，一眼看出内容 | 含 `-` 可能与原文件名冲突（如 `my-report.pdf` → `my-report-a1b2c3d4.pdf`） |
| B | `{name}__{uuid8}{ext}` | `report__a1b2c3d4.pdf` | 原始名+8位+双下划线 | 分隔清晰，不干扰原文件名 | 略长 |
| C | `{uuid8}_{name}{ext}` | `a1b2c3d4_report.pdf` | 8位+下划线+原始名 | 排序时同批文件聚在一起 | 开头是随机字符，不利于浏览 |

## 推荐方案：A

格式：`{name_without_ext}-{uuid8}{ext}`

```python
def _generate_stored_name(original_name: str) -> str:
    import uuid
    name, ext = Path(original_name).stem, Path(original_name).suffix
    return f"{name}-{uuid.uuid4().hex[:8]}{ext}"
```

示例：
- `report.pdf` → `report-a1b2c3d4.pdf`
- `data_analysis.csv` → `data_analysis-e7f8a9b0.csv`
- `notes.txt` → `notes-3c4d5e6f.txt`

**UUID 长度选择 8 位（32bit）**：生日悖论下，1000 个文件碰撞概率 ~0.01%，足够安全。

## 修改文件

| 文件 | 修改 |
|------|------|
| `main_server.py` | `_generate_stored_name()` 改为 `{stem}-{uuid8}{ext}` 格式 |
