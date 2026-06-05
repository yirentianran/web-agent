# Web Agent — 启动与调试

## 环境要求

| 组件 | 最低 | 推荐 |
|------|------|------|
| Python | 3.12 | 3.12+ |
| Node.js | 18 | 20+ |
| Docker | 24+ | 最新版（容器模式需要） |

## 快速开始

```bash
cp .env.example .env          # 编辑 ANTHROPIC_AUTH_TOKEN 和 MODEL
./setup.sh && ./start-dev.sh  # 一键安装 + 启动
```

| 服务 | 地址 |
|------|------|
| 前端 | http://localhost:3000 |
| API | http://localhost:8000 |
| Swagger | http://localhost:8000/docs |

开发模式无需密码即可登录。

## 生产模式

```bash
cd frontend && npm run build:deploy
PROD=true uvicorn main_server:app --host 0.0.0.0 --port 8000
```

## Docker

```bash
docker compose up --build                    # 开发
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build  # 生产
```

## 测试

```bash
uv run pytest tests/ -v                      # 全部
uv run pytest tests/unit/ -v                 # 单元
uv run pytest tests/integration/ -v          # 集成
uv run pytest --cov=src --cov-report=term-missing tests/
cd frontend && npm test
```

## 代码检查

```bash
uv run ruff check src/ main_server.py        # lint
uv run mypy src/                             # type check
cd frontend && npx tsc --noEmit              # TS check
```

## 调试

### 后端

- 设置 `LOG_LEVEL=debug` 启用详细日志（WebSocket 帧、buffer 状态等）
- Python 调试：`breakpoint()` + `uvicorn main_server:app --port 8000`（不加 `--reload`）
- Docker：`docker compose logs -f main-server`

### 前端

- React DevTools 浏览器扩展
- `BACKEND_PORT=8000 npx vite --port 3000 --debug` 启用 Vite 调试
- DevTools → Network → 筛选 `ws` 查看 WebSocket 帧

### WebSocket

```bash
npx wscat -c ws://localhost:8000/ws
> {"type": "chat", "message": "hello", "user_id": "test"}
```

### 容器

```bash
docker ps -a                    # 容器状态
docker exec -it <name> bash     # 进入容器
docker logs <name>              # 容器日志
```

## 常见问题

| 现象 | 解决 |
|------|------|
| 端口占用 | `lsof -i :8000` 查看进程并 kill |
| API key 未设置 | 创建 `.env` 并设 `ANTHROPIC_AUTH_TOKEN` |
| 前端连不上 | 确认后端在 8000 运行 |
| WebSocket 403 | 开发模式确认 `ENFORCE_AUTH=false` |
| Docker 连不上 | `open -a Docker` (macOS) |
| 容器崩溃 | `docker compose config` 检查环境变量 |
| 新路由 404 | 重启 uvicorn（`--reload` 有时不生效） |

## 生产部署检查

- [ ] `ANTHROPIC_AUTH_TOKEN` 设为生产密钥
- [ ] `JWT_SECRET` 至少 32 字符随机值
- [ ] `ENFORCE_AUTH=true`
- [ ] `CONTAINER_MODE=true`（如需容器隔离）
- [ ] `PROD=true`
- [ ] `LOG_LEVEL=info`
- [ ] 配置 HTTPS / TLS
- [ ] 定期备份 `data/` 目录
