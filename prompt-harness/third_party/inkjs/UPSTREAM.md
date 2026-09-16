# inkjs vendored 依赖说明

## 上游项目

- 项目：inkjs（inkle 官方 ink 叙事脚本语言的 JavaScript 移植：编译器 + 运行时）
- 仓库：<https://github.com/inkle/inkjs>
- npm 包名：`inkjs`
- vendored 版本：**2.4.0**
- 许可证：MIT（见同目录 `LICENSE.md`，上游随包分发）

## 获取方式

```bash
npm pack inkjs@2.4.0 --registry=https://registry.npmmirror.com
# 解包 tarball，取 package/ 内容放入本目录 inkjs/ 子目录
```

保留的文件（其余 source map / TS 源码 / bin 已剔除）：

| 保留项 | 用途 |
|---|---|
| `inkjs/dist/ink.js` | 自包含 UMD 运行时 bundle（`inkjs.Story`），供 `preview.html` 内嵌与浏览器运行 |
| `inkjs/compiler/` + `inkjs/engine/` | 编译器源码（CommonJS），供 `prompt_harness/adaptation/node/compile_ink.cjs` require |
| `inkjs/package.json`、`ink.js`、`ink.d.ts`、`ink.d.mts` | 包元数据与类型入口（`compiler/` 内部相对引用依赖其存在） |

## 用途

1. **编译**：`adaptation/node/compile_ink.cjs` 读 stdin 的 ink 文本，用
   `new Compiler(src, { errorHandler }).Compile().ToJson()` 产出 `story.ink.json`（inkVersion 21）。
2. **调试预览**：`packer.py` 把 `dist/ink.js` 与编译产物一起内嵌进 `preview.html`，
   提供 flag 面板 / stage 提示 / 结局 / 重开（仅验收调试用，非产品形态）。
3. 未来 godot-ink（C#）消费同一份 `story.ink.json`，不依赖本目录。

## 已验证的关键 API 约定（2.4.0）

- `new Compiler(src, { errorHandler: (message, errorType) => {} })`；errorType 为
  `ErrorType` 枚举（Author=0 / Warning=1 / Error=2）；编译错误 message 自带 `line N:` 行号。
- `Compile()` 返回 Story 对象，`.ToJson()` 才是 `story.ink.json` 字符串。
- 一行式 `* [选项] ~ var = true -> next` 不合法；必须用多行缩进式 choice。
- 外部函数必须先在 ink 头部声明 `EXTERNAL stage(cue)`，运行时 `story.BindExternalFunction("stage", fn)`。

## fork 纪律

- 本目录是**原样 vendored**（未修改上游任何文件）；升级/替换整目录覆盖，禁止就地魔改。
- 需要上游新特性时：重新 `npm pack` 指定版本覆盖 `inkjs/`，并更新本文件的版本与验证记录。
- 禁止把本目录的改动直接提给上游；如需 hack，写在 `compile_ink.cjs` / Python 侧。
