# R1b 在线切片设计规格（同骨架接 LLM 导演）

> 日期：2026-09-12 · 状态：待审阅 · 上游文档：`docs/总计划-三线串联与开源集成.md` v2、`docs/superpowers/specs/2026-09-11-adaptation-layer-design.md`
> 定位：期 5。在期 4 离线切片（R1a）之上，同一份 pack 同一付骨架接入 LLM 导演与云 TTS 流式，
> 让自由输入成为可能，同时保住离线可玩性、硬事件归状态机、断线无缝降级三条底线。
> 本文档为设计先行；实现依赖期 3（资产管线/云 TTS 真实连接）与期 4（R1a 离线切片）交付。

---

## 1. 背景与目标

期 4（R1a）交付：godot-ink(.NET) 运行时 + 空间交互（走/看/拿/放/用/躲）+ Kokoro 预生成配音，
完全离线可玩。玩家输入被限定在预定义动词与交互物上。

期 5（R1b）目标：**骨架不动，长出在线层**——

1. 玩家可以打字自由输入；输入先映射到合法动词/交互物（确定性，零成本），映射不了才交给 LLM 演出。
2. LLM 只做「插播演出」，绝不推进剧情、不触发硬事件、不改结构（骨架唯一真相）。
3. 语音升级：对白/旁白可用云 TTS 流式播出，音色与离线 Kokoro 预生成保持同一映射表。
4. 断网/超时/欠费时自动降级回离线模式，玩家无感、进度不丢。
5. 每一回合的延迟与成本可测量、可记录（为期 7 代理计费提供对账口径）。

## 2. 范围

### 2.1 做

1. 后端「导演服务」（LLM 演出 + persona_guard 注入 + 越权 schema 强校验 + 回合成本记录）
2. 客户端（C#）自由输入管线：本地动词映射（fallback_map）→ 未命中 → 后端 LLM 回合
3. 云 TTS 流式播放（C# 直连火山 WSS，PCM 推流，首句先行）
4. 断线检测、自动降级离线、恢复探测、自动回在线
5. 回合日志（route/latency/tokens/cost）与会话汇总
6. `online_config.json` 端点配置（开发期=本机后端；预留期 7=云网关，客户端零改动）
7. 契约测试、越权对抗语料集、延迟/成本统计脚本、E2E 验收材料

### 2.2 不做

- 不做期 7 的云网关、限流、计费、账号体系（本 spec 只留端点配置位）
- 不做内容审核（期 7 专项；本 spec 的 fallback 文案即审核拦截时的落点）
- 不做 LLM 多轮对话记忆（一回合一上下文；对话式深聊留待设计层 v1）
- 不改离线运行时行为：InkBridge 状态机、硬事件触发、预生成配音播放路径零语义变化
- 不改改编层 pack schema 的既有字段语义（只做 §7 的向后兼容扩展）
- 不做剧情推进的 LLM 化（选择/结局仍由骨架决定；LLM 无权改写 next/flags/ending）

## 3. 术语

| 术语 | 含义 |
|---|---|
| 合法动词 | interaction.json 中定义的交互动作（inspect/take/…），是自由输入唯一可「执行」的东西 |
| 映射 | 把玩家自由输入解析为 (动词, 交互实体) 的确定性过程 |
| LLM 演出 | 映射失败后，LLM 在 persona_guard 约束下生成的一段插播表演（不改骨架） |
| 插播层 | LLM 演出文本的呈现层：叠加在当前节点之上，播完回到原有交互，不产生状态变更 |
| 越权 | LLM 输出试图触发 stage cue / 设置 flag / 新增具名角色 / 修改节点结构 |
| 降级 | 在线层不可用时自动切回纯离线模式（期 4 形态） |
| 回合（turn） | 一次玩家自由输入从提交到响应完整的处理单元 |

## 4. 与上游文档的锚点

| 锚点 | 内容 | 本文对应 |
|---|---|---|
| 总计划 §4 期 5 行 + 期 5 细节 | 先映射合法动词，映射不了才 LLM 演出；三条硬规则；云 TTS 流式；验收指标 | §2/§6/§7/§9/§13 |
| 总计划 §5 仓库布局/进程模型 | 模板工程、Python 后端进程、发行版走代理 | §5/§11 |
| 总计划 §7 决策 4/6 | 双模式共用骨架、硬事件归状态机、断线降级；TTS 混合与统一音色表 | §6/§9 |
| 总计划 §6 风险表 | 在线 LLM 代理计费与防滥用（期 7 专项）；Kokoro/云音色一致性 | §5.3/§9/§15 |
| 改编层 spec §5.3/§5.7/§5.8 | story 节点、interaction（动词/结果）、llm_zones（fallback_map/persona_guard/budget） | §7/§8 |
| 改编层 spec §5.4 | characters.voice = {kokoro, cloud}（音色映射唯一来源） | §9 |
| `media/drivers/cloud_tts.py` | 火山 WSS 请求形状 build_request / STREAM_EVENT_TYPES 契约（期 3 接入真实连接） | §9 |
| `templates/godot/`、`game/paper-classroom/` | R1a 已交付的模板工程与实例工程 | §11 |

## 5. 架构与数据流

### 5.1 总览

```
玩家文字输入 / 空间交互
        │
        ▼  C# InputRouter（客户端本地，确定性）
   fallback_map + 交互物标签 匹配
        │                                  │
   命中│未命中                            │命中
        ▼                                  ▼
  本地执行 interaction.result    ┌────────────────────┐
  （零网络、预生成配音/静默）    │ 后端导演服务（Python）│
                                 │  persona_guard 注入  │
                                 │  LLM chat_json       │
                                 │  越权 schema 强校验   │
                                 │  成本/延迟记录        │
                                 └─────────┬──────────┘
                                           │ SSE 逐行回传
                                           ▼
                              C# 插播层上屏（首句先行）
                                           │ 逐行
                                           ▼
                              C# TtsStreamer ──WSS──▶ 火山云 TTS
                                           │ PCM 分片
                                           ▼
                              AudioStreamGenerator 推流播放
```

### 5.2 关键架构决策：LLM 调用链路（运行时直连 vs 后端代理）

**已拍板（2026-09-12 用户裁定）：LLM 由游戏运行时直连云 LLM API（方案 A），AInovel 端使用自有 key；与总计划 §5「运行时直连」字面一致。**

| 维度 | A：C# 运行时直连 LLM API（✅ 采用） | B：后端代理（备选，未采用） |
|---|---|---|
| key 安全 | key 落客户端配置/内存，发行版即泄露面 → **开发期可接受；发行版由期 7 代理+计费统一解决** | key 只在本机后端/期 7 网关，客户端永不接触 |
| 成本记录 | C# 侧逐回合埋点（`turns.jsonl` + 会话 `summary.json`，见 §10），期 7 网关接入后服务端记账接管 | 天然进现有 `llm_calls` 用量日志 |
| 越权防护 | persona_guard/schema 校验需在 C# 重复实现 → 用「LLM 输出 schema 不含 stage/flags/next 字段」的白名单硬约束兜底（§6.3） | 服务端单点强校验 |
| 延迟 | 少一跳，回合延迟预算更宽松 | 多一跳本机 loopback（~5-15ms，可忽略） |

与总计划 §5 的关系：LLM 与 TTS 均为「运行时直连云服务（AInovel 端自有 key）」；发行版的代理+计费+防滥用
由期 7 网关统一接管（总计划 §6 风险表锁定项），R1b 开发态不涉及。

期 7 衔接：本 spec 定义「导演服务接口契约 v1」（§8），R1b 开发态由 C# 运行时直连实现；期 7 云网关实现同一契约
供发行版，客户端仅切换 `online_config.json` 的 base_url。

### 5.3 降级形态（常备）

离线能力不是回退路径的补丁，而是常态基座：pack（含 ink/interaction/llm_zones/预生成配音）始终完整
打包在游戏本地。在线层只是叠加物——任何一层失效，游戏自动回到期 4 验收过的形态。

## 6. 三条硬规则（细则）

### 6.1 骨架唯一真相

- 剧情推进只由 ink 运行时（godot-ink）驱动；LLM 输出永远是「插播演出」，不产生任何状态变更。
- LLM 可见的世界状态：当前节点 id、节点文本摘要、在场角色、可用交互物清单（只读注入 prompt）。
- LLM 不可见/不可改：flags、next、ending condition、硬事件队列。
- 插播层实现：C# 把 LLM 行渲染为临时演出序列，播完即销毁，不写入 ink 变量、不调用 stage()。

### 6.2 硬事件归状态机

- stage cue（paper_figures_turn 等）只能由 ink `~ stage("...")` 回调触发（期 4 机制，不变）。
- 双重防线：
  1. 服务端：导演 LLM 的输出 schema 中**不存在** stage/cue/flags/next 字段（§8.3），pydantic 白名单
     校验天然拒绝；
  2. 客户端：插播层渲染器只认 `narration/inner/dialogue` 三种行类型，其余一律丢弃并上报。
- LLM 试图在文本里「暗示」硬事件（如写出纸人转头）不视为越权（属氛围演出），但输出后置检查若命中
  硬事件专属短语表 → 判越权重试（短语表从 zone.hard_events 派生）。

### 6.3 断线自动降级离线

| 触发 | 动作 |
|---|---|
| turn 请求失败/超时（3s）/ SSE 中断 | 立即进入离线模式；UI 横幅「离线模式」；后台指数退避探测（5s→10s→…上限 60s） |
| 后端返回 `budget_exhausted`（期 7 预算闸，字段预留） | 同上，且本次会话不再自动回在线 |
| 恢复探测成功 | 自动回在线，UI 提示；**进度不丢**（ink 状态在本地，插播层无状态） |
| 离线期间自由输入 | 本地映射照常（§7）；未命中给标准提示文案（无 LLM） |

## 7. 自由输入处理管线

### 7.1 第一层：本地映射（客户端，确定性）

映射在 C# 客户端本地完成（不进网络）——零延迟、离线可用、映射结果只执行 pack 内确定性内容，
天然不越权。匹配顺序：

1. **fallback_map 关键词**：玩家输入含 zone.fallback_map 的 key（子串匹配）→ 对应交互实体；
   多个 key 命中 → 若解析到同一实体则执行，否则转 LLM（带候选）。
2. **宾语匹配**：输入含某交互实体 label（或别名）→ 执行该实体的默认动词（entity.kind 对应动词）。
3. **动词裸匹配**：输入只含动词（如「看看」「拿起来」）→ 取当前节点唯一可交互实体；不唯一转 LLM。
4. **全部未命中** → 转 LLM 演出（§7.2）。

llm_zones v0.2 向后兼容扩展（新增字段，老 pack 忽略）：

```json
{
  "id": "zone_n005",
  "node": "n005",
  "allow": ["ask_paper_figure", "free_action"],
  "persona_guard": "维持中式恐怖氛围；不得让纸人开口说出成句人话；不得提前泄露循环真相",
  "hard_events": ["paper_figures_turn"],
  "fallback_map": { "拿": "int_cabinet", "看": "int_cabinet" },
  "entity_aliases": { "int_cabinet": ["储物柜", "柜子", "铁皮柜"] },
  "budget": { "max_turns": 6, "max_chars": 1200, "input_max_chars": 200, "turn_lines_max": 3, "turn_chars_max": 200 }
}
```

### 7.2 第二层：LLM 演出（后端导演服务）

仅当本地映射失败时调用。流程：

1. C# POST `/ai-creation/game/turn`（§8.1），输入限长 `input_max_chars`（默认 200 字，超出截断并提示）。
2. 后端组装 prompt：system = zone.persona_guard + 全局禁令（llm_zones.global.forbidden）+ 输出 schema
   说明；user = 节点上下文（title/在场角色/可用交互物表）+ 玩家输入 + 超时提示（预算余量）。
3. LLM 调用走现有 `llm_client.chat_json`（一次调用，非流式；输出短，流式收益见 §10 说明）。
4. 输出过 §8.3 强校验；违规 → 附违规原因重试 1 次 → 再违规 → `verdict=fallback` 兜底，
   记 `overreach_blocked=true`。
5. SSE 逐行回传；C# 首行上屏 + 逐行送 TTS（§9）。

`zone.budget.max_turns`：同一 zone 内 LLM 演出回合数上限（默认 6），超限后自由输入只走本地映射，
超出部分给引导文案「你决定先做眼前的事」（防滥用 + 防体验涣散，数值可调，见 OQ-5）。

## 8. 接口草案

### 8.1 导演服务接口契约 v1（后端实现；期 7 网关实现同一契约）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/ai-creation/game/session` | `{book_root, pack_id}` → `{session_id, zones_loaded, voice_table_ok}`；服务端加载 pack 元信息（无状态校验用） |
| POST | `/ai-creation/game/turn` | 回合主接口，SSE 响应（§8.2） |
| POST | `/ai-creation/game/session/summary` | 会话结束：汇总 turns.jsonl → `summary.json`（总延迟分布/成本/路由占比） |

turn 请求（无状态：服务端不保存游戏进度）：

```json
{
  "session_id": "s_20260912_143022_a1b2",
  "pack_id": "pack_firstbook_arc_xxx_c3d4e5",
  "node_id": "n005",
  "zone_id": "zone_n005",
  "input": "我想问问那些纸人",
  "turn_seq": 3,
  "lang": "zh-CN"
}
```

### 8.2 SSE 事件流（响应）

```
event: route   data: {"route": "llm", "turn_id": 12}          // 或 "fallback"（服务端兜底，无 LLM）
event: line    data: {"line_seq": 0, "kind": "narration", "text": "……"}
event: line    data: {"line_seq": 1, "kind": "dialogue", "speaker": "char_chen", "text": "……"}
event: done    data: {"usage": {"llm_calls": 1, "tokens_in": 830, "tokens_out": 96, "cost_est": 0.0011},
                      "latency_ms": 1480}
event: error   data: {"code": "timeout|overreach_blocked|backend_error|budget_exhausted",
                      "message": "…", "fallback_lines": [...]}   // fallback_lines 非空时客户端照常播
```

### 8.3 导演 LLM 输出 schema（强校验，白名单之外一律违规）

```json
{
  "verdict": "perform | redirect | fallback",
  "lines": [
    { "kind": "narration | inner | dialogue", "speaker": "char_xxx（dialogue 必填）", "text": "≤200 字" }
  ],
  "redirect_to": "int_cabinet | null"
}
```

校验规则：

| 规则 | 违规处置 |
|---|---|
| verdict ∈ 三值；lines ≤ `turn_lines_max`；单行 ≤ `turn_chars_max` | 重试 |
| kind ∈ {narration, inner, dialogue}；**无** stage/flags/next 字段（schema 层面即不存在） | 重试 |
| dialogue.speaker ∈ 当前节点 present ∩ characters.json | 重试 |
| redirect_to ∈ 当前节点 interaction_refs | 重试 |
| 文本命中硬事件专属短语表（由 zone.hard_events 派生） | 重试 |

重试仍违规 → `verdict=fallback`（标准提示文案），`overreach_blocked=true` 记入回合日志。

### 8.4 online_config.json（客户端，游戏包内）

```json
{
  "schema_version": "1",
  "enabled": true,
  "director_endpoint": "http://127.0.0.1:5010/ai-creation/game",
  "tts": {
    "mode": "direct_cloud",
    "gateway": "wss://openspeech.bytedance.com/api/v3/tts/unidirectional",
    "credentials_source": "local_config"
  },
  "degrade": { "timeout_ms": 3000, "probe_backoff_max_ms": 60000 }
}
```

发行版差异（期 7）：`director_endpoint` → 网关 URL；`credentials_source` → `gateway_session`
（客户端不持有云 key）。字段结构不变，客户端零改动。**客户端包内任何位置不得出现云 LLM/TTS key**。

## 9. 云 TTS 流式与音色一致性

### 9.1 链路与决策

- C# 直连火山 WSS（`media/drivers/cloud_tts.py` 的 build_request 形状为契约基准，C# 首帧 JSON 与其
  golden 对齐做契约测试）；编码 **pcm（16bit/24kHz/单声道）**，规避 mp3 分帧播放问题；
  C# 用 AudioStreamGenerator 逐分片推流。
- Python 侧 CloudTTSDriver 继续服务漫剧/预生成场景；R1b 不经后端转发音频（省一跳，保起播预算）。
- 凭据：开发期从本机配置读取（自有 key，符合总计划 §5「AInovel 端用自有 key」）；发行版走网关
  （期 7），配置位已预留。

### 9.2 音色一致性（单一数据源）

- 音色映射唯一来源 = pack `characters.json` 的 `voice.kokoro` / `voice.cloud`（改编层 spec §5.4）。
  R1b **不新增音色表文件**，避免双真相。
- 前置依赖：期 3 音色校准任务把 `voice.cloud` 从占位填充为真实火山音色 id，并对主要角色做
  Kokoro/云 TTS 同句 A/B 抽听（对应总计划 §6 风险「Kokoro 离线音质」对策）。
- 插播层 dialogue 行按 speaker 解析音色；narration/inner 用主角中性音色（期 4 既定）。

### 9.3 首句先行（流水线）

LLM 响应按行 SSE 回传 → 每收到一行完整文本立即：上屏 + 送 TTS（每行一个 reqid）→ 音频分片到达即
推流播放。行间播放用队列衔接；后行 TTS 未就绪时字幕先行（文本轨与音频轨解耦，允许语音晚于字幕，
不允许画面卡死等待）。

## 10. 延迟预算与成本记录

### 10.1 延迟预算（验收口径）

| 指标 | 定义 | 预算 | 说明 |
|---|---|---|---|
| mapped 回合延迟 | 输入提交 → 本地交互结果开始呈现 | P90 < 300ms | 零网络 |
| LLM 回合延迟（总） | 输入提交 → 演出文本完整可用 | P90 < 2s | 总计划期 5 验收行 |
| 其中：LLM 首 token | 提交 → 后端首行就绪 | P90 < 1s | 输出限长 ≤3 行/200 字 |
| TTS 起播 | 首行文本就绪 → 首音频出声 | P90 < 1s | 总计划期 5 验收行；含 WSS 握手 + 首包 + 播放缓冲 |
| LLM 超时判失败 | 提交 → 判定失败进入降级 | 3s | 超时即降级，不干等 |

注：TTS 起播从「文本就绪」起算（LLM 生成时间计入回合延迟而非 TTS 起播）——两条验收指标独立可测；
体感优化靠首句先行（字幕先于语音，§9.3）。

### 10.2 成本记录

每回合追加一行 JSONL：`<book>/.ainovel/game_runs/<session_id>/turns.jsonl`

```json
{"ts": "2026-09-12T14:31:05+08:00", "turn_seq": 3, "route": "llm",
 "latency_ms": 1480, "tts_start_ms": 860, "overreach_blocked": false,
 "llm": {"model": "doubao-lite-32k", "tokens_in": 830, "tokens_out": 96, "cost_est": 0.0011},
 "tts": {"chars": 86, "voice_type": "zh_female_XXX", "cost_est": 0.0009}}
```

- llm 成本自动进现有 `llm_calls` 用量日志（llm_client 统一埋点，不靠 handler 自觉）。
- mapped 路由成本为 0，也记录（route=mapped，用于路由占比统计）。
- 会话结束汇总 `summary.json`：回合数、路由占比、P50/P90 延迟、总成本——即期 7 代理计费的对账口径雏形。

## 11. 模块位置与分工

### 11.1 后端 Python（`prompt-harness/prompt_harness/game_director/`，新增，与 adaptation 平级）

```
game_director/
  __init__.py
  service.py      # 会话/回合端点（挂 server.py，/ai-creation/game/*），SSE 输出
  director.py     # LLM 导演：prompt 组装 + chat_json + schema 强校验 + 重试/兜底
  guards.py       # 越权检查（白名单校验 + 硬事件短语表派生）
  turns.py        # 回合日志/会话汇总（turns.jsonl / summary.json）
  tests/
```

C# 端信任边界：客户端不重复实现越权校验，只按 §8.2 消费响应（双重防线中的客户端防线仅做
「行类型白名单」丢弃，§6.2）。

### 11.2 客户端 C#（`templates/godot/` 模板工程内，实例工程引用）

```
templates/godot/
  addons/ainovel_online/
    OnlineDirector.cs    # 会话初始化 + turn SSE 客户端（薄，只解析契约）
    TtsStreamer.cs       # 火山 WSS 客户端 + AudioStreamGenerator PCM 推流 + 播放队列
    DegradeManager.cs    # 超时/断线判定、降级、指数退避探测、恢复回在线
    OnlineConfig.cs      # online_config.json 加载
  Scripts/Runtime/
    InputRouter.cs       # 自由输入框 → 本地映射（fallback_map/宾语/裸动词）→ 本地执行或调后端
    （InkBridge/stage 回调等 R1a 既有文件不改语义）
game/paper-classroom/
  online_config.json     # 实例工程端点配置
```

分工原则：**确定性归客户端（本地 pack 单一真相），生成归服务端（LLM/越权/计费单点）**；
C# 侧不出现任何 LLM prompt 拼装与云 LLM key。

## 12. 测试策略

| 测试 | 内容 |
|---|---|
| 映射单测（C#，或用 GDScript 测试桩/导出 headless 用例） | fallback_map 命中/多义歧义/宾语别名/裸动词/全未命中，逐条断言路由 |
| director 单测 | mock LLM：正常 perform / redirect / 越权（试图输出 stage.cue、新角色、flags）→ 拦截+重试+fallback |
| guards 单测 | 白名单校验逐条命中；硬事件短语表派生正确 |
| 契约测试 | turn 请求/响应 schema golden；SSE 事件序列固定；C# 首帧 JSON 与 build_request 对齐（共享 golden fixture） |
| 降级测试 | 模拟后端不可达（关端口）/中途杀进程 → <1s 降级、玩法不中断、恢复自动回在线、ink 进度不变 |
| 延迟/成本统计 | turns.jsonl 回放脚本输出 P50/P90 与成本汇总，核对预算表 |
| E2E | 期 4 的 5 beat 场景 + 1 个 llm_zone：mapped 路由 + LLM 路由 + 断线降级三段手测脚本，产出延迟/成本报告 |

## 13. 验收标准

1. 同一 pack 双模式回归：期 4 离线验收项全部复跑通过（离线行为零回归）。
2. 映射层：≥20 条测试输入集，应映射条目 100% 命中且不调 LLM；mapped 回合延迟 P90 < 300ms。
3. LLM 演出：自由输入可得到符合 persona_guard 的插播演出；LLM 回合延迟 P90 < 2s。
4. TTS：起播 P90 < 1s；所用音色 = 该角色 `voice.cloud`（与 Kokoro 预生成同表）；同句 A/B 抽听记录在案。
5. 不越权：对抗语料集（诱导触发硬事件/新增具名角色/修改结构/设 flag）**100% 被拦**
   （拦 = schema 拒 + 重试 + fallback），单测与人工对抗各一轮，拦截记录入日志。
6. 断线降级：运行中关闭后端 → ≤1s 进入离线模式、玩法不中断、UI 有提示；恢复后自动回在线；全程进度无损。
7. 成本记录：每回合 turns.jsonl 字段齐全；session summary 可对账（llm_calls 日志与回合日志一致）。
8. 发布卫生：客户端包内无任何云 LLM/TTS key；online_config.json 无密钥字段。
9. 不越界：git diff 仅涉及 §11 列出的新增/修改位置，不改期 4 离线运行时语义。

## 14. 里程碑拆分（3-4 阶段）

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 | 后端 director 服务骨架（session/turn 端点 + SSE + mock LLM）+ C# OnlineDirector/InputRouter/OnlineConfig + online_config.json | mapped 路由端到端走通（未命中 → mock LLM 插播）；契约测试绿 |
| M2 | LLM 导演真实接入（persona_guard + schema 强校验 + 重试/兜底）+ guards + 对抗语料集 + 回合日志 | 自由输入可演出；越权单测全拦；turns.jsonl 记录完整 |
| M3 | 云 TTS 流式（TtsStreamer：WSS + PCM 推流 + 首句先行 + 音色解析）+ 音色 A/B 抽听 | TTS 起播 P90 < 1s；音色一致性确认 |
| M4 | DegradeManager 全链路 + 延迟统计脚本 + summary + E2E 验收材料 | §13 全过，报告上报看板 |

## 15. 风险

| 风险 | 等级 | 对策 |
|---|---|---|
| LLM 回合延迟抖动超 2s | 高 | 快模型选型（OQ-2）+ 输出限长 ≤3 行/200 字 + 超时 3s 判失败即降级 + 字幕先行保证体感 |
| LLM 越权漏防 | 高 | 服务端 schema 白名单单点强校验 + 重试一次 + fallback 兜底 + 对抗语料回归进 CI |
| 云 TTS 与 Kokoro 听感割裂 | 中 | 期 3 音色校准为前置依赖；同句 A/B 抽听记录；离线包仍用 Kokoro 不受影响 |
| 火山 WSS 在 C# 侧接入复杂度 | 中 | 请求形状以 build_request 为 golden 契约先行；PCM 推流方案规避 mp3 分帧；连接池优化仅在实测握手 >300ms 时做 |
| 断线误判（网络抖动频繁降级） | 中 | 失败即降级（安全侧）+ 自动探测恢复；降级态本身可玩（期 4 保证），体验损失可控 |
| 成本记录遗漏/失真 | 低 | C# 侧逐回合埋点（turns.jsonl/summary.json）；期 7 网关接入后服务端记账接管 |
| 本 spec 与总计划 §5「直连」字面冲突 | — | **已消除（OQ-1 拍板=运行时直连，2026-09-12）**；期 7 网关实现同一契约供发行版切换 |

## 16. 开放问题

| # | 问题 | 需要谁拍板/动作 |
|---|---|---|
| OQ-1 | ~~LLM 链路细化为「运行时 → 本机后端代理」~~ **已拍板（2026-09-12）：采用方案 A 运行时直连**，与总计划 §5 一致，无需回写；发行版代理由期 7 承接 | **用户已拍板** |
| OQ-2 | 在线模式 LLM 具体模型选型（快模型：doubao-lite 系 / flash 系，需时延+成本实测核价） | 用户拍板；实施 M2 前用 20 条样本实测 |
| OQ-3 | 火山语音服务开通 + 音色商用授权确认 + 主要角色音色 id 选型（`voice.cloud` 填充） | **外部开通**（火山控制台）；期 3 音色校准任务承接 |
| OQ-4 | 发行版 TTS 是否也强制走期 7 网关（key 防泄露） | 期 7 spec 默认「是」；R1b 仅预留配置位，用户可在期 7 审阅时改 |
| OQ-5 | zone.budget 数值（max_turns=6 / turn 200 字 / 输入 200 字）是否合适 | 验收试玩后可调；先按默认实施 |
| OQ-6 | 回合通道 SSE vs WebSocket（v1 用 SSE：单向够用、实现简单；若需中途打断/双向再切 WS） | 实施期技术决策，默认 SSE |
| OQ-7 | 插播层演出是否计入 zone.budget.max_turns 与结局判定 flag（v0 不计入、不影响 flag） | 设计层 v1 再议，v0 按纯插播实现 |
