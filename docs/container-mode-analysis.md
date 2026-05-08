# 用户容器模式分析与实现方案

## 一、当前架构分析

### 1.1 两种运行模式

项目通过 `CONTAINER_MODE` 环境变量（默认 `false`）控制两种执行模式：

**非容器模式（Phase 1）：**
- `main_server.py` 进程直接运行 Claude Agent SDK
- SDK 在宿主机上启动 `claude` CLI 子进程
- 所有文件在宿主机路径直接操作

**容器模式（Phase 2）：**
- 每个用户分配一个独立的 Docker 容器，运行 `agent_server.py`
- 主服务器作为 WebSocket 桥接，将浏览器消息转发到用户容器
- 用户容器通过 bind mount 访问宿主机上的数据文件

### 1.1.1 架构选择：Docker-out-of-Docker (DooD)

web-agent 的 CONTAINER_MODE 采用 **Docker-out-of-Docker** 架构，而非嵌套 Docker-in-Docker。

#### 核心机制

```yaml
# docker-compose.yml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

将宿主机 Docker daemon socket 挂入 web-agent 容器。main_server 通过 `docker.from_env()` 直接与宿主机 Docker daemon 通信，创建的用户容器是宿主机的**兄弟容器**（sibling container），而非嵌套在 web-agent 容器内部。

#### 进程结构

```
宿主机
├── Docker Daemon (通过 /var/run/docker.sock)
│   ├── web-agent 容器         ← docker-compose 启动
│   ├── web-agent-yguo 容器    ← main_server 通过 socket 创建
│   ├── web-agent-xiangyan     ← 同上
│   └── ...
```

#### 为什么不使用 Docker-in-Docker (DinD)

| 对比维度 | DooD (当前方案) | DinD (嵌套 Docker) |
|---------|----------------|---------------------|
| 复杂度 | 低，只需挂载一个 socket | 高，需要在容器内运行完整 Docker daemon |
| 性能 | 直接共享宿主机内核，无额外开销 | 多一层虚拟化开销 |
| 存储 | 用户容器镜像复用，不用重复拉取 | 需要独立镜像存储层 |
| 文件共享 | 宿主机路径直接绑定挂载 | 路径映射复杂，多层嵌套 |
| 安全 | 需 socket 访问权限 | 也需 privileged 模式 |

#### 路径映射要点

Docker daemon 在宿主机上解析卷挂载源路径，因此容器内代码需要知道宿主机的真实路径。这就是 `HOST_DATA_ROOT` 存在的原因：

```python
# src/container_manager.py
_HOST_DATA_ROOT = os.getenv("HOST_DATA_ROOT")
if _HOST_DATA_ROOT:
    HOST_DATA_ROOT = Path(_HOST_DATA_ROOT)  # docker-compose 部署：显式宿主机路径
else:
    HOST_DATA_ROOT = DATA_ROOT.resolve()     # 本地开发：main_server 直接在宿主机运行
```

#### 数据库一致性

两种部署模式操作的是同一个 SQLite 数据库文件：

| 模式 | 数据库路径来源 | 实际文件 |
|------|-------------|---------|
| docker-compose | `.env.docker` → `DATA_DB_PATH=/data/web-agent.db` | 通过 `./data:/data` 卷挂载 → 宿主机 `./data/web-agent.db` |
| 本地 uvicorn | `.env` → `DATA_DB_PATH=./data/web-agent.db` | 直接读写宿主机 `./data/web-agent.db` |

> macOS Docker 通过 Linux VM 运行，bind mount 跨文件系统，inode 必然不同，不代表是不同文件。

### 1.2 模式检测与路由

`main_server.py` 第 267-285 行：

```python
CONTAINER_MODE = os.getenv("CONTAINER_MODE", "false").lower() == "true"

def _get_container_manager():
    """惰性导入 container_manager 模块"""
    try:
        import src.container_manager as cm
        return cm
    except ImportError:
        logger.warning("docker-py not installed; container mode disabled")
        return None
```

第 2831-2832 行，WebSocket 消息路由：

```python
target_func = run_agent_task_container if CONTAINER_MODE else run_agent_task
```

### 1.3 数据流（容器模式）

```
Browser ──WebSocket──► main_server.py ──WebSocket──► 用户容器 (agent_server.py)
                             │                              │
                        消息缓冲区                    Claude SDK → Anthropic API
                             │
                      订阅循环推送 ──WebSocket──► Browser（实时更新）
```

### 1.4 容器生命周期

`src/container_manager.py` (296行) 负责：

| 功能 | 函数 | 说明 |
|------|------|------|
| 创建/恢复容器 | `ensure_container()` | 镜像 `web-agent-user:latest`，4GB内存，1CPU |
| 暂停容器 | `pause_container()` | 冻结进程，保留内存 |
| 停止容器 | `stop_container()` | 优雅停止（30秒超时） |
| 销毁容器 | `destroy_container()` | 强制删除 |
| 空闲监控 | `start_idle_monitor()` | 每60秒检查，默认30分钟TTL |

### 1.5 目录结构

```
{data_root}/                           # 数据根目录
├── web-agent.db                       # SQLite 数据库
├── shared-skills/                     # 共享技能库
└── users/
    └── {user_id}/
        ├── workspace/                 # 用户工作空间
        │   ├── uploads/               # 上传文件
        │   ├── outputs/               # 生成的文件
        │   └── reports/               # 报告
        ├── .claude/                   # Claude 数据（会话、记忆、设置）
        │   └── memory/
        ├── skills/                    # 用户个人技能
        └── logs/                      # 容器日志
```

### 1.6 路径环境变量

| 变量 | 非容器模式 | 容器模式（主服务器在Docker中） |
|------|-----------|------------------------------|
| `DATA_ROOT` | `./data`（相对于项目根目录） | `/data`（容器内路径） |
| 工作空间 | `./data/users/{uid}/workspace` | `/data/users/{uid}/workspace`（主服务器内） |
| `CONTAINER_MODE` | `false` | `true` |

## 二、核心问题：路径不匹配

### 2.1 问题分析

当主服务器运行在 Docker 中（通过 `docker-compose.yml`）且 `CONTAINER_MODE=true` 时：

1. **docker-compose.yml** 挂载：`./data:/data` → 宿主机 `./data` 映射到主服务器容器的 `/data`
2. **container_manager.py** `get_user_volumes()` 使用 `DATA_ROOT`（值为 `/data`）构建卷源路径
3. **Docker Socket** 解析卷路径时，是在 **Docker 宿主机** 上解析，而非主服务器容器内

### 2.2 路径解析示例

当前代码（`container_manager.py` 第 68-104 行）：

```python
def get_user_volumes(user_id: str) -> dict[str, dict[str, str]]:
    base = user_data_dir(user_id)        # /data/users/alice
    root = DATA_ROOT.resolve()           # /data
    return {
        str(base.resolve() / "workspace"): {   # 源：/data/users/alice/workspace
            "bind": "/workspace",              # 目标：/workspace
            "mode": "rw",
        },
        # ...
    }
```

卷源路径 `/data/users/alice/workspace` 被 Docker daemon 在**宿主机**上解析：
- 宿主机路径：`/data/users/alice/workspace`（根目录下的 /data，不是项目的 ./data）
- 期望路径：`/home/ubuntu/web-agent/data/users/alice/workspace`（项目目录下的 data）

### 2.3 路径对应关系

| 层次 | 非容器模式路径 | 容器模式路径（当前） | 容器模式路径（期望） |
|------|--------------|-------------------|-------------------|
| 宿主机 | `./data/users/alice/workspace/` | `./data/users/alice/workspace/` ✓ | 同左 |
| 主服务器容器内 | — | `/data/users/alice/workspace/` | 同左 |
| 用户容器内 | — | `/workspace/` ✗ | `{HOST_DATA_ROOT}/users/alice/workspace/` |
| 文件最终宿主机位置 | `./data/users/alice/workspace/` | 取决于Docker宿主机配置 ⚠️ | `{HOST_DATA_ROOT}/users/alice/workspace/` |

## 三、解决方案

### 3.1 核心思路

引入 `HOST_DATA_ROOT` 环境变量，指定**Docker宿主机上**数据目录的绝对路径。

卷挂载策略变更：
- **之前**：`{DATA_ROOT}/users/{uid}/workspace` → `/workspace`（路径不同）
- **之后**：`{HOST_DATA_ROOT}/users/{uid}/workspace` → `{HOST_DATA_ROOT}/users/{uid}/workspace`（路径相同）

### 3.2 HOST_DATA_ROOT 约定

```python
_HOST_DATA_ROOT = os.getenv("HOST_DATA_ROOT")
if _HOST_DATA_ROOT:
    HOST_DATA_ROOT = Path(_HOST_DATA_ROOT)   # 显式设置（Docker部署）
else:
    HOST_DATA_ROOT = DATA_ROOT.resolve()     # 默认（本地开发）
```

- **本地开发**（主服务器在宿主机运行）：不设置 `HOST_DATA_ROOT`，自动退化为 `DATA_ROOT.resolve()`
- **Docker部署**（主服务器在容器中）：显式设置，如 `HOST_DATA_ROOT=/home/ubuntu/web-agent/data`

### 3.3 卷挂载变更对照

**变更前：**
| 宿主机路径 | 容器内路径 | 模式 |
|-----------|-----------|------|
| `{data}/shared-skills` | `/home/agent/.claude/shared-skills` | ro |
| `{data}/users/{uid}/skills` | `/home/agent/.claude/personal-skills` | rw |
| `{data}/users/{uid}/workspace` | `/workspace` | rw |
| `{data}/users/{uid}/.claude` | `/home/agent/.claude` | rw |
| `{project}/src/hooks` | `/hooks` | ro |
| `{data}/users/{uid}/logs` | `/app/logs` | rw |

**变更后：**
| 宿主机路径 | 容器内路径 | 模式 |
|-----------|-----------|------|
| `{HOST}/shared-skills` | `{HOST}/shared-skills` | ro |
| `{HOST}/users/{uid}/skills` | `{HOST}/users/{uid}/skills` | rw |
| `{HOST}/users/{uid}/workspace` | `{HOST}/users/{uid}/workspace` | rw |
| `{HOST}/users/{uid}/.claude` | `{HOST}/users/{uid}/.claude` | rw |
| `{project}/src/hooks` | `/hooks` | ro |
| `{HOST}/users/{uid}/logs` | `{HOST}/users/{uid}/logs` | rw |

> `{HOST}` = `HOST_DATA_ROOT`

## 四、详细实现计划

### 4.1 修改文件清单

| 文件 | 修改内容 | 优先级 |
|------|---------|--------|
| `src/container_manager.py` | 添加 HOST_DATA_ROOT、新辅助函数、更新卷和环境变量 | 🔴 核心 |
| `main_server.py` | 更新 `build_container_options_dict()` 的 cwd 和 skills_dirs | 🔴 核心 |
| `src/workspace_enforcement.py` | 更新 bash 命令检查，允许 `/home/` 路径在 user_dir 内的写入 | 🟡 配套 |
| `agent_server.py` | 验证新路径下的正确性，添加文档注释 | 🟡 配套 |
| `docker-compose.yml` | 添加 `HOST_DATA_ROOT` 环境变量 | 🟡 配置 |
| `.env.docker` | 添加 `HOST_DATA_ROOT` 配置文档 | 🟡 配置 |
| `Dockerfile.user` | 移除硬编码目录创建 | 🟢 优化 |
| `tests/unit/test_container_manager.py` | 更新测试用例 | 🟢 测试 |

### 4.2 详细变更

#### 4.2.1 container_manager.py

**A. 添加 HOST_DATA_ROOT 解析（第 27 行后新增）：**

```python
# 宿主机数据目录的绝对路径
# 当主服务器在 Docker 中运行时，此路径必须指向 Docker 宿主机上的数据目录
_HOST_DATA_ROOT = os.getenv("HOST_DATA_ROOT")
if _HOST_DATA_ROOT:
    HOST_DATA_ROOT = Path(_HOST_DATA_ROOT)
else:
    HOST_DATA_ROOT = DATA_ROOT.resolve()  # 本地开发：DATA_ROOT 就是宿主机路径
```

**B. 添加容器内路径辅助函数（第 49 行后新增）：**

```python
def container_user_dir(user_id: str) -> Path:
    """用户容器内的用户数据目录绝对路径。
    使用 HOST_DATA_ROOT，使得容器内路径与宿主机路径一致。
    """
    return HOST_DATA_ROOT / "users" / user_id


def container_workspace_dir(user_id: str) -> Path:
    """用户容器内的工作空间目录绝对路径。"""
    return container_user_dir(user_id) / "workspace"
```

**C. 重写 `get_user_volumes()`（替换第 68-104 行）：**

```python
def get_user_volumes(user_id: str) -> dict[str, dict[str, str]]:
    """返回用户容器的 Docker 卷绑定配置。
    源路径和目标路径都使用 HOST_DATA_ROOT，确保容器内外路径一致。
    """
    base = container_user_dir(user_id)
    hooks_dir = (Path(__file__).parent / "hooks").resolve()
    return {
        # 共享技能 — 只读
        str(HOST_DATA_ROOT / "shared-skills"): {
            "bind": str(HOST_DATA_ROOT / "shared-skills"),
            "mode": "ro",
        },
        # 个人技能 — 读写
        str(base / "skills"): {
            "bind": str(base / "skills"),
            "mode": "rw",
        },
        # 工作空间
        str(base / "workspace"): {
            "bind": str(base / "workspace"),
            "mode": "rw",
        },
        # Claude 数据（会话、设置、记忆）
        str(base / ".claude"): {
            "bind": str(base / ".claude"),
            "mode": "rw",
        },
        # Hook 脚本（保持 /hooks 路径不变 — 属于基础设施，非用户数据）
        str(hooks_dir): {
            "bind": "/hooks",
            "mode": "ro",
        },
        # 容器日志
        str(base / "logs"): {
            "bind": str(base / "logs"),
            "mode": "rw",
        },
    }
```

**D. 更新 `get_user_env()`（第 110-137 行）：**

```python
def get_user_env(user_id: str, mcp_config: dict | None = None) -> dict[str, str]:
    """构建用户容器的环境变量。"""
    base = container_user_dir(user_id)
    workspace = container_workspace_dir(user_id)

    env: dict[str, str] = {
        "USER_ID": user_id,
        "WORKSPACE": str(workspace),          # 新增：容器内工作空间路径
        "HOME": str(base),                     # 新增：容器内用户目录（用于 .claude）
        "ANTHROPIC_API_KEY": os.getenv(
            f"ANTHROPIC_API_KEY_{user_id.upper()}",
            os.getenv("ANTHROPIC_API_KEY", ""),
        ),
        "CLAUDE_SKILLS_DIRS": (
            f"{HOST_DATA_ROOT}/shared-skills,"  # 更新为宿主机匹配路径
            f"{base}/skills"                    # 更新为宿主机匹配路径
        ),
    }
    # ... settings.json 写入逻辑保持不变
```

#### 4.2.2 main_server.py

**更新 `build_container_options_dict()`（第 1202-1206 行）：**

```python
# 变更前：
"cwd": "/workspace",
"skills_dirs": [
    "/home/agent/.claude/shared-skills",
    "/home/agent/.claude/personal-skills",
],

# 变更后：
cm = _get_container_manager()
if cm is not None:
    container_cwd = str(cm.container_workspace_dir(user_id))
    container_skills = [
        str(cm.HOST_DATA_ROOT / "shared-skills"),
        str(cm.container_user_dir(user_id) / "skills"),
    ]
else:
    container_cwd = "/workspace"  # 回退（不应在容器模式下触发）
    container_skills = [
        "/home/agent/.claude/shared-skills",
        "/home/agent/.claude/personal-skills",
    ]

# 在返回字典中使用：
"cwd": container_cwd,
"skills_dirs": container_skills,
```

#### 4.2.3 workspace_enforcement.py

**更新 `check_bash_command_for_external_writes()`（第 100-118 行）：**

当工作空间路径包含 `/home/`（如 `/home/ubuntu/web-agent/data/users/alice/workspace`），现有的 `/home/` 模式匹配会误判。需增加 user_dir 检测：

```python
def check_bash_command_for_external_writes(cmd: str, paths: PathContext) -> str | None:
    # ... 模式列表保持不变 ...
    _user_dir = str(paths.user_dir.resolve())
    for pat in outside_patterns:
        match = re.search(pat, cmd)
        if match:
            target = match.group(1) if match.lastindex else match.group(0)
            target_path = Path(target)
            # 如果目标路径在用户数据目录内，允许写入
            if target_path.is_absolute() and str(target_path.resolve()).startswith(_user_dir):
                continue
            return (
                f"Command writes to '{target}' which is outside the workspace. "
                "Save all files within the workspace directory."
            )
    return None
```

#### 4.2.4 Docker 配置

**docker-compose.yml：**
```yaml
services:
  web-agent:
    # ...
    environment:
      - HOST_DATA_ROOT=${HOST_DATA_ROOT:-/home/ubuntu/web-agent/data}
```

**Dockerfile.user（第 24-26 行）：**
```dockerfile
# 变更前：
RUN useradd --create-home --uid 1000 agent && \
    mkdir -p /workspace /home/agent/.claude && \
    chown -R agent:agent /workspace /home/agent /app

# 变更后（目录由卷挂载自动创建）：
RUN useradd --create-home --uid 1000 agent && \
    chown -R agent:agent /home/agent /app
```

#### 4.2.5 agent_server.py

现有代码（第 58-60 行）已从环境变量读取路径，无需修改：
```python
WORKSPACE = Path(os.getenv("WORKSPACE", "/workspace"))    # 已有回退值
HOME_DIR = Path(os.getenv("HOME", "/home/agent"))          # 已有回退值
CLAUDE_DIR = HOME_DIR / ".claude"
```

`ContainerPaths` 初始化也无需修改：
```python
container_paths = ContainerPaths(workspace=WORKSPACE, home_dir=HOME_DIR)
```

#### 4.2.6 主服务器中的重复路径检查函数

`main_server.py` 第 664 行的 `check_bash_command_for_external_writes` 是 `workspace_enforcement.py` 中同名函数的副本，接受 `workspace: Path` 参数而非 `PathContext`。需同步添加 `user_dir` 参数：

```python
def check_bash_command_for_external_writes(
    cmd: str, workspace: Path, user_dir: Path | None = None
) -> str | None:
    if user_dir is None:
        user_dir = workspace
    # ... 同上添加 user_dir 范围内的路径豁免逻辑
```

调用处（约第 1740 行）需同步传入 `user_data_dir(user_id)`。

### 4.3 边界情况

| 场景 | 处理方式 |
|------|---------|
| `HOST_DATA_ROOT` 未设置 | 默认使用 `DATA_ROOT.resolve()` |
| `HOST_DATA_ROOT` 设为空字符串 | `if _HOST_DATA_ROOT` 判定为假，使用默认值 |
| 工作空间路径在 `/home/` 下 | `workspace_enforcement.py` 添加 user_dir 豁免 |
| 用户容器单独运行（无卷挂载） | `agent_server.py` 的回退默认值 `/workspace`、`/home/agent` |
| 用户ID包含特殊字符 | 由API边界的输入验证保障（已有机制） |
| 并发创建同一用户容器 | `ensure_user_dirs` 的 `mkdir(exist_ok=True)` 幂等保障 |

## 五、验证方案

### 5.1 单元测试

```bash
uv run pytest tests/unit/test_container_manager.py -v
```

测试覆盖：
- 卷绑定的源路径和目标路径使用正确的 HOST_DATA_ROOT
- `container_user_dir()` 和 `container_workspace_dir()` 返回正确路径
- `get_user_env()` 包含 `WORKSPACE` 和 `HOME` 环境变量
- `HOST_DATA_ROOT` 默认退化为 `DATA_ROOT.resolve()`
- bash 命令检查在 `/home/` 路径下正确处理 user_dir 内写入

### 5.2 集成验证

```bash
# 1. 设置环境变量
export HOST_DATA_ROOT=$(pwd)/data
export CONTAINER_MODE=true

# 2. 构建用户容器镜像
docker build -t web-agent-user:latest -f Dockerfile.user .

# 3. 启动主服务器，触发用户会话后检查容器挂载
docker inspect web-agent-testuser --format '{{json .Mounts}}' | python -m json.tool

# 4. 验证挂载配置：
#    - bind target 与 source 路径相同
#    - 路径使用正确的 HOST_DATA_ROOT
```

### 5.3 隔离性验证

```bash
# 验证两个用户的容器工作空间路径不同：
docker inspect web-agent-alice --format '{{json .Mounts}}'
docker inspect web-agent-bob --format '{{json .Mounts}}'
# alice 的 workspace 应为：{HOST_DATA_ROOT}/users/alice/workspace
# bob 的 workspace 应为：{HOST_DATA_ROOT}/users/bob/workspace
```

---

> **文档版本**：v1.0
> **分析日期**：2026-05-07
> **分支**：dev_20260428
