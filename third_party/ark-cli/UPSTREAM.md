# ark-cli vendor 记录（UPSTREAM.md）

> 集成分级：L2 源码集成（fork 入 `third_party/ark-cli/`，对齐总计划 §3.2 台账行）
> 用途：Seedream 生图 / Seedance 视频 / 模型与参数校验 / 用量记账 —— 期 3「资产管线」的生图驱动底座。

## 上游信息

| 项 | 值 |
|---|---|
| 项目 | volcengine/ark-cli（火山方舟 CLI，Go 实现 + Node 启动器壳） |
| 上游仓库 | https://github.com/volcengine/ark-cli |
| npm 包 | `@volcengine/ark-cli` |
| 上游版本 | **1.0.27**（tag v1.0.27） |
| 许可证 | Apache-2.0（本目录 `LICENSE` 为上游原文） |
| 形态 | npm 包只是启动器壳（`scripts/run.js` exec 平台二进制）；真正逻辑是 Go 二进制，由 `manifest.json` 按平台 CDN 分发（bytednsdoc CDN，GitHub Release 兜底），sha256 校验 |
| 上游 commit | 本次未走 git clone；以 npm `1.0.27` + CDN 二进制快照为准，回源锚点 = GitHub tag `v1.0.27` |

## 本目录布局

```
third_party/ark-cli/
  UPSTREAM.md                    # 本文件
  LICENSE                        # Apache-2.0（上游原文）
  package.json / manifest.json   # 上游 npm 壳包原样（未改动）
  scripts/run.js                 # 上游启动器原样：exec bin/<平台二进制>
  scripts/postinstall.js         # 上游 postinstall（本仓库不执行，见下方"获取方式"）
  bin/arkcli-windows-amd64.exe   # 1.0.27 windows-amd64 二进制（sha256 已校验）
```

二进制校验：`648d164859ab0fb6883c805947322a479bea4bdad683480e2621f5b5760b21c9`
（与上游 `manifest.json` 中 windows-amd64 条目逐字一致，`sha256sum` 可复核。）

## 获取方式（2026-09-12，共 3 次网络尝试，全部命中）

1. `npm view @volcengine/ark-cli` → 确认存在 1.0.27、Apache-2.0、tarball 走 npmmirror。
2. `npm pack @volcengine/ark-cli` → 下载壳包 tarball 并解压落盘（package/LICENSE/README/manifest/scripts）。
3. `curl` 字节 CDN 下载 `arkcli-1.0.27-windows-amd64.exe` → sha256 校验通过 → `bin/`。
   未执行上游 `postinstall.js`（避免其 `+connect --refresh` 副作用：下载 skills 并写入本机 agent 配置；
   对应下方 patches「裁剪 skills/docs」）。

运行确认：`./bin/arkcli-windows-amd64.exe --version` → `arkcli version 1.0.27`。
关键命令面（供适配层使用）：`+gen --model <id> --size WxH "<prompt>"`（图片 inline 返回，视频回 task id，`--wait` 阻塞）、
`api <action> --params '{...}'`、`models search seedream`、`usage stats --start --end`、
`init-volc`（无交互引导 profile）、全局 `--api-key` 覆盖。

## 计划 patches（对应总计划 §3.2 ark-cli 行，均未实施）

> 落法：能不改二进制就在适配层（`prompt_harness/media/ark_runner.py` 与驱动层）解决；
> 需要改 CLI 行为的，等上游 fork 仓库建立后在 `patches/` 下维护补丁文件并记录改动理由与去除成本（fork 纪律 §3.3）。

| # | patch | 理由 | 去除成本 | 目前落点 |
|---|---|---|---|---|
| 1 | 去交互式 SSO：认证改读应用配置/profile（`init-volc` 路线），杜绝首跑交互挂起 | 产品内嵌 runner 不允许等用户敲键盘 | 上游已带 `init-volc`/`--api-key`，大概率零 patch，仅适配层封 stdin | ark_runner.py（subprocess 关 stdin + 超时） |
| 2 | 稳定 JSON 输出：`+gen`/`api` 输出机器可解析的单对象 JSON（`--output json` 类开关），日志走 stderr | runner 需要稳定 parsed_json | 中：需上游加输出模式；先在适配层做容错解析（剥日志取 JSON） | ark_runner.py 解析层 |
| 3 | 产物直落资产库：`+gen` 图片/视频产物落指定目录（而非临时 CDN URL 过期） | 资产库内容寻址需要本地字节 | 中：适配层先落盘再 put_bytes；上游补丁可省一次中转 | seedream.py + asset_store.py |
| 4 | 批量/轮询适配：Seedance 任务 id 批量提交 + `--wait` 语义可控轮询 | 长任务批量排队与断点续跑 | 中 | **已落适配层**：`media/drivers/seedance.py`（任务提交/轮询/超时/draft 控本，无凭据 skipped） |
| 5 | 成本记账钩子：`usage stats`/`billing` 输出结构化、可回写 `llm_calls` 同款账本 | 预算闸需要按次记账 | 低：适配层解析即可 | **已落适配层**：`media/usage_ledger.py`（jsonl 账本+驱动钩子+按驱动/按日聚合，实测 seedance 行与汇总） |
| 6 | 裁剪 skills/docs：不执行 `+connect`，不分发 skills.tar.gz | 产品零安装体积与副作用 | 零（本仓库本就不跑 postinstall） | 本目录布局已体现 |

## 同步策略

- 节奏：每季度或上游关键特性发布时评估一次（fork 纪律 §3.3）。
- 同步动作：`npm view @volcengine/ark-cli` 看新版 → `npm pack` 重拉壳包 → CDN 重下平台二进制 → sha256 校验 → 更新本文件版本行。
- 许可合规：Apache-2.0 保留 LICENSE 与上游版权声明；产品打包时汇总第三方声明（总计划 §5「许可随包」）。
