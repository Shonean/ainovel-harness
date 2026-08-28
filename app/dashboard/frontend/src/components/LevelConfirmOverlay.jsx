// 层级确认浮层：step_ladder 某一级生成后（待确认态）从底部升起的审阅面板。
// 借鉴 codex TUI overlay 模式：主视图（阶梯转录）保持只读可见，所有确认/重生成/
// 对话修改操作收进浮层，完事浮层关闭，不把用户从创作流里拽走。
import { useEffect } from 'react'

const LABEL = {
    l1: 'l1 一句话极简',
    l2: 'l2 情节概要',
    l3: 'l3 章核心',
    l4: 'l4 场景分解',
    l5: 'l5 正文',
}

export default function LevelConfirmOverlay({
    open, level, busy = false,
    onConfirm, onRegenerate, onDiscuss, onClose,
    children,
}) {
    // Esc 关闭浮层
    useEffect(() => {
        if (!open) return
        const onKey = (e) => { if (e.key === 'Escape' && !busy) onClose?.() }
        window.addEventListener('keydown', onKey)
        return () => window.removeEventListener('keydown', onKey)
    }, [open, busy, onClose])

    if (!open || !level) return null

    return (
        <div className="lco-backdrop" onClick={() => !busy && onClose?.()}>
            <div className="lco-panel" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true">
                <div className="lco-head">
                    <span className="lco-title">审阅 {LABEL[level] || level}</span>
                    <span className="lco-badge">待确认</span>
                    <button className="lco-close" title="关闭（Esc）" disabled={busy} onClick={onClose}>✕</button>
                </div>
                <div className="lco-body">
                    {children}
                </div>
                <div className="lco-actions">
                    <button className="lco-btn primary" disabled={busy} onClick={onConfirm}>
                        {busy ? '处理中…' : '确认，继续下一级'}
                    </button>
                    <button className="lco-btn" disabled={busy} onClick={onRegenerate} title="重新生成本级">
                        重新生成
                    </button>
                    <button className="lco-btn" disabled={busy} onClick={onDiscuss} title="在对话里对本级提修改意见">
                        对话修改
                    </button>
                    <button className="lco-btn ghost" disabled={busy} onClick={onClose}>
                        稍后处理
                    </button>
                </div>
            </div>
        </div>
    )
}
