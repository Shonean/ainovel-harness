// bridge.js — renderer ⇄ 宿主通信（Electron 版）。
// 协议与扩展版 webviewBridge 完全一致：REST 代理 + RPC 事件 + 书状态。
// CSP 禁止直连后端：全部请求经 window.ainovelBridge（preload 注入）由 main 代发。
const host = (typeof window !== "undefined" && window.ainovelBridge) || null;

const pending = new Map();
let nextId = 1;

const listeners = new Map(); // key: rpcEvent:<method> | init | connected | setDiscuss | bookChanged
export function on(key, fn) {
  if (!listeners.has(key)) listeners.set(key, new Set());
  listeners.get(key).add(fn);
  return () => listeners.get(key)?.delete(fn);
}
function emit(key, payload) {
  for (const fn of listeners.get(key) || []) fn(payload);
}

if (host) {
  host.onMessage((m) => {
    if (!m || typeof m !== "object") return;
    switch (m.t) {
      case "restResult": {
        const p = pending.get(m.id);
        if (p) {
          pending.delete(m.id);
          if (m.ok) p.resolve(m.data);
          else p.reject(new Error(m.error));
        }
        break;
      }
      case "rpcEvent":
        emit(`rpcEvent:${m.method}`, m.params);
        break;
      case "init":
        state.bookRoot = m.bookRoot || "";
        state.bookName = m.bookName || "";
        state.connected = !!m.connected;
        emit("init", { ...state });
        break;
      case "connected":
        state.connected = !!m.value;
        emit("connected", state.connected);
        break;
      case "setDiscuss":
        emit("setDiscuss", m.payload);
        break;
      case "bookChanged":
        state.bookRoot = m.bookRoot || "";
        state.bookName = m.bookName || "";
        emit("bookChanged", { ...state });
        break;
      case "openPanel":
        // 宿主指示切换面板（如启动即主窗口）
        if (m.kind) window.dispatchEvent(new CustomEvent("ainovel:nav", { detail: m.kind }));
        break;
      case "refreshWorkbench":
        window.dispatchEvent(new CustomEvent("ainovel:refresh"));
        break;
      default:
        break;
    }
  });
}

export const state = {
  bookRoot: "",
  bookName: "",
  connected: false,
  panelKind: "workbench",
};

function hostRequest(msg) {
  return new Promise((resolve, reject) => {
    if (!host) {
      reject(new Error("未在 AInovel 桌面端中运行"));
      return;
    }
    const id = nextId++;
    pending.set(id, { resolve, reject });
    host.postMessage({ ...msg, id });
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error("宿主请求超时"));
      }
    }, 180000); // LLM 调用可能很长
  });
}

/** REST GET（ph=true → /api/prompt-harness 前缀） */
export function restGet(path, ph = false) {
  return hostRequest({ t: "rest", method: "GET", path, ph });
}
/** REST POST（ph=true → /api/prompt-harness 前缀） */
export function restPost(path, body, ph = false) {
  return hostRequest({ t: "rest", method: "POST", path, body, ph });
}
/** REST PUT（ph=true → /api/prompt-harness 前缀） */
export function restPut(path, body, ph = false) {
  return hostRequest({ t: "rest", method: "PUT", path, body, ph });
}
/** REST DELETE（ph=true → /api/prompt-harness 前缀） */
export function restDel(path, ph = false) {
  return hostRequest({ t: "rest", method: "DELETE", path, ph });
}
/** WS JSON-RPC 命令（chat.start / approval.respond / task.subscribe …） */
export function rpcCall(method, params) {
  return hostRequest({ t: "rpcCall", method, params });
}

/** 面板导航类命令在 renderer 内处理（单窗口切换），其余走 IPC 交 main。 */
const NAV_KINDS = {
  "ainovel.openWorkbench": "workbench",
  "ainovel.openWorkbenchEditor": "workbench",
  "ainovel.openMindMap": "mindmap",
  "ainovel.openAnalysis": "analysis",
  "ainovel.openInspire": "inspire",
  "ainovel.openFragment": "fragment",
  "ainovel.openSystem": "system",
  "ainovel.openTrain": "train",
  "ainovel.openLogs": "logs",
};
export function hostCommand(command, args) {
  if (NAV_KINDS[command]) {
    window.dispatchEvent(new CustomEvent("ainovel:nav", { detail: NAV_KINDS[command] }));
    return;
  }
  if (command === "ainovel.openAssistantPanel") {
    window.dispatchEvent(new CustomEvent("ainovel:assistant"));
    return;
  }
  host?.postMessage({ t: "cmd", command, args });
}
export const PH = "/api/prompt-harness";

if (host) {
  host.postMessage({ t: "ready" });
}
