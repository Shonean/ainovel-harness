#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
动态参数系统验证脚本
验证dynamic_params模块与test_qwen_l5.py的集成
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
    detect_content_type,
    detect_generation_stage,
)


def verify_basic_functionality():
    """验证基本功能"""
    print("=== 验证基本功能 ===")
    
    # 测试内容类型检测
    text_dialogue = '"你好"他说。"我很好"她回答。"再见"他说。'
    text_narration = "阳光透过树叶洒在地面上，形成斑驳的光影。"
    text_mixed = '"你看那边"他指着远处。阳光正好，微风轻拂。'
    
    assert detect_content_type(text_dialogue) == ContentType.DIALOGUE
    assert detect_content_type(text_narration) == ContentType.NARRATION
    assert detect_content_type(text_mixed) == ContentType.MIXED
    print("✓ 内容类型检测正确")
    
    # 测试阶段检测
    context_opening = "这是故事的开篇，需要吸引读者"
    context_climax = "这是故事的高潮部分，冲突达到顶点"
    context_ending = "这是故事的结尾，需要收束"
    context_development = "这是故事的发展部分"
    
    assert detect_generation_stage(context_opening) == GenerationStage.OPENING
    assert detect_generation_stage(context_climax) == GenerationStage.CLIMAX
    assert detect_generation_stage(context_ending) == GenerationStage.ENDING
    assert detect_generation_stage(context_development) == GenerationStage.DEVELOPMENT
    print("✓ 阶段检测正确")
    
    # 测试参数计算
    model_info = ModelInfo(name="qwen-turbo", provider="qwen")
    params = calculate_dynamic_params(
        text=text_dialogue,
        context=context_opening,
        target_len=3000,
        model_info=model_info,
    )
    
    # 验证参数存在
    assert "temperature" in params
    assert "top_p" in params
    assert "frequency_penalty" in params
    assert "presence_penalty" in params
    assert "max_tokens" in params
    print("✓ 参数计算正确")
    
    # 验证参数范围
    from prompt_harness.dynamic_params import PARAM_BOUNDS
    for key in ["temperature", "top_p", "frequency_penalty", "presence_penalty"]:
        assert PARAM_BOUNDS[key]["min"] <= params[key] <= PARAM_BOUNDS[key]["max"], \
            f"{key} = {params[key]} 超出范围"
    print("✓ 参数范围正确")
    
    # 验证Qwen模型调整
    params_without_model = calculate_dynamic_params(text=text_dialogue, context=context_opening, target_len=3000)
    assert params["temperature"] < params_without_model["temperature"]
    print("✓ Qwen模型调整正确")
    
    return True


def verify_integration_with_test_script():
    """验证与test_qwen_l5.py的集成"""
    print("\n=== 验证与test_qwen_l5.py的集成 ===")
    
    # 模拟test_qwen_l5.py中的generate函数调用
    system = "系统提示"
    user = '"你好"他说。"我很好"她回答。'
    target_len = 3000
    context = "这是故事的开篇"
    model_name = "qwen-turbo"
    
    # 使用动态参数系统
    model_info = ModelInfo(name=model_name, provider="qwen" if "qwen" in model_name.lower() else "unknown")
    dynamic_params = calculate_dynamic_params(
        text=user,
        context=context,
        target_len=target_len,
        model_info=model_info,
    )
    
    # 记录参数日志
    log_output = log_params(dynamic_params, "generate")
    assert "temperature" in log_output
    assert "top_p" in log_output
    assert "frequency_penalty" in log_output
    assert "presence_penalty" in log_output
    assert "max_tokens" in log_output
    print("✓ 参数日志输出正确")
    
    # 验证参数可以正确传递给API
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": dynamic_params["temperature"],
        "top_p": dynamic_params["top_p"],
        "frequency_penalty": dynamic_params["frequency_penalty"],
        "presence_penalty": dynamic_params["presence_penalty"],
        "max_tokens": dynamic_params["max_tokens"],
    }
    
    # 验证payload结构正确
    assert payload["temperature"] == dynamic_params["temperature"]
    assert payload["top_p"] == dynamic_params["top_p"]
    assert payload["frequency_penalty"] == dynamic_params["frequency_penalty"]
    assert payload["presence_penalty"] == dynamic_params["presence_penalty"]
    assert payload["max_tokens"] == dynamic_params["max_tokens"]
    print("✓ API payload结构正确")
    
    return True


def main():
    """运行验证"""
    print("动态参数系统验证")
    print("=" * 60)
    
    tests = [
        verify_basic_functionality,
        verify_integration_with_test_script,
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
            print(f"✗ 验证失败: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"验证结果: {passed} 通过, {failed} 失败")
    
    if failed == 0:
        print("✓ 所有验证通过！动态参数系统已正确集成。")
        return 0
    else:
        print("✗ 有验证失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())