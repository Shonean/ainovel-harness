/**
 * rpc.js — WS JSON-RPC 客户端（从扩展版 rpcClient.ts 平移，去掉 vscode 依赖）。
 *
 * 协议契约见 prompt-harness/tests/test_appserver_contract.py：
 *   initialize {protocol_version, token} → result
 *   ping / status / task.subscribe / task.unsubscribe / chat.start / approval.respond
 *   events: event/task.event, event/chat.token|tool_call|pending_proposal|done|error|applied,
 *           approval/request
 *
 * 状态恢复：重连成功后自动重放全部 task 订阅（last_seq 续传不重放历史）。
 */
const WebSocket = require("ws");

class RpcClient {
  constructor(backend) {
    this.backend = backend;
    this.ws = undefined;
    this.pending = new Map();
    this.handlers = new Map();
    this.nextId = 1;
    this.backoffMs = 500;
    this.closedByUser = false;
    this.subscriptions = new Map();
    this.reconnectTimer = undefined;
  }

  get connected() {
    return this.ws?.readyState === 1;
  }

  /** 订阅事件；返回 { dispose }。 */
  on(method, handler) {
    let set = this.handlers.get(method);
    if (!set) {
      set = new Set();
      this.handlers.set(method, set);
    }
    set.add(handler);
    return { dispose: () => set.delete(handler) };
  }

  async connect() {
    if (this.connected) {
      return true;
    }
    const token = this.backend.readToken();
    if (!token) {
      return false;
    }
    const url = `ws://127.0.0.1:${this.backend.port}/api/appserver/ws`;
    return new Promise((resolve) => {
      const ws = new WebSocket(url);
      let settled = false;
      const finish = (ok) => {
        if (!settled) {
          settled = true;
          resolve(ok);
        }
      };
      const guard = setTimeout(() => {
        ws.terminate();
        finish(false);
      }, 8000);

      ws.on("open", () => {
        ws.send(JSON.stringify({
          jsonrpc: "2.0", id: this.nextId++, method: "initialize",
          params: { protocol_version: 1, token },
        }));
      });
      ws.on("message", (raw) => {
        let msg;
        try {
          msg = JSON.parse(raw.toString());
        } catch {
          return;
        }
        // 响应帧
        if (msg.id !== undefined && (msg.result !== undefined || msg.error !== undefined)) {
          if (msg.id === 1 && !this.connected) {
            clearTimeout(guard);
            if (msg.error) {
              ws.close();
              finish(false);
              return;
            }
            this.ws = ws;
            this.backoffMs = 500;
            finish(true);
            void this.resubscribeAll();
            this.emit("_connected", {});
            return;
          }
          const p = this.pending.get(msg.id);
          if (p) {
            clearTimeout(p.timer);
            this.pending.delete(msg.id);
            if (msg.error) {
              p.reject(new Error(`[${msg.error.code}] ${msg.error.message}`));
            } else {
              p.resolve(msg.result);
            }
          }
          return;
        }
        // 事件帧
        if (typeof msg.method === "string") {
          if (msg.method.startsWith("event/task.event")) {
            const evSeq = Number(msg.params?.event?.seq ?? 0);
            const tid = String(msg.params?.task_id ?? "");
            if (tid && evSeq) {
              this.subscriptions.set(tid, Math.max(this.subscriptions.get(tid) ?? 0, evSeq));
            }
          }
          this.emit(msg.method, msg.params);
        }
      });
      ws.on("close", () => {
        clearTimeout(guard);
        const wasConnected = this.ws === ws || this.connected;
        this.ws = undefined;
        for (const p of this.pending.values()) {
          p.reject(new Error("连接已断开"));
        }
        this.pending.clear();
        this.emit("_disconnected", {});
        if (!this.closedByUser) {
          this.scheduleReconnect(wasConnected);
        }
        finish(false);
      });
      ws.on("error", () => { /* close 事件随后到达 */ });
    });
  }

  disconnect() {
    this.closedByUser = true;
    try {
      this.ws?.close();
    } catch {
      /* ignore */
    }
    this.ws = undefined;
  }

  scheduleReconnect(_wasConnected) {
    if (this.reconnectTimer) {
      return;
    }
    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = undefined;
      await this.connect();
    }, this.backoffMs);
    this.backoffMs = Math.min(this.backoffMs * 2, 15000);
  }

  async resubscribeAll() {
    for (const [tid, seq] of this.subscriptions) {
      try {
        await this.call("task.subscribe", { task_id: tid, last_seq: seq });
      } catch {
        /* 任务可能已结束 */
      }
    }
  }

  async call(method, params, timeoutMs = 30000) {
    if (!this.connected) {
      throw new Error("appserver 未连接");
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} 超时`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.ws.send(JSON.stringify({ jsonrpc: "2.0", id, method, params: params ?? {} }));
    });
  }

  emit(method, params) {
    for (const h of this.handlers.get(method) ?? []) {
      try {
        h(params);
      } catch (e) {
        console.error("[ainovel] handler error", method, e);
      }
    }
  }
}

module.exports = { RpcClient };
