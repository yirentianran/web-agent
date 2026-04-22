# Implementation Plan: Fix MCP Tool Selection for Uploaded Files

## Overview

用户上传 xlsx 文件时，agent 先尝试用 `mcp__mineru__parse_documents` 解析，失败后才转向正确的技能。这是因为 agent 不知道每个 MCP 工具支持的文件类型。通过在系统提示词中注入文件类型指南，让 agent 在选工具前就判断文件类型。

## Root Cause

1. `build_system_prompt()` 没有提供关于 MCP 工具文件类型能力的指导
2. mineru 工具的 MCP 描述中未列出支持的文件格式
3. agent 没有"先看文件扩展名再选工具"的规则

## Architecture Changes

- **File: `main_server.py`** — `build_system_prompt()` 函数增强，加入文件类型与工具的映射规则
- **File: `main_server.py`** — `build_sdk_options()` 中 MCP 服务器描述注入系统提示词
- **Optional: `src/hooks.py`** (new) — PreToolUse hook 拦截不支持的文件类型

## Implementation Steps

### Phase 1: System Prompt 增强 (1 file)

#### Step 1: Add file-type tool selection rules to `build_system_prompt()`
**File: `main_server.py`** (around line 525)
- **Action**: 在 `build_system_prompt()` 的 skills 和 skill creation rules 之间，插入一段文件类型工具选择规则
- **Content**:
  ```
  ## Tool Selection by File Type

  When the user uploads a file, check its extension BEFORE choosing a tool:

  - **.xlsx / .xls / .csv** → Use Python (pandas, openpyxl, csv) to read. Do NOT use document parsers.
  - **.pdf** → Use mcp__mineru__parse_documents or similar PDF tools.
  - **.docx / .doc** → Use mcp__mineru__parse_documents or similar document parsers.
  - **.md / .txt / .json / .yaml / .yml / .xml / .html** → Use the Read tool directly.
  - **.png / .jpg / .jpeg / .gif / .svg** → Read the image file directly with the Read tool.
  - **.zip / .tar / .gz** → Use Bash to extract, then Read individual files.
  - **.py / .js / .ts / .java / .go / .rs** → Use the Read tool directly.

  Never guess. Always check the file extension first, then pick the appropriate tool.
  ```
- **Why**: 这是 agent 在选工具前会看到的规则，直接告诉它"先看扩展名再选工具"
- **Dependencies**: None
- **Risk**: Low — pure prompt addition, no code logic change

#### Step 2: Inject MCP server descriptions into system prompt
**File: `main_server.py`** (around line 503-510, after skills section)
- **Action**: 在 skills 列表之后，加入 MCP 服务器的描述信息，让 agent 知道每个 MCP 工具的能力范围
- **Detail**: `load_mcp_config_sync()` 已经返回 `description` 字段，需要把这些描述拼入系统提示词
- **Why**: mineru 的描述应该写明"支持 PDF, Word 等文档解析，不支持 Excel 表格"，这样 agent 看到描述就知道不该用它处理 xlsx
- **Dependencies**: Step 1
- **Risk**: Low

### Phase 2: MCP Server Description 补全 (operational)

#### Step 3: Update mineru MCP server description
**Not a code change** — this is done via the web UI admin panel or API
- **Action**: 通过 `/api/admin/mcp-servers` 更新 mineru 的描述为：
  ```
  Document parser for PDF, Word (.docx), images, and scanned documents. Does NOT support Excel (.xlsx, .xls), CSV, or structured data files. For Excel/CSV use Python pandas/openpyxl instead.
  ```
- **Why**: 让工具描述本身也传达文件格式限制，作为系统提示词的补充
- **Risk**: None — operational change

### Phase 3: PreToolUse Hook (defensive, optional)

#### Step 4: Add file extension check hook
**File: `main_server.py`** (around line 675-720, near existing PreToolUse hooks)
- **Action**: 为 `mcp__mineru__` 工具添加 PreToolUse 拦截，如果输入包含不支持的扩展名(.xlsx, .xls, .csv)，返回一个友好的错误提示而不是让 MCP 调用失败
- **Detail**: 在现有的 Write/Bash 拦截 hook 旁边，新增一个 mineru 文件类型检查 hook
- **Why**: 作为系统提示词的最后一道防线，即使 agent 选错工具也能在调用前拦截
- **Dependencies**: Phase 1
- **Risk**: Low — only affects mineru tool calls, doesn't change other behavior

## Testing Strategy

- **Manual**: Upload an xlsx file and verify the agent doesn't try mineru first
- **Manual**: Upload a pdf and verify mineru is still used for documents
- **Unit**: Test `build_system_prompt()` output includes the new rules
- **Unit**: Test the PreToolUse hook logic (Phase 3)

## Risks & Mitigations

- **Risk**: 系统提示词变长，消耗更多 token
  - Mitigation: 文件类型规则只有约 10 行，影响很小
- **Risk**: 新增文件类型后规则需要维护
  - Mitigation: 规则是通用的，不依赖具体 MCP 工具名
- **Risk**: PreToolUse hook 可能误拦截
  - Mitigation: Phase 3 是可选的，先做 Phase 1-2 看效果

## Success Criteria

- [ ] 上传 xlsx 文件后，agent 直接用 Python/pandas 读取，不再先尝试 mineru
- [ ] 上传 pdf 文件后，agent 仍然正确使用 mineru
- [ ] 系统提示词中包含文件类型选择规则
- [ ] MCP 服务器描述包含支持的文件格式说明
