/**
 * electron-smoke.js — Electron 主进程环境下的核心链路冒烟（无窗口）。
 *
 * 用法：env -u ELECTRON_RUN_AS_NODE -u NODE_OPTIONS \
 *        D:/electron/dist/electron.exe --no-sandbox src/electron-smoke.js
 * 验证：Electron 运行时下 spawn Python 后端 → WS 握手 → Bridge 协议代理。
 */
const { app } = require("electron");
const path = require("path");
const os = require("os");
const { BackendManager } = require("./core/backend");
const { RestClient } = require("./core/rest");
const { RpcClient } = require("./core/rpc");
const { BridgeHost } = require("./core/bridge");

app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("disable-gpu-compositing");
app.commandLine.appendSwitch("disable-software-rasterizer");

async function run() {
  const PORT = 8772;
  const backend = new BackendManager({
    port: PORT,
    tokenFile: path.join(os.tmpdir(), `ainovel-esmoke-token-${process.pid}.json`),
    onLog: (l) => console.log("  [log]", l),
  });
  const rest = new RestClient(backend);
  const rpc = new RpcClient(backend);

  console.log("== E1. Electron 下启动后端 ==");
  const ok = await backend.ensureRunning();
  if (!ok) throw new Error("后端启动失败");

  console.log("== E2. RPC 握手 ==");
  if (!(await rpc.connect())) throw new Error("WS 握手失败");
  const st = await rpc.call("status");
  console.log("  status:", JSON.stringify(st));

  console.log("== E3. Bridge 代理 ==");
  const bridge = new BridgeHost({
    rpc, rest,
    getCurrentBook: () => ({ root: "", name: "" }),
    setCurrentBook: () => {},
    executeCommand: () => {},
  });
  bridge.wire();
  const replies = [];
  await bridge.handle({ t: "ready" }, (m) => replies.push(m));
  await bridge.handle({ t: "rpcCall", id: 201, method: "status", params: {} }, (m) => replies.push(m));
  await bridge.handle({ t: "rest", id: 202, method: "GET", path: "/api/projects", ph: false }, (m) => replies.push(m));
  const initR = replies.find((r) => r.t === "init");
  const rpcR = replies.find((r) => r.id === 201);
  const restR = replies.find((r) => r.id === 202);
  console.log("  init:", JSON.stringify(initR));
  console.log("  rpcCall ok:", rpcR?.ok, "rest ok:", restR?.ok, "projects:", Array.isArray(restR?.data?.projects) ? restR.data.projects.length : "?");

  console.log("\n✅ ELECTRON SMOKE PASS");
  rpc.disconnect();
  await backend.stop();
  app.exit(0);
}

app.whenReady().then(() => {
  run().catch((e) => {
    console.error("❌ ELECTRON SMOKE FAIL:", e);
    app.exit(1);
  });
});
setTimeout(() => { console.error("TIMEOUT"); app.exit(2); }, 120000);
