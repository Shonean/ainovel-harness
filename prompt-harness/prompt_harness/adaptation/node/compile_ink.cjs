#!/usr/bin/env node
/* 改编层 v0 — ink 编译 runner（规格 §9.2）。
 *
 * stdin 收 ink 文本 → 用 vendored inkjs 编译器编译 → stdout 输出 story.ink.json。
 * 成功：exit 0，stdout = story.ink.json（inkVersion 21 字符串）。
 * 失败：exit 1，stderr = JSON {ok:false, errors:[{line, message, severity}]}。
 *
 * vendored inkjs：third_party/inkjs/inkjs/（见 UPSTREAM.md，禁止就地魔改上游）。
 */
"use strict";

const path = require("path");
const readline = require("readline");

const INKJS_DIR = path.resolve(
  __dirname, "..", "..", "..", "third_party", "inkjs", "inkjs");

function fail(errors) {
  process.stderr.write(JSON.stringify({ ok: false, errors: errors }) + "\n");
  process.exit(1);
}

let input = "";
const rl = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});
rl.on("line", (line) => { input += line + "\n"; });
rl.on("close", () => {
  let Compiler;
  try {
    // 直接 require 具体文件：inkjs 包根无 index.js / main，不能整包 require
    Compiler = require(path.join(INKJS_DIR, "compiler", "Compiler.js")).Compiler;
  } catch (e) {
    fail([{ line: 0, message: "inkjs 编译器加载失败: " + e.message, severity: "Error" }]);
    return;
  }

  const errors = [];
  let story;
  try {
    const compiler = new Compiler(input, {
      errorHandler: (message, errorType) => {
        // inkjs ErrorType：Author=0 / Warning=1 / Error=2
        const t = typeof errorType === "number" ? errorType : String(errorType);
        const m = /line (\d+)/.exec(String(message));
        errors.push({
          line: m ? parseInt(m[1], 10) : 0,
          message: String(message).replace(/^ERROR:\s*/, ""),
          severity: t === 2 || t === "Error" ? "Error" : (t === 1 || t === "Warning" ? "Warning" : "Author"),
        });
      },
    });
    story = compiler.Compile(); // 返回 Story 对象；.ToJson() 才是编译产物
  } catch (e) {
    if (!errors.length) {
      const m = /line (\d+)/.exec(String(e.message));
      errors.push({ line: m ? parseInt(m[1], 10) : 0, message: String(e.message), severity: "Error" });
    }
    fail(errors);
    return;
  }

  const hard = errors.filter((e) => e.severity === "Error");
  if (hard.length) {
    fail(errors);
    return;
  }

  try {
    process.stdout.write(story.ToJson());
    process.exit(0);
  } catch (e) {
    fail([{ line: 0, message: "编译产物序列化失败: " + e.message, severity: "Error" }]);
  }
});
