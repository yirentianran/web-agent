# Claude Agent SDK TypeScript 源码分析报告

## 1. 项目概述

### 1.1 基本信息

| 项目属性 | 详情 |
|---------|------|
| **项目名称** | @anthropic-ai/claude-agent-sdk |
| **仓库地址** | https://github.com/anthropics/claude-agent-sdk-typescript |
| **包管理** | npm (原 Claude Code SDK) |
| **Node.js版本** | 18+ |
| **当前版本** | 0.2.101 |
| **语言** | TypeScript |
| **官方文档** | https://docs.claude.com/en/api/agent-sdk/overview |

### 1.2 核心定位

Claude Agent SDK 是 Anthropic 官方提供的 TypeScript/Python 库，用于**程序化构建 AI 代理**。它封装了 Claude Code CLI 的全部能力，使开发者可以：

- 创建自主代理理解代码库
- 编辑文件、运行命令
- 执行复杂工作流
- 构建生产级 AI 应用

### 1.3 技术栈架构图

```mermaid
graph TB
    subgraph SDK["Claude Agent SDK"]
        Core["Core API<br/>query()"]
        QueryInterface["Query Interface<br/>AsyncGenerator"]
        Options["Options<br/>ClaudeAgentOptions"]
    end
    
    subgraph Features["核心能力"]
        Tools["工具系统<br/>Built-in + MCP"]
        Subagents["子代理<br/>AgentDefinition"]
        Hooks["钩子系统<br/>Pre/Post/Stop"]
        Sessions["会话管理<br/>Resume/Fork"]
        Permissions["权限系统<br/>PermissionMode"]
    end
    
    subgraph Extensions["扩展机制"]
        MCP["MCP Servers<br/>createSdkMcpServer()"]
        CustomTools["自定义工具<br/>@tool decorator"]
        Plugins["插件系统<br/>SdkPluginConfig"]
    end
    
    subgraph Runtime["运行时"]
        ClaudeCLI["Claude Code CLI<br/>Subprocess"]
        AnthropicAPI["Anthropic API<br/>Streaming"]
    end
    
    Core --> QueryInterface
    Core --> Options
    Options --> Features
    Features --> Extensions
    Core --> Runtime
    Runtime --> AnthropicAPI
```

## 2. 核心架构设计

### 2.1 整体架构图

```mermaid
flowchart TB
    subgraph Entry["入口层"]
        Query["query()<br/>主入口函数"]
        V2Session["V2 Session API<br/>unstable_v2_*"]
        Startup["startup()<br/>预热进程"]
    end
    
    subgraph CoreInterface["核心接口层"]
        QueryInterface["Query Interface<br/>AsyncGenerator<SDKMessage>"]
        Methods["Query方法<br/>interrupt/close/setModel等"]
        Control["控制协议<br/>initialize/control"]
    end
    
    subgraph MessageTypes["消息类型"]
        Assistant["AssistantMessage<br/>助手响应"]
        User["UserMessage<br/>用户消息"]
        Result["ResultMessage<br/>执行结果"]
        System["SystemMessage<br/>系统消息"]
        ToolResult["ToolResultMessage<br/>工具结果"]
    end
    
    subgraph Configuration["配置层"]
        Options["ClaudeAgentOptions<br/>完整配置"]
        ToolsConfig["工具配置<br/>tools/allowedTools"]
        PromptConfig["提示配置<br/>systemPrompt"]
        MCPConfig["MCP配置<br/>mcpServers"]
    end
    
    subgraph Process["进程管理层"]
        Spawn["进程生成<br/>spawnClaudeCodeProcess"]
        Transport["传输层<br/>JSON-RPC/SSE"]
        Lifecycle["生命周期<br/>close/interrupt"]
    end
    
    Query --> QueryInterface
    QueryInterface --> Methods
    Methods --> Control
    Query --> Configuration
    Configuration --> Options
    Options --> ToolsConfig
    Options --> PromptConfig
    Options --> MCPConfig
    QueryInterface --> MessageTypes
    Query --> Process
    Process --> Spawn
    Spawn --> Transport
```

### 2.2 版本演进历程

```mermaid
timeline
    title Claude Agent SDK 版本演进
    section v0.1.x (2025)
        v0.1.0 : 合并prompt选项<br/>无默认系统提示
        v0.1.10 : Zod peer依赖
        v0.1.45 : 支持Azure Foundry<br/>结构化输出
        v0.1.51 : Opus 4.5支持
        v0.1.54 : V2 Session API实验
    section v0.2.x (2026)
        v0.2.0 : MCP错误字段<br/>与CLI v2.1.0同步
        v0.2.10 : 自定义代理skills/maxTurns
        v0.2.53 : listSessions()
        v0.2.59 : getSessionMessages()
        v0.2.76 : forkSession()<br/>MCP elicitation
        v0.2.89 : startup()预热<br/>subagent消息获取
        v0.2.101 : 安全更新<br/>依赖版本升级
```

## 3. Query 接口详解

### 3.1 Query Interface 定义

```mermaid
classDiagram
    class Query {
        +interrupt() Promise~void~
        +rewindFiles(userMessageId, options) Promise~RewindFilesResult~
        +setPermissionMode(mode) Promise~void~
        +setModel(model) Promise~void~
        +setMaxThinkingTokens(tokens) Promise~void~
        +initializationResult() Promise~SDKControlInitializeResponse~
        +supportedCommands() Promise~SlashCommand[]~
        +supportedModels() Promise~ModelInfo[]~
        +supportedAgents() Promise~AgentInfo[]~
        +mcpServerStatus() Promise~McpServerStatus[]~
        +accountInfo() Promise~AccountInfo~
        +reconnectMcpServer(name) Promise~void~
        +toggleMcpServer(name, enabled) Promise~void~
        +setMcpServers(servers) Promise~McpSetServersResult~
        +streamInput(stream) Promise~void~
        +stopTask(taskId) Promise~void~
        +close() void
    }
    
    class AsyncGenerator {
        +next() Promise~IteratorResult~
        +return() Promise~IteratorResult~
        +throw() Promise~IteratorResult~
    }
    
    Query --|> AsyncGenerator : extends
```

### 3.2 Query 方法分类

| 方法类别 | 方法名 | 功能 |
|---------|-------|------|
| **中断控制** | `interrupt()` | 强制中断当前查询 |
| | `close()` | 关闭查询并清理资源 |
| **文件管理** | `rewindFiles()` | 回滚文件到指定状态 |
| **模型配置** | `setModel()` | 动态切换模型 |
| | `setMaxThinkingTokens()` | 设置思考token上限 |
| **权限管理** | `setPermissionMode()` | 设置权限模式 |
| **能力查询** | `supportedCommands()` | 获取可用斜杠命令 |
| | `supportedModels()` | 获取可用模型列表 |
| | `supportedAgents()` | 获取可用子代理列表 |
| | `mcpServerStatus()` | 获取MCP服务器状态 |
| | `accountInfo()` | 获取账户信息 |
| **MCP管理** | `reconnectMcpServer()` | 重连MCP服务器 |
| | `toggleMcpServer()` | 启用/禁用MCP服务器 |
| | `setMcpServers()` | 动态设置MCP服务器 |
| **任务管理** | `stopTask()` | 停止运行中的任务 |
| | `streamInput()` | 流式输入用户消息 |

### 3.3 Query 执行流程

```mermaid
sequenceDiagram
    participant App as 应用程序
    participant Query as Query()
    participant Process as Claude CLI进程
    participant API as Anthropic API
    
    App->>Query: query({prompt, options})
    Query->>Process: spawn Claude Code subprocess
    Query->>Process: send initialize control
    Process-->>Query: SDKControlInitializeResponse
    loop Agentic Loop
        Query->>Process: send user message
        Process->>API: API request (streaming)
        API-->>Process: streaming response
        Process-->>Query: yield SDKMessage (assistant)
        alt Tool Call
            Process-->>Query: yield SDKMessage (tool_use)
            Query-->>App: tool_use message
            App->>Query: canUseTool callback (可选)
            Process->>Process: execute tool
            Process-->>Query: yield SDKMessage (tool_result)
        end
    end
    Process-->>Query: yield ResultMessage
    Query-->>App: result message
    App->>Query: close()
    Query->>Process: terminate subprocess
```

## 4. 配置系统详解

### 4.1 ClaudeAgentOptions 配置结构

```mermaid
flowchart LR
    subgraph ClaudeAgentOptions["ClaudeAgentOptions"]
        subgraph Tools["工具配置"]
            A["tools<br/>string[] | preset"]
            B["allowedTools<br/>预批准工具"]
            C["disallowedTools<br/>禁止工具"]
        end
        
        subgraph Prompt["提示配置"]
            D["systemPrompt<br/>string | preset"]
            E["append<br/>追加提示"]
        end
        
        subgraph MCP["MCP配置"]
            F["mcpServers<br/>服务器定义"]
            G["sdkMcpServers<br/>SDK内联服务器"]
        end
        
        subgraph Execution["执行配置"]
            H["model<br/>模型选择"]
            I["maxTurns<br/>最大轮次"]
            J["maxBudgetUsd<br/>预算限制"]
            K["thinking<br/>思考配置"]
            L["effort<br/>努力级别"]
        end
        
        subgraph Session["会话配置"]
            M["resume<br/>会话ID"]
            N["forkSession<br/>分支会话"]
            O["continueConversation<br/>继续对话"]
        end
        
        subgraph Permission["权限配置"]
            P["permissionMode<br/>权限模式"]
            Q["canUseTool<br/>权限回调"]
        end
        
        subgraph Environment["环境配置"]
            R["cwd<br/>工作目录"]
            S["env<br/>环境变量"]
            T["settingSources<br/>设置来源"]
        end
        
        subgraph Hooks["钩子配置"]
            U["hooks<br/>事件钩子"]
        end
        
        subgraph Agents["代理配置"]
            V["agents<br/>子代理定义"]
        end
    end
```

### 4.2 配置参数详解

#### 工具配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `tools` | `string[] \| {type:'preset', preset:'claude_code'}` | undefined | 可用工具列表或预设 |
| `allowedTools` | `string[]` | [] | 预批准工具（无需权限提示） |
| `disallowedTools` | `string[]` | [] | 禁止使用的工具 |

#### 系统提示配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `systemPrompt` | `string \| {type:'preset', preset:'claude_code', append?:string}` | undefined | 自定义提示或预设 |
| - | `{type:'preset', preset:'claude_code'}` | - | 使用Claude Code默认提示 |
| - | `{..., append:'额外指令'}` | - | 扩展默认提示 |

#### MCP服务器配置

| 参数 | 类型 | 说明 |
|------|------|------|
| `mcpServers` | `Record<string, McpServerConfig>` | MCP服务器配置字典 |
| `McpServerConfig.type` | `'stdio' \| 'sse' \| 'http'` | 传输类型 |
| `McpServerConfig.command` | `string` | stdio模式命令 |
| `McpServerConfig.url` | `string` | http/sse模式URL |

#### 执行约束配置

| 参数 | 类型 | 说明 |
|------|------|------|
| `maxTurns` | `number` | 最大代理轮次（API往返次数） |
| `maxBudgetUsd` | `number` | 最大预算（美元） |
| `taskBudget` | `{total: number}` | API侧token预算 |
| `thinking` | `ThinkingConfig` | 思考模式配置 |
| `effort` | `'low' \| 'medium' \| 'high' \| 'max'` | 执行努力级别 |

## 5. 工具系统架构

### 5.1 内置工具列表

```mermaid
flowchart TB
    subgraph FileTools["文件操作工具"]
        Read["Read<br/>读取文件/图片/PDF"]
        Edit["Edit<br/>字符串替换编辑"]
        Write["Write<br/>文件创建/覆写"]
        Glob["Glob<br/>文件模式匹配"]
        Grep["Grep<br/>内容搜索"]
    end
    
    subgraph ExecutionTools["执行工具"]
        Bash["Bash<br/>Shell命令执行"]
        NotebookEdit["NotebookEdit<br/>Jupyter笔记本编辑"]
    end
    
    subgraph WebTools["Web工具"]
        WebFetch["WebFetch<br/>URL内容获取"]
        WebSearch["WebSearch<br/>Web搜索"]
    end
    
    subgraph AgentTools["代理工具"]
        Agent["Agent<br/>子代理调用"]
        Skill["Skill<br/>技能执行"]
    end
    
    subgraph InteractionTools["交互工具"]
        AskUserQuestion["AskUserQuestion<br/>用户提问"]
    end
    
    subgraph PlanningTools["规划工具"]
        EnterPlanMode["EnterPlanMode<br/>进入规划模式"]
        ExitPlanMode["ExitPlanMode<br/>退出规划模式"]
    end
    
    subgraph SessionTools["会话工具"]
        TaskCreate["TaskCreate<br/>创建任务"]
        TaskUpdate["TaskUpdate<br/>更新任务"]
        TaskList["TaskList<br/>任务列表"]
        EnterWorktree["EnterWorktree<br/>Worktree隔离"]
    end
```

### 5.2 自定义工具创建流程

```mermaid
flowchart LR
    subgraph Definition["工具定义"]
        A["@tool装饰器<br/>name, description, schema"] --> B["handler函数<br/>async执行逻辑"]
    end
    
    subgraph ServerCreation["服务器创建"]
        B --> C["createSdkMcpServer()<br/>name, version, tools"]
        C --> D["McpSdkServerConfig"]
    end
    
    subgraph Registration["注册使用"]
        D --> E["mcpServers选项<br/>{'serverName': config}"]
        E --> F["allowedTools<br/>['mcp__serverName__toolName']"]
        F --> G["query()调用"]
    end
```

### 5.3 自定义工具代码示例

```typescript
import { tool, createSdkMcpServer } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";

// 定义工具：名称、描述、输入Schema、处理函数
const getTemperature = tool(
  "get_temperature",
  "Get the current temperature at a location",
  {
    latitude: z.number().describe("Latitude coordinate"),
    longitude: z.number().describe("Longitude coordinate")
  },
  async (args) => {
    // args 类型从 schema 推断: { latitude: number; longitude: number }
    const response = await fetch(
      `https://api.open-meteo.com/v1/forecast?` +
      `latitude=${args.latitude}&longitude=${args.longitude}` +
      `&current=temperature_2m&temperature_unit=fahrenheit`
    );
    const data = await response.json();
    
    // 返回 content 数组 - Claude 看到的是工具结果
    return {
      content: [
        { type: "text", text: `Temperature: ${data.current.temperature_2m}°F` }
      ]
    };
  }
);

// 包装成 MCP 服务器
const weatherServer = createSdkMcpServer({
  name: "weather",
  version: "1.0.0",
  tools: [getTemperature]
});

// 在 query 中使用
for await (const message of query({
  prompt: "What's the temperature in San Francisco?",
  options: {
    mcpServers: { weather: weatherServer },
    allowedTools: ["mcp__weather__get_temperature"]
  }
})) {
  if ("result" in message) console.log(message.result);
}
```

## 6. 子代理系统

### 6.1 子代理架构图

```mermaid
flowchart TB
    subgraph MainAgent["主代理"]
        Query["query()"]
        Options["agents选项"]
    end
    
    subgraph SubagentDefinitions["子代理定义"]
        AgentDef["AgentDefinition<br/>description, prompt, tools"]
        Agent1["code-reviewer<br/>代码审查"]
        Agent2["test-runner<br/>测试执行"]
        Agent3["security-scanner<br/>安全扫描"]
    end
    
    subgraph AgentTool["Agent Tool调用"]
        Call["Agent工具调用"]
        Prompt["prompt传递"]
        Model["model选择"]
        Isolation["isolation模式"]
    end
    
    subgraph SubagentExecution["子代理执行"]
        Spawn["生成子进程"]
        Context["独立上下文"]
        Tools["受限工具集"]
        Result["返回结果"]
    end
    
    Query --> Options
    Options --> AgentDef
    AgentDef --> Agent1
    AgentDef --> Agent2
    AgentDef --> Agent3
    Query --> Call
    Call --> Prompt
    Call --> Model
    Call --> Isolation
    Call --> Spawn
    Spawn --> Context
    Context --> Tools
    Tools --> Result
    Result --> Query
```

### 6.2 AgentDefinition 类型定义

```typescript
type AgentDefinition = {
  description: string;              // 必需：何时使用此代理的描述
  prompt: string;                   // 必需：代理的系统提示
  tools?: string[];                 // 可选：允许的工具列表
  disallowedTools?: string[];       // 可选：禁止的工具列表
  model?: "sonnet" | "opus" | "haiku" | "inherit";  // 可选：模型选择
  mcpServers?: AgentMcpServerSpec[]; // 可选：MCP服务器
  skills?: string[];                // 可选：预加载技能
  maxTurns?: number;                // 可选：最大轮次
  criticalSystemReminder_EXPERIMENTAL?: string;  // 实验：关键提醒
};
```

### 6.3 子代理使用示例

```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
  prompt: "Review the authentication module for security issues",
  options: {
    allowedTools: ["Read", "Grep", "Glob", "Agent"],
    agents: {
      "code-reviewer": {
        description: "Expert code review specialist.",
        prompt: `You are a code review specialist with expertise in security.
                 Identify security vulnerabilities.
                 Check for performance issues.
                 Suggest specific improvements.`,
        tools: ["Read", "Grep", "Glob"],  // 只读工具
        model: "sonnet"
      },
      "test-runner": {
        description: "Runs and analyzes test suites.",
        prompt: `You are a test execution specialist.
                 Run tests and analyze results.`,
        tools: ["Bash", "Read", "Grep"],  // 可执行命令
        model: "haiku"  // 使用更快模型
      }
    }
  }
})) {
  if ("result" in message) console.log(message.result);
}
```

## 7. 钩子系统

### 7.1 钩子事件类型

```mermaid
flowchart LR
    subgraph ToolHooks["工具钩子"]
        PreToolUse["PreToolUse<br/>工具执行前"]
        PostToolUse["PostToolUse<br/>工具执行后"]
        PostToolUseFailure["PostToolUseFailure<br/>工具失败"]
    end
    
    subgraph SessionHooks["会话钩子"]
        Stop["Stop<br/>会话停止"]
        Notification["Notification<br/>通知事件"]
        ConfigChange["ConfigChange<br/>配置变更"]
    end
    
    subgraph PromptHooks["提示钩子"]
        UserPromptSubmit["UserPromptSubmit<br/>用户提交"]
    end
    
    subgraph TaskHooks["任务钩子"]
        TaskCompleted["TaskCompleted<br/>任务完成"]
        TeammateIdle["TeammateIdle<br/>队友空闲"]
    end
```

### 7.2 钩子配置结构

```typescript
type HooksConfig = {
  [hookEventName: string]: HookMatcher[];
};

type HookMatcher = {
  matcher?: string;     // 正则模式过滤工具名
  hooks: HookCallback[]; // 回调函数数组
  timeout?: number;     // 超时秒数
};

type HookCallback = (
  input: HookInput,
  toolUseId: string | null,
  context: HookContext
) => Promise<HookOutput>;
```

### 7.3 PreToolUse 钩子流程

```mermaid
sequenceDiagram
    participant Agent as 代理
    participant Hook as PreToolUse Hook
    participant Tool as 工具执行
    
    Agent->>Hook: 触发 PreToolUse
    Hook->>Hook: 检查 matcher 匹配
    Hook->>Hook: 执行 callback
    
    alt Hook返回 deny
        Hook-->>Agent: permissionDecision: "deny"
        Agent-->>Tool: 阻止工具执行
    else Hook返回 allow
        Hook-->>Agent: permissionDecision: "allow"
        Agent->>Tool: 执行工具
    else Hook返回 ask
        Hook-->>Agent: permissionDecision: "ask"
        Agent->>Agent: 显示权限对话框
    else Hook无决策
        Agent->>Tool: 按默认权限执行
    end
```

### 7.4 钩子使用示例

```typescript
for await (const message of query({
  prompt: "List files in current directory",
  options: {
    allowedTools: ["Read", "Write", "Bash"],
    hooks: {
      PreToolUse: [
        {
          matcher: "Bash",
          hooks: [async (input) => {
            const toolName = input.tool_name;
            const toolInput = input.tool_input;
            
            // 阻止危险命令
            if (toolInput?.command?.includes("rm -rf")) {
              return {
                hookSpecificOutput: {
                  hookEventName: "PreToolUse",
                  permissionDecision: "deny",
                  permissionDecisionReason: "Dangerous command blocked"
                }
              };
            }
            return {};
          }]
        }
      ],
      PostToolUse: [
        {
          hooks: [async (input) => {
            console.log(`[POST] Tool ${input.tool_name} completed`);
            return {};
          }]
        }
      ]
    }
  }
})) {
  console.log(message);
}
```

## 8. 会话管理系统

### 8.1 会话生命周期

```mermaid
stateDiagram-v2
    [*] --> Created: query() 调用
    Created --> Running: 发送用户消息
    Running --> ToolExecution: 工具调用
    ToolExecution --> Running: 工具完成
    Running --> Completed: 结果返回
    Running --> Interrupted: interrupt() 调用
    Completed --> [*]: close()
    Interrupted --> [*]: close()
    
    Completed --> Resumed: resume(sessionId)
    Resumed --> Running: 继续对话
    
    Completed --> Forked: forkSession=true
    Forked --> Running: 新会话分支
```

### 8.2 会话管理API

```mermaid
flowchart TB
    subgraph SessionAPIs["会话管理API"]
        listSessions["listSessions()<br/>列出历史会话"]
        getSessionInfo["getSessionInfo(id)<br/>获取会话信息"]
        getSessionMessages["getSessionMessages(id)<br/>获取会话消息"]
        renameSession["renameSession(id, title)<br/>重命名会话"]
        tagSession["tagSession(id, tag)<br/>标记会话"]
        forkSession["forkSession(id)<br/>分支会话"]
        resumeSession["resume: sessionId<br/>恢复会话"]
    end
    
    subgraph SessionData["会话数据"]
        sessionId["session_id<br/>UUID"]
        title["title<br/>会话标题"]
        tag["tag<br/>标记"]
        createdAt["createdAt<br/>创建时间"]
        messages["messages<br/>消息列表"]
    end
```

### 8.3 会话恢复与分支示例

```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";

let sessionId: string | undefined;

// 第一次查询：捕获 session ID
for await (const message of query({
  prompt: "Read the authentication module",
  options: { allowedTools: ["Read", "Glob"] }
})) {
  if (message.type === "system" && message.subtype === "init") {
    sessionId = message.session_id;
  }
}

// 恢复会话：保持完整上下文
for await (const message of query({
  prompt: "Now find all places that call it",
  options: { resume: sessionId }
})) {
  if ("result" in message) console.log(message.result);
}

// 分支会话：探索不同方案
let forkedId: string | undefined;
for await (const message of query({
  prompt: "Instead of JWT, implement OAuth2",
  options: { resume: sessionId, forkSession: true }
})) {
  if (message.type === "system" && message.subtype === "init") {
    forkedId = message.session_id;
  }
}

// 原会话不受影响，继续JWT方案
for await (const message of query({
  prompt: "Continue with the JWT approach",
  options: { resume: sessionId }
})) {
  if ("result" in message) console.log(message.result);
}
```

## 9. 权限系统

### 9.1 权限模式

```mermaid
flowchart LR
    subgraph Modes["权限模式"]
        default["default<br/>默认交互模式"]
        dontAsk["dontAsk<br/>拒绝未批准工具"]
        acceptEdits["acceptEdits<br/>自动批准编辑"]
        bypassPermissions["bypassPermissions<br/>绕过所有权限"]
        plan["plan<br/>规划模式"]
        auto["auto<br/>自动模式"]
    end
```

### 9.2 权限模式说明

| 模式 | 行为 |
|------|------|
| `default` | 未批准工具显示权限对话框 |
| `dontAsk` | 未批准工具自动拒绝（适合CI/CD） |
| `acceptEdits` | 自动批准 Edit/Write 操作 |
| `bypassPermissions` | 绕过所有权限检查（仅限信任环境） |
| `plan` | 进入规划模式 |
| `auto` | 基于规则自动决策 |

### 9.3 canUseTool 回调

```mermaid
sequenceDiagram
    participant Agent as 代理
    participant Callback as canUseTool回调
    participant Dialog as 权限对话框
    
    Agent->>Callback: 工具调用请求
    Callback->>Callback: 检查规则
    
    alt 回调返回 allow
        Callback-->>Agent: {behavior: "allow"}
        Agent->>Agent: 执行工具
    else 回调返回 deny
        Callback-->>Agent: {behavior: "deny"}
        Agent-->>Agent: 拒绝执行
    else 回调返回 ask
        Callback-->>Agent: {behavior: "ask"}
        Agent->>Dialog: 显示权限对话框
        Dialog-->>Agent: 用户决策
    end
```

## 10. MCP集成

### 10.1 MCP服务器配置类型

```mermaid
flowchart TB
    subgraph McpServerTypes["MCP服务器类型"]
        stdio["stdio<br/>进程通信"]
        sse["sse<br/>Server-Sent Events"]
        http["http<br/>HTTP请求"]
        sdk["sdk<br/>内联SDK服务器"]
    end
    
    subgraph stdioConfig["stdio配置"]
        sCommand["command<br/>命令路径"]
        sArgs["args<br/>命令参数"]
        sEnv["env<br/>环境变量"]
    end
    
    subgraph httpConfig["http/sse配置"]
        hUrl["url<br/>服务器URL"]
        hHeaders["headers<br/>请求头"]
        hTransport["transport<br/>传输类型"]
    end
    
    subgraph sdkConfig["SDK内联配置"]
        sdkTools["tools<br/>工具列表"]
        sdkName["name<br/>服务器名"]
        sdkVersion["version<br/>版本号"]
    end
```

### 10.2 MCP服务器状态管理

| 方法 | 功能 |
|------|------|
| `mcpServerStatus()` | 获取所有MCP服务器状态 |
| `reconnectMcpServer(name)` | 重连指定服务器 |
| `toggleMcpServer(name, enabled)` | 启用/禁用服务器 |
| `setMcpServers(servers)` | 动态设置服务器配置 |
| `enableChannel()` | 激活MCP通道 |

### 10.3 MCP工具命名规则

```typescript
// MCP工具命名格式: mcp__<serverName>__<toolName>
// 示例:
allowedTools: [
  "mcp__weather__get_temperature",
  "mcp__utils__calculate",
  "mcp__enterprise-tools__*"  // 通配符批准所有工具
]
```

## 11. 消息类型系统

### 11.1 SDK消息类型层次

```mermaid
classDiagram
    class SDKMessage {
        +type: string
        +session_id: string
    }
    
    class AssistantMessage {
        +type: "assistant"
        +message: Message
        +content: ContentBlock[]
    }
    
    class UserMessage {
        +type: "user"
        +message: Message
        +content: ContentBlock[]
    }
    
    class ResultMessage {
        +type: "result"
        +subtype: "success" | "error"
        +result: string
        +stop_reason: string
        +terminal_reason: string
    }
    
    class SystemMessage {
        +type: "system"
        +subtype: string
        +data: object
    }
    
    class ToolResultMessage {
        +type: "tool_result"
        +tool_use_id: string
        +content: ContentBlock[]
    }
    
    SDKMessage <|-- AssistantMessage
    SDKMessage <|-- UserMessage
    SDKMessage <|-- ResultMessage
    SDKMessage <|-- SystemMessage
    SDKMessage <|-- ToolResultMessage
```

### 11.2 SystemMessage子类型

| subtype | 说明 |
|---------|------|
| `init` | 会话初始化（包含session_id） |
| `compact_boundary` | 上下文压缩边界 |
| `task_progress` | 任务进度更新 |
| `task_notification` | 任务完成通知 |
| `hook_progress` | Hook执行进度 |
| `hook_response` | Hook响应 |
| `api_retry` | API重试事件 |
| `session_state_changed` | 会话状态变更 |
| `mcp_connection` | MCP连接事件 |

## 12. 设计模式总结

### 12.1 核心设计模式

```mermaid
mindmap
  root((设计模式))
    流式处理
      AsyncGenerator
      for-await-of
      实时消息流
    进程隔离
      CLI子进程
      JSON-RPC通信
      状态同步
    配置驱动
      ClaudeAgentOptions
      预设模式
      细粒度控制
    扩展机制
      MCP协议
      自定义工具
      钩子拦截
    代理循环
      Tool-Call Loop
      权限检查
      流式响应
    会话管理
      Resume恢复
      Fork分支
      历史查询
```

### 12.2 SDK vs CLI 对比

| 特性 | Claude Code CLI | Claude Agent SDK |
|------|----------------|------------------|
| **交互方式** | 命令行交互 | 程序化API |
| **消息处理** | 终端渲染 | 流式消息 |
| **权限控制** | 用户对话框 | canUseTool回调 |
| **配置方式** | CLI参数/配置文件 | ClaudeAgentOptions |
| **自定义工具** | MCP配置文件 | createSdkMcpServer() |
| **会话管理** | --resume参数 | resume/forkSession选项 |
| **适用场景** | 交互式开发 | 自动化/生产应用 |

## 13. 使用最佳实践

### 13.1 基础使用模式

```mermaid
flowchart TB
    subgraph Basic["基础模式"]
        A["安装SDK"] --> B["导入query"]
        B --> C["配置options"]
        C --> D["for-await消息"]
        D --> E["处理结果"]
    end
    
    subgraph Advanced["高级模式"]
        F["预热startup()"] --> G["创建子代理"]
        G --> H["配置hooks"]
        H --> I["MCP服务器"]
        I --> J["会话管理"]
    end
    
    subgraph Production["生产模式"]
        K["权限控制"] --> L["预算限制"]
        L --> M["错误处理"]
        M --> N["日志记录"]
    end
```

### 13.2 性能优化建议

| 建议 | 方法 |
|------|------|
| **预热进程** | 使用 `startup()` 提前初始化 |
| **工具筛选** | 使用 `tools` 限制可用工具 |
| **预批准** | 使用 `allowedTools` 减少权限提示 |
| **预算控制** | 使用 `maxBudgetUsd` 和 `taskBudget` |
| **模型选择** | 子代理使用 `haiku` 加速简单任务 |

### 13.3 安全最佳实践

| 建议 | 方法 |
|------|------|
| **权限限制** | 使用 `permissionMode: "dontAsk"` |
| **工具白名单** | 明确指定 `allowedTools` |
| **钩子拦截** | PreToolUse hook 阻止危险操作 |
| **沙箱执行** | 配置 `sandbox` 选项 |
| **设置隔离** | 不加载用户设置 `settingSources: []` |

---

## 附录：Mermaid图表索引

1. **技术栈架构图** - SDK整体技术层次
2. **整体架构图** - 核心模块关系
3. **版本演进时间线** - SDK发展历程
4. **Query Interface类图** - Query方法定义
5. **Query执行流程** - SequenceDiagram展示调用
6. **ClaudeAgentOptions配置结构** - Flowchart展示配置
7. **内置工具列表** - 工具分类图
8. **自定义工具创建流程** - 工具定义步骤
9. **子代理架构图** - 代理系统结构
10. **钩子事件类型** - Hook分类
11. **PreToolUse钩子流程** - 权限拦截SequenceDiagram
12. **会话生命周期** - StateDiagram状态转换
13. **会话管理API** - API功能图
14. **权限模式** - PermissionMode分类
15. **canUseTool回调流程** - 权限决策SequenceDiagram
16. **MCP服务器配置类型** - MCP配置分类
17. **SDK消息类型层次** - ClassDiagram消息类型
18. **核心设计模式思维导图** - Mindmap设计模式
19. **使用模式流程** - 最佳实践流程

---

*报告生成日期: 2026-04-11*
*分析版本: Claude Agent SDK v0.2.101*
*文档来源: GitHub仓库 + Context7官方文档*