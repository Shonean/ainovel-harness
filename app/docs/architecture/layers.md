# AInovel Harness 架构分层（2026-06-30 整理）

> 解决「代码分离差，独立性差，功能相互污染」。重划数据所有权，明确 import 方向，让重写循环从根上无法形成。

## 一、分层模型

```
┌────────────────────────────────────────────────────────┐
│ 第 4 层：调用方                                         │
│   agents/  · skills/  · dashboard/                     │
│   可调用任何下层，但不能被下层 import                    │
│                                                         │
│   Phase 1-2 新增:                                        │
│   constants.py (DecisionLevel + FreedomLevel)            │
│   decision_log.py (决策日志 JSONL)                       │
│   hard_constraints.py (3行硬约束注入)                    │
│   constraint_engine.py (21条规则校验引擎)                │
└────────────────┬───────────────────────────────────────┘
                 │
┌────────────────▼───────────────────────────────────────┐
│ 第 3 层：CLI 入口（scripts/）                          │
│   chapter_commit.py · chapter_reset_service.py · ...    │
│   装配模块、解析参数、调用 writer；不写业务逻辑         │
└────────────────┬───────────────────────────────────────┘
                 │
┌────────────────▼───────────────────────────────────────┐
│ 第 2 层：业务逻辑（data_modules/）                     │
│   内部禁止横向 import（reader ⇎ writer）                │
│   ├─ 2a. 读模块（reader）                               │
│   │   RAGAdapter · memory.orchestrator                  │
│   │   context_manager · style_sampler                   │
│   │   loaders（chapter_outline_loader 等）              │
│   └─ 2b. 写模块（writer）                               │
│       VectorProjectionWriter · MemoryProjectionWriter  │
│       SummaryProjectionWriter · StateProjectionWriter  │
│       IndexProjectionWriter · StyleSampler.extract     │
└────────────────────────────────────────────────────────┘
```

## 二、import 方向规则

| 起点 → 终点 | 允许 | 备注 |
|---|---|---|
| 4 → 3, 4 → 2 | ✅ | agents/skills/dashboard 可调用任何下层 |
| 3 → 2 | ✅ | scripts/ 装配下层 |
| 2a (reader) → 2a (reader) | ⚠️ 慎 | 同类内部可用，但需明确理由 |
| 2a (reader) → 2b (writer) | ❌ | reader 不能 import writer（防止 reader 写出脏数据） |
| 2b (writer) → 2a (reader) | ❌ | writer 写完即返回，不读 reader 推导 |
| 2b (writer) → 2b (writer) | ❌ | writer 之间不能互相 import（防 A 写 B 也写） |
| 2 → 3, 2 → 4 | ❌ | 下层不能调用上层 |

**自动检查**：`python tools/check_imports.py` 静态扫描 `data_modules/` 下的违规 import。

## 三、数据所有权（单一 source of truth）

每类数据**只允许一个 writer**。其他模块只能读。

| 数据 | 唯一 writer | 唯一读路径 | 写入触发 |
|---|---|---|---|
| `state.json` 顶层 | `StateProjectionWriter` | `state_manager` | commit accepted |
| `state.json.entity_state` | `StateProjectionWriter` | `state_manager` | commit accepted |
| `memory_scratchpad.json` | `MemoryProjectionWriter` | `memory.orchestrator` | commit accepted |
| `index.db` 实体/关系/事件 | `IndexProjectionWriter` | `index_manager` | commit accepted |
| `vectors.db` | `VectorProjectionWriter` | `RAGAdapter` | commit accepted |
| `summaries/chNNNN.md` | `SummaryProjectionWriter` | `extract_chapter_context` | commit accepted |
| `chapter_directives/chapter_NNN.json` | `plan` skill | `chapter_outline_loader` | /ainovel-plan 全量覆盖 |
| `约束大纲/第NNNN章-章纲.md` | `plan` skill | `chapter_outline_loader` | /ainovel-plan（Phase 3: 4段扁平格式） |
| `style_samples.db` | `StyleSampler.extract` | `style_sampler.select` | /ainovel-finalize |
| `constraint-rules.json` | 手动维护 | `ConstraintEngine` | Phase 2: 规则注册表 |
| `decision_logs/current.jsonl` | `_decision_check()` | `DecisionLog.list_decisions()` | Phase 1: 决策流水账 |

⚠️ **已知架构债务**：`state.json.entity_state` 与 `memory_scratchpad.character_state` 是同一事实的两份副本（都从 `state_deltas + character_state_changed` 派生）。完整修复需让 state 从 scratchpad 派生，改造面大，本轮未做（见 `state_projection_writer.py` 文件头注释）。

## 四、Writer 单一所有权合约

每个 `*ProjectionWriter` 必须暴露两个方法：
- `apply(commit_payload, project_root) -> dict` — 写
- `purge_chapter(project_root, chapter) -> Any` — 重置

`chapter_reset_service` 只调用 `purge_chapter`，永远不直接 SQL。

读模块（RAG / orchestrator / style_sampler）只 import writer 的类型/常量，不调用写方法。

## 五、自馈循环防护（按读 / 写边界）

### 读边界
- `RAGAdapter.search` 的 `chapter` 参数语义：**`chapter < ?`**（严格小于），排除当前章 → 防止 RAG 搜回自己
- `style_sampler.select_samples_for_chapter` 新增 `exclude_chapter` 参数 → 防止风格锚点自引用
- `load-context` 通过 `memory.orchestrator` 读 scratchpad；scratchpad 中 `source_chapter = N` 的 items 在 commit 时被标 outdated → query 时只取 active

### 写边界
- `MemoryProjectionWriter.apply_commit_projection` 第一步调用 `mark_outdated_by_source_chapter(chapter)` → 重写前清旧版
- `SummaryProjectionWriter.apply` 前 `unlink(summaries/chNNNN.md)` → 重写前清旧版
- `VectorProjectionWriter.purge_chapter(chapter)` → chapter_reset_service 手动调用

### 重写统一入口
- `python -m scripts.chapter_reset_service <chapter>` 一次性调用 4 个 writer 的 `purge_chapter`，切断所有自馈路径

## 六、变更日志

| 日期 | 改动 | Phase |
|---|---|---|
| 2026-06-30 | RAG: `chapter <= ?` → `chapter < ?`（8 处） | — |
| 2026-06-30 | memory: 新增 `ScratchpadManager.mark_outdated_by_source_chapter` | — |
| 2026-06-30 | summary: apply 前 unlink 旧版 | — |
| 2026-06-30 | style_sampler: 新增 `exclude_chapter` 参数 | — |
| 2026-06-30 | 新增 `chapter_reset_service.py` + 4 个 writer.purge_chapter | — |
| 2026-06-30 | 新增 `tools/check_imports.py` | — |
| 2026-06-30 | 新增 `dashboard/constants.py` (DecisionLevel + FreedomLevel) | 1 |
| 2026-06-30 | 新增 `dashboard/decision_log.py` + `GET /api/decision-log` | 1 |
| 2026-06-30 | workflows.py: 所有 step 接入 `_decision_check()` 阻塞检查 | 1 |
| 2026-06-30 | task_manager.py: Task 增加 decision_records + project_root | 1 |
| 2026-06-30 | 新增 `dashboard/hard_constraints.py` (3行硬约束注入) | 2 |
| 2026-06-30 | 新增 `dashboard/constraint_engine.py` (21条规则引擎) | 2 |
| 2026-06-30 | 新增 `references/shared/constraint-rules.json` (规则注册表) | 2 |
| 2026-06-30 | workflows.py: 全部 agent 调用接入 `build_constrained_prompt()` | 2 |
| 2026-06-30 | agents.py: `_stream_agent_response()` 自动注入约束 | 2 |
| 2026-06-30 | agents/context-agent.md: 新增自由度感知段落 | 2 |
| 2026-06-30 | agents/reviewer.md: 新增第6.6维「约束合规性」审查 | 2 |
| 2026-06-30 | constants.py: `FREEDOM_LEVEL_CONSTRAINT_MAP` + `validate_freedom_level()` | 2 |
| 2026-06-30 | chapter_outline_loader.py: 新4段格式解析 + 旧格式兼容 | 3 |
| 2026-06-30 | ainovel-plan/SKILL.md: 21字段→4段格式 + 删除卷脚手架步骤 | 3 |
| 2026-06-30 | 删除卷摘要/卷时间线/总纲写回生成；master-outline-sync 不再自动调用 | 3 |
| 2026-06-30 | 前端: App.jsx sidebar 16→10项, 3核心流程组 | 4 |
| 2026-06-30 | 前端: 新增 AnalysisPage (合并角色/节奏/伏笔) | 4 |
| 2026-06-30 | 前端: WriteChapterPage 新增审查+精修标签 | 4 |
| 2026-06-30 | 前端: 移除 ReviewPage/FinalizePage 独立路由 | 4 |
| 2026-06-30 | tools/check_imports.py: 新增 dashboard/scripts 层规则 + --json | 5 |
| 2026-06-30 | 新增 `tools/check_constraints.py` (规则完整性6项检查) | 5 |
| 2026-06-30 | 新增 `tools/check_flow.py` (workflow/skill/agent完整性5项检查) | 5 |
| 2026-06-30 | 新增 `docs/architecture/system-manual.md` (系统说明书) | — |
