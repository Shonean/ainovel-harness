/**
 * backend.js — 后端引擎进程管理（Electron/Node 版，无 Electron 依赖，可在纯 Node 冒烟）。
 *
 * 职责：spawn `py -m dashboard.server --host 127.0.0.1 --port <port> --no-browser`
 * （cwd=引擎仓库 app/）、端口占用清理、健康轮询、token 读取、随宿主退出 kill。
 * 仅回环监听（GUI设计参考.md §7.1）。
 *
 * 与 VS Code 扩展版 backend.ts 的差异：无 vscode API，配置由外部注入，
 * 日志经 onLog 回调输出，状态经 onState 回调广播。
 */
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const net = require("net");
const os = require("os");
const path = require("path");
const { execFile } = require("child_process");

const ENGINE_MARKER = ["app", "dashboard", "app.py"];
/** 引擎仓库默认位（与扩展版 fallback 一致） */
const DEFAULT_REPO_ROOT = "C:\\Users\\24357\\Desktop\\AInovel Harness\\小说系统\\ainovel-write";
/** 默认 Python 命令：本机 `py` 指向 Python 3.12（依赖完整）；PATH 里的 python 是 3.13 无依赖 */
const DEFAULT_PYTHON = "py";

class BackendManager {
  /**
   * @param {object} opts
   * @param {string} [opts.repoRoot] 引擎仓库根目录
   * @param {string} [opts.pythonCmd] Python 解释器命令
   * @param {number} [opts.port] 回环端口
   * @param {(line: string) => void} [opts.onLog] 日志回调
   * @param {(state: string, msg?: string) => void} [opts.onState] 状态回调
   * @param {string} [opts.tokenFile] token 文件路径（默认 %TEMP%/ainovel-appserver-token.json）
   */
  constructor(opts = {}) {
    this.opts = opts;
    this.proc = undefined;
    this.starting = false;
    this.log = opts.onLog || (() => {});
    this.state = opts.onState || (() => {});
  }

  get port() {
    return Number(this.opts.port) || 8765;
  }

  get baseUrl() {
    return `http://127.0.0.1:${this.port}`;
  }

  get tokenFile() {
    return this.opts.tokenFile || path.join(os.tmpdir(), "ainovel-appserver-token.json");
  }

  /** 引擎仓库根：env 覆盖 → 配置 → 默认位（用引擎标志文件验证）。 */
  resolveRepoRoot() {
    const hasEngine = (p) => fs.existsSync(path.join(p, ...ENGINE_MARKER));
    for (const cand of [
      process.env.AINOVEL_REPO_ROOT,
      this.opts.repoRoot,
      DEFAULT_REPO_ROOT,
    ]) {
      if (cand && hasEngine(cand)) {
        return cand;
      }
    }
    this.log(`[backend] 未找到引擎仓库（缺 ${ENGINE_MARKER.join("/")}），回退默认位：${DEFAULT_REPO_ROOT}`);
    return DEFAULT_REPO_ROOT;
  }

  /** Python 命令：env 覆盖 → 配置 → 默认 py。 */
  resolvePython() {
    return process.env.AINOVEL_PYTHON || this.opts.pythonCmd || DEFAULT_PYTHON;
  }

  readToken() {
    try {
      const data = JSON.parse(fs.readFileSync(this.tokenFile, "utf8"));
      return typeof data?.token === "string" ? data.token : null;
    } catch {
      return null;
    }
  }

  isHealthy() {
    return new Promise((resolve) => {
      const req = http.get(`${this.baseUrl}/api/prompt-harness/health`, { timeout: 1500 }, (res) => {
        let body = "";
        res.on("data", (d) => { body += d.toString(); });
        res.on("end", () => resolve(res.statusCode === 200 && body.includes('"ok"')));
      });
      req.on("error", () => resolve(false));
      req.on("timeout", () => { req.destroy(); resolve(false); });
    });
  }

  /** 杀掉占用回环端口的旧进程（与扩展版 freePort 行为一致）。 */
  async freePort() {
    const port = this.port;
    await new Promise((resolve) => {
      const socket = net.connect({ port, host: "127.0.0.1" });
      socket.once("connect", () => {
        socket.destroy();
        this.killPortOwner(port).finally(() => resolve());
      });
      socket.once("error", () => resolve());
    });
  }

  async killPortOwner(port) {
    try {
      const { promisify } = require("util");
      const exec = promisify(execFile);
      const { stdout } = await exec("netstat", ["-ano", "-p", "TCP"]);
      for (const line of String(stdout).split(/\r?\n/)) {
        const m = line.match(new RegExp(`127\\.0\\.0\\.1:${port}\\s+.*LISTENING\\s+(\\d+)`, "i"))
          || line.match(new RegExp(`0\\.0\\.0\\.0:${port}\\s+.*LISTENING\\s+(\\d+)`, "i"));
        if (m) {
          const pid = Number(m[1]);
          if (pid > 0 && pid !== process.pid) {
            this.log(`[backend] 端口 ${port} 被旧进程 PID=${pid} 占用，清理中…`);
            try {
              process.kill(pid);
            } catch {
              /* 已退出则忽略 */
            }
            await new Promise((r) => setTimeout(r, 800));
          }
        }
      }
    } catch (e) {
      this.log(`[backend] 清理端口失败（忽略）：${e}`);
    }
  }

  /**
   * 确保后端运行。返回 true 表示健康可用。
   */
  async ensureRunning() {
    if (await this.isHealthy()) {
      return true;
    }
    if (this.starting) {
      return false;
    }
    this.starting = true;
    try {
      this.state("starting", "清理端口…");
      await this.freePort();

      const python = this.resolvePython();
      const appDir = path.join(this.resolveRepoRoot(), "app");
      if (!fs.existsSync(path.join(appDir, "dashboard", "app.py"))) {
        throw new Error(`找不到引擎目录：${appDir}（请检查 repoRoot 配置）`);
      }

      this.state("starting", "启动 Python 进程…");
      const env = { ...process.env };
      // 隔离 token 文件到宿主可读位置（避免多实例互相踩）
      if (this.opts.tokenFile) {
        env.AINOVEL_APPSERVER_TOKEN_FILE = this.opts.tokenFile;
      }
      this.proc = spawn(python, [
        "-m", "dashboard.server",
        "--host", "127.0.0.1",
        "--port", String(this.port),
        "--no-browser",
      ], {
        cwd: appDir,
        env,
        windowsHide: true,
      });
      this.proc.stdout?.on("data", (d) => this.log(String(d).replace(/\n$/, "")));
      this.proc.stderr?.on("data", (d) => this.log(String(d).replace(/\n$/, "")));
      this.proc.on("exit", (code) => {
        this.log(`[backend] 进程退出 code=${code}`);
        this.proc = undefined;
      });

      this.state("starting", "等待健康检查（首次初始化约 10-30s）…");
      const deadline = Date.now() + 90_000;
      while (Date.now() < deadline) {
        if (!this.proc) {
          throw new Error("后端进程提前退出，详见日志");
        }
        if (await this.isHealthy()) {
          this.state("ready", "");
          this.log("[backend] 就绪");
          return true;
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
      throw new Error("健康检查超时（90s），详见日志");
    } catch (e) {
      this.state("error", e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      this.starting = false;
    }
  }

  async restart() {
    await this.stop();
    return this.ensureRunning();
  }

  async stop() {
    if (this.proc) {
      const p = this.proc;
      this.proc = undefined;
      p.kill();
      await new Promise((resolve) => {
        const t = setTimeout(() => {
          try { p.kill("SIGKILL"); } catch { /* ignore */ }
          resolve();
        }, 3000);
        p.once("exit", () => { clearTimeout(t); resolve(); });
      });
    }
  }

  dispose() {
    try {
      this.proc?.kill();
    } catch {
      /* ignore */
    }
  }
}

module.exports = { BackendManager };
