"""L5生成动态参数调整系统

根据内容类型、生成阶段、目标字数、模型特性等多维度因子，
动态调整temperature、top_p、frequency_penalty、presence_penalty等参数。
"""
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum


class ContentType(Enum):
    """内容类型"""
    DIALOGUE = "dialogue"      # 对白为主
    NARRATION = "narration"    # 叙述为主
    MIXED = "mixed"            # 混合内容


class GenerationStage(Enum):
    """生成阶段"""
    OPENING = "opening"        # 开篇
    DEVELOPMENT = "development"  # 发展
    CLIMAX = "climax"          # 高潮
    ENDING = "ending"          # 收尾


@dataclass
class ModelInfo:
    """模型信息"""
    name: str
    provider: str  # qwen, doubao, glm, kimi等
    max_tokens_limit: int = 8000


# 默认参数配置
DEFAULT_PARAMS = {
    "temperature": 0.7,
    "top_p": 0.7,
    "frequency_penalty": 0.1,
    "presence_penalty": 0.1,
    "max_tokens": 3500,
}

# 参数边界
PARAM_BOUNDS = {
    "temperature": {"min": 0.5, "max": 0.9},
    "top_p": {"min": 0.6, "max": 0.95},
    "frequency_penalty": {"min": 0.0, "max": 0.3},
    "presence_penalty": {"min": 0.0, "max": 0.3},
}

# 内容类型调整因子
CONTENT_ADJUSTMENTS = {
    ContentType.DIALOGUE: {
        "temperature": 0.85,  # 对白降低创造性
        "frequency_penalty": 1.5,  # 增加惩罚避免重复
    },
    ContentType.NARRATION: {
        "temperature": 1.1,  # 叙述增加创造性
        "presence_penalty": 1.2,  # 鼓励话题多样性
    },
    ContentType.MIXED: {
        "temperature": 1.0,
        "top_p": 1.0,
    },
}

# 阶段调整因子
STAGE_ADJUSTMENTS = {
    GenerationStage.OPENING: {
        "temperature": 1.15,  # 开篇需要更多创造性
        "top_p": 1.05,
    },
    GenerationStage.DEVELOPMENT: {
        "temperature": 1.0,
        "top_p": 1.0,
    },
    GenerationStage.CLIMAX: {
        "temperature": 0.9,  # 高潮需要稳定
        "top_p": 0.95,
    },
    GenerationStage.ENDING: {
        "temperature": 0.85,  # 收尾需要稳定
        "top_p": 0.9,
        "presence_penalty": 1.3,  # 避免重复结尾模式
    },
}


def detect_content_type(text: str) -> ContentType:
    """检测内容类型（基于对白比例）"""
    dialogue_markers = ['"', '"', '"', '「', '」', '：', ':']
    dialogue_count = sum(1 for char in text if char in dialogue_markers)
    total_chars = len(text)
    
    if total_chars == 0:
        return ContentType.MIXED
    
    dialogue_ratio = dialogue_count / total_chars
    
    if dialogue_ratio > 0.15:  # 对白比例>15%
        return ContentType.DIALOGUE
    elif dialogue_ratio < 0.05:  # 对白比例<5%
        return ContentType.NARRATION
    else:
        return ContentType.MIXED


def detect_generation_stage(context: str) -> GenerationStage:
    """检测生成阶段（基于上下文关键词）"""
    context_lower = context.lower()
    
    if any(word in context_lower for word in ['开篇', '开头', '开始', 'first', 'opening']):
        return GenerationStage.OPENING
    elif any(word in context_lower for word in ['高潮', '转折', '冲突', 'climax', 'turning']):
        return GenerationStage.CLIMAX
    elif any(word in context_lower for word in ['结尾', '收束', '结束', 'ending', 'conclusion']):
        return GenerationStage.ENDING
    else:
        return GenerationStage.DEVELOPMENT


def get_model_adjustments(model_info: ModelInfo) -> Dict[str, float]:
    """获取模型特定的调整因子"""
    adjustments = {}

    # Qwen系列模型调整
    if "qwen" in model_info.name.lower():
        adjustments["temperature"] = 0.95  # Qwen对temperature敏感
        adjustments["top_p"] = 0.9

    # GLM-5.3+ 思考模型：max_tokens 需预留思考空间
    # reasoning_effort=low 时思考约占 1500-3000 tok，放大 2.5 倍确保正文有空间
    if "glm" in model_info.name.lower():
        adjustments["max_tokens"] = 2.5

    # 其他模型使用默认值
    return adjustments


def clamp_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """将参数限制在有效范围内"""
    clamped = {}
    for key, value in params.items():
        if key in PARAM_BOUNDS:
            bounds = PARAM_BOUNDS[key]
            clamped[key] = max(bounds["min"], min(bounds["max"], value))
        else:
            clamped[key] = value
    return clamped


def calculate_dynamic_params(
    text: str,
    context: str = "",
    target_len: int = 3000,
    model_info: Optional[ModelInfo] = None,
    custom_adjustments: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """计算动态参数
    
    Args:
        text: 当前生成的文本内容
        context: 生成上下文（用于阶段检测）
        target_len: 目标字数
        model_info: 模型信息
        custom_adjustments: 自定义调整因子
    
    Returns:
        动态调整后的参数字典
    """
    # 1. 基础参数
    params = DEFAULT_PARAMS.copy()
    
    # 2. 内容类型调整
    content_type = detect_content_type(text)
    content_adj = CONTENT_ADJUSTMENTS.get(content_type, {})
    for key, factor in content_adj.items():
        if key in params:
            params[key] *= factor
    
    # 3. 生成阶段调整
    stage = detect_generation_stage(context)
    stage_adj = STAGE_ADJUSTMENTS.get(stage, {})
    for key, factor in stage_adj.items():
        if key in params:
            params[key] *= factor
    
    # 4. 目标字数调整
    if target_len > 3000:
        # 长文需要更多token
        params["max_tokens"] = min(int(target_len * 0.72 * 1.2), 8000)
    elif target_len < 1000:
        # 短文使用较少token
        params["max_tokens"] = max(int(target_len * 0.72 * 1.2), 3500)
    else:
        params["max_tokens"] = int(target_len * 0.72 * 1.2)
    
    # 5. 模型特性调整
    if model_info:
        model_adj = get_model_adjustments(model_info)
        for key, factor in model_adj.items():
            if key in params:
                params[key] *= factor
    
    # 6. 自定义调整
    if custom_adjustments:
        for key, factor in custom_adjustments.items():
            if key in params:
                params[key] *= factor
    
    # 7. 参数边界检查
    params = clamp_params(params)
    
    return params


def log_params(params: Dict[str, Any], stage: str = "default") -> str:
    """格式化参数日志"""
    lines = [f"[{stage}] 动态参数:"]
    for key, value in params.items():
        if isinstance(value, float):
            lines.append(f"  {key}: {value:.3f}")
        else:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)