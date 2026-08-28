# -*- coding: utf-8 -*-
"""T11 Phase C 验证：反馈闭环（bump_usage）+ 成本预算（估算/门禁）——纯函数隔离，不调 LLM。
运行：python verify_phase_c.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" | {detail}" if detail else ""))
    return cond


def main() -> int:
    ok = True

    # ── C1 反馈闭环 ─────────────────────────────────────────────
    from prompt_harness.plot_library import PlotTemplateLibrary

    tmp = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("harness_runs/t11_phase_c/tpl_test.json")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        tmp.unlink()
    lib = PlotTemplateLibrary(path=tmp)

    # 添加一个模板 → usage 默认字段
    t = lib.add({"id": "ptpl_test1", "name": "测试模板", "description": "x"})
    ok &= check("add 带默认 usage", "usage" in t and t["usage"]["use_count"] == 0)

    # bump_usage 无质量（命中/套用）
    lib.bump_usage("ptpl_test1")
    got = lib.get("ptpl_test1")
    ok &= check("bump_usage 自增 use_count", got["usage"]["use_count"] == 1)
    ok &= check("bump_usage 记录 last_used_at", bool(got["usage"]["last_used_at"]))

    # bump_usage 带质量（finalize 回写）—— 两次滚动平均
    lib.bump_usage("ptpl_test1", quality=0.6)
    lib.bump_usage("ptpl_test1", quality=0.8)
    got = lib.get("ptpl_test1")
    ok &= check("finalize 回写 avg_quality 滚动平均", got["usage"]["n_finalized"] == 2
                and abs(got["usage"]["avg_quality"] - 0.7) < 1e-4,
                f"avg={got['usage']['avg_quality']}")

    # 不存在 id → False
    ok &= check("不存在的 id 返回 False", lib.bump_usage("nope") is False)

    # 持久化：重载保留 usage
    lib2 = PlotTemplateLibrary(path=tmp)
    got2 = lib2.get("ptpl_test1")
    ok &= check("usage 持久化", got2 and got2["usage"]["use_count"] == 3)

    # ── C2 成本预算 ─────────────────────────────────────────────
    from prompt_harness import llm_client

    # 价格估算
    u = {"prompt_tokens": 1_000_000, "completion_tokens": 500_000}
    c = llm_client._estimate_cost_usd("qwen3.5-flash", u)
    ok &= check("qwen3.5-flash 价格估算", abs(c - (0.08 + 0.5 * 0.20)) < 1e-4, f"got={c}")
    c2 = llm_client._estimate_cost_usd("deepseek-v4-flash", u)
    ok &= check("deepseek 兜底价格估算", abs(c2 - (0.14 + 0.5 * 0.28)) < 1e-4, f"got={c2}")
    c3 = llm_client._estimate_cost_usd("unknown-model", {"prompt_tokens": 1000})
    ok &= check("未知模型按默认价", abs(c3 - (1000 * 0.14 / 1e6)) < 1e-6, f"got={c3}")
    ok &= check("无 usage → 0", llm_client._estimate_cost_usd("qwen3.5-flash", None) == 0.0)

    # 预算累计：模拟单次调用累计
    _saved_budget = llm_client._DAILY_BUDGET_USD
    _saved_log = dict(llm_client._BUDGET_LOG)
    llm_client._DAILY_BUDGET_USD = 1.0  # 1 USD/天
    llm_client._BUDGET_LOG.clear()
    _today = "2099-01-01"
    r = llm_client._budget_check(_today, 0.6)
    ok &= check("预算内不熔断", r is None, f"got={r}")
    r2 = llm_client._budget_check(_today, 0.6)  # 累计到 1.2 > 1.0
    ok &= check("超限返回熔断提示", isinstance(r2, str) and "超限" in r2, f"got={r2}")

    # 恢复
    llm_client._DAILY_BUDGET_USD = _saved_budget
    llm_client._BUDGET_LOG.clear()
    llm_client._BUDGET_LOG.update(_saved_log)

    # 编译检查
    import py_compile
    for f in ("prompt_harness/llm_client.py", "prompt_harness/plot_library.py", "prompt_harness/ai_creation.py"):
        try:
            py_compile.compile(f, doraise=True)
            ok &= check(f"编译通过 {f}", True)
        except py_compile.PyCompileError as e:
            ok &= check(f"编译失败 {f}", False, str(e))

    print("\n=== " + ("全部通过" if ok else "有失败项") + " ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
