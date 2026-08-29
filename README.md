# AInovel Harness

> A layered prompt engine with ablation-based evaluation for long-form fiction generation.
>
> AI 长篇小说创作系统。目标只有一个：**让 AI 稳定写出能连载的长篇——不乱编、不忘事、没有 AI 味。**

[![License](https://img.shields.io/badge/License-NON--COMMERCIAL-orange.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](requirements.txt)
[![FastAPI](https://img.shields.io/badge/FastAPI-✓-009688.svg)](app/dashboard/)
[![React](https://img.shields.io/badge/React-19-61DAFB.svg)](app/dashboard/frontend/)
[![Tauri](https://img.shields.io/badge/Tauri-2-24C8D8.svg)](app/dashboard/frontend/src-tauri/)

## 这是什么

长文本生成失败或达不到预期效果，通常不是模型不够强，而是**信息断层**：上下文里该有的信息没到位，或到位了但层级不对。大纲进、正文出，错了不知道该怪哪一层。大多数 AI 写作工具的 prompt 就是这样一个黑盒。

AInovel Harness 把生成前的上下文拆成**五层信息阶梯**，再用**逐层充分性消融**回答一个平时没人量化的问题：

> 模型到底需要哪一层抽象，才能生成不出错的东西？

答案是可以测量的，而且测出来的结论违反直觉（见下面的消融表）。

系统由两部分组成：

- **主系统**（`app/dashboard/`）：FastAPI + React 19 后端与工作台。书项目管理、对话式初始化、逐弧创作、设定集/大纲管理、审查与导出。
- **Prompt Harness**（`prompt-harness/`）：提示工程引擎。从授权语料中提取情节模板，经模板库桥接，驱动阶梯式正文生成（l1 骨架 → l5 成稿），全程带多维评分与 AI 味防线。

## 核心创新

### 1. 五层信息阶梯（l1 → l5）

长文本生成的上下文按抽象度分五层，生成自上而下逐层落地：

```
l1  全书骨架（logline / 主线）
l2  卷级弧线
l3  章纲与场景序列
l4  场景级节拍（含字面锚点硬约束）
l5  正文成稿
```

l4 的场景节拍带**字面锚点**——写进 prompt 的关键事实在成稿中必须原样出现，这是"不乱编"的机制保障，而不是祈祷。

### 2. 逐层充分性消融（本项目的核心方法论）

固定模型与解码参数，只改变喂给模型的阶梯层数（A=l1、B=l1-l2、C=l1-l3、D=l4 直出），跨三本书跑 fact_recall / plot_sim / composite 评分：

| 档位 | 提供的上下文 | composite |
|------|--------------|-----------|
| A | l1 | ~0.30 |
| B | l1–l2 | ~0.30 |
| C | l1–l3 | ~0.30 |
| **D** | **l4 场景节拍** | **0.67** |

**C−B≈0：l3 之前的层对生成质量没有可测量的增量，l4 才是唯一的有效断点。** 后续 A/B 验证进一步证实：把 l4 信息前移进 l3 只带来 +0.009（噪声级）。

这个发现直接改变了工程路线：l1–l3 的价值被重新定位为**作者确认流**（human-in-the-loop 的审批节点），而非喂给模型的信息层。

消融方法、评分维度与实验细节见 [docs/ABLATION.md](docs/ABLATION.md)。

### 3. 双向阶梯

阶梯既能从原文向上抽取骨架（X4→X3→X2→X1），也能从骨架向下生成正文，还能在任意一层修改。"分析已有小说"和"生成新小说"共用同一套表示。

### 4. AI 味防线（`ai_flavor.py`）

标点/句式硬约束、对白间隙公式、防御性写作禁令——一千多行从上百万字生成实践中磨出来的中文 AI 腔检测与修正规则。这是生成后质量体系的领域级防线，coding harness 里没有任何对应物。

### 5. PromptOpt：prompt 与评分器两层共演化

评分器本身会失真（我们抓到过评分器 bug 让消融结论失真），直接拿它当真值优化 prompt 会被 Goodhart。PromptOpt 的解法是两层共演化：

```
Level 0  人类偏好集（冻结）+ canary 对抗集（冻结）
            ↑ Kendall τ / 校准误差 / canary 不退化（gate）
Level 1  ScorerParams（评分器权重/阈值/词表）  ← 被优化
            ↑ 多维度"均不得降"约束（gate）
Level 2  PromptParams（各 prompt 模块）        ← 被优化
```

用冻结的人类偏好集锚定评分器，用已验证的评分器锚定 prompt，canary 对抗集专门拦截"刷分不刷质"。

## 过程证据：这些结论为什么可信

方法论的价值在于可证伪。我们如实记录这条研究线上的三个**过程事实**——包括失败的：

**① 评分器 bug 曾让结论失真。** 一次评分器把中文引号「」与英文引号 "" 的匹配搞错，fact_recall 分数整体失真，连带消融结论被动摇。事后我们把评分器参数（权重/阈值/词表/审查 prompt）版本化为可审计的 `ScorerParams`，从此任何分数都可回溯到当时的评分器版本。

**② 一个被证伪的优化方案。** 我们怀疑"l4 才有效"可能只是信息位置问题，于是把 l4 场景信息整体前移进 l3 重跑消融——只带来 +0.005 到 +0.012（均值 +0.009，噪声级）。**方案放弃。** 结论没有迎合最初的直觉。

**③ canary 对抗集检出 9 处放水。** 我们手写了 15 条故意写坏的段落当对抗样本，结果当时的评分器放过了 9 条。这确立了本项目最硬的一条纪律：

> **先修好评分器，再谈优化 prompt。** 评分工具自身失真时，一切优化都是给噪声拟合。

这套"评估纪律先于优化"的实证方法论，才是本项目的真正产品——小说工具只是它的第一个验证场景。

## 公开版差异（重要）

本仓为**公开版**，以下内容不在仓库中，代码做了对应容错：

| 内容 | 位置 | 说明 |
|------|------|------|
| 语料库 | `prompt-harness/corpus/` | 版权原因。请自备**有权使用**的文本，放入该目录即可 |
| 统一数据库 | `data/ainovel.db` | 首次运行自动创建 |
| 类型模板 / 技能库 | `config/genres/` `config/skills/` `config/templates/` | 长期调优资产，暂不公开 |
| 评分器参数 | `config/scorer_params.json` `prompt-harness/data/` | 同上；PromptOpt 框架代码已包含 |
| 实验产出 | `prompt-harness/harness_runs/` | 含语料衍生文本 |

## 快速开始（开发者）

要求：Python 3.10+，Node.js 18+（前端构建时需要）。

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 LLM API
cp prompt-harness/.env.example prompt-harness/.env
#    编辑填入 ARK_API_KEY、ARK_MODEL_PRO 等（OpenAI 兼容端点亦可）

# 3. 启动后端（主系统与 prompt-harness 同进程，端口 8765）
uvicorn app.dashboard.app:app --host 127.0.0.1 --port 8765

# 4. 浏览器打开 http://127.0.0.1:8765
```

前端开发：`app/dashboard/frontend/`（React 19 + Vite 6），`npm install && npm run build` 后由后端托管静态资源。

测试：

```bash
pytest tests/ prompt-harness/tests/
```

## 目录结构

```
ainovel-harness/
├── app/
│   ├── dashboard/          # 主系统（FastAPI）：routes/ services/ agents/ frontend/
│   └── scripts/            # 业务脚本与数据模块
├── prompt-harness/         # 提示工程引擎（挂载于主系统）
│   ├── prompt_harness/     # 后端包：ladder / bridge / derive / ai_flavor / promptopt / …
│   └── tests/
├── config/
│   └── fixed_prompts.json  # 固定 prompt（本仓唯一携带的调优资产）
├── docs/                   # 架构 / 消融 / prompt 设计文档
└── tests/                  # 端到端冒烟
```

## 适用范围

阶梯抽象适用于所有"不知道模型需要多细粒度才不出错"的长结构化生成，不只小说：剧本、技术文档、教材、游戏分支叙事都可以按同一套 l1→l5 思路换掉叶子实现。这套方法论本身——用消融实验量化 prompt 管道里每一层的贡献——同样适用于任何复杂的 LLM 应用。

## License

本仓库采用 **[非商用许可（NON-COMMERCIAL）](LICENSE)**。

- ✅ 允许：个人学习、研究、非商业使用、非商业二次开发与再分发
- ⛔ 严禁：任何形式的商业使用、以 SaaS / 云服务收费、集成进商业软件

商业用途需另行联系作者签署商用授权协议。详情见 [LICENSE](LICENSE)。
