import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 桌面端 renderer：产物被 Electron loadFile 以 file:// 加载 → 必须 base:'./'
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    assetsDir: ".",
    rollupOptions: {
      output: {
        entryFileNames: "main.js",
        chunkFileNames: "[name].js",
        assetFileNames: "main.[ext]",
      },
    },
  },
  esbuild: { jsx: "automatic" },
});
