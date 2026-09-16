/**
 * preload.js — 桥接注入。renderer 通过 window.ainovelBridge 与 main 通信。
 * 协议与扩展版 webviewBridge 一致（rest/rpcCall/cmd/事件），token 不落 renderer。
 */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("ainovelBridge", {
  /** renderer → main 请求（{t:"rest"|"rpcCall"|"cmd"|"ready", ...}） */
  postMessage(msg) {
    ipcRenderer.send("ainovel:message", msg);
  },
  /** 订阅 main → renderer 事件（init/restResult/rpcEvent/connected/bookChanged/log/openPanel...）；返回退订函数 */
  onMessage(cb) {
    const listener = (_event, msg) => cb(msg);
    ipcRenderer.on("ainovel:event", listener);
    return () => ipcRenderer.removeListener("ainovel:event", listener);
  },
});
