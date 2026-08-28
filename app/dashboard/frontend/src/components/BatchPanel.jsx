import { useState, useEffect, useRef } from 'react'
import { phAiBatchGenerate, phAiOptimizeStatus, phAiCancelTask } from '../api.js'

export default function BatchPanel({ bookRoot, onNotice, onError, onComplete }) {
    const [batchTarget, setBatchTarget] = useState(10)
    const [batchPerArc, setBatchPerArc] = useState(3)
    const [batchBrief, setBatchBrief] = useState('')
    const [batchTaskId, setBatchTaskId] = useState('')
    const [batchStatus, setBatchStatus] = useState(null)
    const batchPollRef = useRef(null)

    // 清理轮询
    useEffect(() => {
        return () => {
            if (batchPollRef.current) {
                clearInterval(batchPollRef.current)
                batchPollRef.current = null
            }
        }
    }, [])

    const btnStyle = (disabled, primary) => ({
        padding: '6px 12px',
        borderRadius: 4,
        border: '1px solid var(--line)',
        background: primary ? 'var(--dai)' : 'var(--paper)',
        color: primary ? 'var(--paper-raised)' : 'var(--ink)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        fontSize: 13,
    })

    const pollBatch = (tid) => {
        if (batchPollRef.current) clearInterval(batchPollRef.current)
        const iv = setInterval(async () => {
            try {
                const s = await phAiOptimizeStatus(tid)
                setBatchStatus(s)
                if (s.status !== 'running') {
                    clearInterval(iv)
                    batchPollRef.current = null
                    onComplete?.()
                }
            } catch { /* 轮询失败忽略 */ }
        }, 3000)
        batchPollRef.current = iv
    }

    const startBatch = async () => {
        if (!bookRoot) return
        try {
            const r = await phAiBatchGenerate({
                book_root: bookRoot,
                target_chapters: Number(batchTarget) || 10,
                n_chapters_per_arc: Number(batchPerArc) || 3,
                arc_briefs: batchBrief.trim() ? batchBrief.split('\n').map(s => s.trim()).filter(Boolean) : null,
                select_all_elements: true,
            })
            setBatchTaskId(r.task_id)
            pollBatch(r.task_id)
            onNotice?.('批量生成已启动（后台逐情节驱动，可在「全书概览」看进度）')
        } catch (e) {
            onError?.(`启动失败: ${e.message || e}`)
        }
    }

    const stopBatch = async () => {
        if (batchTaskId) {
            try { await phAiCancelTask(batchTaskId) } catch {}
        }
        if (batchPollRef.current) {
            clearInterval(batchPollRef.current)
            batchPollRef.current = null
        }
        onNotice?.('批量生成已请求停止')
    }

    const prog = batchStatus?.progress || {}
    const st = batchStatus?.status || 'idle'

    return (
        <div style={{ maxWidth: 920 }}>
            <div className="section-block">
                <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>
                    批量生成全书
                </div>
                <div style={{ fontSize: 13, color: 'var(--ink-sub)', lineHeight: 1.9, marginBottom: 12 }}>
                    自动驱动逐情节流水线：新建情节 → 全选参与元素 → l2 → l3 → 逐章 l4/l5 → 双评分 → 落盘 → 完成。
                    每情节自动衔接上一情节（前文锚点）。可随时停止。<br />
                    <b>下框每行一条 = 每情节的 l1（批量生产 l1）</b>；留空由系统按基本设定自动生成。生成后回创作 hub 逐情节精修。
                </div>
                <div className="field-row" style={{ flexWrap: 'wrap', gap: 10 }}>
                    <label className="field-label">
                        目标总章数：
                        <input
                            type="number"
                            min={1}
                            max={500}
                            value={batchTarget}
                            onChange={e => setBatchTarget(e.target.value)}
                            className="field-input"
                            style={{ width: 90 }}
                        />
                    </label>
                    <label className="field-label">
                        每情节章数：
                        <input
                            type="number"
                            min={1}
                            max={20}
                            value={batchPerArc}
                            onChange={e => setBatchPerArc(e.target.value)}
                            className="field-input"
                            style={{ width: 80 }}
                        />
                    </label>
                    <button
                        style={btnStyle(!!batchTaskId, !batchTaskId)}
                        disabled={!!batchTaskId || !bookRoot}
                        onClick={startBatch}
                    >
                        {batchTaskId ? '生成中…' : '▶ 开始批量生成'}
                    </button>
                    {batchTaskId && (
                        <button style={btnStyle(false, true)} onClick={stopBatch}>
                            ⏹ 停止
                        </button>
                    )}
                </div>
                {batchBrief !== null && (
                    <div style={{ marginTop: 10 }}>
                        <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 4 }}>
                            逐情节一句话剧情（每行一条 = 每情节的 l1，批量生产 l1；留空自动生成）：
                        </div>
                        <textarea
                            value={batchBrief}
                            onChange={e => setBatchBrief(e.target.value)}
                            placeholder={'沈云初入青云宗，在入门大比中崭露头角\n宗门遭袭，沈云用九转玄功力挽狂澜'}
                            style={{
                                width: '100%',
                                minHeight: 90,
                                boxSizing: 'border-box',
                                fontFamily: 'inherit',
                                fontSize: 13,
                                lineHeight: 1.7,
                                padding: 8,
                                border: '1px solid var(--line-soft)',
                                borderRadius: 6
                            }}
                        />
                    </div>
                )}
            </div>
            {batchTaskId && (
                <div className="section-block" style={{ marginTop: 12, borderLeft: '3px solid var(--dai)' }}>
                    <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>
                        批量生成进度：{prog.done || 0} / {prog.target || '?'} 章
                        {st === 'running' ? ' ' : st === 'done' ? ' 完成' : st === 'failed' ? ' 失败' : ''}
                    </div>
                    {st === 'failed' && batchStatus?.error && (
                        <div style={{ fontSize: 12, color: 'var(--cinnabar-d)', marginBottom: 6 }}>
                            ⚠ {batchStatus.error}
                        </div>
                    )}
                    <div style={{ fontSize: 12.5, color: 'var(--ink-sub)', lineHeight: 1.8 }}>
                        当前：{prog.arc_name || '—'} ｜ 步骤：{prog.step || '—'} ｜ 最新章节：第{(prog.last_chapter || 0) || '—'}章
                    </div>
                    {(prog.messages || []).slice(-6).map((m, i) => (
                        <div key={i} style={{ fontSize: 12, color: 'var(--ink-mute)', fontFamily: 'monospace', lineHeight: 1.6 }}>
                            {m}
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}
