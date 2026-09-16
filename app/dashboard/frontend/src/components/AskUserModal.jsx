import { useEffect, useState } from 'react'
import { resumeTask } from '../api.js'

/**
 * 工作流挂起等待用户输入时弹出的模态框。
 *
 * 支持两种 payload 格式：
 *
 * 1. 旧格式（简单问答）：
 *    { prompt: "问题文本", options: [...], placeholder: "...", taskId: "..." }
 *
 * 2. 新格式（决策确认，Phase 1）：
 *    { prompt: { type: "decision_confirmation", step_id, operation_type,
 *                step_description, required_decision_level: "L1"|"L2"|"L3",
 *                required_decision_level_desc, schema }, taskId: "..." }
 *
 * 决策级别 UI：
 *  - L1：确认框 — 显示操作描述 + 批准/拒绝按钮 + 可选理由
 *  - L2：3候选选择 — 显示操作描述 + 3个候选方案供选择 + 可选理由
 *  - L3：自定义Prompt — 显示操作描述 + 可编辑Prompt文本框 + 批准/拒绝
 *  - L0：不会到达此组件（后端自动批准）
 *
 * Props:
 *  - payload: object | null   // awaiting_input 事件（含 taskId）
 *  - onResolved: () => void   // 提交后关闭
 */
export default function AskUserModal({ payload, onResolved }) {
    const [value, setValue] = useState('')
    const [reason, setReason] = useState('')
    const [selectedOption, setSelectedOption] = useState(null)
    const [customPrompt, setCustomPrompt] = useState('')
    const [submitting, setSubmitting] = useState(false)
    const [err, setErr] = useState('')

    // Phase 1 决策确认格式：SSE 事件中 prompt 字段被展开到顶层
    // { phase: "awaiting_input", type: "decision_confirmation", step_id, operation_type, ... }
    const isDecision = payload?.type === 'decision_confirmation'
    const decisionLevel = isDecision ? payload.required_decision_level : null
    const decisionInfo = isDecision ? payload : null

    // 每次新弹窗时重置状态
    useEffect(() => {
        if (!payload) {
            setValue('')
            setReason('')
            setSelectedOption(null)
            setCustomPrompt('')
            return
        }

        if (isOutlineReview) {
            setValue(payload.outline_text || '')
            setReason('')
            setSelectedOption(null)
            setCustomPrompt('')
        } else if (isDecision) {
            setValue('')
            setReason('')
            setSelectedOption(null)
            setCustomPrompt('')
        } else {
            const options = Array.isArray(payload.options) ? payload.options : null
            // 兼容对象格式选项 { label, description } 与纯字符串选项
            const firstOpt = options && options[0]
            const firstVal = typeof firstOpt === 'object' ? (firstOpt.label || '') : (firstOpt || '')
            setValue(payload.placeholder || firstVal || '')
            setReason('')
            setSelectedOption(null)
            setCustomPrompt('')
        }
    }, [payload])

    if (!payload) return null

    const taskId = payload.taskId

    // 解析选项的显示文本：兼容字符串和 { label, description } 对象
    const optionLabel = (opt) => typeof opt === 'object' ? (opt.label || JSON.stringify(opt)) : String(opt)
    const optionValue = (opt) => typeof opt === 'object' ? (opt.label || '') : opt
    // ---- 章纲审阅格式 ----
    const isOutlineReview = payload?.type === 'outline_review'

    if (isOutlineReview) {
        const outlineText = payload.outline_text || ''
        const chapterRange = payload.chapter_range || ''
        const hint = payload.hint || ''

        const submitOutline = async () => {
            if (!taskId) {
                setErr('缺少 task_id，无法恢复')
                return
            }
            setSubmitting(true)
            setErr('')
            try {
                await resumeTask(taskId, { edited_text: value })
                setValue('')
                if (onResolved) onResolved()
            } catch (e) {
                setErr(e.message || '提交失败')
            } finally {
                setSubmitting(false)
            }
        }

        return (
            <div className="askuser-overlay">
                <div className="askuser-modal outline-review-modal" style={{ maxWidth: '900px', width: '95vw' }}>
                    <div className="outline-review-header">
                        <span className="badge badge-amber">章纲审阅</span>
                        <span>{chapterRange ? `第 ${chapterRange} 章` : ''}</span>
                    </div>
                    {hint && <div className="outline-review-hint">{hint}</div>}
                    <div className="outline-review-body">
                        <textarea
                            className="field-input outline-review-textarea"
                            value={value}
                            onChange={e => setValue(e.target.value)}
                            rows={24}
                            disabled={submitting}
                            style={{
                                width: '100%',
                                fontFamily: 'var(--font-mono, monospace)',
                                fontSize: '14px',
                                lineHeight: '1.6',
                                resize: 'vertical',
                                minHeight: '400px',
                            }}
                        />
                    </div>
                    <div className="outline-review-actions" style={{ marginTop: '12px', display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
                        <button
                            className="btn btn-green"
                            disabled={submitting}
                            onClick={submitOutline}
                        >
                            {submitting ? '保存中…' : '保存并继续'}
                        </button>
                    </div>
                    {err && <div className="askuser-error">{err}</div>}
                </div>
            </div>
        )
    }

    if (!isDecision) {
        const question = payload.prompt || payload.question || '需要你的输入才能继续：'
        const options = Array.isArray(payload.options) ? payload.options : null
        const placeholder = payload.placeholder || '输入回答…'

        const submit = async (answer) => {
            if (!taskId) {
                setErr('缺少 task_id，无法恢复')
                return
            }
            setSubmitting(true)
            setErr('')
            try {
                await resumeTask(taskId, answer)
                setValue('')
                if (onResolved) onResolved()
            } catch (e) {
                setErr(e.message || '提交失败')
            } finally {
                setSubmitting(false)
            }
        }

        return (
            <div className="askuser-overlay">
                <div className="askuser-modal">
                    <div className="askuser-question">{question}</div>

                    <div className="askuser-text">
                        <textarea
                            className="field-input"
                            value={value}
                            placeholder={placeholder}
                            rows={4}
                            onChange={e => setValue(e.target.value)}
                            disabled={submitting}
                        />
                        {options && options.length > 0 && (
                            <div className="askuser-options">
                                {options.map((opt, i) => (
                                    <button
                                        key={i}
                                        type="button"
                                        className="btn btn-blue"
                                        disabled={submitting}
                                        onClick={() => setValue(optionValue(opt))}
                                        title={typeof opt === 'object' ? opt.description || '' : ''}
                                    >
                                        {optionLabel(opt)}
                                    </button>
                                ))}
                            </div>
                        )}
                        <button
                            className="btn btn-green"
                            disabled={submitting || !value.trim()}
                            onClick={() => submit(value.trim())}
                        >
                            {submitting ? '提交中…' : '提交并继续'}
                        </button>
                    </div>

                    {err && <div className="askuser-error">{err}</div>}
                </div>
            </div>
        )
    }

    // ---- 新格式：决策确认 ----
    const levelBadge = {
        L1: { cls: 'badge-amber', label: 'L1 · 需确认' },
        L2: { cls: 'badge-blue', label: 'L2 · 生成操作' },
        L3: { cls: 'badge-purple', label: 'L3 · 创造性操作' },
    }[decisionLevel] || { cls: 'badge-neutral', label: decisionLevel }

    const submitDecision = async (approved) => {
        if (!taskId) {
            setErr('缺少 task_id，无法恢复')
            return
        }
        setSubmitting(true)
        setErr('')

        const answer = { approved, decision_level: decisionLevel }
        if (approved && customPrompt.trim()) {
            answer.custom_prompt = customPrompt.trim()
        }
        if (reason.trim()) {
            answer.reason = reason.trim()
        }

        try {
            await resumeTask(taskId, answer)
            if (onResolved) onResolved()
        } catch (e) {
            setErr(e.message || '提交失败')
        } finally {
            setSubmitting(false)
        }
    }

    return (
        <div className="askuser-overlay">
            <div className="askuser-modal decision-modal">
                {/* 级别标签 */}
                <div className="decision-header">
                    <span className={`badge ${levelBadge.cls}`}>{levelBadge.label}</span>
                    <span className="decision-op-type">{decisionInfo.operation_type}</span>
                </div>

                {/* 步骤描述 */}
                <div className="decision-step-desc">
                    {decisionInfo.step_description || `步骤: ${decisionInfo.step_id}`}
                </div>
                <div className="decision-level-desc">
                    {decisionInfo.required_decision_level_desc}
                </div>

                {/* 自定义指令（所有级别可选） */}
                <div className="decision-custom-prompt">
                    <div className="mini-label">自定义指令（可选，可对 AI 追加要求）</div>
                    <textarea
                        className="field-input"
                        value={customPrompt}
                        placeholder="输入你对 AI 的额外指令…"
                        rows={3}
                        onChange={e => setCustomPrompt(e.target.value)}
                        disabled={submitting}
                    />
                </div>

                {/* 备注（可选） */}
                <div className="decision-reason">
                    <div className="mini-label">备注（可选）</div>
                    <textarea
                        className="field-input"
                        value={reason}
                        placeholder="可选备注…"
                        rows={1}
                        onChange={e => setReason(e.target.value)}
                        disabled={submitting}
                    />
                </div>

                {/* 操作按钮 */}
                <div className="decision-actions">
                    <button
                        className="btn btn-green"
                        disabled={submitting}
                        onClick={() => submitDecision(true)}
                    >
                        {submitting ? '提交中…' : '✓ 批准执行'}
                    </button>
                    <button
                        className="btn btn-red"
                        disabled={submitting}
                        onClick={() => submitDecision(false)}
                    >
                        {submitting ? '提交中…' : '✗ 拒绝'}
                    </button>
                </div>

                {err && <div className="askuser-error">{err}</div>}
            </div>
        </div>
    )
}
