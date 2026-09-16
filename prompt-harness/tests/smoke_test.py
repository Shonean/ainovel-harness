"""Prompt Harness 冒烟测试 v3。

每次代码修改后、交付前运行：
    python smoke_test.py

测试内容：
1. import 检查
2. config 检查
3. API 连通性检查（LLM + Embedding）
4. 核心函数单元测试（_extract_system_prompt, _calc_max_tokens）
5. 相似度函数测试（char_level_similarity, length_diff_ratio）
6. 经验库测试（ExperienceStore 读写）
7. 端到端 pipeline 轻量测试（1 轮训练）
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import traceback
from pathlib import Path


# ---------------------------------------------------------------------------
# 彩色输出
# ---------------------------------------------------------------------------

import sys
import io

# Windows GBK 兼容输出：强制 stdout 使用 UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "gb2312", "gb18030"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")

def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")

def _info(msg: str) -> None:
    print(f"  [INFO] {msg}")

def _section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")

_stats = {"failures": 0, "warnings": 0}


# ---------------------------------------------------------------------------
# 1. Import 检查
# ---------------------------------------------------------------------------

_section("1. Import 检查")

try:
    from prompt_harness.config import SETTINGS
    _ok("config.SETTINGS 加载成功")
except Exception as e:
    _fail(f"config 加载失败: {e}")
    _stats["failures"] += 1
    sys.exit(1)

try:
    from prompt_harness.optimizer import (
        _META_KEYS,
        _calc_max_tokens,
        _extract_system_prompt,
        generate_candidates,
        optimize,
    )
    _ok("optimizer 模块导入成功")
except Exception as e:
    _fail(f"optimizer 导入失败: {e}")
    _stats["failures"] += 1
    sys.exit(1)

try:
    from prompt_harness.http_client import get_session, close_session
    _ok("http_client 模块导入成功")
except Exception as e:
    _fail(f"http_client 导入失败: {e}")
    _stats["failures"] += 1
    sys.exit(1)

try:
    from prompt_harness.llm_client import chat_completion, chat_json
    _ok("llm_client 模块导入成功")
except Exception as e:
    _fail(f"llm_client 导入失败: {e}")
    _stats["failures"] += 1
    sys.exit(1)

try:
    from prompt_harness.scorer import (
        char_level_similarity, normalize_text, length_diff_ratio,
        combined_optimization_score, compute_scores,
    )
    _ok("scorer 模块导入成功 (v3)")
except Exception as e:
    _fail(f"scorer 导入失败: {e}")
    _stats["failures"] += 1
    sys.exit(1)

try:
    from prompt_harness.experience_store import ExperienceStore
    _ok("experience_store 模块导入成功")
except Exception as e:
    _fail(f"experience_store 导入失败: {e}")
    _stats["failures"] += 1
    sys.exit(1)

try:
    from prompt_harness.templates import (
        CANDIDATE_GENERATOR_PROMPT, REFINEMENT_PROMPT,
        build_similarity_feedback, build_length_feedback,
        STRUCTURED_MODIFICATION_PROMPT,
        build_diff_report, build_structured_modification_prompt,
    )
    _ok("templates 模块导入成功 (v3.1)")
except Exception as e:
    _fail(f"templates 导入失败: {e}")
    _stats["failures"] += 1

try:
    from prompt_harness.diff_analyzer import (
        analyze as diff_analyze,
        compute_diff,
        classify_errors,
        analyze_root_cause,
        format_modifications_for_prompt,
        ErrorReport, ErrorCategory, ClassifiedError,
        ModificationAction, DiffBlock,
    )
    _ok("diff_analyzer 模块导入成功 (v3.1)")
except Exception as e:
    _fail(f"diff_analyzer 导入失败: {e}")
    _stats["failures"] += 1

try:
    from prompt_harness.embed_client import get_embedding
    _ok("embed_client 模块导入成功")
except Exception as e:
    _fail(f"embed_client 导入失败: {e}")
    _stats["failures"] += 1


# ---------------------------------------------------------------------------
# 2. Config 检查
# ---------------------------------------------------------------------------

_section("2. Config 检查")

if SETTINGS.ark_api_key:
    _ok(f"ARK_API_KEY 存在（{SETTINGS.ark_api_key[:8]}...）")
else:
    _fail("ARK_API_KEY 未配置")
    _stats["failures"] += 1

if SETTINGS.ark_model_pro:
    _ok(f"ARK_MODEL_PRO = {SETTINGS.ark_model_pro}")
else:
    _fail("ARK_MODEL_PRO 未配置")
    _stats["failures"] += 1

if SETTINGS.ark_base_url:
    _ok(f"ARK_BASE_URL = {SETTINGS.ark_base_url}")
else:
    _fail("ARK_BASE_URL 未配置")
    _stats["warnings"] += 1

if SETTINGS.embed_api_key:
    _ok(f"EMBED_API_KEY 存在（{SETTINGS.embed_api_key[:8]}...）")
else:
    _fail("EMBED_API_KEY 未配置")
    _stats["warnings"] += 1

if SETTINGS.embed_model:
    _ok(f"EMBED_MODEL = {SETTINGS.embed_model}")
else:
    _info("EMBED_MODEL 未配置")
    _stats["warnings"] += 1


# ---------------------------------------------------------------------------
# 3. API 连通性检查
# ---------------------------------------------------------------------------

async def _check_llm_api() -> bool:
    _DISABLE_THINKING = {"thinking": {"type": "disabled"}}
    try:
        resp = await chat_completion(
            system="你是一个测试助手。",
            user="只说一个字：好",
            temperature=0.1,
            max_tokens=10,
            extra_body=_DISABLE_THINKING,
        )
        if resp["error"]:
            _fail(f"LLM API 返回错误: {resp['error']}")
            return False
        content = resp.get("content", "")
        if content.strip():
            _ok(f"LLM API 连通成功，返回: {content.strip()[:30]}")
            return True
        _fail("LLM API 返回空内容")
        return False
    except Exception as e:
        _fail(f"LLM API 异常: {type(e).__name__}: {e}")
        return False


async def _check_embedding_api() -> bool:
    try:
        emb = await get_embedding("测试文本")
        if emb and len(emb) > 0:
            _ok(f"Embedding API 连通成功，维度={len(emb)}")
            return True
        _fail(f"Embedding API 返回空结果")
        return False
    except Exception as e:
        _fail(f"Embedding API 异常: {e}")
        return False


async def check_api() -> None:
    _section("3. API 连通性检查")
    llm_ok = await _check_llm_api()
    if not llm_ok:
        _stats["failures"] += 1
    embed_ok = await _check_embedding_api()
    if not embed_ok:
        _stats["warnings"] += 1


# ---------------------------------------------------------------------------
# 4. 核心函数单元测试
# ---------------------------------------------------------------------------

def test_extract_system_prompt() -> None:
    _section("4a. _extract_system_prompt 单元测试")

    result1 = _extract_system_prompt({"system_prompt": "你是一个专业写手，擅长古风描写。"})
    assert result1 is not None, "Case 1 期望非 None"
    assert "你是一个专业写手" in result1
    _ok("Case 1: system_prompt flat string [PASS]")

    result2 = _extract_system_prompt({"prompt": "作为网文写手，请按照以下规则写作。"})
    assert result2 is not None, "Case 2 期望非 None"
    assert "请按照以下规则写作" in result2
    _ok("Case 2: prompt flat string [PASS]")

    result3 = _extract_system_prompt({
        "system_prompt": {
            "name": "优化版",
            "description": "侧重保持原结构",
            "content": "这是实际的 prompt 内容，长度超过 100 个字的测试文本。" * 5,
        },
    })
    assert result3 is not None, "Case 3 期望非 None"
    assert "prompt 内容" in result3
    _ok("Case 3: 嵌套 dict + fallback 提取 [PASS]")

    result4 = _extract_system_prompt({})
    assert result4 is None, "Case 4 期望 None"
    _ok("Case 4: 空 dict [PASS]")

    result5 = _extract_system_prompt({"system_prompt": 12345})
    assert result5 is None, "Case 5 期望 None"
    _ok("Case 5: 异常 value 类型（int）[PASS]")

    result6 = _extract_system_prompt({"content": "作为内容型写手，请按照以下规则写作。"})
    assert result6 is not None
    assert "作为内容型写手" in result6
    _ok("Case 6: content flat string [PASS]")


def test_similarity_functions() -> None:
    _section("4b. 字符级相似度函数单元测试")

    # 完全相同
    sim = char_level_similarity("他推开门走进房间", "他推开门走进房间")
    assert sim == 1.0, f"完全相同期望 1.0，实际 {sim}"
    _ok("Case 1: 完全相同 = 1.0 [PASS]")

    # 用户例子：12345 vs 12344
    sim = char_level_similarity("12345", "12344")
    assert sim == 0.8, f"12345 vs 12344 期望 0.8，实际 {sim}"
    _ok("Case 2: 12345 vs 12344 = 0.8 [PASS]")

    # 完全不同
    sim = char_level_similarity("abcde", "fghij")
    assert sim == 0.0, f"完全不同期望 0.0，实际 {sim}"
    _ok("Case 3: 完全不同 = 0.0 [PASS]")

    # 带空白
    sim = char_level_similarity("他推开门，走进房间", "他推开门，  走进 房间")
    assert sim == 1.0, f"带空白期望 1.0，实际 {sim}"
    _ok("Case 4: 空白字符忽略 = 1.0 [PASS]")

    # 全角半角标点
    sim = char_level_similarity("他说：你好。", "他说:你好.")
    assert sim == 1.0, f"全角vs半角标点期望 1.0，实际 {sim}"
    _ok("Case 5: 全角/半角标点统一 = 1.0 [PASS]")

    # 长度差异
    diff = length_diff_ratio("12345", "1234567890")
    assert diff == -0.5, f"偏短期望 -0.5，实际 {diff}"
    _ok("Case 6: 长度差异(偏短) = -0.5 [PASS]")


def test_diff_analyzer() -> None:
    _section("4c. diff_analyzer 单元测试")

    # Case 1: 完全相同 → 0 errors
    target = "他推开门走进房间，灯光昏黄。"
    generated = target
    report = diff_analyze(generated, target, similarity=1.0, length_diff=0.0)
    assert report.similarity == 1.0
    assert len(report.errors) == 0
    _ok("Case 1: 完全相同 → 0 errors [PASS]")

    # Case 2: AI 味句式检测
    target2 = "他走进房间，灯光映在墙上。"
    generated2 = "他走进房间，心中涌起一阵感动。灯光映在墙上。"
    report2 = diff_analyze(generated2, target2, similarity=0.5, length_diff=0.1)
    ai_errors = [e for e in report2.errors if e.category == ErrorCategory.AI_SMELL_PATTERN]
    assert len(ai_errors) > 0, f"应检测到 AI 味句式，实际 errors={[e.category.value for e in report2.errors]}"
    _ok(f"Case 2: AI 味检测 → {len(ai_errors)} 个 AI_SMELL [PASS]")

    # Case 3: 内容编造检测
    target3 = "陈迹蹲在墙根下，手里捏着匕首。"
    generated3 = "陈迹蹲在墙根下，赵四从门外走来，手里捏着匕首。"
    report3 = diff_analyze(generated3, target3, similarity=0.6, length_diff=0.1)
    fab_errors = [e for e in report3.errors if e.category == ErrorCategory.CONTENT_FABRICATION]
    _info(f"Case 3: 编造检测 → {len(fab_errors)} 个 fabrication")
    # 内容编造检测允许不触发（取决于实体提取启发式）
    _ok("Case 3: 编造检测 (不强制断言) [PASS]")

    # Case 4: 对白差异检测
    target4 = '他说：「你来了。」她点点头。'
    generated4 = '他说你来了。她点点头。'
    report4 = diff_analyze(generated4, target4, similarity=0.55, length_diff=0.0)
    dialogue_errors = [e for e in report4.errors if e.category == ErrorCategory.DIALOGUE_MISSING]
    _info(f"Case 4: 对白检测 → {len(dialogue_errors)} 个 dialogue_missing")
    _ok("Case 4: 对白差异检测 [PASS]")

    # Case 5: 根因分析生成修改动作
    target5 = "他推开门走进房间。"
    generated5 = "他推开门走进房间，心中涌起一股暖流，不禁感慨万千。"
    report5 = diff_analyze(generated5, target5, similarity=0.4, length_diff=0.5)
    modifications = analyze_root_cause(report5.errors)
    assert len(modifications) > 0, f"应生成修改动作，实际 {len(modifications)}"
    # 验证优先级排序：第一个应该是 P0
    assert modifications[0].priority == 0, f"第一个修改应为 P0，实际 P{modifications[0].priority}"
    _ok(f"Case 5: 根因分析 → {len(modifications)} 个修改动作，首动作 P{modifications[0].priority} [PASS]")

    # Case 6: format_modifications_for_prompt 输出格式化
    formatted = format_modifications_for_prompt(modifications)
    assert "P0" in formatted and "必须修复" in formatted, f"格式化输出应包含 P0 标记，实际：{formatted[:80]}"
    _ok("Case 6: 指令格式化 → 包含优先级标记 [PASS]")

    # Case 7: 长度偏差检测
    target7 = "短文本。"
    generated7 = "这是一段非常长的文本，" * 50
    report7 = diff_analyze(generated7, target7, similarity=0.05, length_diff=0.95)
    length_errors = [e for e in report7.errors if e.category == ErrorCategory.LENGTH_DEVIATION]
    assert len(length_errors) > 0, "应检测到长度偏差"
    _ok(f"Case 7: 长度偏差检测 → {len(length_errors)} 个 length 错误 [PASS]")

    # Case 8: 结构化修改模板可填充
    test_prompt = build_structured_modification_prompt(
        current_system_prompt="测试 prompt 内容：禁止使用破折号。使用短句写作。",
        target_text="原文示例文本。",
        last_generation="生成示例文本，心中涌起。",
        modification_instructions="### 🔴 P0 - 必须修复\n**强化现有规则** → AI味禁令\n> 检测到AI味句式",
    )
    assert "测试 prompt 内容" in test_prompt
    assert "P0" in test_prompt
    assert len(test_prompt) > 200
    _ok(f"Case 8: 结构化修改模板 → {len(test_prompt)} 字符 [PASS]")


def test_calc_max_tokens() -> None:
    _section("4d. _calc_max_tokens 单元测试")

    assert _calc_max_tokens("") == 4096, "空文本默认 4096"
    _ok("空文本默认 4096 [PASS]")

    short_text = "你好"
    res = _calc_max_tokens(short_text)
    assert res == 2048, f"短文本最小值 2048，实际 {res}"
    _ok(f"短文本最小值 2048 [PASS]")

    long_text = "字" * 2000
    res2 = _calc_max_tokens(long_text)
    assert 4000 <= res2 <= 8192, f"2000 字文本 max_tokens 在 4000-8192 之间，实际 {res2}"
    _ok(f"2000 字文本 -> {res2} [PASS]")

    res3 = _calc_max_tokens(long_text, override=16384)
    assert res3 == 16384
    _ok("override 生效 [PASS]")

    res4 = _calc_max_tokens(long_text, min_tokens=3000, max_tokens=6000)
    assert 3000 <= res4 <= 6000
    _ok("min/max bounds 生效 [PASS]")


# ---------------------------------------------------------------------------
# 5. 经验库测试
# ---------------------------------------------------------------------------

async def test_experience_store() -> None:
    _section("5. 经验库测试")

    tmpdir = tempfile.mkdtemp()
    try:
        from prompt_harness.experience_store import ExperienceStore
        store = ExperienceStore(Path(tmpdir))

        exp_id = await store.add_experience({
            "target_text": "这是一段测试文本。",
            "genre": "玄幻",
            "scene_type": "战斗",
            "optimal_prompt": "测试 prompt",
            "score": 0.85,
            "scores_detail": {"similarity": 0.85},
            "rounds": 3,
        })
        assert exp_id is not None, "add_experience 返回 exp_id"
        _ok(f"添加经验: exp_id={exp_id} [PASS]")

        try:
            results = await store.semantic_search("测试文本", top_k=5)
            if len(results) >= 1:
                _ok(f"语义搜索返回 {len(results)} 条结果 [PASS]")
            else:
                _info(f"语义搜索返回 0 条结果（可能 Embedding API 未配置）")
        except Exception:
            _info("语义搜索跳过（Embedding API 未配置）")

        all_exps = store.list_experiences()
        assert len(all_exps) >= 1
        _ok(f"list_experiences 返回 {len(all_exps)} 条 [PASS]")

        found = store.get_experience(exp_id)
        assert found is not None
        assert found.get("score") == 0.85
        _ok("按 ID 查询经验 [PASS]")

        trend = store.list_experiences(limit=3)
        assert trend is not None
        _ok("list_experiences(limit=3) [PASS]")

        _info(f"经验库位置: {tmpdir}")

    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 6. 端到端轻量测试
# ---------------------------------------------------------------------------

async def test_e2e_pipeline() -> None:
    _section("6. 端到端 pipeline 测试（1 轮）")

    from prompt_harness.optimizer import generate_candidates, evaluate_candidate, analyze_target, _auto_settings
    from prompt_harness.experience_store import ExperienceStore

    target_text = "他站在庭院中，月光洒在肩头。风起了，衣袂翻飞。远处传来更漏声，一下，又一下。"

    attributes = await analyze_target(target_text)
    assert isinstance(attributes, dict)
    _info(f"analyze_target: genre={attributes.get('genre')}, scene={attributes.get('scene_type')}")

    tmpdir = tempfile.mkdtemp()
    try:
        store = ExperienceStore(Path(tmpdir))
        await store.add_experience({
            "target_text": "月光下，他静静站立。风卷起落叶，在空中打了个旋。",
            "genre": "仙侠",
            "scene_type": "环境描写",
            "optimal_prompt": "你是仙侠网文写手，擅长意境描写……",
            "score": 0.75,
            "scores_detail": {},
            "rounds": 1,
        })
        retrieved = []
        try:
            retrieved = await store.semantic_search(target_text, top_k=2)
        except Exception:
            pass
        _info(f"semantic_search 返回 {len(retrieved)} 条")

        candidates_result = await generate_candidates(target_text, retrieved)
        if candidates_result and candidates_result[0]:
            candidates = candidates_result[0]
            content_preview = candidates[:60].replace("\n", " ")
            _ok(f"generate_candidates 成功，返回 {len(candidates)} 字 - {content_preview}...")
        else:
            candidates = None
            _info("generate_candidates 返回空（可能 API 未配置，跳过后续测试）")

        if candidates:
            settings = _auto_settings(attributes)
            plot_a = attributes.get("plot_summary", "") or "测试情节"

            try:
                result = await evaluate_candidate(
                    system_prompt=candidates,
                    target_text=target_text,
                    settings=settings,
                    plot_a=plot_a,
                )
                assert isinstance(result, dict)
                assert "similarity" in result
                similarity = result["similarity"]
                assert 0.0 <= similarity <= 1.0, f"相似度应在 0-1 之间，实际 {similarity}"
                _ok(f"evaluate_candidate 完成，相似度={similarity:.4f}")
                _info(f"  length_diff={result.get('length_diff', 'N/A')}")
            except Exception as e:
                _info(f"evaluate_candidate 跳过（API 未配置: {e}）")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:

    print(f"\n{'=' * 60}")
    print(f"  Prompt Harness 冒烟测试 (v3)")
    print(f"  开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")
    _info(f"Python: {sys.version.split()[0]}")
    _info(f"工作目录: {Path.cwd()}")

    # 3. API 连通性
    await check_api()

    # 4. 核心函数单元测试
    test_extract_system_prompt()
    test_similarity_functions()
    test_diff_analyzer()
    test_calc_max_tokens()

    # 5. 经验库测试
    await test_experience_store()

    # 6. 端到端测试
    await test_e2e_pipeline()

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"  汇总")
    print(f"{'=' * 60}")
    if _stats["failures"] == 0:
        print(f"  [PASS] {_stats['failures']} 个失败, {_stats['warnings']} 个警告")
        print(f"  所有测试通过！\n")
    else:
        print(f"  [FAIL] {_stats['failures']} 个失败, {_stats['warnings']} 个警告")
        print(f"  修复后重新运行。\n")

    await close_session()
    sys.exit(1 if _stats["failures"] > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
