# 测试体系

> **铁则：所有测试必须是全真模式——真实启动后端，真实发请求，真实验证。**
> **不测语法、不测导入、不 mock。能跑通就是能跑通，跑不通就是跑不通。**

> **教训（v5.2 启动事故）**：Phase 3 改 prompt-harness/server.py 时误删了 `app = FastAPI()`，
> 但 Python 语法检查通过、import 也通过（因为 app 不在模块导入路径上），
> 直到用户真实启动才发现崩了。从此规定：**所有后端改动必须过全真冒烟测试**。

## 测试分层

| 层级 | 工具 | 覆盖范围 | 运行频率 |
|------|------|---------|---------|
| **全真冒烟测试** | `test_api_smoke.py` | 真实启动后端 + 每个 API 端点至少一个成功用例 | 每次后端改动后 |
| **静态检查** | quality_check.py | lint + API 契约 + 文档同步 + 语法 | 每次提交前 |
| **单元测试** | pytest（预留） | 核心算法（评分、结构分析、贝叶斯优化） | 算法改动后 |

## 快速使用

### 1. 质量防线（提交前必跑）

```bash
# 四道防线全部检查
python -X utf8 app/tools/quality_check.py

# 单项
python -X utf8 app/tools/quality_check.py lint   # 前端 ESLint
python -X utf8 app/tools/quality_check.py api    # API 契约
python -X utf8 app/tools/quality_check.py docs   # 文档同步
python -X utf8 app/tools/quality_check.py py     # Python 语法
python -X utf8 app/tools/quality_check.py build  # 构建验证
```

前端目录下快捷方式：
```bash
cd app/dashboard/frontend
npm run check    # 等价于 quality_check.py 全部
npm run lint     # 仅 ESLint
npm run build    # lint + 构建
```

### 2. API 全真冒烟测试（后端改动必跑）

```bash
# 完整测试（自动启动后端 → 等就绪 → 测所有端点 → 自动停止）
python -X utf8 tests/test_api_smoke.py

# 指定端口（避免和正在运行的实例冲突）
python -X utf8 tests/test_api_smoke.py --port 8766

# 只测某一组
python -X utf8 tests/test_api_smoke.py --group system
python -X utf8 tests/test_api_smoke.py --group prompt-harness

# 后端已经在跑了，只测请求
python -X utf8 tests/test_api_smoke.py --base-url http://127.0.0.1:8765 --no-start
```

**当前覆盖**（9 个端点）：
- **system 组**（5 个）：health, env-config, logs/summary, logs/files, projects
- **prompt-harness 组**（4 个）：health, styles, experience/stats, corpus

## 添加新测试

### 冒烟测试

编辑 `tests/test_api_smoke.py`，在对应组里加：

```python
@g_xxx.add
def test_xxx(base_url: str) -> TestResult:
    status, body = http_get(base_url, "/api/xxx")
    return check_response(
        "GET /api/xxx", status, body,
        expected_status=200,
        expected_keys=["ok", "data"],  # 可选
    )
```

### 质量检查项

编辑 `app/tools/quality_check.py`，新增 `check_xxx()` 函数，在 `main()` 注册。

## 原则

1. **冒烟测试优先** — 先保证每个接口都能调通，再谈深度
2. **零依赖起步** — 先用标准库，测试多了再上 pytest
3. **谁改谁补测试** — 加了新接口就要补对应的冒烟测试
4. **失败 = 阻断** — error 级别的问题必须修复才能提交
5. **全真测试铁则** — 不测语法/导入/mock，真实启动+真实请求才算数
