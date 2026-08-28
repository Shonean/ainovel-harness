#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
动态参数系统集成测试
验证动态参数模块与test_qwen_l5.py的集成
"""
import sys
import os

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prompt_harness.dynamic_params import (
    calculate_dynamic_params,
    ModelInfo,
    log_params,
    ContentType,
    GenerationStage,
)


def test_basic_integration():
    """测试基本集成"""
    print("=== 测试基本集成 ===")
    
    # 测试参数计算
    text = '"你好"他说。"我很好"她回答。'
    context = "这是故事的开篇"
    target_len = 3000
    
    model_info = ModelInfo(name="qwen-turbo", provider="qwen")
    params = calculate_dynamic_params(
        text=text,
        context=context,
        target_len=target_len,
        model_info=model_info,
    )
    
    print(f"输入文本: {text[:50]}...")
    print(f"上下文: {context}")
    print(f"目标字数: {target_len}")
    print(f"模型: {model_info.name} ({model_info.provider})")
    print()
    print(log_params(params, "integration_test"))
    print()
    
    # 验证参数
    assert "temperature" in params
    assert "top_p" in params
    assert "frequency_penalty" in params
    assert "presence_penalty" in params
    assert "max_tokens" in params
    
    # 验证参数范围
    from prompt_harness.dynamic_params import PARAM_BOUNDS
    for key in ["temperature", "top_p", "frequency_penalty", "presence_penalty"]:
        assert PARAM_BOUNDS[key]["min"] <= params[key] <= PARAM_BOUNDS[key]["max"], \
            f"{key} = {params[key]} 超出范围 [{PARAM_BOUNDS[key]['min']}, {PARAM_BOUNDS[key]['max']}]"
    
    print("✓ 基本集成测试通过")
    return True


def test_different_content_types():
    """测试不同内容类型"""
    print("\n=== 测试不同内容类型 ===")
    
    test_cases = [
        {
            "name": "对白为主",
            "text": '"你好"他说。"我很好"她回答。"再见"他说。"谢谢"她说。',
            "expected_type": ContentType.DIALOGUE,
        },
        {
            "name": "叙述为主",
            "text": "阳光透过树叶洒在地面上，形成斑驳的光影。远处的山峦在薄雾中若隐若现。",
            "expected_type": ContentType.NARRATION,
        },
        {
            "name": "混合内容",
            "text": '"你看那边"他指着远处。阳光正好，微风轻拂。',
            "expected_type": ContentType.MIXED,
        },
    ]
    
    for case in test_cases:
        print(f"\n测试: {case['name']}")
        print(f"文本: {case['text'][:50]}...")
        
        params = calculate_dynamic_params(text=case['text'])
        print(log_params(params, case['name']))
        
        # 验证参数范围
        from prompt_harness.dynamic_params import PARAM_BOUNDS
        for key in ["temperature", "top_p", "frequency_penalty", "presence_penalty"]:
            assert PARAM_BOUNDS[key]["min"] <= params[key] <= PARAM_BOUNDS[key]["max"], \
                f"{key} = {params[key]} 超出范围"
        
        print(f"✓ {case['name']}测试通过")
    
    return True


def test_different_stages():
    """测试不同生成阶段"""
    print("\n=== 测试不同生成阶段 ===")
    
    test_cases = [
        {
            "name": "开篇阶段",
            "context": "这是故事的开篇，需要吸引读者",
            "expected_stage": GenerationStage.OPENING,
        },
        {
            "name": "高潮阶段",
            "context": "这是故事的高潮部分，冲突达到顶点",
            "expected_stage": GenerationStage.CLIMAX,
        },
        {
            "name": "收尾阶段",
            "context": "这是故事的结尾，需要收束",
            "expected_stage": GenerationStage.ENDING,
        },
        {
            "name": "发展阶段",
            "context": "这是故事的发展部分",
            "expected_stage": GenerationStage.DEVELOPMENT,
        },
    ]
    
    for case in test_cases:
        print(f"\n测试: {case['name']}")
        print(f"上下文: {case['context']}")
        
        params = calculate_dynamic_params(text="普通文本", context=case['context'])
        print(log_params(params, case['name']))
        
        # 验证参数范围
        from prompt_harness.dynamic_params import PARAM_BOUNDS
        for key in ["temperature", "top_p", "frequency_penalty", "presence_penalty"]:
            assert PARAM_BOUNDS[key]["min"] <= params[key] <= PARAM_BOUNDS[key]["max"], \
                f"{key} = {params[key]} 超出范围"
        
        print(f"✓ {case['name']}测试通过")
    
    return True


def test_target_length_adjustment():
    """测试目标字数调整"""
    print("\n=== 测试目标字数调整 ===")
    
    test_cases = [
        {"name": "短文", "target_len": 500, "expected_range": (3500, 3500)},
        {"name": "中等长度", "target_len": 2000, "expected_range": (1728, 1728)},
        {"name": "长文", "target_len": 5000, "expected_range": (4320, 4320)},
    ]
    
    for case in test_cases:
        print(f"\n测试: {case['name']} (目标{case['target_len']}字)")
        
        params = calculate_dynamic_params(text="普通文本", target_len=case['target_len'])
        print(log_params(params, case['name']))
        
        # 验证max_tokens计算
        min_expected, max_expected = case['expected_range']
        assert min_expected <= params['max_tokens'] <= max_expected, \
            f"max_tokens应在[{min_expected}, {max_expected}]范围内，实际{params['max_tokens']}"
        
        print(f"✓ {case['name']}测试通过 (max_tokens={params['max_tokens']})")
    
    return True


def main():
    """运行所有集成测试"""
    print("动态参数系统集成测试")
    print("=" * 60)
    
    tests = [
        test_basic_integration,
        test_different_content_types,
        test_different_stages,
        test_target_length_adjustment,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"✗ 测试失败: {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    
    if failed == 0:
        print("✓ 所有测试通过！")
        return 0
    else:
        print("✗ 有测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())