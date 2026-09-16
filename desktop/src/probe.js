// probe.js — 探测 Electron 在本环境（沙箱/无 GUI 会话）是否可运行（无窗口模式）。
const { app } = require("electron");
app.disableHardwareAcceleration();
app.whenReady().then(() => {
  console.log("ELECTRON_OK", process.versions.electron, process.versions.chrome);
  app.exit(0);
});
setTimeout(() => {
  console.error("ELECTRON_TIMEOUT");
  app.exit(1);
}, 15000);
