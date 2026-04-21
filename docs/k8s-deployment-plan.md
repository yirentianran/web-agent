# Web Agent Kubernetes 部署方案

> 版本: v1.0
> 日期: 2026-04-20
> 状态: 待审批

---

## 目录

1. [现状分析](#1-现状分析)
2. [为什么需要 K8s](#2-为什么需要-k8s)
3. [架构设计](#3-架构设计)
4. [关键决策及理由](#4-关键决策及理由)
5. [容器化方案](#5-容器化方案)
6. [Kubernetes Manifests](#6-kubernetes-manifests)
7. [数据库策略](#7-数据库策略)
8. [WebSocket 支持](#8-websocket-支持)
9. [可观测性](#9-可观测性)
10. [CI/CD 流水线](#10-cicd-流水线)
11. [Per-User 容器隔离](#11-per-user-容器隔离)
12. [分阶段实施路径](#12-分阶段实施路径)
13. [文件清单](#13-文件清单)

---

## 1. 现状分析

### 当前部署方式

| 组件 | 方式 |
|------|------|
| 后端 | `nohup uvicorn main_server:app --host 0.0.0.0 --port 8000 --workers 4` |
| 前端 | Vite 构建到 `src/static/`，由 FastAPI `StaticFiles` 服务 |
| 数据库 | SQLite (4.8MB, WAL mode) |
| 进程管理 | shell 脚本 `scripts/manage.sh` (start/stop/restart/status/logs) |
| 配置 | `.env` 文件 |
| CI/CD | 无 |
| 容器化 | 无 |
| 健康检查 | 基础 `/health` 端点 |

### 项目结构

```
web-agent/
├── main_server.py          # FastAPI 主应用 (REST + WebSocket)
├── src/                    # 后端模块
│   ├── message_buffer.py   # 消息持久化
│   ├── memory.py           # 用户记忆
│   ├── auth.py             # JWT 认证
│   ├── database.py         # SQLite 数据库层
│   └── ...
├── frontend/               # React SPA (Vite + TypeScript)
├── data/                   # 运行时数据 (不提交)
│   ├── web-agent.db        # SQLite 数据库
│   ├── .msg-buffer/        # 消息 JSONL 文件
│   ├── users/              # 用户工作区
│   ├── shared-skills/      # 公共 Skills
│   └── skills/             # 个人 Skills
└── scripts/                # 部署脚本
    ├── manage.sh           # 生产启停
    ├── build.sh            # 前端构建
    └── start-dev.sh        # 开发启动
```

### 关键环境变量

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `ANTHROPIC_API_KEY` | (必需) | LLM API 密钥 |
| `ANTHROPIC_BASE_URL` | (可选) | 自定义 API 端点 |
| `MODEL` | `claude-sonnet-4-6` | 模型标识 |
| `DATA_ROOT` | `./data` | 数据根目录 |
| `DATA_DB_PATH` | `./data/web-agent.db` | SQLite 路径 |
| `PROD` | `false` | 禁用 CORS (生产模式) |
| `CONTAINER_MODE` | `false` | 启用 Docker 用户隔离 |
| `ENFORCE_AUTH` | `false` | 启用 JWT 认证 |
| `JWT_SECRET` | `dev-secret-change...` | JWT 签名密钥 |
| `LOG_LEVEL` | `info` | 日志级别 |

---

## 2. 为什么需要 K8s

| 痛点 | 现状 | K8s 解决 |
|------|------|----------|
| **高可用** | 进程崩溃需手动重启 | 自动重启 + 自愈探针 |
| **滚动更新** | 手动停服 → 更新 → 启服，有停机窗口 | 零停机滚动更新 |
| **水平扩展** | 单机瓶颈，无法扩展 | HPA 自动扩缩容 |
| **资源管理** | 无 CPU/内存限制 | Resource Quota + Limit |
| **配置管理** | `.env` 文件散落 | ConfigMap + Secret，版本控制 |
| **证书管理** | 手动配置 | cert-manager 自动续签 |
| **故障迁移** | 节点宕机即服务中断 | 多节点 Pod 调度 |
| **回滚能力** | 无版本化部署 | 镜像标签 + Deployment 历史 |
| **可观测性** | 本地日志文件 | 结构化日志 + 指标 + 链路追踪 |

---

## 3. 架构设计

### 推荐：单容器单 Pod 单体架构

**不拆分前后端**，理由：
- 前端已构建到 `src/static/`，由 FastAPI 统一服务
- 共享端口 8000，拆分需要额外 Ingress 路由
- 增加网络延迟和运维复杂度
- 无实际收益

### 架构图

```
┌──────────────────────────────────────────────────┐
│                  Ingress (TLS)                    │
│           cert-manager + Let's Encrypt            │
│           WebSocket: 3600s timeout                │
└────────────────────┬─────────────────────────────┘
                     │
                     │ /api/*  /ws/*  /  /health
                     ▼
┌──────────────────────────────────────────────────┐
│              Service (ClusterIP)                  │
│                  port: 8000                       │
└────────────────────┬─────────────────────────────┘
                     │
           ┌─────────┼─────────┐
           ▼         ▼         ▼
      ┌────────┐ ┌────────┐ ┌────────┐
      │ Pod-1  │ │ Pod-2  │ │ Pod-N  │   ← Deployment
      │        │ │        │ │        │
      │ uvicorn│ │ uvicorn│ │ uvicorn│
      │ :8000  │ │ :8000  │ │ :8000  │
      └───┬────┘ └───┬────┘ └───┬────┘
          │          │          │
          └──────────┼──────────┘
                     │
           ┌─────────▼─────────┐
           │   PVC (RWX/RWO)   │
           │   /data           │  SQLite + JSONL + uploads
           │                   │  skills + memory + audit
           └─────────┬─────────┘
                     │
           ┌─────────▼─────────┐
           │ PostgreSQL (opt)  │  Phase 2: 多副本写入
           │ 外部或集群内       │
           └───────────────────┘
```

---

## 4. 关键决策及理由

### 4.1 数据库策略

| 方案 | 适用场景 | 优点 | 缺点 |
|------|----------|------|------|
| **SQLite + 单副本** | Phase 1，<50 并发用户 | 零代码改动，立即可用 | 无法多副本扩展，RWX 卷有延迟 |
| **PostgreSQL + 多副本** | Phase 2，生产级 | 真正水平扩展，连接池 | 需代码改动 (2-4h)，额外基础设施 |

**推荐**：Phase 1 用 SQLite + 1 副本快速上线，Phase 2 迁移 PostgreSQL 支持多副本。

**代码改动评估**：Schema 已是标准 SQL，无 SQLite 特有扩展。仅需：
- `aiosqlite` → `asyncpg`
- 移除 SQLite PRAGMA 语句
- 调整 datetime 默认值表达式

### 4.2 CONTAINER_MODE (用户隔离)

| 方案 | 安全性 | 复杂度 | 推荐度 |
|------|--------|--------|--------|
| Docker Socket 挂载 | ❌ pod 内等于 root 权限 | 低 | 不推荐 |
| DinD Sidecar | ⚠️ 仍有风险 | 高 | 不推荐 |
| K8s Job + RBAC | ✅ 原生安全 | 中 | **推荐 (Phase 4)** |
| 文件系统隔离 | ✅ 够用 | 低 | **Phase 1 默认** |

**推荐**：初始阶段禁用 `CONTAINER_MODE`，使用文件系统级隔离。Phase 4 用 K8s Job 替代 Docker 实现用户隔离。

### 4.3 WebSocket 支持

Agent 会话依赖 WebSocket 长连接，Ingress 必须配置：
- `proxy-http-version: "1.1"` — WebSocket 需要 HTTP/1.1
- `Upgrade` + `Connection "upgrade"` 头 — 信号升级
- `proxy-read-timeout: 3600` — 长连接不超时

### 4.4 副本策略

| 阶段 | 副本数 | 限制因素 |
|------|--------|----------|
| Phase 1 (SQLite) | 1 | SQLite 写锁冲突 |
| Phase 2 (PostgreSQL) | 2-3 | 内存/CPU 资源 |
| Phase 3 (HPA) | 1-5 | 自动扩缩 |

---

## 5. 容器化方案

### 5.1 Dockerfile (多阶段构建)

```dockerfile
# ============================================================
# Stage 1: 构建前端
# ============================================================
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build
# 输出到 ../src/static/ (按 vite.config.ts 配置)

# ============================================================
# Stage 2: 后端运行时
# ============================================================
FROM python:3.12-slim AS runtime

# 安装系统依赖 (claude CLI 所需)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 claude CLI (claude-agent-sdk 依赖)
RUN curl -fsSL https://cli.anthropic.com/install.sh | bash

WORKDIR /app

# 安装 Python 依赖 (使用 uv 加速)
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev

# 复制应用代码
COPY src/ ./src/
COPY main_server.py ./

# 复制构建好的前端
COPY --from=frontend-builder /app/src/static/ ./src/static/

# 创建数据目录
RUN mkdir -p /app/data && chown -R 1000:1000 /app/data

# 非 root 用户运行
USER 1000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main_server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### 5.2 .dockerignore

```
.git
.venv
__pycache__
*.pyc
data/
node_modules/
frontend/node_modules/
frontend/dist/
*.egg-info
.pytest_cache
.mypy_cache
.DS_Store
logs/
```

### 5.3 镜像大小预估

| 层 | 大小 |
|----|------|
| python:3.12-slim 基础 | ~150MB |
| claude CLI | ~100MB |
| Python 依赖 | ~100MB |
| 应用代码 + 前端 | ~20MB |
| **总计** | **~370-450MB** |

---

## 6. Kubernetes Manifests

### 6.1 命名空间

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: web-agent
  labels:
    name: web-agent
    environment: production
```

### 6.2 ConfigMap

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: web-agent-config
  namespace: web-agent
data:
  DATA_ROOT: "/app/data"
  DATA_DB_PATH: "/app/data/web-agent.db"
  PROD: "true"
  CONTAINER_MODE: "false"
  LOG_LEVEL: "INFO"
  LOG_FORMAT: "json"
  MODEL: "qwen3.6-plus"
  # Uvicorn 配置
  UVICORN_WORKERS: "4"
  UVICORN_HOST: "0.0.0.0"
  UVICORN_PORT: "8000"
```

### 6.3 Secrets

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: web-agent-secrets
  namespace: web-agent
type: Opaque
stringData:
  ANTHROPIC_API_KEY: "sk-sp-xxxxx"           # 替换为实际值
  ANTHROPIC_BASE_URL: "https://coding.dashscope.aliyuncs.com/apps/anthropic"
  JWT_SECRET: "your-jwt-secret-min-32-chars" # 至少 32 字符
  ENFORCE_AUTH: "true"
  ADMIN_USER_IDS: '["admin-user-id-1"]'
```

> **安全提示**：生产环境建议使用 External Secrets Operator 对接 Vault/AWS Secrets Manager，而非原生 Secret。

### 6.4 Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web-agent
  namespace: web-agent
  labels:
    app: web-agent
    version: "1.0.0"
spec:
  replicas: 1  # Phase 2 (PostgreSQL) 后可扩容至 2-3
  selector:
    matchLabels:
      app: web-agent
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0  # 零停机：新 Pod 就绪后才停旧 Pod
  template:
    metadata:
      labels:
        app: web-agent
      annotations:
        config-checksum: "placeholder"  # CI/CD 中动态注入
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
      containers:
        - name: web-agent
          image: ghcr.io/your-org/web-agent:main
          imagePullPolicy: Always
          ports:
            - containerPort: 8000
              name: http
              protocol: TCP
          envFrom:
            - configMapRef:
                name: web-agent-config
            - secretRef:
                name: web-agent-secrets
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "2000m"
              memory: "2Gi"
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 30
            timeoutSeconds: 5
            failureThreshold: 3
          startupProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
            failureThreshold: 30  # 允许最长 5 分钟启动
          volumeMounts:
            - name: data
              mountPath: /app/data
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: web-agent-data
        - name: tmp
          emptyDir:
            sizeLimit: 500Mi
      terminationGracePeriodSeconds: 60  # WebSocket 连接优雅关闭
```

### 6.5 PersistentVolumeClaim

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: web-agent-data
  namespace: web-agent
spec:
  accessModes:
    - ReadWriteOnce  # Phase 1 单副本用 RWO；多副本需 RWX (EFS/NFS)
  storageClassName: standard  # 替换为集群存储类 (如 gp3, premium-rssd)
  resources:
    requests:
      storage: 20Gi
```

**存储容量评估**：

| 内容 | 预估大小 |
|------|----------|
| SQLite 数据库 | ~5MB (缓慢增长) |
| 消息缓冲区 (JSONL) | 100MB - 1GB (活跃会话) |
| 用户上传文件 | 可变，可能较大 |
| Skills | <50MB |
| 审计日志 | 10-50MB/月 |
| **初始分配** | **20Gi** |

### 6.6 Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: web-agent
  namespace: web-agent
  labels:
    app: web-agent
spec:
  type: ClusterIP
  ports:
    - port: 8000
      targetPort: 8000
      protocol: TCP
      name: http
  selector:
    app: web-agent
```

### 6.7 Ingress (WebSocket + TLS)

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: web-agent
  namespace: web-agent
  annotations:
    # WebSocket 支持
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-http-version: "1.1"
    nginx.ingress.kubernetes.io/configuration-snippet: |
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection "upgrade";
    # TLS 自动管理
    cert-manager.io/cluster-issuer: letsencrypt-prod
    # 大文件上传支持 (Skill ZIP 最大 50MB)
    nginx.ingress.kubernetes.io/proxy-body-size: "60m"
    # 安全头
    nginx.ingress.kubernetes.io/custom-response-headers: |
      X-Content-Type-Options: nosniff
      X-Frame-Options: DENY
      Strict-Transport-Security: max-age=31536000; includeSubDomains
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - agent.yourdomain.com
      secretName: web-agent-tls
  rules:
    - host: agent.yourdomain.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: web-agent
                port:
                  number: 8000
```

### 6.8 HorizontalPodAutoscaler

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: web-agent
  namespace: web-agent
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: web-agent
  minReplicas: 1
  maxReplicas: 1  # Phase 1: 固定 1 副本 (SQLite 限制)
                  # Phase 2 (PostgreSQL): 改为 3-5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300  # 缩容前等待 5 分钟
      policies:
        - type: Pods
          value: 1
          periodSeconds: 120
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 1
          periodSeconds: 60
```

### 6.9 PodDisruptionBudget

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: web-agent
  namespace: web-agent
spec:
  minAvailable: 0  # 单副本允许维护时中断
                   # 多副本后改为 minAvailable: 1
  selector:
    matchLabels:
      app: web-agent
```

---

## 7. 数据库策略

### 7.1 Phase 1: SQLite (零改动)

```
Deployment (1 replica)
  └── Pod
       └── PVC (ReadWriteOnce)
            └── /app/data/web-agent.db
```

**优点**：零代码改动，立即可用。
**限制**：无法扩展到多副本，写操作序列化。

### 7.2 Phase 2: PostgreSQL (水平扩展)

**代码改动清单**：

| 文件 | 改动 |
|------|------|
| `pyproject.toml` | 添加 `asyncpg` 依赖 |
| `src/database.py` | 替换 `aiosqlite` → `asyncpg`，移除 PRAGMA，添加连接池 |
| `k8s/secrets.yaml` | 添加 `DATABASE_URL` |

**SQLite → PostgreSQL 映射**：

| SQLite | PostgreSQL |
|--------|------------|
| `AUTOINCREMENT` | `SERIAL` 或 `GENERATED ALWAYS AS IDENTITY` |
| `strftime('%s', 'now')` | `EXTRACT(EPOCH FROM NOW())` |
| `PRAGMA journal_mode=WAL` | (无需，PG 默认 WAL) |
| `PRAGMA busy_timeout=5000` | 连接池配置 |

**数据迁移脚本**：
```bash
# 1. 导出 SQLite 数据
sqlite3 data/web-agent.db .dump > sqlite_dump.sql

# 2. 转换并导入 PostgreSQL (Python 脚本处理类型转换)
python scripts/migrate_sqlite_to_pg.py

# 3. 验证数据完整性
python scripts/verify_migration.py
```

---

## 8. WebSocket 支持

### 8.1 Ingress 配置要点

Agent 会话通过 WebSocket 通信，生命周期可能长达数小时。Ingress 必须：

1. **保持 HTTP/1.1** — WebSocket 握手需要
2. **传递 Upgrade 头** — 信号协议升级
3. **延长超时** — 默认 60s 会切断活跃会话
4. **支持 Session Affinity** (可选) — 多副本时确保连接路由到同一 Pod

### 8.2 多副本 WebSocket 问题

```
用户 A → Ingress → Pod-1 (Session 在 Pod-1 内存)
用户 A 重连 → Ingress → Pod-2 (找不到 Session!) ❌
```

**解决方案**：
- **Phase 1** (1 副本)：不存在此问题
- **Phase 2** (多副本)：Session 数据存入 Redis/PostgreSQL，或使用 Ingress Session Affinity

---

## 9. 可观测性

### 9.1 健康检查

需要增强 `main_server.py` 中的 `/health` 端点，并新增 `/ready` 端点：

```python
@app.get("/health")
async def health() -> JSONResponse:
    """Liveness probe — 进程是否存活"""
    return JSONResponse({"status": "ok", "service": "web-agent"})

@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe — 是否能接收流量"""
    try:
        if _db is not None:
            async with _db.connection() as conn:
                await conn.execute("SELECT 1")
        return JSONResponse({"status": "ready"})
    except Exception as e:
        return JSONResponse({"status": f"error: {e}"}, status_code=503)
```

### 9.2 结构化日志

K8s 生态推荐 stdout JSON 日志格式：

```python
import json
import logging

class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        })
```

### 9.3 指标暴露 (可选 Phase 2)

```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app, endpoint="/metrics")
```

配合 Prometheus ServiceMonitor 实现自动采集。

### 9.4 探针总结

| 探针 | 端点 | 用途 | 失败行为 |
|------|------|------|----------|
| startupProbe | `/health` | 启动阶段 | 重启 Pod |
| livenessProbe | `/health` | 运行阶段 | 重启 Pod |
| readinessProbe | `/ready` | 流量就绪 | 从 Service 摘除 |

---

## 10. CI/CD 流水线

### 10.1 GitHub Actions Workflow

```yaml
# .github/workflows/ci-cd.yml
name: CI/CD

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  # ── 后端测试 ──
  test-backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
      - name: 安装依赖
        run: uv sync --frozen
      - name: 运行测试
        run: uv run pytest --cov=src --cov-report=xml -q
      - name: 代码检查
        run: uv run ruff check src/ main_server.py
      - name: 类型检查
        run: uv run mypy src/
      - uses: codecov/codecov-action@v4
        with:
          file: coverage.xml

  # ── 前端测试 ──
  test-frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - name: 安装依赖
        run: npm ci
      - name: 运行测试
        run: npm test -- --run
      - name: 类型检查
        run: npx tsc --noEmit
      - name: 验证构建
        run: npm run build

  # ── 构建和推送镜像 ──
  build-and-push:
    needs: [test-backend, test-frontend]
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    outputs:
      image-digest: ${{ steps.build.outputs.digest }}
    steps:
      - uses: actions/checkout@v4
      - name: 登录 GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: 提取镜像元数据
        uses: docker/metadata-action@v5
        id: meta
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=sha,prefix=
            type=ref,event=branch
            type=semver,pattern={{version}}
      - name: 构建并推送
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  # ── 部署到 K8s ──
  deploy:
    needs: [build-and-push]
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    environment: production
    steps:
      - uses: actions/checkout@v4
      - name: 安装 kubectl
        uses: azure/setup-kubectl@v4
      - name: 配置 kubeconfig
        run: |
          mkdir -p $HOME/.kube
          echo "${{ secrets.KUBE_CONFIG }}" | base64 -d > $HOME/.kube/config
      - name: 更新镜像
        run: |
          IMAGE_TAG="${{ github.sha }}"
          kubectl -n web-agent set image deployment/web-agent \
            web-agent=${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${IMAGE_TAG}
      - name: 等待滚动更新完成
        run: |
          kubectl -n web-agent rollout status deployment/web-agent --timeout=300s
      - name: 冒烟测试
        run: |
          # 等待 10 秒让新 Pod 就绪
          sleep 10
          STATUS=$(kubectl -n web-agent get deploy web-agent -o jsonpath='{.status.conditions[?(@.type=="Available")].status}')
          if [ "$STATUS" != "True" ]; then
            echo "部署后健康检查失败"
            kubectl -n web-agent describe deploy web-agent
            kubectl -n web-agent logs deploy/web-agent --tail=50
            exit 1
          fi
          echo "部署成功，服务可用"
```

### 10.2 流水线特点

| 特性 | 说明 |
|------|------|
| 并行测试 | 前后端测试同时运行 |
| PR 仅测试 | PR 不构建/推送镜像 |
| SHA 标签 | 每次 main 提交有唯一镜像标签，支持回滚 |
| 滚动更新 | `kubectl set image` 触发零停机更新 |
| 构建缓存 | GitHub Actions 缓存加速 Docker 构建 |
| 冒烟测试 | 部署后自动验证服务可用性 |

---

## 11. Per-User 容器隔离

### 11.1 为什么不用 Docker Socket

在 K8s 中挂载 `/var/run/docker.sock` 到 Pod 的风险：

```
攻击者 → 入侵 web-agent Pod → 通过 Docker Socket 获取 Node root 权限
  → 创建特权容器 → 访问所有 Pod 的 Secret → 完全控制集群
```

### 11.2 K8s 原生替代方案

```
┌─────────────────────────────────┐
│     Web Agent Main Pod          │
│  (FastAPI, K8s Client)          │
│                                 │
│  CONTAINER_MODE=true 时:        │
│  1. 调用 K8s API 创建 Job       │
│  2. 使用最小权限 ServiceAccount  │
│  3. 应用 ResourceQuota           │
│  4. 配置 NetworkPolicy           │
│  5. 空闲后自动清理 (TTL)          │
└──────────┬──────────────────────┘
           │ K8s API
           ▼
┌─────────────────────────────────┐
│   Per-User Pod (K8s Job)        │
│   - 独立 Label 标识用户          │
│   - ResourceQuota: 1核/4G       │
│   - NetworkPolicy: 仅通 API     │
│   - TTL: 空闲 30min 自动删除     │
│   - 直接运行 Claude SDK          │
└─────────────────────────────────┘
```

### 11.3 实施改动概要

| 文件 | 改动 |
|------|------|
| `pyproject.toml` | 添加 `kubernetes` Python SDK |
| `src/container_manager.py` | 重写 Docker SDK 调用 → K8s API |
| `k8s/rbac.yaml` | 创建 ServiceAccount + Role + RoleBinding |
| `k8s/network-policy.yaml` | 用户 Pod 网络隔离 |
| `k8s/resource-quota.yaml` | 用户级别资源配额 |

> **注意**：这是 Phase 4 的工作，初始 K8s 部署不涉及。

---

## 12. 分阶段实施路径

### Phase 1: 容器化 + K8s 基础部署

**目标**：在 K8s 中运行与当前等效的服务。

| 步骤 | 内容 | 预计工时 |
|------|------|----------|
| 1 | 创建 Dockerfile (多阶段构建) | 1h |
| 2 | 本地构建并验证镜像 | 1h |
| 3 | 创建 K8s manifests (namespace, configmap, secrets, deployment, service, ingress, pvc) | 2h |
| 4 | 增强 `/health` 端点 + 添加 `/ready` 端点 | 0.5h |
| 5 | 添加 JSON 日志格式支持 | 0.5h |
| 6 | 配置 CI/CD 流水线 | 2h |
| 7 | 部署到 K8s 集群 | 1h |
| 8 | 配置 Ingress + TLS (cert-manager) | 1h |
| 9 | 冒烟测试验证 | 1h |

**总计**：约 10 小时，1-2 个工作日。

### Phase 2: PostgreSQL 迁移 + 水平扩展

**目标**：支持多副本部署，消除 SQLite 写锁瓶颈。

| 步骤 | 内容 | 预计工时 |
|------|------|----------|
| 1 | 部署 PostgreSQL (云服务或 Helm) | 1h |
| 2 | 替换 `aiosqlite` → `asyncpg` | 2h |
| 3 | 编写数据迁移脚本 | 1h |
| 4 | 本地 PostgreSQL 验证 | 1h |
| 5 | 部署到 K8s + 数据迁移 | 1h |
| 6 | 扩容到 2 副本 + 验证 | 1h |
| 7 | 启用 HPA (`maxReplicas: 3`) | 0.5h |
| 8 | Session 持久化 (Redis/PG) | 2h |

**总计**：约 9.5 小时，1-2 个工作日。

### Phase 3: 可观测性 + 运维增强

**目标**：生产级监控和运维能力。

| 步骤 | 内容 | 预计工时 |
|------|------|----------|
| 1 | 添加 Prometheus 指标端点 | 0.5h |
| 2 | 配置 Prometheus ServiceMonitor | 0.5h |
| 3 | 部署日志聚合 (Fluent Bit + Loki) | 2h |
| 4 | 调整 PDB (`minAvailable: 1`) | 0.5h |
| 5 | 配置 Namespace ResourceQuota | 0.5h |
| 6 | 备份策略 (PG + PVC 快照) | 2h |
| 7 | 告警规则配置 | 1h |

**总计**：约 7.5 小时，1 个工作日。

### Phase 4: Per-User K8s 隔离 (可选)

**目标**：用 K8s Job 替代 Docker 实现用户级隔离。

| 步骤 | 内容 | 预计工时 |
|------|------|----------|
| 1 | 重写 `container_manager.py` → K8s API | 3h |
| 2 | 创建 RBAC (ServiceAccount + Role) | 0.5h |
| 3 | 实现 Per-User Job 生命周期管理 | 2h |
| 4 | 配置 NetworkPolicy 隔离 | 1h |
| 5 | 配置 ResourceQuota 限制 | 0.5h |
| 6 | 多用户并发测试验证 | 2h |

**总计**：约 9 小时，1-2 个工作日。

### 总工期汇总

| 阶段 | 工时 | 关键里程碑 |
|------|------|------------|
| Phase 1 | ~10h | 服务在 K8s 中运行，TLS 就绪 |
| Phase 2 | ~9.5h | 多副本部署，无写锁冲突 |
| Phase 3 | ~7.5h | 监控、告警、备份齐全 |
| Phase 4 | ~9h | 用户级容器隔离 |
| **合计** | **~36h** | **约 1-2 周** |

---

## 13. 文件清单

### 需要创建的文件

| 文件路径 | 用途 | 阶段 |
|----------|------|------|
| `Dockerfile` | 多阶段构建 (前端 + 后端) | Phase 1 |
| `.dockerignore` | 排除非必要文件 | Phase 1 |
| `k8s/namespace.yaml` | 命名空间定义 | Phase 1 |
| `k8s/configmap.yaml` | 非敏感配置 | Phase 1 |
| `k8s/secrets.yaml` | 敏感配置 (API Key, JWT) | Phase 1 |
| `k8s/deployment.yaml` | 主 Deployment (探针、资源、卷) | Phase 1 |
| `k8s/service.yaml` | ClusterIP Service | Phase 1 |
| `k8s/ingress.yaml` | Ingress (WebSocket + TLS) | Phase 1 |
| `k8s/pvc.yaml` | 数据持久卷 | Phase 1 |
| `k8s/hpa.yaml` | 自动扩缩容 | Phase 1 |
| `k8s/pdb.yaml` | Pod 中断预算 | Phase 1 |
| `.github/workflows/ci-cd.yml` | CI/CD 流水线 | Phase 1 |

### 需要修改的文件

| 文件 | 改动 | 阶段 |
|------|------|------|
| `main_server.py` | 增强 `/health`、新增 `/ready`、JSON 日志 | Phase 1 |
| `pyproject.toml` | 添加 `asyncpg` (Phase 2)、`kubernetes` (Phase 4)、`prometheus-fastapi-instrumentator` (Phase 3) | Phase 2-4 |
| `src/database.py` | 替换 `aiosqlite` → `asyncpg` | Phase 2 |
| `src/container_manager.py` | 重写 Docker SDK → K8s API | Phase 4 |

---

## 附录

### A. 资源配额建议

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: web-agent-quota
  namespace: web-agent
spec:
  hard:
    requests.cpu: "4"
    requests.memory: 8Gi
    limits.cpu: "8"
    limits.memory: 16Gi
    persistentvolumeclaims: "5"
    pods: "10"
```

### B. 备份策略

| 数据 | 备份方式 | 频率 |
|------|----------|------|
| PostgreSQL | pg_dump / 云服务快照 | 每日 |
| PVC (上传文件) | VolumeSnapshot / Rclone → S3 | 每日 |
| K8s Configs | Git (已在版本控制中) | 每次提交 |

### C. 回滚命令

```bash
# 查看部署历史
kubectl -n web-agent rollout history deployment/web-agent

# 回滚到上一个版本
kubectl -n web-agent rollout undo deployment/web-agent

# 回滚到指定版本
kubectl -n web-agent rollout undo deployment/web-agent --to-revision=2

# 通过 GitHub Actions 直接回滚
kubectl -n web-agent set image deployment/web-agent \
  web-agent=ghcr.io/your-org/web-agent:<old-sha>
```

### D. 故障排查命令

```bash
# 查看 Pod 状态
kubectl -n web-agent get pods

# 查看 Pod 详情
kubectl -n web-agent describe pod <pod-name>

# 查看日志
kubectl -n web-agent logs -f deploy/web-agent

# 查看最近 100 行日志
kubectl -n web-agent logs deploy/web-agent --tail=100

# 进入 Pod Shell
kubectl -n web-agent exec -it deploy/web-agent -- /bin/bash

# 查看 Ingress 状态
kubectl -n web-agent get ingress

# 查看证书状态
kubectl -n web-agent get certificate
```
