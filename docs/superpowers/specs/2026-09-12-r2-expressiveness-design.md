# R2 表现力与漫剧进阶设计规格（Mixamo/Hunyuan3D/镜头导演/口型/Seedance 关键镜头）

> 日期：2026-09-12 · 状态：待审阅 · 上游文档：`docs/总计划-三线串联与开源集成.md` v2、`docs/superpowers/specs/2026-09-11-adaptation-layer-design.md`、`docs/superpowers/specs/2026-09-12-r1b-online-slice-design.md`
> 定位：期 6。两条子线并行推进——①游戏表现力：3D 角色从 CC0 占位升级为 AI 生成模型（接入点+降级链）；
> ②漫剧进阶：镜头导演（确定性优先的 LLM 增强）+ Seedance 关键镜头（draft 控本/白名单/预算闸）+ 口型，
> 最终产出 10 分钟可展示切片。本文档为设计先行；依赖期 3（资产管线/漫剧 MVP）与期 5（R1b）交付。

---

## 1. 背景与目标

期 3 已有：media 驱动管线（seedream/kokoro/cloud_tts + ark_runner + 内容寻址资产库）、drama 投影
（确定性分镜 shots.json + 字幕）与 PyAV 合成（静帧运镜 → 60-90s 竖屏 mp4）。期 4/5 已有：纸人教室
游戏切片，角色以 2D 立绘/billboard 占位（CC0/Seedream 立绘）。

R2 目标：**在不推翻既有管线的前提下，给两条线加表现力，并给「花式烧钱」装上闸门**——

1. 角色 3D：Seedream 立绘 → Hunyuan3D 图生模 → Mixamo 自动绑定 + 动画 → 进游戏/进漫剧；每一环都有降级链，任何失败不卡死管线与游戏。
2. 镜头导演：把 drama 确定性投影的运镜从「固定规则」升级为「确定性基线 + 受限 LLM 增强」，边界收死。
3. 口型：明确 Rhubarb 接入条件（满足才接，不满足不上），失败有兜底。
4. Seedance 关键镜头：draft 两段式控本 + 关键镜头白名单 + 预算闸（media 域内第一个预算闸实例）。
5. 验收锚点：10 分钟可展示切片（漫剧）+ 表现力接入演示（游戏）。

## 2. 范围

### 2.1 做

1. model3d 资产管线：hunyuan3d driver + mixamo 工位流程 + 资产库流转 + 三级降级链
2. 模板工程 3D 角色消费（GLB/FBX 导入 + 动画重定向 + 演示场景 + 自动回退）
3. AI 场景落地形态（高清全景背景 + 布景增强；HunyuanWorld 仅观察）
4. 镜头导演 v1（镜头词汇表 + LLM 标注式增强 + 确定性回退 + golden 保护）
5. Seedance 关键镜头（shot importance 白名单 + draft 两段式 + 预算闸 + 失败降级静帧）
6. 口型（Rhubarb 接入条件验证 + phoneme→blendshape/2D 嘴型映射 + 静态兜底）
7. 10 分钟漫剧切片合成（分段渲染 + 拼接 + 断点续跑）与验收材料

### 2.2 不做

- 不做 HunyuanWorld-1.0 集成（总计划 §3.2 观察项，不押注）
- 不做内核级动画系统自研（绑定/重定向全靠 Mixamo + Godot 原生能力）
- 不做全片 Seedance（只有白名单关键镜头；预算闸兜底）
- 不做镜头导演的剧情/顺序/时长改写（LLM 无权动 shots 的结构、轨道与字幕）
- 不做多角色群口型（本期 ≥1 个对白角色跑通即达标）
- 不做漫剧横屏正片（正片仍竖屏 1080x1920；横屏仅商店预告片 profile，属期 7 store_kit）
- 不做 pyJianYingDraft 剪映导出（可选依赖，列入 backlog，见 OQ-7）

## 3. 术语

| 术语 | 含义 |
|---|---|
| 降级链 | 资产生成/加载失败时按「AI 模型 → CC0 占位 → 2D 立绘」顺序自动回退 |
| 工位流程 | 无 API 的外部服务（Mixamo）用「清单登记 + 人工上传/下载 + 入库核验」半自动化 |
| 镜头导演 | 对确定性 shots 做运镜增强的组件；只标注镜头类型与幅度，不改结构 |
| 关键镜头 | 白名单内的 shot（硬事件/高潮冲突/结局等确定性标记），允许消费 Seedance |
| draft 控本 | Seedance 先出低配小样 → 抽检通过 → 才出正式高清的两段式消费 |
| 预算闸 | 任务提交前核算成本、超限熔断降级的守卫（media 域内第一个实例） |
| 接入条件 | 口型功能启用的前置判据（§8），不满足则维持静态嘴 |

## 4. 与上游文档的锚点

| 锚点 | 内容 | 本文对应 |
|---|---|---|
| 总计划 §4 期 6 行 | Mixamo/Hunyuan3D/AI 场景、镜头导演、口型；漫剧 Seedance 关键镜头（draft 控本）；验收「10 分钟可展示切片 + 动态镜头漫剧」 | 全文/§13 |
| 总计划 §3.2 台账 | Mixamo（外部免费服务）、Hunyuan3D 2.x（观察，后期）、Rhubarb（口型 cue）、ark-cli（Seedance）、CC0 资产库 | §6/§7/§8/§9 |
| 总计划 §3.4 前置条件 | 方舟视觉资源需开通付费 Endpoint（Seedream/Seedance 不在现有 Coding Plan 内） | §9/OQ |
| 总计划 §6 风险表 | 角色一致性（最大技术风险）、成本失控（Seedance）、Rhubarb 中文质量、预算闸缺失 | §9/§10/§15 |
| 总计划 §7 决策 7 | 3D 资产先占位/CC0，AI 3D 延到表现力阶段（即本期） | §6 |
| 总计划 §2.2/§2.3 | 预算闸为 OS 化缺失项；驱动带 manifest（能力/成本项）为既定接口 | §9.3 |
| 改编层 spec §5.3/§5.6 | story 行类型（stage=硬事件）、assets.json kind 枚举（model3d 已预留） | §6/§10 |
| `drama/projector.py`、`drama/compose.py` | 确定性投影与合成（字节级稳定、golden 测试）是本期不可破坏的基线 | §7/§10 |
| `media/drivers/base.py`、`asset_store.py` | 驱动 manifest/DriveResult 约定；资产库 pending→done/failed 状态机 | §6/§9 |
| R1b spec §11 | 模板工程 addons 结构（3D 消费在 templates/godot 内扩展） | §11 |

## 5. 架构与数据流

### 5.1 游戏表现力子线

```
Seedream 立绘/三视图（期 3 资产，资产库已有）
        │
        ▼  hunyuan3d driver（本地 GPU / 云 Endpoint）
   网格 GLB ──质量门（自动检查+人工抽检）──▶ 资产库 model3d 入库（status=done）
        │ 不过门
        ▼
   降级：CC0 人形占位（Quaternius）──缺──▶ 2D 立绘 billboard（期 4 形态）
        │
        ▼  mixamo 工位流程（人工上传/下载 + 核验）
   绑定+动画（idle/walk/talk）──▶ Godot 导入（重定向）──▶ 演示场景
```

### 5.2 漫剧进阶子线

```
adaptation pack ──▶ projector（确定性，不变）──▶ shots.json（+importance/camera 扩展字段）
        │                                    │
        │                                    ▼  镜头导演器（LLM 标注，受限词汇表）
        │                              shots.json.camera（非法 → 逐镜头回退默认）
        ▼
assets 生成：静帧图（Seedream）+ 关键镜头动效（Seedance draft→抽检→正式）
        │
        ▼  compose 分段渲染（每 ~60s 一段，断点续跑）──▶ PyAV 拼接 ──▶ 10 分钟竖屏 mp4
        │
        └──▶ 口型（满足接入条件的对白角色）：Rhubarb → 口型 JSON → blendshape/2D 嘴型驱动
```

## 6. 3D 表现力：资产接入点与降级链

### 6.1 角色模型管线（主链）

| 环节 | 接入点 | 产物 | 说明 |
|---|---|---|---|
| 源图 | 期 3 Seedream 资产（`spr_<char>_default` 及三视图） | 图像 | 三视图用于图生模与 Seedance 参考图，一次生成双线复用 |
| 图生模 | `media/drivers/hunyuan3d.py`（新） | GLB | 本地推理（显存检查 `available()`）或云端 Endpoint（若开通）；输入立绘/三视图，输出网格 |
| 质量门 | driver 内自动检查 + 人工抽检记录 | 通过/拒绝 | 自动：顶点数范围、网格非空、包围盒合理；人工：形似度/破洞抽检表 |
| 绑定动画 | `media/drivers/mixamo.py`（新，工位流程） | 绑定 FBX + 动画集 | 无官方 API，半自动（§6.2） |
| 进引擎 | 模板工程 Godot 导入（4.3+ ufbx 原生 FBX） | 场景内角色 | 动画重定向用 Godot 原生能力；动画集最小集 idle/walk/talk |

### 6.2 Mixamo 工位流程（无 API 的现实约束）

```
mixamo_jobs/<asset_id>/
  todo.json        # 待办：上传哪个源文件、要哪些动画（从 pack assets 清单派生）
  in/              # 人工从 Mixamo 下载的产物放这里（T-pose 绑定 FBX + 各动画 FBX）
  done.json        # 核验记录（骨骼数/动画数/文件指纹）
```

driver.run() 行为：无 in/ 产物 → `skipped`（附人工操作指引，绝不阻塞批处理）；有产物 → 核验 +
登记资产库（content_id 入库）。每次上游条款变化在 UPSTREAM 式备注中复核（OQ-4）。

### 6.3 降级链（硬约定）

资产库 entry 增加回退声明（assets.json v0.2 向后兼容扩展，改编层 spec §5.6 基础上）：

```json
{
  "asset_id": "mdl_char_chen",
  "kind": "model3d",
  "ref": "char_chen",
  "provider_hint": "hunyuan3d",
  "pipeline": {
    "source_portrait": "spr_chen_default",
    "turnaround_views": ["spr_chen_view_front", "spr_chen_view_side", "spr_chen_view_back"],
    "hunyuan3d": { "status": "pending" },
    "mixamo": { "status": "pending", "anim_set": ["idle", "walk", "talk"] }
  },
  "fallback": ["mdl_char_chen_cc0", "spr_chen_default"],
  "status": "pending"
}
```

- 运行时/合成时按 `fallback` 顺序取第一个 `status=done` 的资产；全部缺失 → 2D 立绘兜底（必在）。
- **降级是自动的、静默的**：记日志与验收报告，不弹窗、不中断、不崩溃。
- 任何一环失败（Hunyuan3D 显存不足/Mixamo 未人工处理/导入失败）只影响该资产，不影响整条管线。

### 6.4 AI 场景（落地形态收敛）

- v1 落地 = Seedream 高清全景背景图（横竖双版）+ 简单几何布景 + 天空盒；不生成场景网格。
- HunyuanWorld 仅跟踪观察（总计划既定），不投入实施资源。
- 场景升级失败 → 回退期 3 静态背景，游戏侧无感知。

## 7. 镜头导演（分镜 LLM 化的边界：确定性优先）

### 7.1 边界（硬约定）

| 层 | 归属 | LLM 权限 |
|---|---|---|
| shots 的存在性/顺序/时长/轨道/字幕 | projector 确定性规则 | **无权**（只读输入） |
| stage_cue 镜头、end_card 镜头 | 确定性（硬事件/收尾） | **无权**（不参与增强） |
| 镜头运镜类型与幅度 | 默认确定性规则（现 projector 逻辑）；LLM 可在白名单镜头上做**标注式增强** | 仅从词汇表选择，不发明 |

### 7.2 词汇表与约束

```
镜头词汇（封闭集）：still | push_in | pull_out | pan_l | pan_r | closeup | dolly_l | dolly_r | shake_s
幅度档位：intensity 1~3
```

LLM 输入：shots 列表（含类型/台词摘要/章节情绪标签，来自 l3 core，仅作参考）；
LLM 输出：`[{shot_id, camera, intensity} | null]`（null = 保持默认）。

后置约束（代码校验，非 prompt 约定）：同类型连续 ≤3 个；intensity=3 连续 ≤2 个；
stage_cue/end_card 槽位出现任何输出 → 视为非法回退。任一约束违反 → **该镜头**回退确定性默认
（不整体作废），并记 `camera_director_overrides_rejected` 计数。

### 7.3 golden 保护

确定性路径（不用 LLM）的 shots.json/合成产物字节级稳定测试**原样保留**（现有 projector golden 不动）；
LLM 增强路径用 mock 固定输出测试。两条路径开关可控（`camera_director: off|on`，off 为默认，
验收「动态镜头漫剧」时开）。

## 8. 口型（Rhubarb）接入条件

### 8.1 接入条件（全部满足才启用，缺一即静态嘴）

- C1：场景存在「开口说话」的 dialogue 行（speaker 有成句台词，排除主角 inner）；
- C2：该说话角色镜头面部可见（closeup/shot_reverse_shot 类），且角色已有可动面部——
  3D：blendshape ≥ 15 个（含元音口型基础集）；或 2D：嘴型贴图 ≥ 4 帧；
- C3：该行音频已产出（Kokoro/云 TTS）且时长已知。

R1a 的纸人教室（纸人不开口、主角内心独白）**天然不满足 C1**——与总计划期 4 细节「R1 无口型需求」
一致；R2 在漫剧 dialogue 镜头与游戏对白角色上首次启用。

### 8.2 管线与映射

```
台词音频 + 台词文本 ──▶ Rhubarb CLI（制作侧命令行，L2）──▶ 口型 JSON（phoneme + 时间戳）
        ──▶ 映射表 rhubarb_to_blendshapes_v1（phoneme → blendshape 组合 / 2D 嘴型帧号）
        ──▶ 游戏内 blendshape 驱动 / 漫剧合成逐帧嘴型替换
```

- Rhubarb 中文支持有限：phonetic 模式 + 抽检；效果不达标的角色/语言直接回退静态嘴。
- 失败兜底层级：Rhubarb 失败 → 静态嘴（微开）；不影响该镜头其余合成。
- 验收口径：≥1 个对白角色口型可用即达标；口型质量不做硬验收（中文 phonetic 质量风险已在总计划
  §6 定级为低-中）。

## 9. 漫剧进阶：Seedance 关键镜头

### 9.1 关键镜头白名单（确定性标记）

projector 产出时对每个 shot 打 `importance`：

| 标记 | 规则（确定性） |
|---|---|
| key | stage_cue 镜头；conflicts 派生的高潮场景镜头；ending 镜头；每章开场镜头（≤1 个/章） |
| normal | 其余全部 |

只有 `importance=key` 的镜头进入 Seedance 候选；候选数超预算闸上限时按
（ending > stage_cue > climax > 开场）优先级截断。**normal 镜头无论预算多充裕都不送 Seedance**
（防成本漂移；扩白名单需改 projector 规则 + 验收，不允许运行时临时加）。

### 9.2 draft 两段式控本

```
候选镜头 ──▶ Seedance draft（480p/5s，低配参数，ark_runner 批量）
        ──▶ 抽检 gate：角色一致性（对照三视图）+ 无畸变 + 运动合理；人工抽检表记录
        ├── 通过 ──▶ Seedance 正式（1080p 竖屏，同 prompt 同 seed 策略）
        └── 不通过 ──▶ 调整 prompt 重 draft（≤2 次）──▶ 仍不过 ──▶ 该镜头降级静帧运镜
```

正式生成失败/超时 → 降级静帧（compose 已有能力），绝不重试风暴（对齐 driver 约定「绝不重试风暴」）。

### 9.3 预算闸（media 域内第一个实例）

`media/budget.py`（新）：驱动提交前核算 + 熔断，配置入 driver manifest（对齐总计划 §2.3 第 4 条）。

```json
{
  "scope": "pack",
  "limits": {
    "seedance_draft_calls": 40,
    "seedance_final_calls": 20,
    "seedance_seconds": 120,
    "seedance_cost_est_max": 50.0,
    "seedream_calls": 200,
    "cloud_tts_chars": 50000
  },
  "on_exhaust": "degrade_still",
  "ledger": "<pack>/drama/budget_ledger.jsonl"
}
```

- 每次外呼前 `check(task)`：超额 → 拒绝提交并返回降级建议（degrade_still）；执行后 `commit(actual)`
  记账（成本项口径 = driver manifest.cost_items）。
- 预算闸数值为首版建议值，**具体上限需用户拍板**（OQ-5）；账本随验收材料上报。
- 该闸只做 media 域；跨线统一调度器/预算中心仍不做（总计划 §2.2 按真实瓶颈出现再做）。

### 9.4 seedance 扩展字段（shots.json v0.2 向后兼容）

```json
{
  "shot_id": "s021",
  "type": "closeup",
  "importance": "key",
  "key_reason": "stage_cue",
  "camera": { "type": "push_in", "intensity": 2 },
  "seedance": {
    "enabled": true,
    "stage": "none | drafted | approved | rejected | final | failed",
    "motion_prompt": "镜头缓推，纸人缓缓转头，衣角微动，恐怖氛围",
    "ref_images": ["spr_chen_view_front"],
    "duration": 5
  }
}
```

## 10. 模块位置

```
prompt-harness/prompt_harness/
  media/
    drivers/hunyuan3d.py    # 图生模驱动（本地/云，显存检查，质量门）
    drivers/mixamo.py       # 工位流程驱动（skipped 语义 + 核验入库）
    drivers/seedance.py     # 视频驱动（draft/final 两段参数，ark_runner 调用）
    budget.py               # 预算闸（check/commit + ledger）
  drama/
    camera_director.py      # 镜头导演器（词汇表 + LLM 标注 + 约束校验 + 回退）
    segmenter.py            # 10 分钟分段渲染计划 + 断点续跑
    lipsync.py              # Rhubarb 调用 + 口型 JSON 解析 + 映射表
templates/godot/
  addons/ainovel_expressiveness/   # 3D 角色消费：模型/动画加载、降级链、口型 blendshape 驱动
```

## 11. 测试策略

| 测试 | 内容 |
|---|---|
| driver 单测 | hunyuan3d 显存不足 → skipped（不抛异常）；mixamo 无产物 → skipped + 指引；质量门自动检查逐条命中 |
| 降级链单测 | 构造各级缺失 → 回退顺序正确、静默降级、验收报告计数正确 |
| 镜头导演单测 | mock LLM：合法标注生效；越界（未知镜头类型/动 stage_cue 槽位）逐镜头回退；约束（连续性）校验命中 |
| golden 保护 | 确定性路径 shots.json + 合成产物字节级不变（现有 golden 原样保留并必须继续通过） |
| 预算闸单测 | check 拒绝超额任务；commit 记账；ledger 与 manifest.cost_items 口径一致 |
| 口型单测 | 接入条件判定（C1~C3 缺一即静态）；Rhubarb JSON 解析与映射表正确性 |
| 分段合成 | 段间拼接时基/音频无缝；断点续跑（杀进程后重跑从已完成段继续） |
| E2E | 一个完整弧 → 镜头导演 on + 白名单 Seedance → 10 分钟成片 + 验收材料；游戏演示场景降级演练（拔模型自动回退） |

## 12. 验收标准

### 12.1 10 分钟可展示切片（漫剧，主验收物）

1. **时长与结构**：正片 9~11 分钟，覆盖一个完整弧（片头卡 → ≥3 幕/章 → 结局卡）；来源 pack 与
   游戏共用（单一数据源，锚定总计划 §1.1）。
2. **完整性**：全片无灰帧/黑帧 >0.5s；字幕轨全覆盖（对白+旁白+演出提示）；配音轨全覆盖（允许
   静音段落 ≤2s）；BGM 无断档（淡入淡出衔接）。
3. **动态镜头**：≥6 个 Seedance 关键镜头成功产出并保留在成片中；全部走 draft→抽检→正式两段式
   （ledger 可查）；非白名单镜头 0 个使用 Seedance。
4. **角色一致性**：≥3 个主要角色跨镜头抽检通过率 ≥80%（人工抽检表随材料上报）。
5. **口型**：≥1 个满足接入条件的对白角色口型可用（未满足条件则静态嘴，需在报告中说明未满足哪条）。
6. **预算**：ledger 汇总 Seedance 调用次数/秒数/费用估算，全部在闸内；超预算镜头呈降级静帧形态。
7. **产物**：1080x1920 竖屏 mp4 + shots.json + budget_ledger.jsonl + 抽检记录表 + 成本汇总。

### 12.2 游戏表现力接入（次验收物）

1. ≥1 个角色完成「立绘 → Hunyuan3D → Mixamo 绑定 → 进演示场景」全链，播放 idle/walk/talk 最小动画集。
2. 降级演练：删除 AI 模型资产 → 自动回退 CC0 → 再删 → 回退 2D 立绘，全程无崩溃、无人工干预。
3. 现有离线/在线切片（期 4/5 验收）零回归。

### 12.3 镜头导演

4. 确定性路径 golden 全绿；LLM 增强路径在 mock 下约束校验全命中；`camera_director: off` 时行为与
   期 3 完全一致。

## 13. 里程碑拆分（4-5 阶段）

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 | model3d 资产管线（hunyuan3d + mixamo 工位 + 资产库流转 + 降级链声明） | driver 单测 + 降级链单测绿；≥1 角色网格入库 |
| M2 | 模板工程 3D 消费（ainovel_expressiveness addon：导入/重定向/动画/自动回退）+ 演示场景 | §12.2 全过 |
| M3 | 镜头导演 v1 + shots v0.2 扩展字段（importance/camera） | §12.3 过；golden 不变 |
| M4 | Seedance 关键镜头（driver + 白名单 + draft 两段式 + 预算闸） | 抽检 gate 记录完整；ledger 对账一致 |
| M5 | 口型（接入条件 + 映射）+ 分段合成/拼接/断点续跑 + 10 分钟切片 E2E | §12.1 全过，材料上报看板 |

## 14. 风险

| 风险 | 等级 | 对策 |
|---|---|---|
| Hunyuan3D 生成质量不稳（破洞/形似度低/显存不足） | 高 | 质量门 + 人工抽检 + 三级降级链 + 生成次数计入预算闸；本地显存不足可切云 Endpoint（OQ-2） |
| Seedance 角色一致性（漫剧最大技术风险，总计划 §6） | 高 | 三视图参考图 + draft 抽检 gate + 失败重 draft ≤2 次 + 降级静帧；白名单控量 |
| Seedance 成本失控 | 高 | draft 两段式 + 白名单 + 预算闸熔断 + ledger 上报 |
| Mixamo 无 API、条款可能变化 | 中 | 工位流程人工兜底；条款复核入 OQ-4；不阻塞主链（降级链兜底） |
| Rhubarb 中文口型质量 | 中 | 接入条件先验证（试点 1 角色）；不达标直接静态嘴（不阻塞 §12.1 主验收） |
| LLM 镜头导演破坏节奏/输出不可靠 | 中 | 封闭词汇表 + 代码级约束校验 + 逐镜头回退 + golden 只锁确定性路径；off 开关常备 |
| 10 分钟长合成不稳定（内存/中断） | 中 | 分段渲染（~60s/段）+ 断点续跑 + 段间校验 |
| 方舟视觉资源未开通（Seedance 无 Endpoint） | 高 | 前置外部开通（OQ-3）；未开通前 M4/M5 的 Seedance 部分以 mock/静帧形态先行开发 |

## 15. 开放问题

| # | 问题 | 需要谁拍板/动作 |
|---|---|---|
| OQ-1 | Hunyuan3D 2.x 社区许可商用条款复核 | **外部确认**（许可文本核读）；通过前产物仅限内部验收 |
| OQ-2 | 本机显卡是否满足 Hunyuan3D 本地推理（6~16GB 显存）；不足则走云按量 | **用户确认硬件**；影响 driver 部署形态 |
| OQ-3 | 方舟平台视觉资源开通（Seedance/Seedream 付费 Endpoint）与资费核价（`arkcli pricing models --modality ComputerVision`） | **外部开通**+用户确认预算 |
| OQ-4 | Mixamo 服务条款当前版本商用授权复核 | 外部确认（条款页面核读 + 备份存档） |
| OQ-5 | 预算闸首版数值（§9.3：draft 40 次 / final 20 次 / 120s / ¥50） | **用户拍板**（金额敏感） |
| OQ-6 | Mixamo FBX 在 Godot 4.3+ ufbx 导入链是否顺畅；是否需要 Blender CLI 转换兜底 | 实施期 M2 实测定，默认不引入 Blender |
| OQ-7 | pyJianYingDraft（剪映二剪导出）是否纳入本期 | 用户拍板；默认不做、列 backlog |
| OQ-8 | 「每章开场镜头」是否纳入 key 白名单（白名单规模的口径之一） | 用户拍板；默认纳入（≤1 个/章） |
