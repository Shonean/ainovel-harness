# 模块融合设计：以测试书工作台为主，吸收正式书可取之处

日期：2026-08-10
状态：已获用户批准

## 目标

1. 清除全部测试书与正式书数据（8 本），重置当前书指针。
2. 以「测试书工作台」（AI 创作模块 / AICreationPage）为主形态，吸收正式书模块的可取之处，融合成单一创作模块。
3. 所有书形态统一：不再区分测试书/正式书，统一走同一个工作台。

## 用户拍板（AskUserQuestion 三次确认）

| 决策点 | 结论 |
|---|---|
| 吸收范围 | 故事分析三件套 + 文件树编辑器 + 数据导出 + 一键生成全书 |
| 正式书仪表盘 | 移除，统一 AI 创作工作台 |
| 清除范围 | 全部 8 本 + 重置指针 |
| 融合架构 | **前端聚合**（分析三件套前端聚合 arcs/elements，后端零新增分析端点） |
| 一键生成 | **驱动逐弧流水线**（后端批量端点，复用 ai_creation 内部函数，不依赖 skill 工作流） |
| 建书入口 | **统一为新建书**（test_book 标记废弃，裸建直进工作台） |
| 伏笔追踪 | **降级为「情节进度」甘特**（测试书无伏笔数据模型） |
| 移除方式 | **删文件 + 删路由**（git 可恢复） |

## 数据清除

- 删除 `小说系统/` 下除 `ainovel-write` 外全部书目录：
  - 测试书：测试书0807_192004、测试书0807_221018、诡秘调查员，旧神回响、镇渊帖、长街客、陆知砚、青山大奉·前十章
  - 正式书：甩锅甩成了百官楷模
- 重置 `~/.claude/ainovel-write/workspaces.json`：current_project_root / last_used / last_used_project_root 清空
- 保留：corpus 语料库、plot_extract_results、模板库、api_library.json

## 融合后形态

### 路由
- `/ai-creation` = 唯一创作工作台
- 主页点任何书 → `/ai-creation`
- 删除 `/app` 路由整块 + App.jsx 布局 + CreateTestBookPage

### AICreationPage 侧栏
```
📝 写作   大纲与章纲 · 章节生成
🧩 素材   片段扩写
📊 分析   全书概览 · 角色图鉴 · 节奏雷达   ← 新增
⚙️ 系统   文档 · 检索体检 · 通道配置 · 导出 · 批量生成
```

## 功能实现

### 分析三件套（前端聚合）
数据源 = `/ai-creation/state` 全量返回的 elements + arcs + 已落盘章。
- **全书概览**：总字数/弧数/章数/均分/污染 + 每章 intent/quality/overall 走势（ECharts line）
- **角色图鉴**：元素列表（角色/物品/设定）+ 每元素参与情节数/出场章数 + 关系图谱（elements.relations，ECharts graph）+ 元素详情
- **节奏雷达**：每章字数分布 + 质量趋势折线 + 落盘/草稿状态甘特（情节进度）

### 文档编辑器
复用 `/api/files/tree`、`/api/files/read`、`/api/files/write`（app.py 已有，白名单 正文/AI生成/大纲/设定集/.ainovel/context）。
前端全量 md 编辑，替代只读「设定集文档」。

### 数据导出
前端聚合 state + 已落盘章 → 打包 JSON 下载（零后端改动）。

### 一键生成全书 = 批量逐弧生成
- 新后端端点 `POST /ai-creation/batch-generate`（server.py）：
  请求 `{target_chapters, arc_id?}`；复用 ai_creation 内部函数串行驱动
  新建弧 → 选元素 → l1 → l2 → l3 → 逐章 l4/l5 → 评分 → 落盘 → finish，
  达到目标章数停止。任务记录可轮询进度、可取消。
- 前端「批量生成」面板：目标章数 + 实时进度 + 停止按钮。

## 正式书仪表盘移除清单

删除文件：
- App.jsx（布局）
- main.jsx 中 `/app` 路由整块 + 相关 lazy import
- pages：OverviewPage、OutlinePage、WriteChapterPage、CharactersPage、PacingPage、ForeshadowingPage、FilesPage、AnalysisPage、ToolboxPage、InitProjectPage、AutoGeneratePage、GenerateBookPage、ExportPage、CacheManagerPage、DecisionLogPage、SettingsPage、ChannelConfigPage、CommandConsolePage、CreateTestBookPage
- 保留：ProjectSelectPage、CreateBookPage、ApiPresetsPage、PromptHarnessPage、AICreationPage、TemplateLibraryPage、SystemPage（如有引用再核）

注意：App.jsx 被 main.jsx 引用；useDashboardContext 被多页面用——删页面前先查引用。若共享组件/工具（format.js、charts.js、foreshadowing.js、story.js、files.js）被新视图复用则保留。

## 验证

1. rebuild-and-restart → health 200
2. 新建书 → 进工作台
3. 批量生成 3-5 章
4. 分析三件套渲染
5. 文档编辑读写
6. 导出 JSON
7. 旧路由 `/app` 重定向

## 收尾

- 更新 `docs\AInovel写作系统·完整文档.md` 第五篇 近期改动日志
- 更新 memory（本文件对应 [[ainovel-module-merge]] 或并入既有）
- 写任务日志（主系统 tasks.jsonl + prompt-harness tasks/）
