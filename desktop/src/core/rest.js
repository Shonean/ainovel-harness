/**
 * rest.js — 宿主侧 REST 薄封装（从扩展版 rest.ts 平移，零依赖）。
 */
class RestClient {
  constructor(backend) {
    this.backend = backend;
  }

  async request(method, path, body) {
    const res = await fetch(`${this.backend.baseUrl}${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { raw: text };
    }
    if (!res.ok) {
      const detail = typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail ?? data);
      throw new Error(`HTTP ${res.status} ${path}: ${detail}`);
    }
    return data;
  }

  get(path) { return this.request("GET", path); }
  post(path, body) { return this.request("POST", path, body ?? {}); }
  put(path, body) { return this.request("PUT", path, body ?? {}); }
  del(path) { return this.request("DELETE", path); }
  ph(path) { return this.request("GET", `/api/prompt-harness${path}`); }
  phPost(path, body) { return this.request("POST", `/api/prompt-harness${path}`, body ?? {}); }
  phPut(path, body) { return this.request("PUT", `/api/prompt-harness${path}`, body ?? {}); }
  phDel(path) { return this.request("DELETE", `/api/prompt-harness${path}`); }
}

module.exports = { RestClient };
