# R3 成本、审核与发行设计规格（在线代理计费/内容审核/Steam 上架/一键发布包/LGPL 检查表）

> 日期：2026-09-12 · 状态：待审阅 · 上游文档：`docs/总计划-三线串联与开源集成.md` v2、`docs/superpowers/specs/2026-09-11-adaptation-layer-design.md`、`docs/superpowers/specs/2026-09-12-r1b-online-slice-design.md`、`docs/superpowers/specs/2026-09-12-r2-expressiveness-design.md`
> 定位：期 7。把「能玩」变成「能发行」：在线代理与计费（发行版 LLM/TTS 必走代理+限流+预算闸）、
> 内容审核三层防线、Steam 上架链（GodotSteam/商店素材/AI 披露/多语言）、一键发布包与素材包产物、
> ffmpeg LGPL 构建切换检查表。本文档为设计先行；依赖期 5（R1b 接口契约）与期 6（R2 素材管线）交付。

---

## 1. 背景与目标

R1b 之后：在线模式由本机后端实现导演契约，客户端 key 零暴露，回合成本已按对账口径记录。
但发行出去的游戏没有本机后端，玩家流量必须经我们的代理（key 安全、防滥用、计费对账）；
同时发行行为本身引入新的合规面：内容审核、Steam AI 内容披露、第三方许可（ffmpeg GPL）。

R3 目标：**给在线模式装上计费闸门，给发行行为装上合规闸门，两条闸门都做成「一键」可执行**——

1. 云网关（game-gateway）：鉴权/转发/限流/预算闸/记账，实现 R1b 导演服务接口契约 v1，客户端零改动切换。
2. 内容审核三层：制作期预检 → 在线实时审核 → ContentRiskBlocked 重试与人工兜底。
3. Steam 上架链：GodotSteam 桥接、商店素材（capsule/截图/预告片=漫剧管线副产品）、AI 披露强制项、多语言框架。
4. 一键发布包 + 素材包：产物定义、构建 gate 检查表、LGPL 构建切换。

## 2. 范围

### 2.1 做

1. game-gateway 网关服务（鉴权/LLM+TTS 转发/限流/预算闸/usage 记账），实现导演契约 v1
2. 客户端端点切换（online_config.json：本机后端 → 网关），BYOK（发行者自有 key 托管）最小形态
3. 审核三层 + ContentRiskBlocked 流转 + 人工兜底队列（桌面侧查看）
4. 制作期内容预检并入改编层 validation
5. GodotSteam 集成（成就/云存档桥接）+ store_kit 商店素材导出器 + AI 披露 gate + 多语言框架（zh→en 一版）
6. 一键发布包构建（产物定义 + smoke + 检查表 gate）+ THIRD-PARTY-NOTICES 汇总
7. ffmpeg LGPL 构建切换检查表（license audit 脚本 + 编码器配置化 + 发布 gate）

### 2.2 不做

- 不做完整账号体系/支付系统（玩家侧匿名会话 token + 发行者 BYOK 最小形态；完整商业模型见 OQ-1）
- 不做网关高可用集群/多地域（单实例 + 监控 + 降级离线兜底即可，规模出现再加）
- 不做多语言全量铺开（只做框架 + zh-CN 默认 + en 一版）
- 不做游戏内支付/IAP、DLC 管理等 Steamworks 深度功能（成就/云存档即可）
- 不改 R1b 导演服务接口契约语义（网关是实现方之一，不是新协议）
- 不做漫剧正片横屏化（横屏仅预告片 profile，复用 R2 管线）

## 3. 术语

| 术语 | 含义 |
|---|---|
| game-gateway | 云端代理网关：发行版游戏在线模式的唯一出入口 |
| BYOK | Bring Your Own Key：发行者把自有云 LLM/TTS key 托管到网关，玩家流量记在其账户 |
| 发行者 | 在 AInovel Harness 中制作游戏并对外发布的用户（相对「玩家」） |
| 预算闸 | 会话/用户/全局三级的成本与频率上限，超限返回降级指令 |
| 三层审核 | 制作期预检（离线）/在线实时审核（网关）/人工兜底（队列） |
| ContentRiskBlocked | 在线实时审核拦截事件；有标准重试与兜底流转 |
| store_kit | 商店素材包产物目录（capsule/截图/预告片/披露/文案） |
| 发布 gate | 一键发布包构建前置的强制检查表，未全绿不出包 |
| license audit | ffmpeg/编码器组件许可扫描脚本（LGPL 合规证据） |

## 4. 与上游文档的锚点

| 锚点 | 内容 | 本文对应 |
|---|---|---|
| 总计划 §4 期 7 行 | 在线代理与计费、内容审核、Steam（GodotSteam）、商店素材、AI 披露、多语言、上架脚本；验收「一键发布包 + 素材包；在线模式成本过闸」 | 全文/§12 |
| 总计划 §5 仓库布局 | templates/godot addons 含 GodotSteam；desktop 进程模型；「发行版走代理」；许可随包（THIRD-PARTY 汇总） | §5/§7/§8 |
| 总计划 §6 风险表 | 在线 LLM 代理计费与防滥用（高）；Steam AI 内容披露（高）；ffmpeg GPL（中）；Seedream/TTS 商用授权（高）；成本失控（中） | §5/§7.3/§9/§14 |
| 总计划 §7 决策 4/8 | 发行版走代理+计费；SDK 随包/扩展层不魔改内核 | §5/§7.1 |
| R1b spec §8 | 导演服务接口契约 v1、online_config.json、降级机制、turns 成本口径 | §5.2/§5.4/§6 |
| R2 spec §9.3 | media 预算闸（check/commit/ledger 口径） | §5.5 |
| 改编层 spec §5.9 | validation.json 结构（预检结果并入） | §6.2 |
| `drama/projector.py`/`compose.py` | 预告片/截图素材复用漫剧管线的接入点 | §7.2 |

## 5. 在线代理与计费（game-gateway）

### 5.1 架构决策：为什么必须有网关

- key 安全：发行版客户端包内不得含任何云 key（R1b 已定）；LLM 与 TTS 的凭据只能存在服务端。
- 防滥用：限流（token bucket）+ 输入限长 + 预算闸，防刷量与恶意消耗（总计划 §6 高风险项）。
- 计费对账：所有成本在网关单点记账，按用户/会话/回合粒度可查（R1b turns.jsonl 是同口径雏形）。
- 审核单点：在线实时审核在网关执行（输入、输出双向），避免客户端可绕过。
- 复用：网关实现 R1b 导演契约 v1（persona_guard/越权强校验逻辑直接复用后端代码），非新协议。

### 5.2 网关接口（实现 R1b 契约 v1 + 会话管理）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/session/init` | 匿名设备指纹 + 游戏签名 → session token + 配额余量（玩家侧无账号，OQ-1） |
| POST | `/v1/director/turn` | 与 R1b `/ai-creation/game/turn` 请求/响应/SSE 完全一致 |
| WS | `/v1/tts/stream` | TTS 流式转发（网关持云 key，向玩家回传 PCM 分片） |
| GET | `/v1/quota` | 余量查询（客户端 UI 显示） |
| POST | `/v1/telemetry` | 降级/崩溃遥测（可选，最简 JSON） |

网关内部：`director/turn` 处理链 = 鉴权 → 限流 → 输入审核 →（BYOK key 选择）→ LLM → 越权强校验
（复用 R1b guards）→ 输出审核 → 记账 → SSE 回传。

### 5.3 key 形态（AInovel 端自有 key 的两种形态）

| 形态 | 谁 | key 在哪 | 流量记谁 |
|---|---|---|---|
| 自用（开发/桌面内玩） | AInovel 用户自己 | 本机后端配置（R1b 形态，不经网关） | 本机 llm_calls 日志 |
| 发行（玩家在玩） | 发行者 | 网关加密托管（信封加密，最小权限，支持轮换）；或官方试用额度 | 网关 usage 按发行者账户汇总 |

发行者在 AInovel 桌面「发行设置」中填 key → 加密上传网关托管；未托管 key 的发行者只能发纯离线版
（在线模式入口关闭）或使用官方试用额度（额度数值见 OQ-1）。

### 5.4 限流与预算闸（网关侧）

```json
{
  "per_turn": { "input_max_chars": 200, "cost_est_max": 0.02 },
  "per_session": { "token_cap": 60000, "turn_cap": 500, "ttl_hours": 6 },
  "per_user_daily": { "cost_cap": 1.0, "request_rate_per_min": 10 },
  "on_exhaust": "degrade_offline"
}
```

- 超限返回 `budget_exhausted`（R1b §8.2 error 事件已预留）→ 客户端复用断线降级机制切离线
  （**在线永远只是增强，离线永远可玩**——降级即合规兜底）。
- 数值为首版建议，需用户拍板（OQ-5）；数值变更走配置不发版。

### 5.5 记账（usage 事件）

```json
{"event_id": "u_...", "ts": "...", "issuer_id": "iss_...", "session_id": "s_...",
 "kind": "llm | tts", "model": "doubao-lite-32k", "tokens_in": 830, "tokens_out": 96,
 "chars": 86, "cost_est": 0.0011, "game_slug": "paper-classroom"}
```

- 落 `usage_events`（JSONL 分日滚动 + 汇总表）；与 R1b `turns.jsonl`/R2 `budget_ledger.jsonl`
  同成本口径（driver manifest.cost_items 派生），保证三处账能对上。
- 对账验收：任一会话 `sum(usage_events.cost_est)` == 该会话 turns/ledger 汇总。

## 6. 内容审核（三层防线）

### 6.1 层 1：制作期预检（离线，改编层收口）

- 新增 `prompt_harness/adaptation/content_risk.py`：对 pack 全部文本（story/interaction/llm_zones
  persona_guard）与 assets prompt 做敏感预检——本地敏感词表（首批）+ 可选云审核 API（OQ-3）。
- 结果并入 `validation.json.content_risk`：`{"errors": [...], "warnings": [...], "categories": {...}}`；
  errors 非空 → pack 构建失败（沿用改编层「校验失败即构建失败」语义）。
- 阈值可配置（题材分级：中式恐怖允许中低血腥暗示，政治/色情零容忍）——阈值表需用户拍板（OQ-3）。

### 6.2 层 2：在线实时审核（网关，双向）

- 玩家输入：进 LLM 前检查；LLM 输出：回传与送 TTS 前检查。
- 审核结论 schema：

```json
{"action": "pass | rewrite | block", "category": "politics|porn|violence|other", "score": 0.0, "source": "local|cloud_api"}
```

- v0 实现：本地词表 + 规则（零外部依赖可跑）；云审核 API 为增强项（开通后配置即用）。

### 6.3 层 3：ContentRiskBlocked 流转（重试与人工兜底）

```
block ──▶ 重试 1 次（system 附加收紧提示 + 违规类别）
      ├── pass ──▶ 正常回传（记 rewrite 计数）
      └── 再 block ──▶ fallback 安全文案回传（离线包内置，非 LLM 生成）
                        + turns/usage 记录 content_risk_blocked
                        + 进人工兜底队列
```

- 人工兜底队列：网关侧存 JSONL；AInovel 桌面新增「审核队列」页（拉取列表 → 逐条：维持拦截 /
  放行并加入白名单 / 改写后放行）。发行版没有桌面，队列由发行者在自己的桌面查看（BYOK 账户维度）。
- 兜底文案属 pack 静态内容，走层 1 预检，保证兜底本身绝对安全。

## 7. Steam 上架链

### 7.1 GodotSteam 集成（L2，随模板工程 vendor）

- vendor 至 `templates/godot/addons/godotsteam/`（MIT，附 UPSTREAM 式备注：上游 commit/同步策略）。
- 桥接面（最小集）：
  - 成就：`steam_achievements.json`（声明式：achievement_id ← beat/ending/flag 条件），触发点挂在
    InkBridge 既有回调上（不改状态机语义）；
  - 云存档：ink 状态 + flags + 设置的序列化快照，启动/章节节点自动同步；
  - 其余 Steamworks 能力（创意工坊/交易卡等）本期不做。
- 依赖：Steamworks 合作开发者账号 + App ID（外部，OQ-4）；无 App ID 时用 GodotSteam 沙盒模式开发。

### 7.2 商店素材 = 漫剧管线副产品（store_kit 导出器）

- 新增 `drama/store_kit.py` 导出器，产物（规格以 Steamworks 后台当前要求为准，OQ-4）：

| 素材 | 规格（首版） | 来源 |
|---|---|---|
| 主 capsule | 1230x920 | Seedream 关键帧横版重渲染 + 标题排版模板 |
| header | 920x430 | 同上模板复用 |
| library capsule | 600x900 | 立绘/关键帧竖版 + 模板 |
| 截图 ×5~10 | 1920x1080 | 游戏内捕获脚本（Godot 截图命令）或 Seedream 横版关键帧 |
| 预告片 | 1920x1080、30~60s、H.264 | 漫剧管线横屏 profile：横版关键帧 + 运镜复用（R2 camera 词汇表）+ 配音/字幕轨复用 |

- **横屏策略**：资产库对关键镜头背景/立绘出横竖双版（同 prompt 两个比例，R2 已定双版口径）；
  预告片镜头序列取自 drama shots 的 `importance=key` 子集 + 片头片尾卡模板。
- 素材生成同样计入预算闸（ledger 记账），防止商店素材生成环节成本漂移。

### 7.3 AI 内容披露（强制项）

- Steam 上架问卷要求披露预生成 AI 内容与实时生成 AI 内容 → 发布 gate 强制项：
  - `store_kit/disclosure_ai.md` 模板自动生成，含两段：①预生成（Seedream 图/Seedance 片段/Kokoro
    与云 TTS 配音/LLM 包装文本）②实时（在线模式 LLM 导演与云 TTS 生成内容，含审核措施说明）；
  - 构建脚本要求发行者逐段确认（交互确认或 `--confirm-disclosure`），未确认 → 构建失败；
  - 商店描述模板自动包含披露段落。
- 披露口径是合规红线，最终文案需用户复核（OQ-4/OQ-7）。

### 7.4 多语言（框架 + zh→en 一版）

- pack v0.3（向后兼容）新增 `i18n/<lang>.json`：键 = 节点/行 id，值 = 译文；构建器从 pack 抽取
  字符串 → LLM 翻译（术语表锁定人名/地名/设定词，防漂移）→ 人工校对接口（桌面 diff 审阅）。
- 运行时：dialogue_manager 本地化 CSV 导出对接（改编层台账既定能力）；字体切换（CJK+Latin 双字体集）；
  TTS 按 `lang` 路由音色（characters.voice 增加按语言条目）；LLM 导演 prompt/persona_guard 按
  玩家语言注入。
- 范围：本期 en 一版走通全链即验收；更多语言仅是内容生产（复用同管线）。

## 8. 一键发布包与素材包产物定义

### 8.1 产物目录

```
<book>/.ainovel/release/<game_slug>_v<semver>/
  game/                      # Godot 导出（Windows x64 优先；导出模板按需下载已由 desktop 管）
  online_config.json         # 网关 URL + 环境标识（无任何密钥；离线版则 enabled=false）
  THIRD-PARTY-NOTICES.md     # 汇总：third_party（ark-cli/godot-ink/dialogue_manager/GodotSteam/
                             #   inkjs/Kokoro/Rhubarb）+ 引擎 + ffmpeg/PyAV 许可与 GPL 排除说明
  release_manifest.json      # 版本/构建指纹/pack 来源/license audit 结果/检查表结果
  store_kit/                 # §7.2 全部产物 + disclosure_ai.md + store_description.{zh,en}.md
  smoke_report.json          # 发布后 smoke 结果
```

### 8.2 一键命令与发布 gate（强制检查表）

`python -m prompt_harness.release build --book <path> --pack <pack_id> --version 1.0.0 [--store-kit]`

gate 逐项（任一失败 → 构建失败，输出原因）：

| # | 检查项 | 证据 |
|---|---|---|
| 1 | pack validation 全绿（含 content_risk 预检 errors 为空） | validation.json |
| 2 | 离线模式可玩 smoke（导出后 headless 启动 → 加载 pack → 到达首节点 → 退出） | smoke_report.json |
| 3 | license audit 全绿（无 GPL 编码器/组件进入发布链） | license_audit 结果（§9） |
| 4 | AI 披露确认（§7.3） | 确认记录入 manifest |
| 5 | 在线模式：网关连通性 + 发行者 key 托管状态校验（纯离线版跳过） | 网关探活响应 |
| 6 | 客户端包内无云 key 扫描（对 game/ 目录做密钥模式扫描） | 扫描报告 |
| 7 | THIRD-PARTY-NOTICES 完整性（third_party 清单比对） | 清单 diff 为空 |

### 8.3 smoke（离线可玩自动验证）

Godot `--headless` 启动实例工程 + 自动化脚本：加载 pack → ink 推进至首节点 → 模拟一次 mapped 交互
→ 断言无错误退出码。作为 gate 第 2 项的机器证据。

## 9. ffmpeg LGPL 构建切换检查表（发布强制项）

背景：漫剧/预告片合成依赖 PyAV（进程内 ffmpeg）；ffmpeg 的 x264/x265 为 GPL，商用发布构建必须排除
（总计划 §6 风险表对策「换 LGPL 构建或硬件编码」）。**不假设 PyAV 官方 wheel 的构建配置，一切以
audit 实测为准**。

| # | 检查项 | 方法/口径 |
|---|---|---|
| 1 | 组件扫描 | `python -m prompt_harness.media.license_audit`：枚举运行库编码器（`av.codecs_available`）与库版本（`av.library_versions`），检出 libx264/libx265 等 GPL 组件 → 报告明示 |
| 2 | 发布构建切换 | 若检出 GPL：发布用自建 LGPL ffmpeg（`--disable-gpl --disable-libx264`，可 `--enable-libopenh264`）重编 PyAV wheel，固化到 `third_party/wheels/` 附构建记录 |
| 3 | 编码器配置化 | compose.py 编码器参数抽出配置：`auto | h264_mf（Windows 硬编，LGPL 兼容兜底）| libopenh264 | libx264（仅内部构建）`；发布构建禁用 libx264（配置校验强制） |
| 4 | 音频编码确认 | 原生 aac 编码器随 LGPL 构建可用；若被裁剪则配置 pcm/aac 替代路径并实测 |
| 5 | 产物验证 | LGPL 构建产物：合成一段样片 → ffprobe/PyAV 复读校验（时长/分辨率/音画同步），在干净环境（无 GPL 组件）复跑 |
| 6 | 声明更新 | THIRD-PARTY-NOTICES 记录 ffmpeg 构建配置与许可；GPL 组件「未随包分发」说明 |
| 7 | gate 固化 | 发布 gate 第 3 项读 audit 结果；audit 未跑或非绿 → 构建失败；结果指纹写入 release_manifest.json |

开发期可继续用现有环境（含 GPL 组件）跑内部验证；**gate 只卡发布构建**，避免拖慢迭代。

## 10. 模块位置

```
prompt-harness/prompt_harness/
  release/                 # 新：一键发布包（gate 检查表 / smoke 编排 / manifest / notices 汇总）
  adaptation/content_risk.py   # 层 1 预检（并入 validator 流程）
  media/license_audit.py   # ffmpeg 组件许可扫描
gateway/                   # 新顶层目录（与 ainovel-write 同仓库；部署形态见 OQ-2）
  app.py                   # FastAPI：session/turn/tts/quota/telemetry
  auth.py  ratelimit.py  budget.py  ledger.py
  moderation.py            # 层 2 双向审核（本地词表 + 云 API 适配位）
  keys.py                  # BYOK 托管（信封加密）
  tests/
小说系统/ainovel-write/
  third_party/godotsteam/  # vendor + UPSTREAM 式备注
  desktop/…                # 发行设置（key 托管入口）+ 审核队列页（最小 UI）
```

## 11. 测试策略

| 测试 | 内容 |
|---|---|
| 网关契约测试 | `/v1/director/turn` 与 R1b 契约逐字段一致（共享 golden fixture）；SSE 事件序列一致 |
| 限流/预算闸 | 注入超限流量（并发/超长输入/超额）→ 每级熔断路径返回 `budget_exhausted`；客户端降级联动 |
| 记账对账 | 会话内 usage_events 求和 == turns/ledger 汇总（自动断言） |
| BYOK | key 托管加密存储/轮换/删除；客户端包扫描无 key |
| 审核 | 构造样本集（20 条正/负例）三层各命中；ContentRiskBlocked 重试→兜底→队列全流转记录 |
| store_kit | 产物尺寸/数量/时长规格断言（脚本）；预告片横屏 profile 音画同步检查 |
| 发布 gate | 逐项人为破坏（缺披露确认/audit 非绿/包内藏 key）→ 构建失败且原因明确 |
| license audit | 构造含 GPL 编码器环境 → 检出；LGPL 构建产物在干净环境复跑合成 |
| 多语言 | en 语言包构建 → 游戏内切换 → 对白/TTS/UI/导演 prompt 语言跟随 |
| E2E | 桌面里一次操作 → 发布包 + 素材包齐全 + gate 全绿 + smoke 通过（含在线模式经网关回合） |

## 12. 验收标准

1. 网关：发行版形态在线回合（经 `/v1/director/turn` + `/v1/tts/stream`）与 R1b 本机形态行为一致；
   限流/预算闸注入测试全过；usage 对账一致（§11）。
2. 审核：样本集三层命中记录完整；ContentRiskBlocked 重试与兜底流转日志可查；预检结果入
   validation.json 且 errors 拦截构建。
3. Steam 链：store_kit 产物齐全且规格断言通过；disclosure 模板含全部强制项并有确认记录；
   GodotSteam 成就/云存档在测试 App 上演示成功（依赖 OQ-4 的 App ID）。
4. 多语言：en 语言包走通「构建 → 游戏内切换 → TTS/导演跟随」全链。
5. 一键发布包：单命令产出 §8.1 完整目录；gate 七项全过；smoke_report 通过。
6. LGPL：发布构建 license audit 全绿（无 GPL 编码器）；产物在干净环境合成与播放验证通过；
   指纹入 release_manifest.json。
7. 客户端卫生：发布包内扫描不到任何云 LLM/TTS key；THIRD-PARTY-NOTICES 与实际 third_party 清单一致。

## 13. 里程碑拆分（3-4 阶段）

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 | 网关 MVP（session/turn/tts 转发 + 限流 + 预算闸 + usage 记账）+ 客户端 endpoint 切换 + BYOK 托管最小形态 | 契约测试绿；超限降级联动；对账一致 |
| M2 | 审核三层（预检并入改编层 + 网关双向 + ContentRiskBlocked 流转 + 桌面兜底队列页） | 样本集全命中；流转日志完整 |
| M3 | Steam 链（GodotSteam vendor + 成就/云存档桥接 + store_kit 导出器 + 披露 gate + 多语言框架 zh→en） | store_kit 规格断言过；en 全链通；披露 gate 生效 |
| M4 | 一键发布包（gate 七项 + smoke + manifest/notices）+ license_audit/LGPL 切换 + 全量 E2E | §12 全过，材料上报看板 |

## 14. 风险

| 风险 | 等级 | 对策 |
|---|---|---|
| 网关被滥用/刷量（总计划 §6 高风险项） | 高 | 鉴权 + 设备指纹 + 三级限流 + 预算闸 + usage 异常告警；损失上限由预算闸封顶 |
| 计费主体/商业模型未定 | 高 | 先落 BYOK 最小形态（不垫资）；官方额度默认 0，开放后配置化（OQ-1） |
| key 托管泄露 | 高 | 信封加密 + 最小权限 + 轮换 + 审计日志；客户端包扫描进发布 gate |
| Steam AI 披露不到位 → 下架风险 | 高 | 披露强制 gate + 文案模板化 + 用户复核（OQ-4/OQ-7）；宁可披露过度不可不足 |
| ffmpeg GPL 误用 | 中 | audit 实测（不假设 wheel 配置）+ 编码器配置化 + 发布 gate 固化 + 硬编兜底 |
| 网关单点故障 | 中 | 客户端断线降级离线可玩（期 4/5 底线）；网关监控 + 一键重启脚本；不做复杂高可用 |
| 多语言机翻质量 | 中 | 术语表锁定 + 人工校对接口 + 首版只做 en |
| Steamworks/GodotSteam 上游变动 | 低 | UPSTREAM 式备注 + 每季同步评估（fork 纪律） |

## 15. 开放问题

| # | 问题 | 需要谁拍板/动作 |
|---|---|---|
| OQ-1 | 计费主体与账号体系：发行者 BYOK 托管 vs 官方统一额度（定价、试用额度数值、玩家侧是否需要账号） | **用户拍板**（商业决策）；M1 前定，默认先 BYOK |
| OQ-2 | 网关部署形态：云服务器选型/域名/备案/运维责任；代码位置（同仓库 `gateway/` vs 独立仓库，影响分工） | **用户拍板** + 外部开通 |
| OQ-3 | 云审核 API 选型与开通；审核阈值表（题材分级口径，尤其恐怖类容忍度） | **外部开通** + **用户拍板阈值** |
| OQ-4 | Steamworks 合作开发者账号与 App ID（外部，约 $100/款上架费）；商店素材最终规格以 Steamworks 后台为准 | **外部开通** |
| OQ-5 | 网关限流/预算闸数值（§5.4）与 store_kit 素材生成预算 | **用户拍板** |
| OQ-6 | 发行形态：在线模式是否为发行版可选开关（纯离线发行不接网关——默认支持） | 用户确认默认口径 |
| OQ-7 | 披露文案与商店描述的最终法务口径；商用授权书面确认清单（Seedream/Seedance/火山音色/Hunyuan3D/Mixamo） | **用户复核** + 外部书面确认（发行前必须闭环） |
| OQ-8 | 多语言清单（本期 en；后续语言优先级与机翻 vs 人工比例） | 用户拍板 |
