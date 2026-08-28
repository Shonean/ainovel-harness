"""动态参数模块测试"""
import pytest
from prompt_harness.dynamic_params import (
    detect_content_type,
    detect_generation_stage,
    calculate_dynamic_params,
    ContentType,
    GenerationStage,
    ModelInfo,
    clamp_params,
    PARAM_BOUNDS,
)


def test_detect_content_type_dialogue():
    """测试对白内容检测"""
    text = '"你好"他说。"我很好"她回答。"再见"他说。'
    result = detect_content_type(text)
    assert result == ContentType.DIALOGUE


def test_detect_content_type_narration():
    """测试叙述内容检测"""
    text = "阳光透过树叶洒在地面上，形成斑驳的光影。远处的山峦在薄雾中若隐若现。"
    result = detect_content_type(text)
    assert result == ContentType.NARRATION


def test_detect_content_type_mixed():
    """测试混合内容检测"""
    text = '"你看那边"他指着远处。阳光正好，微风轻拂。'
    result = detect_content_type(text)
    assert result == ContentType.MIXED


def test_detect_generation_stage_opening():
    """测试开篇阶段检测"""
    context = "这是故事的开篇，需要吸引读者"
    result = detect_generation_stage(context)
    assert result == GenerationStage.OPENING


def test_detect_generation_stage_climax():
    """测试高潮阶段检测"""
    context = "这是故事的高潮部分，冲突达到顶点"
    result = detect_generation_stage(context)
    assert result == GenerationStage.CLIMAX


def test_calculate_dynamic_params_basic():
    """测试基础动态参数计算"""
    text = "一段普通文本"
    params = calculate_dynamic_params(text)
    
    # 检查参数存在
    assert "temperature" in params
    assert "top_p" in params
    assert "frequency_penalty" in params
    assert "presence_penalty" in params
    assert "max_tokens" in params
    
    # 检查参数范围
    for key in ["temperature", "top_p", "frequency_penalty", "presence_penalty"]:
        assert PARAM_BOUNDS[key]["min"] <= params[key] <= PARAM_BOUNDS[key]["max"]


def test_calculate_dynamic_params_with_model():
    """测试带模型信息的参数计算"""
    text = "一段文本"
    model = ModelInfo(name="qwen-turbo", provider="qwen")
    params = calculate_dynamic_params(text, model_info=model)
    
    # Qwen模型应该有调整（乘以0.95）
    # 默认temperature=0.7，内容类型检测为NARRATION(因子1.1)，阶段DEVELOPMENT(因子1.0)
    # 0.7 * 1.1 * 1.0 * 0.95 = 0.7315，所以检查相对于无模型调整的变化
    params_without_model = calculate_dynamic_params(text, model_info=None)
    assert params["temperature"] < params_without_model["temperature"]  # Qwen模型降低了temperature


def test_clamp_params():
    """测试参数边界检查"""
    params = {
        "temperature": 1.5,  # 超出范围
        "top_p": 0.3,        # 低于范围
        "frequency_penalty": 0.5,  # 超出范围
    }
    
    clamped = clamp_params(params)
    
    assert clamped["temperature"] == PARAM_BOUNDS["temperature"]["max"]
    assert clamped["top_p"] == PARAM_BOUNDS["top_p"]["min"]
    assert clamped["frequency_penalty"] == PARAM_BOUNDS["frequency_penalty"]["max"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])