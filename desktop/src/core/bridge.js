/**
 * bridge.js — renderer ⇄ 宿主消息桥（协议与扩展版 webviewBridge.ts 完全一致）。
 *
 * renderer → host: {t:"ready"} {t:"rest",id,method,path,body,ph?} {t:"rpcCall",id,method,params} {t:"cmd",command,args}
 * host → renderer: {t:"init",bookRoot,bookName,connected} {t:"restResult",id,ok,data|error}
 *                  {t:"rpcEvent",method,params} {t:"connected",value} {t:"bookChanged",bookRoot,bookName}
 *
 * token 只存宿主；renderer 不直连后端（安全模型 §7）。
 */
const FORWARDED_EVENTS = [
  "event/chat.token",
  "event/chat.tool_call",
  "event/chat.pending_proposal",
  "event/chat.done",
  "event/chat.error",
  "event/chat.applied",
  "approval/request",
  "event/task.event",
];

class BridgeHost {
  /**
   * @param {object} deps
   * @param {import('./rpc').RpcClient} deps.rpc
   * @param {import('./rest').RestClient} deps.rest
   * @param {() => {root:string,name:string}} deps.getCurrentBook
   * @param {(root:string,name:string) => void} deps.setCurrentBook
   * @param {(command:string,args?:any) => void} [deps.executeCommand] 宿主命令分发
   */
  constructor(deps) {
    this.rpc = deps.rpc;
    this.rest = deps.rest;
    this.getCurrentBook = deps.getCurrentBook;
    this.setCurrentBook = deps.setCurrentBook;
    this.executeCommand = deps.executeCommand || (() => {});
    /** 事件推送出口（构造注入；renderer 就绪前为 no-op，避免空指针） */
    this.send = deps.send || (() => {});
    this.subs = [];
  }

  wire() {
    for (const method of FORWARDED_EVENTS) {
      this.subs.push(this.rpc.on(method, (params) => this.send({ t: "rpcEvent", method, params })));
    }
    this.subs.push(this.rpc.on("_connected", () => this.send({ t: "connected", value: true })));
    this.subs.push(this.rpc.on("_disconnected", () => this.send({ t: "connected", value: false })));
  }

  dispose() {
    for (const s of this.subs) {
      s.dispose();
    }
    this.subs = [];
  }

  /** 处理来自 renderer 的一条消息；reply(msg) 回发结果。 */
  async handle(m, reply) {
    if (!m || typeof m !== "object") {
      return;
    }
    switch (m.t) {
      case "ready":
        reply({
          t: "init",
          bookRoot: this.getCurrentBook().root,
          bookName: this.getCurrentBook().name,
          connected: this.rpc.connected,
        });
        break;
      case "rest": {
        try {
          const method = String(m.method || "GET").toUpperCase();
          const ph = !!m.ph;
          let data;
          if (method === "POST") {
            data = ph ? await this.rest.phPost(m.path, m.body) : await this.rest.post(m.path, m.body);
          } else if (method === "PUT") {
            data = ph ? await this.rest.phPut(m.path, m.body) : await this.rest.put(m.path, m.body);
          } else if (method === "DELETE") {
            data = ph ? await this.rest.phDel(m.path) : await this.rest.del(m.path);
          } else {
            data = ph ? await this.rest.ph(m.path) : await this.rest.get(m.path);
          }
          reply({ t: "restResult", id: m.id, ok: true, data });
        } catch (e) {
          reply({ t: "restResult", id: m.id, ok: false, error: String(e instanceof Error ? e.message : e) });
        }
        break;
      }
      case "rpcCall":
        try {
          const result = await this.rpc.call(m.method, m.params);
          reply({ t: "restResult", id: m.id, ok: true, data: result });
        } catch (e) {
          reply({ t: "restResult", id: m.id, ok: false, error: String(e instanceof Error ? e.message : e) });
        }
        break;
      case "cmd":
        this.executeCommand(String(m.command), m.args);
        break;
      default:
        break;
    }
  }
}

module.exports = { BridgeHost, FORWARDED_EVENTS };
