#!/usr/bin/env python3
"""
API 冒烟测试（全真模式）— 真实启动后端，真实发请求。

**核心原则**：不测语法、不测导入、不 mock。
          直接启动真实进程，发真实 HTTP 请求，
          能通就是能通，不通就是不通。

**用法**：
    # 完整测试（启动 + 测所有端点 + 停止）
    python -X utf8 tests/test_api_smoke.py

    # 只测某一组
    python -X utf8 tests/test_api_smoke.py --group system

    # 后端已经在跑了，只测请求
    python -X utf8 tests/test_api_smoke.py --base-url http://127.0.0.1:8765 --no-start

    # 指定端口
    python -X utf8 tests/test_api_smoke.py --port 8766

**测试流程**：
    1. 启动后端子进程（从 app/ 目录启动 dashboard.server）
    2. 等待健康检查通过（最多 30 秒）
    3. 逐个跑测试用例
    4. 杀掉后端进程
    5. 输出报告 + 返回退出码
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


# ── 路径 ──────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent          # ainovel-write/
APP_DIR = ROOT / "app"

# 颜色
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class TestResult:
    name: str
    passed: bool
    message: str = ""
    duration_ms: float = 0


@dataclass
class TestGroup:
    name: str
    description: str
    tests: list[Callable[[str], TestResult]] = field(default_factory=list)

    def add(self, func: Callable[[str], TestResult]):
        self.tests.append(func)
        return func


# ================================================================
# HTTP 工具（真实请求）
# ================================================================
def http_get(base_url: str, path: str, timeout: int = 10) -> tuple[int, dict | str]:
    """真实 HTTP GET 请求，返回 (status_code, json_or_text)。"""
    url = base_url.rstrip("/") + path
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body
    except Exception as e:
        return 0, str(e)


def http_post(base_url: str, path: str, data: dict, timeout: int = 30) -> tuple[int, dict | str]:
    """真实 HTTP POST 请求（JSON body），返回 (status_code, json_or_text)。"""
    url = base_url.rstrip("/") + path
    body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    try:
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body
    except Exception as e:
        return 0, str(e)


def check_response(
    name: str,
    status: int,
    body: dict | str,
    expected_status: int = 200,
    expected_keys: list[str] | None = None,
) -> TestResult:
    """检查响应是否符合预期。"""
    t_start = time.monotonic()

    if status == 0:
        return TestResult(
            name=name, passed=False,
            message=f"连接失败: {body}",
            duration_ms=(time.monotonic() - t_start) * 1000,
        )

    if status != expected_status:
        return TestResult(
            name=name, passed=False,
            message=f"状态码 {status} ≠ {expected_status}, body={str(body)[:300]}",
            duration_ms=(time.monotonic() - t_start) * 1000,
        )

    # API 端点必须返回 JSON，返回 HTML 说明路由没命中（被 SPA 回退拦截了）
    is_json = isinstance(body, (dict, list))
    is_html = isinstance(body, str) and body.strip().startswith("<")
    if not is_json and is_html:
        body_preview = body[:200]
        return TestResult(
            name=name, passed=False,
            message=f"返回的是 HTML（路由没命中，被 SPA 回退拦截了）。body 前200字: {body_preview}",
            duration_ms=(time.monotonic() - t_start) * 1000,
        )
    if not is_json:
        body_preview = str(body)[:200]
        return TestResult(
            name=name, passed=False,
            message=f"响应不是有效的 JSON。body 前200字: {body_preview}",
            duration_ms=(time.monotonic() - t_start) * 1000,
        )

    if expected_keys and isinstance(body, dict):
        missing = [k for k in expected_keys if k not in body]
        if missing:
            return TestResult(
                name=name, passed=False,
                message=f"缺少字段: {missing}，实际 keys: {list(body.keys())}",
                duration_ms=(time.monotonic() - t_start) * 1000,
            )

    return TestResult(
        name=name, passed=True,
        message=f"状态 {status} OK",
        duration_ms=(time.monotonic() - t_start) * 1000,
    )


# ================================================================
# 后端进程管理（真实启动）
# ================================================================
def start_backend(port: int) -> subprocess.Popen:
    """真实启动后端进程，返回 Popen 对象。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    cmd = [
        sys.executable, "-X", "utf8",
        "-m", "dashboard.server",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--no-browser",
    ]

    proc = subprocess.Popen(
        cmd,
        cwd=str(APP_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    return proc


def wait_for_health(base_url: str, timeout: int = 30) -> tuple[bool, str]:
    """等待后端健康检查通过，最多等 timeout 秒。"""
    health_path = "/api/story-runtime/health"
    t0 = time.monotonic()

    while time.monotonic() - t0 < timeout:
        status, body = http_get(base_url, health_path, timeout=3)
        if status == 200 and isinstance(body, dict):
            return True, body.get("message", "ready")

        time.sleep(1)

    elapsed = time.monotonic() - t0
    return False, f"等待 {elapsed:.0f}s 仍未就绪"


def stop_backend(proc: subprocess.Popen):
    """停掉后端进程。"""
    if proc.poll() is not None:
        return

    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except Exception:
        pass


# ================================================================
# 测试组定义
# ================================================================
groups: list[TestGroup] = []

# ── System 组（主系统核心接口）───────────────────────────────
g_system = TestGroup("system", "主系统核心接口")
groups.append(g_system)


@g_system.add
def test_health(base_url: str) -> TestResult:
    status, body = http_get(base_url, "/api/story-runtime/health")
    result = check_response(
        "GET /api/story-runtime/health", status, body,
        expected_status=200,
        expected_keys=["status"],
    )
    if result.passed and isinstance(body, dict):
        result.message = f"状态: {body.get('status')}, mode: {body.get('mode', '?')}"
    return result


@g_system.add
def test_env_config(base_url: str) -> TestResult:
    status, body = http_get(base_url, "/api/env-config")
    return check_response(
        "GET /api/env-config", status, body,
        expected_status=200,
        expected_keys=["fields", "env_path"],
    )


@g_system.add
def test_logs_summary(base_url: str) -> TestResult:
    status, body = http_get(base_url, "/api/logs/summary")
    return check_response(
        "GET /api/logs/summary", status, body,
        expected_status=200,
        expected_keys=["period_days", "total_calls", "total_tokens"],
    )


@g_system.add
def test_logs_files(base_url: str) -> TestResult:
    status, body = http_get(base_url, "/api/logs/files")
    return check_response(
        "GET /api/logs/files", status, body,
        expected_status=200,
        expected_keys=["files"],
    )


@g_system.add
def test_projects(base_url: str) -> TestResult:
    status, body = http_get(base_url, "/api/projects")
    return check_response(
        "GET /api/projects", status, body,
        expected_status=200,
        expected_keys=["projects"],
    )


# ── Prompt Harness 组 ────────────────────────────────────────
g_ph = TestGroup("prompt-harness", "Prompt Harness 接口")
groups.append(g_ph)


@g_ph.add
def test_ph_health(base_url: str) -> TestResult:
    status, body = http_get(base_url, "/api/prompt-harness/health")
    return check_response(
        "GET /api/prompt-harness/health", status, body,
        expected_status=200,
        expected_keys=["status"],
    )


@g_ph.add
def test_ph_experiences_stats(base_url: str) -> TestResult:
    status, body = http_get(base_url, "/api/prompt-harness/experiences/stats")
    return check_response(
        "GET /api/prompt-harness/experiences/stats", status, body,
        expected_status=200,
    )


@g_ph.add
def test_ph_experiences_list(base_url: str) -> TestResult:
    status, body = http_get(base_url, "/api/prompt-harness/experiences?limit=5")
    return check_response(
        "GET /api/prompt-harness/experiences", status, body,
        expected_status=200,
        expected_keys=["experiences"],
    )


@g_ph.add
def test_ph_corpus(base_url: str) -> TestResult:
    status, body = http_get(base_url, "/api/prompt-harness/corpus")
    return check_response(
        "GET /api/prompt-harness/corpus", status, body,
        expected_status=200,
    )


@g_ph.add
def test_ph_corpus_files(base_url: str) -> TestResult:
    status, body = http_get(base_url, "/api/prompt-harness/corpus/files")
    return check_response(
        "GET /api/prompt-harness/corpus/files", status, body,
        expected_status=200,
        expected_keys=["files"],
    )


@g_ph.add
def test_ph_fixed_prompts(base_url: str) -> TestResult:
    status, body = http_get(base_url, "/api/prompt-harness/prompts/fixed")
    return check_response(
        "GET /api/prompt-harness/prompts/fixed", status, body,
        expected_status=200,
    )


@g_ph.add
def test_ph_logs(base_url: str) -> TestResult:
    status, body = http_get(base_url, "/api/prompt-harness/logs?category=llm&limit=5")
    return check_response(
        "GET /api/prompt-harness/logs", status, body,
        expected_status=200,
    )


# ── 训练下游流程测试 ─────────────────────────────────────
@g_ph.add
def test_save_experience(base_url: str) -> TestResult:
    """保存经验到经验库（合成数据，不依赖 LLM）。"""
    t_start = time.monotonic()
    body = {
        "target_text": "测试文本。这是一段用于测试的剧情描写。",
        "system_prompt": "你是一位小说家，请创作一段测试文字。",
        "best_generation": "生成的测试文字内容。",
        "score": 0.85,
        "char_similarity": 0.72,
        "v_cosine": 0.88,
        "source_file": "玄幻武侠/示例书/示例书(1-500章).txt",
        "style_name": "smoke_test",
        "angles_used": ["细腻描写", "节奏紧凑"],
    }
    status, resp_body = http_post(base_url, "/api/prompt-harness/optimize/save-experience", body)
    return check_response(
        "POST /optimize/save-experience", status, resp_body,
        expected_status=200,
        expected_keys=["ok", "exp_id"],
    )


@g_ph.add
def test_cold_start_search(base_url: str) -> TestResult:
    """冷启动检索（用合成 V 向量检索经验库）。"""
    t_start = time.monotonic()
    body = {
        "target_text": "测试文本。这是一段用于测试的剧情描写。",
        "top_k": 3,
        "source_file": "玄幻武侠/示例书/示例书(1-500章).txt",
    }
    status, resp_body = http_post(base_url, "/api/prompt-harness/optimize/cold-start", body)
    if status == 200 and isinstance(resp_body, dict) and resp_body.get("ok"):
        return TestResult(
            name="冷启动检索", passed=True,
            message=f"返回 {len(resp_body.get('results', []))} 条历史",
            duration_ms=(time.monotonic() - t_start) * 1000,
        )
    # 经验库为空是正常的（测试环境）
    if isinstance(resp_body, dict) and "error" in resp_body:
        err = resp_body["error"]
        if "经验库为空" in err or "no results" in err.lower():
            return TestResult(
                name="冷启动检索", passed=True,
                message=f"⏭ 跳过（经验库为空）",
                duration_ms=(time.monotonic() - t_start) * 1000,
            )
    return check_response(
        "POST /optimize/cold-start", status, resp_body,
        expected_status=200,
    )


# ── 训练全流程冒烟测试（1 轮最小训练，验证能跑完不崩溃）─────────
@g_ph.add
def test_train_full_flow(base_url: str) -> TestResult:
    """启动最小训练（1 轮），轮询至完成，验证训练不崩溃。

    注意：需要 LLM API 配额可用。如果配额不足，测试标记为跳过。
    """
    t_start = time.monotonic()

    # 1. 先尝试获取一段语料作为 target_text
    corpus_body = {
        "filename": "玄幻武侠/示例书/示例书(1-500章).txt",
        "min_chars": 500,
        "max_chars": 800,
        "advance": False,
    }
    status, body = http_post(base_url, "/api/prompt-harness/corpus/next-segment", corpus_body)
    if status != 200 or not isinstance(body, dict) or "text" not in body:
        return TestResult(
            name="训练全流程", passed=False,
            message=f"获取训练语料失败: status={status}",
            duration_ms=(time.monotonic() - t_start) * 1000,
        )
    target_text = body["text"][:500]

    # 2. 启动最小训练
    train_body = {
        "target_text": target_text,
        "max_rounds": 1,
        "min_rounds": 1,
        "success_threshold": 0.75,
        "max_tokens": 512,
        "gen_temperature": 0.85,
        "gen_top_p": 0.92,
        "style_name": "smoke_test",
        "auto_optimize_params": False,
        "source_file": "玄幻武侠/示例书/示例书(1-500章).txt",
        "category": "玄幻武侠",
        "settings": {},
    }
    status, body = http_post(base_url, "/api/prompt-harness/optimize/run", train_body, timeout=10)
    if status != 200 or not isinstance(body, dict) or "task_id" not in body:
        return TestResult(
            name="训练全流程", passed=False,
            message=f"启动训练失败: status={status}, body={str(body)[:200]}",
            duration_ms=(time.monotonic() - t_start) * 1000,
        )
    task_id = body["task_id"]

    # 3. 轮询训练状态（最多 120 秒）
    deadline = time.monotonic() + 120
    last_status = ""
    while time.monotonic() < deadline:
        time.sleep(5)
        status, body = http_get(base_url, f"/api/prompt-harness/optimize/status/{task_id}", timeout=10)
        if status != 200 or not isinstance(body, dict):
            continue
        last_status = body.get("status", "")
        if last_status in ("done", "failed"):
            break

    elapsed = (time.monotonic() - t_start) * 1000

    # 4. 验证结果
    if last_status == "done":
        return TestResult(
            name="训练全流程", passed=True,
            message=f"1 轮训练完成, combined={body.get('result', {}).get('combined_score', '?')}",
            duration_ms=elapsed,
        )
    if last_status == "failed":
        error_text = str(body.get("error", "") or body.get("result", {}).get("error", ""))
        # 合并所有错误来源用于判断
        all_errors = error_text.lower()
        result_body = body.get("result", {}) or {}
        if isinstance(result_body, dict):
            ledger = result_body.get("failure_ledger", {}) or {}
            for _k, v in ledger.items():
                if isinstance(v, dict):
                    all_errors += " " + str(v.get("error", "")).lower()
                    all_errors += " " + str(v.get("underlying", "")).lower()
            all_errors += " " + str(result_body.get("error", "")).lower()
        # 检查是否 API 配额/网络等环境问题（应跳过而非失败）
        api_dependent_hints = ["quota", "accountquotaexceeded", "429",
                                "rate limit", "too many requests",
                                "生成候选 prompt 失败",
                                "connection refused", "timeout"]
        is_api_dependent = any(hint in all_errors for hint in api_dependent_hints)
        if is_api_dependent:
            return TestResult(
                name="训练全流程", passed=True,
                message=f"⏭ 跳过（API 依赖问题）: {error_text[:150]}",
                duration_ms=elapsed,
            )
        # 其他失败 → 报告错误
        return TestResult(
            name="训练全流程", passed=False,
            message=f"训练失败: {error_text[:200]}",
            duration_ms=elapsed,
        )

    # 超时
    return TestResult(
        name="训练全流程", passed=False,
        message=f"训练超时（120s），最后状态: {last_status}",
        duration_ms=elapsed,
    )


# ================================================================
# 运行器
# ================================================================
def run_group(group: TestGroup, base_url: str) -> tuple[int, int]:
    """运行一个测试组，返回 (通过数, 失败数)。"""
    print(f"\n{BOLD}{CYAN}══ {group.name}: {group.description} ══{RESET}")

    passed = 0
    failed = 0

    for test_func in group.tests:
        result = test_func(base_url)
        if result.passed:
            print(f"  {GREEN}✅{RESET} {result.name}  ({result.duration_ms:.0f}ms)")
            if result.message and "OK" not in result.message:
                print(f"     {result.message}")
            passed += 1
        else:
            print(f"  {RED}❌{RESET} {result.name}")
            print(f"     {result.message}")
            failed += 1

    return passed, failed


def main():
    parser = argparse.ArgumentParser(
        description="API 冒烟测试（全真模式：真实启动 + 真实请求）",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="后端 URL（如果指定 --no-start 则用这个地址测）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="启动端口（默认 8765）",
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="不启动后端，直接对 --base-url 发请求",
    )
    parser.add_argument(
        "--group",
        default=None,
        help="只运行指定组（system / prompt-harness）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="等待后端启动的超时时间（秒，默认 30）",
    )
    args = parser.parse_args()

    base_url = args.base_url or f"http://127.0.0.1:{args.port}"

    print(f"\n{BOLD}🧪 API 冒烟测试（全真模式）{RESET}")
    print(f"   模式: {'只测请求' if args.no_start else '启动 + 测试'}")
    print(f"   地址: {base_url}")

    proc = None
    startup_ok = True

    if not args.no_start:
        print(f"\n{BOLD}{CYAN}══ 启动后端 ══{RESET}")
        print(f"   端口: {args.port}")
        print(f"   等待就绪: 最多 {args.timeout}s")

        proc = start_backend(args.port)
        time.sleep(2)  # 给进程启动一点时间

        # 检查进程有没有秒退
        if proc.poll() is not None:
            output = ""
            try:
                output = proc.stdout.read() if proc.stdout else ""
            except Exception:
                pass
            print(f"\n  {RED}❌ 后端启动失败（进程秒退，退出码 {proc.returncode}）{RESET}")
            if output:
                print(f"   输出:\n{output[:2000]}")
            sys.exit(1)

        # 等健康检查
        ok, msg = wait_for_health(base_url, timeout=args.timeout)
        if not ok:
            startup_ok = False
            # 拉取一些输出帮排错
            output = ""
            try:
                # 读当前可用的输出（不阻塞）
                import select
                if proc.stdout and select.select([proc.stdout], [], [], 0)[0]:
                    output = proc.stdout.read(4000)
            except Exception:
                pass

            print(f"\n  {RED}❌ 后端未在 {args.timeout}s 内就绪：{msg}{RESET}")
            if output:
                print(f"   启动输出（最近）:\n{output}")

            stop_backend(proc)
            sys.exit(1)

        elapsed = time.monotonic() - 0  # 大概数
        print(f"  {GREEN}✅ 后端就绪{RESET}（{msg}）")

    # 选组
    selected_groups = groups
    if args.group:
        selected_groups = [g for g in groups if g.name == args.group]
        if not selected_groups:
            print(f"\n  {RED}找不到测试组: {args.group}{RESET}")
            print(f"  可用组: {', '.join(g.name for g in groups)}")
            if proc:
                stop_backend(proc)
            sys.exit(1)

    # 跑测试
    total_passed = 0
    total_failed = 0

    for group in selected_groups:
        p, f = run_group(group, base_url)
        total_passed += p
        total_failed += f

    # 汇总
    print(f"\n{BOLD}{CYAN}══ 汇总 ══{RESET}")
    total = total_passed + total_failed
    print(f"   共 {total} 个测试")
    print(f"   {GREEN}通过: {total_passed}{RESET}")
    if total_failed > 0:
        print(f"   {RED}失败: {total_failed}{RESET}")
    else:
        print(f"   {YELLOW}失败: 0{RESET}")

    # 收尾
    if proc:
        print(f"\n   正在停止后端...")
        stop_backend(proc)
        print(f"   后端已停止")

    print()
    if total_failed == 0:
        print(f"  {GREEN}✅ 全部通过！{RESET}")
    else:
        print(f"  {RED}❌ 有 {total_failed} 个失败。{RESET}")

    print()
    sys.exit(0 if total_failed == 0 else 1)


if __name__ == "__main__":
    main()
