/**
 * main.js — AInovel Harness 桌面端主进程。
 *
 * 形态：本地后端（FastAPI）+ 桌面窗口两种 UI 模式：
 * - spa（默认）：loadURL 后端自带 dashboard SPA（2026-09-05 前的唯一形态）。
 * - app（独立前端）：窗口 file:// 内嵌 dashboard dist（app/dashboard/frontend/dist
 *   构建产物，UI 与 spa 完全一致，SPA 代码零改动）。前端不再由 8765 承载：
 *   后端未起时窗口仍可显示内嵌页面（仅数据不可用）。
 * 端口解耦（2026-09-06）：SPA 源码烘焙了 http://127.0.0.1:8765（api.js BASE 总源 +
 * main.jsx 版本轮询），主进程在会话层把对该字面量的请求改写到真实后端地址，并把
 * app://dist/api/* 相对路径反代到后端——实际端口以 BackendManager 为准
 * （config.json "port" 字段可改），SPA 源码与后端代码零改动。
 * 模式选择：AINOVEL_UI 环境变量 > config.json uiMode > spa。
 * 启动链：加载配置 → 起后端（健康轮询/端口清理）→ 创建窗口 → 装载 UI。
 */
const { app, BrowserWindow, dialog, protocol, net, session } = require("electron");
const path = require("path");
const fs = require("fs");
const url_ = require("url");
const { BackendManager } = require("./core/backend");

// 软件渲染：无 GPU/远程桌面/虚拟机环境同样稳定
app.disableHardwareAcceleration();

// app:// 特权协议：standard+secure+supportFetchAPI 让 ES module 正常加载
// （file:// 下 module script 被 Chromium CORS 策略阻止，这就是不能直接 loadFile 的原因）。
// origin = app://dist，后端 CORS regex 已放行 ^app://（app.py 2026-09-05 补丁）。
// 必须在 app.ready 前注册。
protocol.registerSchemesAsPrivileged([
  { scheme: "app", privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true } },
]);

// userData 覆盖（测试/多开）：必须在 ready 前设置，requestSingleInstanceLock 按它区分实例
if (process.env.AINOVEL_USER_DATA) {
  app.setPath("userData", process.env.AINOVEL_USER_DATA);
}

let win = null;
let backend = null;

function loadConfig() {
  const p = path.join(app.getPath("userData"), "config.json");
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return {};
  }
}

/** 探测内嵌 dashboard dist（app 模式）。dev 态直接引用仓库构建产物（单一真相源）。 */
function resolveEmbeddedDistRoot() {
  const candidates = [
    // dev：src → desktop → ainovel-write（2 层）→ app/dashboard/frontend/dist
    path.join(__dirname, "..", "..", "app", "dashboard", "frontend", "dist"),
    // 打包：resources/dashboard-dist（electron-builder extraResources 随包携带，主候选）
    path.join(__dirname, "..", "..", "dashboard-dist"),
    // 打包（兼容布局）：resources/app/dashboard-dist
    path.join(__dirname, "..", "dashboard-dist"),
    // 打包且仓库在旁：win-unpacked/resources/app/src → 上 6 层 = ainovel-write
    path.join(__dirname, "..", "..", "..", "..", "..", "..", "app", "dashboard", "frontend", "dist"),
  ];
  for (const c of candidates) {
    if (fs.existsSync(path.join(c, "index.html"))) return c;
  }
  return null;
}

/** app://dist/<path> → 内嵌 dist 文件。index/目录回退 index.html。幂等（重复注册会抛）。 */
function ensureAppProtocol() {
  try {
    if (protocol.isProtocolHandled("app")) return true;
  } catch { /* ignore */ }
  const root = resolveEmbeddedDistRoot();
  if (!root) return false;
  protocol.handle("app", async (request) => {
    const u = new URL(request.url);
    let rel = decodeURIComponent(u.pathname || "/").replace(/^\/+/, "");
    // 相对路径 API（SPA 少量 fetch 不带 BASE，解析为本协议 origin）→ 反代到真实后端。
    // 请求体均为小 JSON（前端日志/情节应用），缓冲透传；响应原样返回不缓冲（保留流式）。
    if (backend && rel.startsWith("api/")) {
      const init = {
        method: request.method,
        headers: { "content-type": request.headers.get("content-type") || "application/json" },
      };
      if (!["GET", "HEAD"].includes(request.method)) {
        init.body = await request.arrayBuffer();
      }
      return net.fetch(`${backend.baseUrl}/${rel}${u.search}`, init);
    }
    if (!rel || rel.endsWith("/")) rel += "index.html";
    const filePath = path.join(root, rel);
    // 防目录穿越：解析后必须仍在 root 内
    if (!path.resolve(filePath).startsWith(path.resolve(root))) {
      return new Response("forbidden", { status: 403 });
    }
    return net.fetch(url_.pathToFileURL(filePath).toString());
  });
  return true;
}

// SPA 源码烘焙的后端地址（frontend/src/api.js:5 的 BASE 总源 + main.jsx:16 版本轮询）。
// 它仅作为「改写源字面量」存在：系统实际端口以 BackendManager 为准（config.json
// "port" 字段可改），主进程在会话层把对该字面量的 fetch/EventSource 请求改写到真实
// 地址——SPA 源码、dist、后端代码零改动。
const SPA_BAKED_ORIGIN = "http://127.0.0.1:8765";

let rewriterInstalled = false;

/** 会话级请求改写：SPA 烘焙的 8765 绝对地址 → 真实后端地址。
 *  Chromium match-pattern 不支持带端口的过滤串，filter 用不带端口的 127.0.0.1、
 *  端口判断放回调；幂等守卫（Electron 同一 webRequest 事件只允许一个监听器）。 */
function ensureRequestRewriter() {
  if (rewriterInstalled) return;
  rewriterInstalled = true;
  session.defaultSession.webRequest.onBeforeRequest({ urls: ["http://127.0.0.1/*"] }, (details, callback) => {
    if (backend && details.url.startsWith(`${SPA_BAKED_ORIGIN}/`) && backend.baseUrl !== SPA_BAKED_ORIGIN) {
      callback({ redirectURL: `${backend.baseUrl}${details.url.slice(SPA_BAKED_ORIGIN.length)}` });
      return;
    }
    callback({});
  });
}

function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 700,
    backgroundColor: "#faf7f0",
    title: "AInovel Harness",
    icon: path.join(__dirname, "..", "media", "icon.png"),
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      // SPA 是普通网页直连后端（app 模式跨源由后端 CORS 放行），无需 preload 桥
    },
  });
  win.setMenuBarVisibility(false);
  win.once("ready-to-show", () => win.show());
  win.on("page-title-updated", (e) => e.preventDefault());
  win.on("closed", () => { win = null; });
  // 后端就绪前先显示启动占位
  win.loadFile(path.join(__dirname, "loading.html"));
}

function boot() {
  const cfg = loadConfig();
  // 默认 spa（用户现有体验）；独立内嵌版 = AINOVEL_UI=app 或 config.json uiMode:"app"
  const uiMode = String(process.env.AINOVEL_UI || cfg.uiMode || "spa").toLowerCase();
  backend = new BackendManager({
    repoRoot: cfg.repoRoot,
    pythonCmd: cfg.pythonCmd,
    port: cfg.port,
    tokenFile: process.env.AINOVEL_TOKEN_FILE
      || path.join(app.getPath("userData"), "appserver-token.json"),
    onLog: (line) => console.log("[backend]", line),
    onState: (state, msg) => console.log("[backend]", state, msg || ""),
  });

  void (async () => {
    const ok = await backend.ensureRunning();
    if (ok && win && !win.isDestroyed()) {
      if (uiMode === "app" && ensureAppProtocol()) {
        await win.loadURL("app://dist/index.html");
        return;
      }
      // 协议注册失败（找不到内嵌 dist）：回退 spa，体验不断
      console.log("[main] 内嵌 dashboard dist 不可用，走 spa 模式");
      win.loadURL(backend.baseUrl);
      return;
    }
    // 后端启动失败：窗口内提示，保留可重试/退出入口
    if (win && !win.isDestroyed()) {
      const msg = encodeURIComponent("后端引擎启动失败，请检查 Python 3.12 环境与日志。可点击重试。");
      win.loadURL(`data:text/html;charset=utf-8,<body style="background:%23faf7f0;color:%23464034;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column"><h3>${msg}</h3><button onclick="location.reload()" style="padding:8px 20px;margin-top:12px">重试</button></body>`);
    }
  })();
}

// 单实例锁：重复启动时聚焦已有窗口
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(() => {
    // 打包产物真实链路冒烟（无窗口）：AINOVEL_SMOKE=1 <app.exe>
    if (process.env.AINOVEL_SMOKE === "1") {
      const backend = new BackendManager({
        port: Number(process.env.AINOVEL_SMOKE_PORT) || 8765,
        tokenFile: path.join(app.getPath("userData"), "appserver-token.json"),
        onLog: () => {},
      });
      void (async () => {
        const ok = await backend.ensureRunning();
        const tag = ok ? "SMOKE_OK" : "SMOKE_FAIL";
        try { process.stdout.write(tag + "\n"); } catch { /* ignore */ }
        console.log(tag);
        backend.dispose();
        setTimeout(() => app.exit(ok ? 0 : 1), 150);
      })();
      return;
    }

    // 会话级 8765 改写：spa/app 两模式都装（spa 换端口时，页面内烘焙地址同样命中真实后端）
    ensureRequestRewriter();
    createWindow();
    // app:// 协议挂载（ready 后注册；spa 模式注册了也无害）
    if (!ensureAppProtocol()) {
      console.log("[main] 内嵌 dashboard dist 未找到，app 模式将回退 spa");
    }
    boot();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on("before-quit", () => {
    backend?.dispose();
  });

  app.on("window-all-closed", () => {
    app.quit();
  });
}
