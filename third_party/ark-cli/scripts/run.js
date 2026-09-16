#!/usr/bin/env node
// @volcengine/ark-cli npm bin wrapper。
// 二进制不内嵌在包里——由 postinstall 按当前平台从 CDN 下载到 <pkg>/bin/，
// 这里只负责 exec 它。对外只有 arkcli 一个产品，故产品名硬编码。
const path = require("path");
const fs = require("fs");
const { execFileSync } = require("child_process");

const platformMap = { darwin: "darwin", linux: "linux", win32: "windows" };
const archMap = { x64: "amd64", arm64: "arm64" };
const platform = platformMap[process.platform];
const arch = archMap[process.arch];

if (!platform || !arch) {
  console.error(`Unsupported platform: ${process.platform}-${process.arch}`);
  process.exit(1);
}

const ext = platform === "windows" ? ".exe" : "";
const binary = path.join(__dirname, "..", "bin", `arkcli-${platform}-${arch}${ext}`);

if (!fs.existsSync(binary)) {
  console.error(
    `arkcli: binary not found at ${binary}\n` +
      `安装时平台二进制可能下载失败。请尝试重新安装:\n` +
      `  npm install -g @volcengine/ark-cli\n` +
      `或手动重跑安装步骤:\n` +
      `  node ${path.join(__dirname, "postinstall.js")}`,
  );
  process.exit(1);
}

try {
  execFileSync(binary, process.argv.slice(2), { stdio: "inherit" });
} catch (err) {
  process.exit(Number.isInteger(err.status) ? err.status : 1);
}
