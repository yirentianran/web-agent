# Windows WebSocket 连接问题分析（更新）

## 问题描述

在 Windows 上，WebSocket 连接失败，错误信息：

```
WebSocket connection to 'ws://127.0.0.1:3000/ws?token=...' failed:
WebSocket is closed before the connection is established.
```

**关键发现：即使使用 `127.0.0.1`，问题仍然存在！**

这证明问题**不是 IPv6/IPv4 地址解析问题**，而是 **React StrictMode 导致的 WebSocket 过早关闭**。

## 根本原因：React StrictMode Double Invoke

### 堆栈跟踪分析

```
doubleInvokeEffectsOnFiber    ← React StrictMode DEV 模式
commitDoubleInvokeEffectsInDEV
disconnectPassiveEffect
commitHookEffectListUnmount   ← cleanup() 被调用
ws.close()                    ← WebSocket 被关闭 (line 171)
```

### StrictMode 时间线

```
T=0ms   Mount #1:
        useEffect → connect() → new WebSocket(ws://127.0.0.1:3000/ws)
        WebSocket 状态 = CONNECTING (readyState = 0)

T=1ms   StrictMode 立即 Unmount #1:
        cleanup() → intentionalClose = true → ws.close()
        WebSocket 从 CONNECTING → CLOSED (readyState = 3)
        浏览器警告: "WebSocket is closed before connection established"

T=2ms   Mount #2:
        useEffect → connect() → new WebSocket(...)
        新 WebSocket 开始连接

T=?ms   第二次连接应该成功...
```

### 问题代码（已修复）

**位置：** `useWebSocket.ts:169-177`

```typescript
// 原代码（有问题）
const cleanup = () => {
  intentionalClose = true;
  ws.close();  // ← 在 CONNECTING 状态调用，产生警告
  ...
};

// 修复后
const cleanup = () => {
  intentionalClose = true;
  // 只在 WebSocket 不是 CLOSED 状态时才调用 close
  if (ws.readyState !== WebSocket.CLOSED) {
    ws.close(1000, "cleanup");
  }
  ...
};
```

## 为什么第二次连接也可能失败？

即使 StrictMode 的 double invoke 是正常的，第二次 mount 的 WebSocket 应该能成功连接。如果仍然失败，可能原因：

1. **Backend 未就绪**：uvicorn 启动需要时间
2. **Vite Proxy 问题**：WebSocket 代理可能有延迟
3. **连续 cleanup**：如果第二次 mount 也被 cleanup，循环失败

## 解决方案

### 修复 1: cleanup 函数优化（已实现）

```typescript
if (ws.readyState !== WebSocket.CLOSED) {
  ws.close(1000, "cleanup");
}
```

### 修复 2: 确保 Backend 就绪

**验证 Backend：**

```powershell
curl http://127.0.0.1:8000/health
# 应返回 {"status": "ok"}
```

### 修复 3: 检查 Vite 配置

**已配置 IPv4：**

```typescript
// vite.config.ts
server: {
  host: '127.0.0.1',  // Force IPv4
  port: 3000,
  proxy: {
    '/ws': {
      target: 'http://127.0.0.1:8000',
      ws: true,
      changeOrigin: true,
    },
  },
}
```

## 验证步骤

1. **重启所有服务**：
   ```powershell
   # 停止所有进程
   .\start-dev.ps1  # 会自动清理旧进程
   ```

2. **等待 Backend 完全启动**：
   ```
   [API] INFO:     Uvicorn running on http://127.0.0.1:8000
   [WEB] ➜  Local:   http://127.0.0.1:3000/
   ```

3. **访问 `http://127.0.0.1:3000`**

4. **检查 WebSocket**：
   - DevTools → Network → WS
   - 应看到 `101 Switching Protocols`

## 修改记录

| 文件 | 修改 | 状态 |
|------|------|------|
| `useWebSocket.ts` | cleanup 函数检查 readyState | 已修改 |
| `vite.config.ts` | `host: '127.0.0.1'` | 已修改 |
| `README.md` | IPv4 URL 说明 | 已修改 |
| `setup.ps1` | IPv4 URL 说明 | 已修改 |

## 注意事项

1. **StrictMode 仅在 DEV 模式**：生产环境不会有 double invoke
2. **浏览器警告无害**：第一个 WebSocket 的警告不影响第二个
3. **Backend 必须就绪**：WebSocket 需要 Backend 正常监听

## 相关参考资料

- [React StrictMode](https://react.dev/reference/react/StrictMode)
- [WebSocket readyState](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket/readyState)