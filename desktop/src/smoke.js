/**
 * smoke.js — 桌面端核心链路冒烟（纯 Node，无需 Electron/GUI）。
 *
 * 验证：后端启动 → 健康检查 → token 读取 → WS 握手 → RPC status →
 *       BridgeHost 协议（模拟 renderer 发 rest/rpcCall → 回执）。
 * 用法：node src/smoke.js
 */
const path = require("path");
const os = require("os");
const { BackendManager } = require("./core/backend");
const { RestClient } = require("./core/rest");
const { RpcClient } = require("./core/rpc");
const { BridgeHost } = require("./core/bridge");

const PORT = 8771; // 冒烟用独立端口，避免干扰

async function main() {
  const lines = [];
  const backend = new BackendManager({
    port: PORT,
    tokenFile: path.join(os.tmpdir(), `ainovel-smoke-token-${process.pid}.json`),
    onLog: (l) => { lines.push(l); console.log("  [log]", l); },
    onState: (s, m) => console.log("  [state]", s, m || ""),
  });
  const rest = new RestClient(backend);
  const rpc = new RpcClient(backend);

  console.log("== 1. 启动后端 ==");
  const ok = await backend.ensureRunning();
  if (!ok) {
    console.error("FAIL: 后端启动失败");
    process.exit(1);
  }
  console.log("== 2. REST 健康 ==");
  const health = await rest.get("/api/prompt-harness/health");
  console.log("  health:", JSON.stringify(health));
  if (String(health?.status) !== "ok") throw new Error("health 非 ok");

  console.log("== 3. RPC 握手 + status ==");
  const connected = await rpc.connect();
  if (!connected) throw new Error("WS 握手失败");
  const st = await rpc.call("status");
  console.log("  status:", JSON.stringify(st));
  if (st?.server !== "ainovel-appserver") throw new Error("status 异常");

  console.log("== 4. Bridge 协议（模拟 renderer）==");
  const bridge = new BridgeHost({
    rpc,
    rest,
    getCurrentBook: () => ({ root: "", name: "" }),
    setCurrentBook: () => {},
    executeCommand: (cmd, args) => console.log("  [cmd]", cmd, JSON.stringify(args)),
  });
  bridge.wire();
  const replies = [];
  const reply = (m) => { replies.push(m); };
  await bridge.handle({ t: "ready" }, reply);
  await bridge.handle({ t: "rest", id: 100, method: "GET", path: "/api/prompt-harness/health", ph: true }, reply);
  await bridge.handle({ t: "rpcCall", id: 101, method: "status", params: {} }, reply);
  console.log("  replies:", JSON.stringify(replies));
  if (!replies.some((r) => r.t === "init")) throw new Error("init 未回");
  if (!replies.some((r) => r.t === "restResult" && r.id === 100 && r.ok)) throw new Error("rest 代理失败");
  if (!replies.some((r) => r.t === "restResult" && r.id === 101 && r.ok)) throw new Error("rpcCall 代理失败");

  console.log("== 5. 真实 REST 项目列表 ==");
  const projects = await rest.get("/api/projects");
  console.log("  projects:", JSON.stringify(projects).slice(0, 200));

  console.log("\n✅ SMOKE PASS（全部链路 OK）");
  rpc.disconnect();
  await backend.stop();
  process.exit(0);
}

main().catch((e) => {
  console.error("❌ SMOKE FAIL:", e);
  process.exit(1);
});
