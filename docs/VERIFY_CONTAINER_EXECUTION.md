# Container Execution Verification

验证 `CONTAINER_MODE=true` 时用户任务在容器内执行，而非宿主机。

## 方法 1：对比进程表

发送消息后立即运行：

```bash
# 容器内应有 Claude CLI 进程
docker exec web-agent-$(whoami) ps aux
```

```bash
# 宿主机应无 Claude CLI 进程
ps aux | grep claude | grep -v grep
```

## 方法 2：对比容器日志

**桥接前**（仅空闲心跳）：

```
Agent WebSocket connected
Agent WebSocket disconnected
```

**桥接后**（应有任务执行日志）：

```
Agent WebSocket connected
PreToolUse[Write]: '/Users/...' → 'outputs/...'
```

```bash
docker logs web-agent-$(whoami)
```

## 方法 3：三终端实时监控

| 终端 | 命令 | 预期 |
|------|------|------|
| 1 | `docker logs -f web-agent-$(whoami)` | 显示 agent_server 活动 |
| 2 | `watch -n1 'docker exec web-agent-$(whoami) ps aux \| grep claude'` | Claude 进程出现后消失 |
| 3 | `watch -n1 'ps aux \| grep claude \| grep -v grep'` | 保持空白 |

在浏览器中发送消息，终端 1 和 2 应有活动，终端 3 保持空白。
