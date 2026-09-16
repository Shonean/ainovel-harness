# AInovel Harness 桌面端（Electron）

AInovel 创作驾驶舱的 Electron 桌面端：五级阶梯写作流水线 + Copilot 式创作助手。
替代原 VS Code 扩展形态——单窗口应用，左侧导航切换 8 个面板，右栏常驻创作助手。

## 架构

```
Electron main (src/main.js)
  ├─ src/core/backend.js  — 后端进程管理（spawn Python + 健康轮询 + 端口清理）
  ├─ src/core/rest.js     — REST 薄封装（复用扩展版 rest.ts）
  ├─ src/core/rpc.js      — WS JSON-RPC 客户端（复用扩展版 rpcClient.ts）
  ├─ src/core/bridge.js   — renderer ⇄ 宿主协议桥（与扩展版 webviewBridge 一致）
  ├─ src/preload.js       — contextBridge 注入 window.ainovelBridge
  └─ renderer/            — Vite React 壳（复用扩展版 8 面板 + 创作助手侧栏）
```

安全模型：renderer **不直连后端**（token 只存 main），全部请求经 IPC → BridgeHost 代理。

## 运行前提

- **Node.js**（打包/开发用）
- **Python 3.12**（`py` 命令指向的解释器，已装 fastapi/uvicorn/websockets 等依赖）
- **Electron 发行版**：本地 `D:/electron/dist`（33.4.11），或修改 `electron-builder.yml` 的 `electronDist`

> 注意：PATH 里的 `python`（WorkBuddy 自带 3.13）**没有** fastapi 依赖，
> 后端必须用 `py`（3.12）。可通过环境变量 `AINOVEL_PYTHON` 覆盖。

## 常用命令

```bash
# 冒烟测试（纯 Node，验证后端/WS/Bridge 链路）
npm run smoke

# Electron 环境冒烟（无窗口，验证 Electron 运行时下完整链路）
env -u ELECTRON_RUN_AS_NODE -u NODE_OPTIONS \
  D:/electron/dist/electron.exe --disable-gpu --in-process-gpu src/electron-smoke.js

# 开发运行（Electron 打开窗口）
npm start

# 打包 portable exe（自动先构建 renderer）
npm run dist
```

## 产物

`release/AInovel-Harness-0.1.0.exe`（portable，双击即用；首次启动自动拉起后端，
若 8765 端口被旧进程占用会自动清理）。

## 配置

`%APPDATA%/ainovel-harness/config.json`（首次运行自动创建）：

| 字段 | 默认 | 说明 |
|---|---|---|
| repoRoot | 探测默认位 | 引擎仓库根（含 app/dashboard/app.py） |
| pythonCmd | `py` | Python 解释器命令 |
| port | 8765 | 后端回环端口 |
| autoStart | true | 启动时自动拉起后端 |
| bookRoot/bookName | - | 当前书状态 |

环境变量覆盖：`AINOVEL_REPO_ROOT` / `AINOVEL_PYTHON` / `AINOVEL_APPSERVER_TOKEN_FILE`。
