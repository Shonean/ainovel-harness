# -*- coding: utf-8 -*-
"""PromptOpt：prompt + 评分器共演化框架。

Phase 0 只提供统一评分入口（scorer_adapter）与冻结数据地基；
训练循环在 Phase 1+ 加入。导入本包不触发任何 LLM/embed 连接。
"""
