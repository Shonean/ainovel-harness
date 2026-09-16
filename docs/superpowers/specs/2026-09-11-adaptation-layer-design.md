# 改编层 v0 设计规格（Adaptation Layer）

> 日期：2026-09-11 · 状态：待审阅 · 上游文档：`docs/总计划-三线串联与开源集成.md` v2
> 定位：三线共用地基。把一本书的弧/章节数据编译为引擎无关的 Adaptation Pack + ink 骨架，
> 供 3D 剧情游戏（godot-ink）与调试预览（inkjs）消费。
> 排期说明：本文档为设计先行；实现顺序上「一键生文量产模式」先于本层开工。

---

## 1. 背景与目标

现有 AI 创作系统已把一本书结构化到 l1~l5（`arcs.json`）+ 元素表（`elements.json`）：

- l1 一句话 → l2 情节概要（起因/冲突/转折/结局）→ l3 章纲（title/core/beats）→ l4 场景叶子（environment/actions/dialogues/narration/psychologies/conflicts/details）→ l5 正文
- `finalize_chapter` 落盘：`大纲/第NNNN章-章纲.md`、`AI生成/第NNNN章.md`、`审查报告/第NNNN章-评分.json`

改编层目标：**不写引擎代码、不生成媒体资产**，只做一件事——
把上述数据确定性地编译成可校验的 Pack，让下游（游戏运行时、漫剧管线、调试预览）吃同一份数据。

## 2. 范围

### 2.1 做

1. Pack 目录结构与 JSON schema（含双模式预留字段）
2. 确定性映射：l4 场景叶子 → scene 节点；元素表 → characters/world；资产需求 → assets 清单
3. 对白说话人归一化（自由叙事体 → 结构化对白行）
4. 骨架版分支：章末收敛式选择 + 弧末双结局（LLM 仅做包装，不改结构）
5. ink 导出与编译（`.ink` → `.ink.json`，godot-ink 与 inkjs 共用）
6. 校验器 + 校验报告
7. inkjs 调试预览页（仅调试，不作为产品形态）
8. 阶段测试与 E2E 验收

### 2.2 不做

- 不做 Ren'Py/2D VN（已砍）；不做 `.rpy` 探针
- 不做资产生成（Seedream/TTS/3D 模型）——只出清单与 prompt
- 不做 runtime LLM 导演实现（只定义 `llm_zones` schema 与占位内容）
- 不做 3D 交互运行时（属游戏线 R1）
- 不改动现有生成链路（`arc_step`/`finalize_chapter`）的数据格式

## 3. 术语

| 术语 | 含义 |
|---|---|
| Pack | 一次改编产物目录，含全部 JSON 与 ink 文件 |
| scene 节点 | 最小叙事单元，来自 1 个 l4 场景叶子 |
| 收敛式选择 | 影响 flag/好感但最终合流的分支点 |
| 硬事件 | 只能由 beat/触发条件驱动的演出（如全场纸人转头），LLM 无权触发 |
| llm_zone | 允许自由输入的时机/对象定义（在线模式用，离线模式忽略） |
| fallback | 自由输入映射失败时回落的合法动作/标准演出 |

## 4. 架构与数据流

```
<book>/.ainovel/{elements.json, arcs.json}
AI生成/第NNNN章.md、大纲/第NNNN章-章纲.md
        │
        ▼  loader（薄读取层）
    ArcSnapshot（弧 + 已完成章节 + 场景 + 元素）
        │
        ▼  mapper（确定性）
    story / characters / world / assets / interaction
        │
        ▼  designer（LLM 仅两处：对白归一兜底、选择/结局措辞）
    story（完整骨架）
        │
        ▼  validator
    validation.json
        │
        ├── writer → pack/（JSON + ink/story.ink）
        └── compiler（Node runner / inkjs）→ ink/story.ink.json
                        │
                        ├── inkjs 调试预览页（pack/preview.html）
                        └── 未来：godot-ink 运行时
```

模块位置：`prompt-harness/prompt_harness/adaptation/`

```
adaptation/
  __init__.py
  models.py        # pydantic 模型（pack 全结构 + schema_version）
  loader.py        # 书数据读取（只读，不依赖 ai_creation 内部实现细节）
  mapper.py        # l4/elements → story/characters/world/assets
  dialogue.py      # 对白说话人归一化（正则 + LLM 兜底）
  designer.py      # 章末选择 + 弧末双结局（LLM 包装，结构不变）
  ink_export.py    # pack → story.ink
  compiler.py      # story.ink → story.ink.json（调 Node runner）
  validator.py     # schema/引用/可达性校验
  packer.py        # 落盘 + 预览页生成
  cli.py           # python -m prompt_harness.adaptation
  tests/
```

## 5. Game Pack schema v0

### 5.1 目录结构

```
<book>/.ainovel/adaptation/<pack_id>/
  pack.json          # 元信息、来源、入口、文件索引
  story.json         # 骨架：节点图（scene/choice/ending）
  characters.json    # 角色 + 立绘/表情/音色需求
  world.json         # 地点/物品/设定/术语（投影 elements）
  assets.json        # 资产清单（含 prompt 与状态）
  interaction.json   # 预留：可交互物 + 动词 + 结果（游戏线）
  llm_zones.json     # 预留：自由输入时机与导演约束（在线模式）
  validation.json    # 校验报告
  ink/
    story.ink        # 人类可读骨架
    story.ink.json   # 编译产物（godot-ink / inkjs）
  preview.html       # inkjs 调试预览（仅调试）
```

`pack_id` 规则：`pack_<book_slug>_<arc_id>_<6位hash>`，hash 取自输入快照（元素+弧数据）。

### 5.2 pack.json

```json
{
  "schema_version": "0.1",
  "pack_id": "pack_testbook_arc_072861406870_a1b2c3",
  "created_at": "2026-09-11T12:00:00+00:00",
  "source": {
    "book_title": "测试书-纸人教室",
    "book_root": "C:/.../TestBook",
    "arc_ids": ["arc_072861406870"],
    "chapter_nums": [1],
    "builder_version": "0.1.0"
  },
  "game": {
    "title": "纸人教室",
    "logline": "陈守念在破旧教室中醒来……",
    "genre_tags": ["悬疑", "恐怖"],
    "play_minutes_est": 8,
    "entry_node": "n001",
    "endings": ["end_accept", "end_reject"]
  },
  "modes": {
    "offline": true,
    "online": { "llm_zones": "llm_zones.json", "enabled": false }
  },
  "files": {
    "story": "story.json",
    "characters": "characters.json",
    "world": "world.json",
    "assets": "assets.json",
    "interaction": "interaction.json",
    "llm_zones": "llm_zones.json",
    "ink": "ink/story.ink.json"
  }
}
```

### 5.3 story.json（骨架核心）

```json
{
  "start": "n001",
  "nodes": [
    {
      "id": "n001",
      "type": "scene",
      "title": "纸人教室中醒来",
      "background": "bg_classroom_night",
      "present": ["char_chen"],
      "lines": [
        { "kind": "narration", "text": "深夜的旧教室……" },
        { "kind": "inner", "text": "这是哪儿？" },
        { "kind": "stage", "cue": "paper_figures_turn", "text": "所有纸人同时转过头来。" }
      ],
      "interaction_refs": ["int_cabinet", "int_desk"],
      "next": "n002",
      "source": { "arc_id": "arc_...", "scene_index": 0 }
    },
    {
      "id": "n005",
      "type": "choice",
      "prompt": "你要先做什么？",
      "options": [
        { "text": "先看那张病人卡", "next": "n006", "set": { "flags.read_card": true } },
        { "text": "先去翻储物柜", "next": "n007", "set": { "flags.searched_cabinet": true } }
      ],
      "converge_to": "n008",
      "llm_zone": "zone_n005",
      "source": { "arc_id": "arc_...", "chapter": 1 }
    },
    {
      "id": "end_accept",
      "type": "ending",
      "title": "接受真相",
      "text": "……",
      "condition": null
    }
  ]
}
```

行类型（line.kind）：

| kind | 含义 | 运行时行为 |
|---|---|---|
| narration | 旁白 | 直接显示/播放 |
| inner | 主角内心独白 | 第一人称配音（离线预生成） |
| dialogue | 角色对白 | 需 speaker/expression |
| stage | 演出指示（硬事件） | 由状态机执行，LLM 不可改 |

`stage.cue` 为枚举，v0 允许：`paper_figures_turn` / `note_flip` / `light_flicker` / `scene_shift`（后续扩展）。

### 5.4 characters.json

```json
{
  "characters": [
    {
      "id": "char_chen",
      "name": "陈守念",
      "role": "protagonist",
      "aliases": [],
      "description": "故事主角，以穿越者身份开局，开局在破旧教室里。",
      "source_element": "e5",
      "presence": ["n001", "n002"],
      "assets": {
        "portrait": "spr_chen_default",
        "expressions": ["default", "fear", "calm"]
      },
      "voice": { "kokoro": "zm_x", "cloud": "voice_id_placeholder" }
    }
  ]
}
```

性别/人格卡留白：不生成性别相关台词；`voice` 用中性音色占位。

### 5.5 world.json

直接投影 `elements.json`（保留 source id），供游戏图鉴/漫剧参考：

```json
{
  "locations": [{ "id": "loc_classroom", "name": "纸人教室", "source_element": "e1", "description": "…" }],
  "items": [{ "id": "item_card", "name": "病人卡", "source_element": "e6", "description": "…" }],
  "settings": [{ "id": "set_loop", "name": "时间循环", "source_element": "e2", "description": "…" }],
  "terms": []
}
```

### 5.6 assets.json（清单，不生成）

```json
{
  "assets": [
    {
      "asset_id": "bg_classroom_night",
      "kind": "background",
      "ref": "loc_classroom",
      "prompt": "中式恐怖，深夜旧教室，课桌椅整齐，每个座位上一个纸人，窗外路灯光斜切，压抑",
      "used_by": ["n001", "n002"],
      "status": "pending",
      "hash": null,
      "provider_hint": "seedream"
    },
    {
      "asset_id": "voice_inner_n001_001",
      "kind": "voice",
      "ref": "char_chen",
      "prompt": "内心独白：这是哪儿？（中性、压抑）",
      "used_by": ["n001"],
      "status": "pending",
      "provider_hint": "kokoro|cloud"
    }
  ]
}
```

kind 枚举：`background | portrait | model3d | bgm | sfx | voice | ui`。
id 规则：`bg_*` / `spr_<char>_<expr>` / `bgm_<mood>` / `sfx_<cue>` / `voice_<kind>_<node>_<seq>`。

### 5.7 interaction.json（预留，v0 最小可用）

```json
{
  "entities": [
    {
      "id": "int_cabinet",
      "node": "n001",
      "kind": "inspect",
      "label": "后排储物柜",
      "prompt": "灰绿色铁皮柜，柜顶有个白纸人",
      "result": { "narration": "你走近柜子……", "set": { "flags.saw_white_figure": true } },
      "next": null
    }
  ]
}
```

v0 只支持动词：`inspect`（观察）与 `advance`（推进）。
预留动词（schema 允许、v0 不生成）：`take/replace/use/open/hide/listen`。

### 5.8 llm_zones.json（预留，在线模式）

```json
{
  "zones": [
    {
      "id": "zone_n005",
      "node": "n005",
      "allow": ["ask_paper_figure", "free_action"],
      "persona_guard": "维持中式恐怖氛围；不得让纸人开口说出成句人话；不得提前泄露循环真相",
      "hard_events": ["paper_figures_turn"],
      "fallback_map": { "拿": "int_cabinet", "看": "int_cabinet" },
      "budget": { "max_turns": 6, "max_chars": 1200 }
    }
  ],
  "global": {
    "rule": "先映射合法动词，映射失败才交给 LLM 演出",
    "forbidden": ["触发硬事件", "新增具名角色", "修改节点结构"]
  }
}
```

### 5.9 validation.json

```json
{
  "ok": true,
  "errors": [],
  "warnings": [
    { "code": "SPEAKER_UNRESOLVED", "node": "n003", "detail": "第2条对白说话人未识别，已降级为 narration" }
  ],
  "stats": {
    "nodes": 12, "scenes": 8, "choices": 3, "endings": 2,
    "lines": 96, "assets": 21, "llm_calls": 3, "cost_est": 0.004
  }
}
```

## 6. 映射规则（确定性）

| 输入 | 输出 | 规则 |
|---|---|---|
| l4 scene `environment` | background 资产 + 开场 narration | 按地点名/文本 hash 去重；同背景复用 |
| l4 `narration` / `psychologies` / `details` | `narration` / `inner` 行 | psychologies → inner；details → narration（可标 `detail: true`） |
| l4 `actions` | narration 行 | 动作叙述保留原文 |
| l4 `dialogues` | `dialogue` 行 | 走 §7 归一化；失败降级 narration |
| l4 `conflicts` | 章末 choice 素材 | 只作为 prompt 素材，不直接成节点 |
| l3 chapter `core`/`beats` | scene.title / 章末锚点 | 不单独成节点（避免与 l4 重复） |
| l2 结局段 | 双结局素材 | 走 §8 designer |
| `elements.characters` | characters.json | scope/arc_name 过滤；selected 优先 |
| `elements.items/settings/maps` | world.json | 原样投影，保留 source id |
| 每章最终场景的下一章 | `next` 串联 | 最后一章末接 choice → endings |

## 7. 对白说话人归一化

真实数据是自由叙事体（样例来自实际数据）：

```
"陈守念对着空教室低声问道：“这是哪儿？”"
"他摇了摇头，把这句话咽回去：“胡说八道。”"
```

处理流程：

1. 正则优先：`^(?P<speaker>[^，。：]{2,8})(?:[^“”]{0,12})[：:]\s*[“"](?P<line>.*?)[”"]$`
2. 代词回填：`他/她/它` → 当前场景 `present` 中唯一角色；不唯一则不回填
3. LLM 兜底（批量，1 次调用处理整章未解析对白）：输入 `present` + 对白列表，输出 `{index: speaker|""}`；任何新增角色名视为非法
4. 仍未解析：降级为 `narration`，并记 `SPEAKER_UNRESOLVED` 警告
5. 语气词（低声/急道）提取为 `expression`（能识别就填，识别不了留空）

## 8. 分支与结局（骨架版）

### 8.1 章末收敛式选择

- 位置：每章最后一个 scene 之后
- 数量：2~3 个选项；选项文本由 LLM 基于该章 `conflicts` 包装（不得新增事实）
- 效果：`set { flags.* }` 或好感值；**全部选项 `next` 指向同一个下一章首节点**（收敛）
- 离线模式：选择照常生效（影响后续演出变体与结局判定），在线模式可额外挂 `llm_zone`

### 8.2 弧末双结局

- 数量：v0 固定 2 个（`end_a` / `end_b`）
- 素材：l2 的「结局」段 + 最后一章核心冲突
- LLM 约束：1~3 句/结局；不得新增具名角色/地点/数字；关键事实（`key_facts`）不得丢失；结局差异必须来自已建立的选择/flag
- 判定：`condition` 支持极简表达式（v0 仅 `flags.<name>` 布尔组合），不满足条件时走默认结局

## 9. ink 导出与编译

### 9.1 导出（pack → story.ink）

- scene → `=== n001 ===` knot；`next` → `-> n002`
- choice → `* [选项文本] -> n006`（选项文本做 ink 转义）
- ending → `-> END`
- flags → `VAR flags_read_card = false`；`set` → 赋值
- stage → `~ stage("paper_figures_turn")`（运行时桥接回调；inkjs 预览页里显示为演出提示）

### 9.2 编译（story.ink → story.ink.json）

- 工具：**inkjs 编译器**（随内嵌 Node runner 调用，避免额外二进制依赖）
- 失败处理：编译报错 → `validation.ok=false`，附行号与原文行；pack 不视为有效产物
- 同一 `.ink.json` 同时供：inkjs 预览页（浏览器）与未来 godot-ink（C#）

## 10. 校验器规则

| 类别 | 规则 |
|---|---|
| Schema | 全文件过 pydantic 模型；版本号必须为 `0.1` |
| 引用完整性 | `next`/`converge_to`/`background`/`voice`/`interaction_refs`/`llm_zone` 引用必须存在 |
| 图可达性 | 从 `start` BFS：所有节点可达；所有结局可达；每条路径必然终止于 ending；v0 不允许环 |
| 内容 | 行文本非空；dialogue 的 speaker ∈ characters；stage.cue ∈ 枚举 |
| 资产 | `used_by` 中的节点必须存在；asset_id 唯一 |
| 模式字段 | 离线必需文件齐全；online 字段缺失只警告 |

校验失败（errors 非空）时：**构建失败**，保留中间产物到 `<pack_id>.failed/` 供排查。

## 11. 调试预览（inkjs）

- `pack/preview.html`：内嵌 `story.ink.json` + vendored inkjs（`third_party/inkjs/`）
- 功能：显示旁白/对白/内心、选择、flag 调试面板、结局、重开
- 定位：验收工具，不作为产品形态；不做美术/配音

## 12. API 与 CLI

后端（挂在现有 `server.py`，前缀 `/ai-creation`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/adaptation/build` | 入参 `{book_root, arc_ids[], ending_count=2, options?}`；默认同步返回（LLM 调用少）；超过 30s 走任务制 |
| POST | `/adaptation/packs` | 列出某书的 pack（按 mtime） |
| POST | `/adaptation/pack` | 取单个 pack 的 `pack.json` + `validation.json`（大文件按需分段） |
| POST | `/adaptation/pack/delete` | 删除 pack |

CLI：

```
python -m prompt_harness.adaptation build --book <path> --arc <arc_id> [--ending-count 2]
python -m prompt_harness.adaptation validate --pack <path>
python -m prompt_harness.adaptation preview --pack <path>   # 打印预览页路径
```

## 13. 测试策略与 Fixtures

| 测试 | 内容 |
|---|---|
| mapper 单测 | 真实 l4 片段 fixture → 输出逐字节稳定（golden file） |
| dialogue 单测 | 真实样例（含 `X道：“…”`、代词、无引号） |
| validator 单测 | 构造坏 pack（悬空 next/死节点/未知 speaker）逐条命中 |
| designer 单测 | mock LLM 固定输出；断言结构不变、约束生效 |
| compiler 单测 | 最小 ink → 编译通过；人为语法错 → 报错含行号 |
| E2E | fixture 书 → build → validate 全绿 → inkjs 走到 2 结局（手动验收 + 简化自动断言） |

**v0 fixtures**：
- 技术 fixture：`TestBook` 的「情节3」弧（当前唯一有完整 l4/l5 的弧）
- 正式 fixture：`第一本书`（剧情尚未写出，占位先行；正文产出后切换）
- 已知数据风险：TestBook 书态混杂（情节3 为修仙 l1/l2 却含纸人教室 l4）；《第一本书》仅有 l1。改编前做数据体检，只警告不修

## 14. 验收标准（v0）

1. `build` 在 fixture 弧上产出完整 pack，`validation.ok=true`
2. `story.ink.json` 编译通过，`preview.html` 可玩到 2 个结局
3. 全部单测绿；mapper golden 文件稳定
4. 不修改现有生成链路任何文件（git diff 可证）
5. 构建过程 LLM 调用 ≤ 4 次/包，成本记录进 `validation.stats`

## 15. 成本与埋点

- LLM 仅 2 处：对白归一兜底（批量 1 次/章）、选择/结局措辞（1~2 次/弧）
- 预估 ≤ 4 次调用/包（doubao 量级：几分钱）
- 所有调用走现有 `llm_client`，自然进入 `llm_calls` 用量日志
- `validation.stats` 记录 `llm_calls/cost_est`

## 16. 里程碑拆分（设计 → 实现）

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 | 包结构 + pydantic 模型 + validator（无 LLM，手写 fixture pack 可过） | 校验单测绿 |
| M2 | 确定性映射 + 对白归一 + assets 清单 | fixture 弧 → pack（无选择/结局） |
| M3 | 分支层：章末收敛选择 + 双结局（LLM 包装） | 结构不变约束单测绿 |
| M4 | ink 导出 + 编译 + inkjs 预览页 | 预览走到 2 结局 |
| M5 | 预留字段（interaction/llm_zones）+ 全量测试 + E2E | §14 全过 |

## 17. 风险与开放问题

| 风险/问题 | 说明 | 处置 |
|---|---|---|
| 多章弧的 l4 快照缺失 | `arcs.json` 只在 `state.levels.l4.scenes` 保留最新章场景；历史章只有正文与评分 | v0 限制单章弧；中期在 `finalize_chapter` 增加 l4 快照到 `chapters[]`（另立任务） |
| 对白说话人识别准确率 | 自由叙事体复杂 | 正则+代词+LLM 批量兜底；降级 narration 并警告 |
| 分支设计感弱 | 骨架版选择机械 | 设计层（v1）再增强；v0 先验证数据模型 |
| 数据质量混杂 | l1/l2 与 l4 内容不一致 | 数据体检警告；打包时记录 `source` 便于溯源 |
| ink 编译依赖 Node runner | 需内嵌 runner 可用 | compiler 失败即构建失败；runner 生命周期由 Electron 管 |
| 单包对应单弧 | 多弧成系列尚无表达 | pack 加 `series` 字段留待 v1；v0 一弧一包 |

## 18. 附录：最小示例（单场景 + 1 选择 + 2 结局）

```json
{
  "start": "n001",
  "nodes": [
    { "id": "n001", "type": "scene", "title": "醒来", "background": "bg_room",
      "present": ["char_a"], "lines": [{ "kind": "inner", "text": "这是哪儿？" }],
      "next": "c001" },
    { "id": "c001", "type": "choice", "prompt": "怎么办？", "converge_to": "n002",
      "options": [
        { "text": "查看纸条", "next": "n002", "set": { "flags.read": true } },
        { "text": "离开教室", "next": "n002", "set": { "flags.left": true } }
      ] },
    { "id": "n002", "type": "scene", "title": "面临真相", "background": "bg_room",
      "present": ["char_a"], "lines": [{ "kind": "narration", "text": "……" }],
      "next": "end_a" },
    { "id": "end_a", "type": "ending", "title": "接受", "text": "……", "condition": "flags.read" },
    { "id": "end_b", "type": "ending", "title": "拒绝", "text": "……", "condition": null }
  ]
}
```

对应 ink：

```ink
VAR flags_read = false
VAR flags_left = false

-> n001

=== n001 ===
这是哪儿？
-> c001

=== c001 ===
* [查看纸条] ~ flags_read = true -> n002
* [离开教室] ~ flags_left = true -> n002

=== n002 ===
……
{ flags_read: -> end_a }
-> end_b

=== end_a ===
接受
-> END

=== end_b ===
拒绝
-> END
```
