# AInovel Harness 系统说明书（旧版）

> ⚠️ **本文档已被 `README.md` 取代**（2026-07-07）。
> 请查阅项目根目录的 `README.md` 获取最新版本。
> 本文档保留作为历史参考。
>
> 最后更新：2026-06-30（Phase 1-5 改造完成后）

---

## 目录

1. [架构分层](#1-架构分层)
2. [模块清单](#2-模块清单)
3. [决策系统 (Phase 1)](#3-决策系统-phase-1)
4. [约束系统 (Phase 2)](#4-约束系统-phase-2)
5. [章纲系统 (Phase 3)](#5-章纲系统-phase-3)
6. [前端结构 (Phase 4)](#6-前端结构-phase-4)
7. [静态检查 (Phase 5)](#7-静态检查-phase-5)
8. [API 端点清单](#8-api-端点清单)
9. [运维命令](#9-运维命令)

---

## 1. 架构分层

```
第 4 层：调用方
  agents/   skills/   dashboard/
  可调用任何下层，不能被下层 import

第 3 层：CLI 入口 (scripts/)
  chapter_commit.py   chapter_reset_service.py   ainovel.py
  装配模块、解析参数、调用 writer；不写业务逻辑

第 2 层：业务逻辑 (scripts/data_modules/)
  ├─ Reader (读模块): RAGAdapter  memory.orchestrator  style_sampler  loaders
  └─ Writer (写模块): *ProjectionWriter  memory.writer  禁止相互 import

第 1 层：存储
  state.json   index.db   vectors.db   style_samples.db   memory_scratchpad.json
```

### Import 方向规则

| 起点 → 终点 | 允许 | 备注 |
|---|---|---|
| 4 → 3, 4 → 2 | ✅ | agents/skills/dashboard 可调用任何下层 |
| 3 → 2 | ✅ | scripts/ 装配下层 |
| reader → writer | ❌ | reader 不能写 |
| writer → reader | ❌ | writer 不能推导 |
| 2 → 3, 2 → 4 | ❌ | 下层不能调上层 |

**自动检查**: `python tools/check_imports.py`

---

## 2. 模块清单

### dashboard/ (第 4 层 — Web 仪表板)

| 文件 | 功能 | Phase |
|------|------|-------|
| `app.py` | FastAPI 主应用 + SSE 事件流 | — |
| `workflows.py` | 10 个工作流的编排逻辑 | 1,2 |
| `agents.py` | 7 个独立 agent 路由 | 2 |
| `agent_runner.py` | AnthropicAgentRunner（SDK 封装） | — |
| `task_manager.py` | 任务队列 + SSE 推送 + 挂起/恢复 | 1 |
| `constants.py` | DecisionLevel + FreedomLevel + 约束映射 | 1,2 |
| `decision_log.py` | 决策日志（JSONL 持久化） | 1 |
| `hard_constraints.py` | 3 行硬约束头部构建器 | 2 |
| `constraint_engine.py` | 21 条规则校验引擎 | 2 |

### scripts/ (第 3 层 — CLI 和数据模块)

| 文件 | 功能 |
|------|------|
| `chapter_outline_loader.py` | 章纲解析器（新旧格式兼容） |
| `register_chapters.py` | 章纲→结构化 JSON 契约 |
| `extract_chapter_context.py` | 为 context-agent 组装上下文 |
| `update_state.py` | state.json 增量更新 |
| `update_master_outline.py` | 总纲同步（Phase 3 后不再自动调用） |
| `chapter_reset_service.py` | 章节重置（调 4 个 writer.purge_chapter） |

### data_modules/ (第 2 层 — 业务逻辑)

| 模块 | 角色 | 功能 |
|------|------|------|
| `style_sampler.py` | Reader | 风格锚点检索（SQLite + 向量） |
| `deterministic_lint.py` | Reader | 机械违规扫描（R1-R24） |
| `quantitative_audit.py` | Reader | 量化审计 |
| `audit_merger.py` | Shared | 审查结果合并 |
| `discussion_loader.py` | Reader | 用户讨论加载 |
| `*ProjectionWriter` | Writer | 投影写入（state/memory/index/vector/summary） |

### agents/ (第 4 层 — Agent 提示词)

| 文件 | 功能 |
|------|------|
| `context-agent.md` | 写前研究，组装 5 段写作任务书 |
| `reviewer.md` | 统一审查（7 维度含约束合规性） |
| `critic-agent.md` | AI 味检测（4 维度：句式/叙事/情感/对话） |
| `data-agent.md` | 结构化数据提取 |
| `deconstruction-agent.md` | 参考书拆解 |

### tools/ (运维工具)

| 文件 | 功能 | Phase |
|------|------|-------|
| `check_imports.py` | 跨层 import 违规静态检查 | 5 |
| `check_constraints.py` | constraint-rules.json 完整性验证 | 5 |
| `check_flow.py` | workflow/skill/agent 引用完整性检查 | 5 |

---

## 3. 决策系统 (Phase 1)

### DecisionLevel 四级决策

| 级别 | 含义 | 默认操作类型 |
|------|------|------------|
| L0 | AI 自动执行 | formatting, spell_check, duplicate_cleanup, log_query, cache_view |
| L1 | 需用户确认 | plot_adjustment, paragraph_edit, character_edit, setting_edit, cache_delete |
| L2 | 3 候选选择 | chapter_draft, chapter_polish, chapter_review, outline_generate, plot_branch_generate |
| L3 | 自定义 Prompt | volume_setting, character_design, core_plot_adjustment, genre_setting |

### 工作流

每个 step 执行前调 `_decision_check(task, step_id, operation_type, step_description)`：
- L0 → 自动通过，记录 auto_approved
- L1/L2/L3 → `TASKS.suspend_for_input()` 挂起，SSE 推 `awaiting_input` 事件到前端
- 前端 `AskUserModal` 展示对应 UI（确认/候选/自定义），用户回答后 `resumeTask()` 继续

### 决策日志

- 存储：`{project_root}/.ainovel/decision_logs/current.jsonl`
- API：`GET /api/decision-log?task_id=&step_id=&limit=100`
- 前端：`/decisions` 页面可视化查看

### 前端决策组件

- `AskUserModal.jsx` — L1/L2/L3 三种 UI 模式 + 旧格式兼容
- `CandidateSelector.jsx` — L2 候选方案选择器
- `DecisionPointCard.jsx` — SSE 流中实时决策点
- `DecisionLogPage.jsx` — 历史决策日志
- `SettingsPage.jsx` — 决策级别配置

---

## 4. 约束系统 (Phase 2)

### FreedomLevel 三级自由度

| 级别 | 修饰键 | 含义 |
|------|--------|------|
| 0% (strict) | 严格遵守 | 所有约束绝对执行，Anti-AI 全部激活 |
| 10% (normal) | 细微描述调整 | 环境细节/动作顺序可调，核心约束不变 |
| 20% (relaxed) | minor 剧情调整 | 分支对话/次要事件可调，三大定律不变 |

### 3 行硬约束

所有 agent system_prompt 自动注入（通过 `build_constrained_prompt()`）：

```
===== 三大硬约束（此三行不可协商，优先级高于一切其他指令）=====
[硬约束1] 大纲即法律：严格执行大纲，不得擅自发挥
[硬约束2] 设定即物理：角色能力≤已有记录
[硬约束3] 新实体必须可识别：新角色/地点/物品必须有明确名称和描写
============================================================
```

### ConstraintEngine 21 条规则

| 类别 | ID 范围 | 数量 | 检查方式 |
|------|---------|------|----------|
| Hard | H001-H006 | 6 | programmatic + regex |
| Soft | S001-S002 | 2 | programmatic |
| Style | ST001-ST002 | 2 | llm（预留） |
| Anti-AI | A001-A003 | 3 | programmatic |
| 机械规则 | R1-R24 | 8 | wrapped (deterministic_lint) |

### 使用

```python
from dashboard.constraint_engine import ConstraintEngine
from dashboard.constants import FreedomLevel

engine = ConstraintEngine()
violations = engine.validate(text, freedom_level=FreedomLevel.ZERO)
summary = engine.get_summary(violations)
# {"total": 6, "hard_count": 6, "passed": False, ...}
```

### 写入流程中的约束检查

写入流程 Step 3.5：draft → `_run_constraint_check()` → 输出 `.ainovel/tmp/constraint_audit.json` → review

---

## 5. 章纲系统 (Phase 3)

### 新格式：4 段扁平清单

```markdown
# 第{NNNN}章：{章节标题}

## 基础信息
- **目标**：...
- **阻力**：...
- **代价**：...
- **Strand**：Quest|Fire|Constellation
- **反派层级**：S|A|B|C
- **关键实体**：角色A, 角色B, 地点X
- **章末钩子**：类型 / 强度 — 描述
- **未闭合问题**：...

## 事件清单
1. **CBN（章节起点）**：主体 | 动作 | 对象
2. **CPN-1**：主体 | 动作 | 对象
3. **CPN-2**：...
4. **CEN（章节终点）**：主体 | 动作 | 对象

## 逐段推进
1. 【场景：...】【视角：...】【情绪：...】【字数：...】【感官：...】【对话：...】核心事件
2. ...

## 硬约束
- **必须覆盖**：1. ... 2. ...（最多4条）
- **本章禁区**：1. ... 2. ...（最多5条）
```

### 新旧兼容

- `chapter_outline_loader.py` 自动检测格式
- 新格式优先，失败回退旧正则解析
- 下游 dict key 名不变（goal/obstacles/cost/cbn/cpns/cen/paragraph_beats...）

### 已删除

- 卷摘要.md / 卷时间线.md / 总纲写回.json（不再生成）
- master-outline-sync（不再自动调用）
- 时间锚点/章内跨度/时间差/倒计时/爽点/视角/本章变化（从章纲字段中移除）

---

## 6. 前端结构 (Phase 4)

### Sidebar 导航（10 项，3 组）

**大纲与写章**：
- 总览 (`/`)
- 大纲与章纲 (`/outline`) — plan/confirm-plot/replan
- 章节生成 (`/write`) — 起草/审查/精修 3 标签

**分析工具**：
- 故事分析 (`/analysis`) — 角色/节奏/伏笔 3 标签

**系统管理**：
- 初始化 (`/init`)
- 文档 (`/files`)
- API 配置 (`/env-config`)
- 决策日志 (`/decisions`)
- 决策设置 (`/settings`)
- 工具箱 (`/toolbox`，仅专家模式)

### 路由表

| 路径 | 页面 | 懒加载 |
|------|------|--------|
| `/` | OverviewPage | ✅ |
| `/outline` | OutlinePage | ✅ |
| `/write` | WriteChapterPage | ✅ |
| `/analysis` | AnalysisPage | ✅ |
| `/init` | InitProjectPage | ✅ |
| `/files` | FilesPage | ✅ |
| `/env-config` | EnvConfigPage | ✅ |
| `/decisions` | DecisionLogPage | ✅ |
| `/settings` | SettingsPage | ✅ |
| `/toolbox` | ToolboxPage | ✅ |
| `/console` | CommandConsolePage (专家) | ✅ |
| `/cache` | CacheManagerPage (专家) | ✅ |
| `/system` | SystemPage | ✅ |

### 组件清单

| 组件 | 位置 | Phase |
|------|------|-------|
| AskUserModal | components/ | 1 |
| CandidateSelector | components/ | 1 |
| DecisionPointCard | components/ | 1 |
| StreamLog | components/ | — |
| SidebarNav | components/ | 1,4 |
| StepProgress | components/ | — |
| DataTable | components/ | — |
| ChartWrapper | components/ | — |

---

## 7. 静态检查 (Phase 5)

### check_imports.py
```bash
python tools/check_imports.py              # 文本输出
python tools/check_imports.py --strict     # WARN→ERROR
python tools/check_imports.py --json       # JSON 输出（CI）
```
检查：data_modules reader/writer 隔离 + dashboard 层规则 + scripts 入口规则

### check_constraints.py
```bash
python tools/check_constraints.py          # 验证 constraint-rules.json
python tools/check_constraints.py --json
```
检查：ID 唯一性、wrapped 映射、自由度合法、pattern 编译、规则总数、核心规则

### check_flow.py
```bash
python tools/check_flow.py                 # 全部检查
python tools/check_flow.py --skip-frontend # 仅后端
python tools/check_flow.py --json
```
检查：agent 文件引用、skill 完整性、workflow 路由、API 端点、孤立文件

---

## 8. API 端点清单

### Workflows (POST /api/workflows/{name})
write, review, plan, finalize, unfinalize, confirm-plot, replan, learn, query, init

### Agents (POST /api/agents/{name})
context, reviewer, critic, data, deconstruction, draft, polish

### 其他
- `GET /api/events` — SSE 事件流
- `GET /api/project/info` — 项目信息
- `GET /api/decision-log` — 决策日志查询
- `GET/POST /api/env-config` — API 配置读写
- `GET /api/files/tree` — 文件树
- `POST /api/tasks/{id}/resume` — 恢复挂起任务
- `POST /api/tasks/{id}/cancel` — 取消任务

---

## 9. 运维命令

```bash
# 启动 Dashboard
cd dashboard/frontend && npm run dev     # 前端开发服务器
python -m dashboard                       # 后端服务器

# 构建前端
cd dashboard/frontend && npm run build

# 静态检查（每次提交前运行）
python tools/check_imports.py
python tools/check_constraints.py
python tools/check_flow.py

# 章节重置（切断自馈循环）
python -m scripts.chapter_reset_service <chapter>

# 章纲注册
python scripts/register_chapters.py --start 1 --end 10

# 状态更新
python scripts/ainovel.py --project-root <dir> update-state -- \
  --add-chapters-planned --volume 1 --chapters-range "1-10"

# AST 语法验证
python -c "import ast; ast.parse(open('dashboard/workflows.py').read())"
```
