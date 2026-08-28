# Prompt Harness v2.1

独立网文 Prompt 优化工具。通过泛化训练 + LLM-as-Judge 风格评分，自动发现最佳 prompt 结构。

## 核心设计

### 不变层 vs 可变层

```
┌─────────────────────────────────────────────────┐
│ 不变层（训练产出）           │ 可变层（每次使用填入）│
│ - prompt 结构（LLM 自设计）  │ - 题材/世界观       │
│ - 写作规则/约束              │ - 角色信息          │
│ - 输出格式要求               │ - 本次情节方向      │
│ - 自检清单                   │                     │
└─────────────────────────────────────────────────┘
```

**关键**：不变层的具体字段和结构由 LLM 在训练中自己决定，不是人预设。

### 评分体系

| 维度 | 评分方式 | 权重 |
|---|---|---|
| 风格复刻（L5→L1） | LLM-as-judge 对比目标段落 | 35% |
| 泛化能力 | LLM-as-judge 评价未见情节的生成 | 40% |
| 结构质量 | LLM 评估 prompt 清晰/完整/简洁/通用 | 25% |

权重可通过「校准」标签页实时调整，无需重启。

## 快速开始

1. 复制配置：
   ```powershell
   cp .env.example .env
   # 编辑 .env，填入 ARK_API_KEY、ARK_MODEL_PRO、EMBED_API_KEY 等
   # 可选 JUDGE_MODEL（评分模型，默认复用 ARK_MODEL_PRO）
   ```

2. 安装依赖：
   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   cd frontend
   npm install
   ```

3. 准备语料：
   把授权网文文本放到 `corpus/` 目录下，`.txt` 或 `.md` 均可。支持子目录。

4. 从仓库根启动后端（主系统与 prompt-harness 同进程）：
   ```bash
   uvicorn app.dashboard.app:app --host 127.0.0.1 --port 8765
   ```

5. 打开浏览器访问：`http://127.0.0.1:8765/prompt-harness/`（**推荐，主系统入口**）。

## 界面标签页

### 训练

核心工作流。输入目标风格段落，LLM 自动提取题材/基调/视角/关键元素，生成多个 prompt 变体并迭代优化。

**风格管理**：训练前可选择一个风格，风格关联 corpus/ 下的语料文件，用于语义检索参考经验。点击「管理风格」弹出管理器，可创建/编辑/删除风格，勾选关联文件。

**文档分段提取**（v2.1 新增）：对于长篇文档，无需手动粘贴：

1. 先在「📤 上传到语料库」上传文件，或使用已上传的文件
2. 在「📄 文档分段提取」下拉菜单选择文档
3. 点击「📥 提取下一段」——系统自动在 2000–3000 字的自然断点处（场景标记 → 段落空行 → 句子结束 → 精确截断）切分
4. 提取的段落自动填入目标段落框（此时为只读模式）
5. 训练完成后再次提取下一段，阅读位置自动推进
6. 进度条显示当前字数/总字数，读完显示「✅ 文档已读完」
7. 可随时「🔄 重置」回到开头

也可以跳过文档提取，直接粘贴文本或点击「📄 读取文档」选择本地文件（退出分段提取模式）。

### 生成

从已有风格积累生成新的 System Prompt，无需提供目标段落。

1. 选择训练过的风格（至少有一条经验）
2. 填入基本设定（题材、主角、世界观等）
3. 可选填入情节方向用于自动测试
4. 点击「✨ 生成 Prompt」——LLM 从该风格的高分经验中提取模式，合成新 prompt
5. 如果开启自动测试，会对生成结果进行风格+泛化+结构评分
6. 评分不理想会自动改进（最多几轮）

### 验证

人工验证训练好的 prompt 在新设定和情节下的生成效果。

1. 选择一个高分 Prompt（可按风格过滤）
2. 填入可变层设定和本次情节方向
3. 调整生成温度
4. 点击生成，查看输出质量
5. 可展开查看完整 System Prompt 和评分详情

### 经验库

查看、搜索、删除历史所有训练和生成的经验。支持按题材/场景/风格过滤，可按综合分/泛化分/时间排序。

### 蒸馏

按题材/场景聚合高分经验，蒸馏通用写作规则。支持预览模式（dry run）和设置最低分阈值。

### 校准

**人工评估统计**：记录训练结果页的相似度评分，统计系统分 vs 人工分的偏差。偏差 > 0.2 会高亮提示。

**评分权重配置**：实时调整综合分（α×风格 + β×泛化 + γ×结构）及各维度子权重，无需重启。支持恢复默认。

## 使用流程

### 典型工作流

```
上传语料 → 创建风格 → 关联文件 → 训练优化 → 生成 Prompt → 验证 → 校准
```

### 长篇文档工作流（v2.1）

```
上传长文档 → 选择文档 → 提取第1段 → 训练 → 提取第2段 → 训练 → ... → 文档耗尽
```

每次训练一条经验，积累到风格下。积累足够后使用「生成」标签页合成 prompt。

## API 端点参考

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/optimize/run` | 启动优化训练 |
| GET | `/api/optimize/status/{id}` | 查询训练进度 |
| POST | `/api/prompts/generate` | 从风格生成 Prompt |
| GET | `/api/prompts/status/{id}` | 查询生成进度 |
| GET | `/api/experiences` | 列出经验库 |
| GET | `/api/experiences/{id}` | 查看经验详情 |
| DELETE | `/api/experiences/{id}` | 删除经验 |
| GET | `/api/verify/prompts` | 列出可验证的高分 prompt |
| GET | `/api/verify/prompts/{id}` | 查看 prompt 详情 |
| POST | `/api/verify/generate` | 用选定 prompt 生成正文 |
| GET | `/api/styles` | 列出所有风格 |
| POST | `/api/styles` | 创建风格 |
| PUT | `/api/styles/{name}` | 更新风格 |
| DELETE | `/api/styles/{name}` | 删除风格 |
| GET | `/api/styles/{name}/segments` | 获取风格关联语料段落 |
| GET | `/api/corpus` | 语料概览 |
| GET | `/api/corpus/files` | 列出 corpus/ 下文件 |
| POST | `/api/corpus/upload` | 上传文件到 corpus/ |
| POST | `/api/corpus/next-segment` | 提取文档下一段（自动追踪位置） |
| GET | `/api/corpus/reading-position/{file}` | 查询文档阅读位置 |
| POST | `/api/corpus/reset-position/{file}` | 重置文档阅读位置 |
| POST | `/api/distill/run` | 启动蒸馏 |
| GET | `/api/distill/status/{id}` | 查询蒸馏进度 |
| GET | `/api/eval/stats` | 人工评估统计 |
| POST | `/api/eval/record` | 提交人工评分 |
| GET | `/api/scoring/weights` | 获取评分权重 |
| PUT | `/api/scoring/weights` | 更新评分权重 |
| GET | `/api/health` | 健康检查 |

## 目录结构

```
prompt-harness/
├── prompt_harness/         # Python 后端核心
│   ├── server.py           # FastAPI 应用 + 全部端点
│   ├── optimizer.py        # 优化训练主循环
│   ├── scorer.py           # LLM-as-Judge 评分
│   ├── experience_store.py # SQLite + FAISS 经验库
│   ├── corpus_loader.py    # 语料加载 + 文档分段提取
│   ├── prompt_generator.py # 从风格生成 Prompt
│   ├── style_manager.py    # 风格 CRUD
│   ├── harness_builder.py  # prompt 组装与验证生成
│   ├── distiller.py        # 经验蒸馏
│   ├── llm_client.py       # LLM 调用客户端
│   ├── embed_client.py     # Embedding 客户端
│   ├── config.py           # 配置读取
│   └── templates.py        # Prompt 模板
├── frontend/               # React 前端
│   └── src/
│       ├── App.jsx         # 全部 6 个标签页组件
│       ├── api.js          # API 客户端
│       ├── main.jsx        # 入口
│       └── index.css       # 样式
├── corpus/                 # 语料目录（自备，见仓库根 README「公开版差异」）
├── tests/                  # 测试
└── fixed_prompts.json      # 固定 prompt（与 ../config/ 同源）
```

> 📖 **方法论文档见仓库根 [`docs/`](../docs/)** — 五层阶梯架构、充分性消融方法与结论、prompt 设计原则。
