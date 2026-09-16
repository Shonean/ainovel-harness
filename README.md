# AInovel Harness

「书 → 媒体」垂直内容生产线：一键生文产出长篇小说，经改编层编译为引擎无关的 Adaptation Pack，再分发到漫剧等成品出口；内置阶梯提示工程与 PromptOpt 共演化工具链。

[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](requirements.txt)
[![FastAPI](https://img.shields.io/badge/FastAPI-★-009688.svg)](app/dashboard/)
[![React](https://img.shields.io/badge/React-19-61DAFB.svg)](app/dashboard/frontend/)
[![Electron](https://img.shields.io/badge/Electron-33-47848F.svg)](desktop/)

## 功能

### 一键生文（五级阶梯）
- l1→l5 分层生成：基本设定 / 元素 → 弧与章纲 → l4 场景快照 → l5 正文
- 精品与量产双模式（建书时选择、可切换），量产模式支持断点续跑与批量驱动
- 章节评分、污染检测、AI 味防护（确定性门槛 + 审阅修复）

### 改编层（Adaptation Pack）
- l4 场景 → 引擎无关 Pack 的确定性映射：分镜、对白归一、选择点、双结局
- 产物：`story / characters / world / assets / interaction / llm_zones` + pack 元信息与校验报告
- ink 主出口（编译 `.ink.json`）+ inkjs 浏览器调试预览
- 校验器：schema、引用完整性、分支可达性、无死节点

### 资产管线
- 内容寻址资产库（sha256 去重、跨 Pack 复用）
- 媒体驱动：Seedream 生图 / Seedance 视频 / Kokoro 离线 TTS / 云 TTS（统一 manifest；凭据或依赖缺失时如实降级，不阻塞任务）
- `usage_ledger` 成本账本与预算闸

### 漫剧线
- 漫剧工作台六步：分镜 / 资产 / 配音 / 关键镜头 / 字幕 / 合成导出
- ark 控制台（自建前端）：生成 / 理解 / 对话 / 模型 / 精调部署 / 用量账单 / 账号 / 高级
- 成片管理（单集与合辑）、发布导出（封面 / 预告片 / 多语言 / 审核预检 / 发布包）、批量出片
- PyAV 进程内合成：竖屏 1080×1920、字幕烧录、BGM ducking、crossfade

### 3D 剧情游戏（预留）
- ink 骨架 + Godot(.NET) 模板 + GodotInk / Dialogue Manager（本期暂停，工作台隐藏保留）

### 桌面端与前端
- Electron 桌面端（应用自带后端，Windows 优先）
- React 工作台：书架 / 创作 / 改编中心 / 漫剧线全流程 / AI 创作助手
- 一键应用新代码（前端构建 + 后端自重启）

### PromptOpt（提示工程评估）
- 评分器与 canary 护栏、消融验证、漂移监控、争议与审计工具
- 两层共演化训练器（PromptOpt）与域适配接口

## 版本更新

### v0.2.0（2026-09-16）
- **改编层 v0**：Pack schema、确定性映射、对白归一、双结局、ink 编译、校验器与 CLI
- **资产管线**：内容寻址 `asset_store`、`usage_ledger` 账本、`ark_runner`、Seedream / Seedance / Kokoro / 云 TTS 驱动
- **漫剧线**：分镜投影、PyAV 合成、字幕对齐；漫剧工作台六步；成片管理 / 发布导出 / 批量出片
- **ark 控制台**：ark-cli 自建前端（生成 / 理解 / 对话 / 模型 / 精调部署 / 用量账单 / 账号 / 高级），任务队列、回填资产库、记账与预算
- **桌面端**：Electron 形态（替换旧 Tauri 形态），随包支持一键应用新代码
- **前端 v9**：改编中心书墙、情节树三态、漫剧工作台、作品详情 3 Tab、书架与书卡深链
- **PromptOpt**：评分器迭代（封堵事实捏造盲区）、Phase 4 持续护栏、Phase 5 域无关化接口
- **许可证**：全仓改为 MIT

### v0.1.0（2026-08-28）
- 首个开源快照：五级阶梯、AI 味防护、PromptOpt 基础

## 快速开始

### 环境
- Python 3.10+
- Node.js 18+（前端与 Electron 打包）

### 安装与启动（桌面端）
```bash
pip install -r requirements.txt
cp prompt-harness/.env.example prompt-harness/.env   # 填写模型与密钥配置

cd desktop
npm install
npm run dist        # 打包 Windows 桌面端；或直接运行已打包的可执行文件
```

### 开发模式（前端单独构建）
```bash
cd app/dashboard/frontend
npm install
npm run build
```

## 目录结构

```
app/
  dashboard/            # 后端（FastAPI）+ 前端源码（React SPA）
  scripts/              # 创作脚本与数据模块
  agents/ channels/     # 代理与频道配置
desktop/                # Electron 桌面端
prompt-harness/
  prompt_harness/
    adaptation/         # 改编层（Pack / ink / 校验）
    media/              # 资产管线（资产库 / 账本 / 驱动）
    drama/              # 漫剧（分镜投影 / 合成 / 字幕对齐 / 成片发布）
    promptopt/          # PromptOpt 评估与训练
  tests/                # 改编 / 漫剧 / 媒体 / 服务端测试
  third_party/inkjs/    # ink 引擎（vendored）
third_party/ark-cli/    # 方舟 CLI（fork，附 UPSTREAM.md）
docs/                   # 架构 / 提示设计 / 消融实验文档
```

## 第三方与许可

本仓库采用 **[MIT 许可证](LICENSE)**。随包或按需集成的第三方组件保留其原始许可：

| 组件 | 用途 | 许可 |
|---|---|---|
| [ark-cli](https://github.com/volcengine/ark-cli)（fork，`third_party/ark-cli`） | 方舟 Seedream / Seedance 生成 | Apache-2.0 |
| [inkjs](https://github.com/y-lohse/inkjs)（`prompt-harness/third_party/inkjs`） | ink 剧本编译与浏览器预览 | MIT |
| [GodotInk](https://github.com/paulloz/godot-ink)（模板内 vendor） | Godot 内 ink 运行时（C#/.NET） | MIT |
| [Dialogue Manager](https://github.com/nathanhoad/godot_dialogue_manager)（模板内 vendor） | 对话 UI / 条件 / 本地化 | MIT |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | 离线 TTS（按需安装） | Apache-2.0 |
| PyAV / ffmpeg | 漫剧合成（进程内链接） | BSD-3 / LGPL |
