#!/usr/bin/env node
// @volcengine/ark-cli postinstall。
// 由 npm 的 postinstall lifecycle 触发:读 manifest.json,按当前平台从 CDN 下载
// 对应二进制;稳定版 CDN 失败时回退 GitHub Release asset。下载后统一校验
// manifest 里的 sha256,落到 <pkg>/bin/,然后(非 CI 时)调 `arkcli +connect
// --refresh` —— 由二进制【收口的 Go 实现】下载最新 skill 到 <pkg>/skills/ 再装进
// 各 agent。skill 的下载/解压/校验逻辑不再在本脚本维护(避免 node / Go 两份),
// 与用户手动 `arkcli +connect --refresh` 重装/刷新【共用同一份实现】。
//
// 与内场 postinstall 的区别(对外洁净契约):
//   - 无 TEA 埋点
//   - 无 bytecloud 卸载逻辑
//   - 二进制不内嵌,运行时从 CDN 按平台下载
//
// 失败策略:
//   - 下载/校验二进制 = 核心,所有源失败则 exit 1(没有二进制这个包不可用,fail loud)
//   - +connect --refresh(下 skill + 装 agent) = 增强,失败仅告警,不影响安装结果(exit 0)

const fs = require("fs");
const path = require("path");
const os = require("os");
const http = require("http");
const https = require("https");
const crypto = require("crypto");
const { execFileSync } = require("child_process");

function writeInstallNotice(message, warning = false) {
  const terminal = process.platform === "win32" ? "\\\\.\\CONOUT$" : "/dev/tty";
  let fd = null;
  try {
    fd = fs.openSync(terminal, "w");
    fs.writeSync(fd, `${message}${process.platform === "win32" ? "\r\n" : "\n"}`);
    return;
  } catch (_) {
    (warning ? console.warn : console.log)(message);
  } finally {
    if (fd !== null) {
      try { fs.closeSync(fd); } catch (_) {}
    }
  }
}

function hasInteractiveInstallConsole() {
  if (process.stdout.isTTY || process.stderr.isTTY) return true;
  if (process.platform !== "win32") return false;
  let fd = null;
  try {
    fd = fs.openSync("\\\\.\\CONOUT$", "w");
    return true;
  } catch (_) {
    return false;
  } finally {
    if (fd !== null) {
      try { fs.closeSync(fd); } catch (_) {}
    }
  }
}

// —— 逃生阀 ——
if (process.env.ARKCLI_SKIP_POSTINSTALL === "1") {
  process.exit(0);
}

const platformMap = { darwin: "darwin", linux: "linux", win32: "windows" };
const archMap = { x64: "amd64", arm64: "arm64" };
const platform = platformMap[process.platform];
const arch = archMap[process.arch];
if (!platform || !arch) {
  console.error(
    `@volcengine/ark-cli: unsupported platform ${process.platform}-${process.arch}`,
  );
  process.exit(1);
}
const key = `${platform}-${arch}`;
const ext = platform === "windows" ? ".exe" : "";

// Snapshot product state before bootstrap or the downloaded binary can create
// it. Any lookup failure is historical/ambiguous and therefore not fresh.
let freshStateAbsent = false;
try {
  const home = os.homedir();
  freshStateAbsent = home !== "" && !fs.existsSync(path.join(home, ".arkcli"));
} catch (_) {}

// —— 读 manifest(发布时烘焙的"平台 -> 真实 cdnUrl + sha256"对照表) ——
let manifest;
try {
  manifest = JSON.parse(
    fs.readFileSync(path.join(__dirname, "..", "manifest.json"), "utf-8"),
  );
} catch (err) {
  console.error(
    `@volcengine/ark-cli: cannot read manifest.json: ${err.message || err}`,
  );
  process.exit(1);
}

const entry = manifest.platforms && manifest.platforms[key];
if (!entry || !entry.url) {
  console.error(`@volcengine/ark-cli: no binary entry for platform ${key} in manifest`);
  process.exit(1);
}
if (!/^[0-9a-f]{64}$/i.test(entry.sha256 || "")) {
  console.error(`@volcengine/ark-cli: missing or malformed sha256 for platform ${key}`);
  process.exit(1);
}

const binDir = path.join(__dirname, "..", "bin");
const binPath = path.join(binDir, `arkcli-${platform}-${arch}${ext}`);

function httpMod(url) {
  return url.startsWith("http://") ? http : https;
}

// 下载到临时文件,跟随 3xx 重定向(CDN/GitHub 边缘常有跳转)。
function download(url, dest, redirects = 0) {
  return new Promise((resolve, reject) => {
    if (redirects > 5) return reject(new Error("too many redirects"));
    const req = httpMod(url).get(url, (res) => {
      if (
        res.statusCode >= 300 &&
        res.statusCode < 400 &&
        res.headers.location
      ) {
        res.resume();
        const nextURL = new URL(res.headers.location, url).toString();
        return resolve(download(nextURL, dest, redirects + 1));
      }
      if (res.statusCode !== 200) {
        res.resume();
        return reject(new Error(`HTTP ${res.statusCode} for ${url}`));
      }
      const file = fs.createWriteStream(dest);
      file.on("error", (err) => {
        fs.rmSync(dest, { force: true });
        reject(err);
      });
      res.on("error", (err) => {
        file.close();
        fs.rmSync(dest, { force: true });
        reject(err);
      });
      res.pipe(file);
      file.on("finish", () => file.close(() => resolve()));
    });
    req.on("error", (err) => {
      fs.rmSync(dest, { force: true });
      reject(err);
    });
  });
}

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function binarySources(entry) {
  const sources = [{ kind: entry.kind || "cdn", url: entry.url }];
  if (entry.fallback && entry.fallback.url) {
    sources.push({
      kind: entry.fallback.kind || "fallback",
      url: entry.fallback.url,
    });
  }
  return sources;
}

async function downloadVerifiedBinary(sources, dest, expectedSHA) {
  const errors = [];
  for (const src of sources) {
    fs.rmSync(dest, { force: true });
    try {
      console.log(`@volcengine/ark-cli: downloading ${key} binary from ${src.kind}...`);
      await download(src.url, dest);
      if (expectedSHA) {
        const actual = sha256(dest);
        if (actual !== expectedSHA.toLowerCase()) {
          throw new Error(
            `sha256 mismatch\n  expected ${expectedSHA}\n  actual   ${actual}`,
          );
        }
      }
      return src;
    } catch (err) {
      fs.rmSync(dest, { force: true });
      errors.push(`${src.kind}: ${err.message || err}`);
      console.warn(
        `@volcengine/ark-cli: ${src.kind} binary source failed: ${err.message || err}`,
      );
    }
  }
  throw new Error(errors.join("; "));
}

(async () => {
  fs.mkdirSync(binDir, { recursive: true });
  const tmp = `${binPath}.download`;

  let installedFrom;
  try {
    installedFrom = await downloadVerifiedBinary(binarySources(entry), tmp, entry.sha256);
  } catch (err) {
    fs.rmSync(tmp, { force: true });
    console.error(`@volcengine/ark-cli: download failed: ${err.message || err}`);
    process.exit(1);
  }

  fs.renameSync(tmp, binPath);
  if (platform !== "windows") {
    fs.chmodSync(binPath, 0o755);
  }
  console.log(
    `@volcengine/ark-cli: installed ${path.basename(binPath)} from ${installedFrom.kind}`,
  );

  // CI 环境:不下 skill、不 +connect(无 agent 交互场景,且 CI 常无外网),直接收工。
  if (process.env.CI || process.env.BUILD_NUMBER || process.env.RUN_ID) {
    process.exit(0);
  }

  if (process.env.npm_config_global === "true") {
    // Enrollment publishes only inert exact-install evidence. Active consent
    // can be created only by later successful human CLI invocations.
    let bootstrapReady = process.platform !== "win32";
    let bootstrapError;
    if (process.platform === "win32") {
      try {
        execFileSync(binPath, ["_initialize-update-bootstrap"], {
          timeout: 120000,
          stdio: "ignore",
        });
        bootstrapReady = true;
      } catch (err) {
        bootstrapError = err;
      }
    }
    let automaticSupported;
    let mode;
    let enrollmentPhase;
    let policyError;
    if (bootstrapReady) {
      try {
        const supported = execFileSync(
          binPath,
          ["_refresh-update-cache", "--print-automatic-supported"],
          { timeout: 10000, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
        ).trim();
        if (supported !== "true" && supported !== "false") {
          throw new Error(`unexpected automatic-update capability ${JSON.stringify(supported)}`);
        }
        automaticSupported = supported === "true";
        if (automaticSupported) {
          enrollmentPhase = execFileSync(
            binPath,
            [
              "_refresh-update-cache", "--initialize-enrollment",
              `--fresh-state-absent=${freshStateAbsent}`,
            ],
            { timeout: 10000, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
          ).trim();
          if (![
            "disabled",
            "fresh_pending",
            "grace_completed",
            "automatic_active",
            "manual_reinstall_suspended",
          ].includes(enrollmentPhase)) {
            throw new Error(`unexpected automatic-update enrollment phase ${JSON.stringify(enrollmentPhase)}`);
          }
        }
        mode = execFileSync(
          binPath,
          ["_refresh-update-cache", "--print-mode"],
          { timeout: 10000, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
        ).trim();
      } catch (err) {
        policyError = err;
      }
    }
    if (mode === undefined) {
      try {
        mode = execFileSync(
          binPath,
          ["_refresh-update-cache", "--print-mode"],
          { timeout: 10000, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
        ).trim();
      } catch (err) {
        if (!policyError) policyError = err;
      }
    }
    if (mode !== "managed_silent") {
      try {
        execFileSync(binPath, ["_refresh-update-cache"], {
          timeout: 10000,
          stdio: "ignore",
        });
      } catch (_) {}
      if (bootstrapError) {
        writeInstallNotice(
          `@volcengine/ark-cli: recoverable automatic-update bootstrap is unavailable; this install remains notice/manual-only (${bootstrapError.message || bootstrapError})`,
          true,
        );
      } else if (policyError) {
        writeInstallNotice(
          `@volcengine/ark-cli: automatic-update capability could not be confirmed; this install remains notice/manual-only (${policyError.message || policyError})`,
          true,
        );
      } else if (automaticSupported === false) {
        writeInstallNotice("@volcengine/ark-cli: update notices/manual update remain active by default; silent automatic update is not enabled for this product and platform.");
      } else if (enrollmentPhase === "fresh_pending") {
        writeInstallNotice("@volcengine/ark-cli: silent stable patch updates are enabled by default for this new install; the first successful eligible human business command may schedule a background update. Disable with `arkcli config set update.mode disabled`.");
      } else if (enrollmentPhase === "manual_reinstall_suspended") {
        writeInstallNotice("@volcengine/ark-cli: a manual npm install or version change was detected, so silent automatic updates are paused. Keep this version with `arkcli config set update.mode disabled`; resume with `arkcli config set update.mode automatic`.");
      } else if (enrollmentPhase === "disabled" || mode === "disabled" || mode === "notify") {
        if (mode === "notify") {
          writeInstallNotice("@volcengine/ark-cli: the legacy notify-only update policy was preserved; silent automatic update is not active. Resume with `arkcli config set update.mode automatic`.");
        } else {
          writeInstallNotice("@volcengine/ark-cli: the disabled update policy was preserved; update notices remain active and silent automatic update is not active.");
        }
      } else if (enrollmentPhase === "grace_completed") {
        writeInstallNotice("@volcengine/ark-cli: a legacy first-run grace state was detected; the next successful eligible human business command may activate consent and schedule a background update. Disable with `arkcli config set update.mode disabled`.");
      } else if (enrollmentPhase === "automatic_active") {
        writeInstallNotice("@volcengine/ark-cli: silent stable patch update consent remains active for this exact install. Disable with `arkcli config set update.mode disabled`.");
      }
    }
  }

  // —— +connect --refresh:下载最新 skill 到 <pkg>/skills/ 再装进各 agent ——
  // skill 的下载/解压/校验【收口在 Go 二进制】(internal/skillfs.Refresh),postinstall
  // 不再自己下 skill。+connect 非交互(自动检测 agent + 全装),有/无 tty 都能跑:有
  // tty 时把日志接到 /dev/tty(体验更好),无 tty 时走默认 stdio(connect 不读 stdin、
  // 不会卡)。整体是【增强项】:下载或安装失败仅告警,不影响包安装结果(exit 0)。
  let ttyFd = null;
  try {
    ttyFd = fs.openSync("/dev/tty", "r+");
  } catch (_) {
    // 无 controlling tty(管道 / Windows 无 /dev/tty 等)— 仍执行,只是输出走默认 stdio
  }
  const stdio = ttyFd !== null ? ["ignore", ttyFd, ttyFd] : "inherit";
  try {
    execFileSync(binPath, ["+connect", "--refresh"], { stdio });
  } catch (err) {
    const msg =
      `arkcli: skill 下载/安装跳过 (${err.message || err});` +
      "稍后可手动跑 `arkcli +connect --refresh` 重新下载并安装";
    if (ttyFd !== null) {
      try {
        fs.writeSync(ttyFd, msg + "\n");
      } catch (_) {}
    } else {
      console.warn(msg);
    }
  }
  if (ttyFd !== null) {
    try {
      fs.closeSync(ttyFd);
    } catch (_) {}
  }
  process.exit(0);
})();
