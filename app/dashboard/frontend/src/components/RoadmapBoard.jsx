/**
 * 量产路线图面板：幕分组 + 弧卡（逐卡可改/重生成）+ 伏笔台账 + 冻结确认。
 *
 * 数据：/ai-creation/roadmap（roadmap.json）；冻结后供生产线做弧卡队列。
 * 设计原型：docs/计划稿/2026-09-11-量产模式-UI原型.html（05 路线图 / 06 弧卡编辑 / 07 冻结）。
 */
import { useCallback, useEffect, useState } from 'react'
import {
    phAiRoadmapGet, phAiRoadmapGenerate, phAiRoadmapSave,
    phAiRoadmapFreeze, phAiRoadmapCardRegenerate, phAiRoadmapContinue,
    phAiArcChat, phAiArcChatApply,
} from '../api.js'

const ROLE_TONE = {
    铺垫: 'badge-neutral', 升级: 'badge-blue', 转折: 'badge-purple',
    高潮: 'badge-red', 收束: 'badge-green',
}

const inputStyle = {
    width: '100%', boxSizing: 'border-box', padding: '7px 9px', fontSize: 13,
    border: '1px solid var(--line)', borderRadius: 6,
    background: 'var(--paper)', color: 'var(--ink)',
}
const labelStyle = { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12.5, fontWeight: 600 }

function Overlay({ children, onClose }) {
    return (
        <div
            style={{
                position: 'fixed', inset: 0, background: 'rgba(20,16,10,.45)', zIndex: 9998,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
            onMouseDown={onClose}
        >
            <div
                style={{
                    background: 'var(--paper-raised)', border: '1px solid var(--line)',
                    borderRadius: 12, padding: '16px 18px', width: 680, maxWidth: '94vw',
                    maxHeight: '88vh', overflowY: 'auto', boxShadow: '0 8px 30px rgba(0,0,0,.2)',
                }}
                onMouseDown={(e) => e.stopPropagation()}
            >
                {children}
            </div>
        </div>
    )
}

export default function RoadmapBoard({ bookRoot }) {
    const [roadmap, setRoadmap] = useState(null)
    const [validation, setValidation] = useState(null)
    const [loading, setLoading] = useState(true)
    const [busy, setBusy] = useState('')
    const [error, setError] = useState('')
    const [notice, setNotice] = useState('')
    const [editing, setEditing] = useState(null)       // 弧卡编辑副本
    const [regenNote, setRegenNote] = useState('')
    const [freezeOpen, setFreezeOpen] = useState(false)
    const [freezeResult, setFreezeResult] = useState(null)
    const [forceFreeze, setForceFreeze] = useState(false)
    const [genTarget, setGenTarget] = useState(30)
    const [genPerArc, setGenPerArc] = useState(3)
    const [aiInput, setAiInput] = useState('')
    const [aiBusy, setAiBusy] = useState(false)
    const [aiReply, setAiReply] = useState('')
    const [aiPending, setAiPending] = useState(null)

    const load = useCallback(async () => {
        if (!bookRoot) return
        setLoading(true)
        try {
            const r = await phAiRoadmapGet(bookRoot)
            setRoadmap(r?.roadmap || null)
            setValidation(r?.validation || null)
        } catch (e) {
            setError(e?.message || String(e))
        } finally {
            setLoading(false)
        }
    }, [bookRoot])

    useEffect(() => { load() }, [load])

    const flash = (msg) => { setNotice(msg); setTimeout(() => setNotice(''), 3000) }

    const doGenerate = useCallback(async () => {
        if (busy) return
        setBusy('generate'); setError('')
        try {
            const r = await phAiRoadmapGenerate(bookRoot, {
                target_chapters: Number(genTarget) || 30,
                n_chapters_per_arc: Number(genPerArc) || 3,
            })
            if (!r?.ok) throw new Error(r?.error || '生成失败')
            setRoadmap(r.roadmap)
            setValidation(r.validation)
            flash(`已生成 ${r.roadmap?.arcs?.length || 0} 张弧卡草案`)
        } catch (e) {
            setError(e?.message || String(e))
        } finally {
            setBusy('')
        }
    }, [bookRoot, busy, genTarget, genPerArc])

    const persist = useCallback(async (next) => {
        const r = await phAiRoadmapSave(bookRoot, next)
        if (!r?.ok) throw new Error(r?.error || '保存失败')
        setRoadmap(r.roadmap)
        setValidation(r.validation)
        return r.roadmap
    }, [bookRoot])

    const saveEditing = useCallback(async () => {
        if (!editing || !roadmap) return
        setBusy('save'); setError('')
        try {
            const next = {
                ...roadmap,
                arcs: (roadmap.arcs || []).map((a) => (a.id === editing.id ? { ...editing } : a)),
            }
            await persist(next)
            setEditing(null)
            flash('弧卡已保存（路线图回到草案，需重新冻结）')
        } catch (e) {
            setError(e?.message || String(e))
        } finally {
            setBusy('')
        }
    }, [editing, roadmap, persist])

    const regenerate = useCallback(async (arc) => {
        setBusy('regen'); setError('')
        try {
            const r = await phAiRoadmapCardRegenerate(bookRoot, arc.id, regenNote)
            if (!r?.ok) throw new Error(r?.error || '重生成失败')
            setRoadmap(r.roadmap)
            setValidation(r.validation)
            setEditing((cur) => (cur && cur.id === arc.id
                ? { ...(r.roadmap.arcs || []).find((a) => a.id === arc.id) } : cur))
            setRegenNote('')
            flash('弧卡已重生成')
        } catch (e) {
            setError(e?.message || String(e))
        } finally {
            setBusy('')
        }
    }, [bookRoot, regenNote])

    const addArcs = useCallback(async () => {
        setBusy('continue'); setError('')
        try {
            const r = await phAiRoadmapContinue(bookRoot, 1)
            if (!r?.ok) throw new Error(r?.error || '续写失败')
            setRoadmap(r.roadmap)
            setValidation(null)
            flash('已续写 1 张弧卡')
        } catch (e) {
            setError(e?.message || String(e))
        } finally {
            setBusy('')
        }
    }, [bookRoot])

    const doFreeze = useCallback(async () => {
        setBusy('freeze'); setError('')
        try {
            const r = await phAiRoadmapFreeze(bookRoot, forceFreeze)
            if (!r?.ok) {
                setFreezeResult({ ok: false, errors: r?.errors || [r?.error || '冻结失败'], warnings: r?.warnings || [] })
                return
            }
            setRoadmap(r.roadmap)
            setFreezeOpen(false)
            setFreezeResult(null)
            flash('路线图已冻结，可进入生产线批量生成')
        } catch (e) {
            setError(e?.message || String(e))
        } finally {
            setBusy('')
        }
    }, [bookRoot, forceFreeze])

    const errs = validation?.errors || []
    const warns = validation?.warnings || []

    // ── 助手调整路线图（复用 arc_chat 通道 + roadmap 工具）──
    const roadmapSummary = () => {
        if (!roadmap) return ''
        const lines = (roadmap.arcs || []).map(
            (a) => `${a.id}｜第${a.index}弧《${a.title}》｜${a.role}｜${a.l1 || ''}`)
        const fs = (roadmap.foreshadow || []).map(
            (f) => `${f.name}（开启 ${f.open_arc || '?'} → 回收 ${f.close_arc || '未回收'}）`)
        return `【路线图现状】\n${lines.join('\n')}\n【伏笔】\n${fs.join('\n')}`
    }

    const askAssistant = useCallback(async () => {
        const text = aiInput.trim()
        if (!text || aiBusy) return
        setAiBusy(true)
        setAiReply('')
        setAiPending(null)
        setError('')
        try {
            const content = `${roadmapSummary()}\n\n【作者要求】${text}`
            const r = await phAiArcChat(bookRoot, '', [{ role: 'user', content }], false, null, null, 'normal')
            if (r?.ok === false) throw new Error(r.error || '对话失败')
            setAiReply(r?.reply || '')
            if (r?.pending?.length) {
                setAiPending(r.pending[0])
            } else if (r?.changed) {
                await load()
            }
        } catch (e) {
            setError(e?.message || String(e))
        } finally {
            setAiBusy(false)
        }
    }, [aiInput, aiBusy, bookRoot, roadmap])  // eslint-disable-line react-hooks/exhaustive-deps

    const applyAiPending = useCallback(async () => {
        if (!aiPending) return
        setAiBusy(true)
        setError('')
        try {
            const r = await phAiArcChatApply(bookRoot, '', aiPending.tool, aiPending.args || {}, '')
            if (r?.ok === false) throw new Error(r.error || '执行失败')
            setAiReply(r?.event?.summary || '已执行')
            setAiPending(null)
            await load()
        } catch (e) {
            setError(e?.message || String(e))
        } finally {
            setAiBusy(false)
        }
    }, [aiPending, bookRoot, load])

    if (loading) {
        return <div style={{ padding: 30, color: 'var(--ink-sub)' }}>加载路线图…</div>
    }

    return (
        <div style={{ padding: '6px 2px' }}>
            {notice && (
                <div style={{ marginBottom: 10, padding: '6px 12px', borderRadius: 6, background: 'var(--green-wash)', color: 'var(--green)', fontSize: 12.5 }}>{notice}</div>
            )}
            {error && (
                <div style={{ marginBottom: 10, padding: '6px 12px', borderRadius: 6, background: 'var(--cinnabar-wash)', color: 'var(--cinnabar-d)', fontSize: 12.5 }}>{error}</div>
            )}

            {!roadmap ? (
                <div style={{ border: '1px solid var(--line)', borderRadius: 10, padding: 24, textAlign: 'center', background: 'var(--paper-raised)' }}>
                    <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>还没有路线图</div>
                    <div style={{ fontSize: 13, color: 'var(--ink-sub)', marginBottom: 14 }}>
                        助手会按基本设定与元素库起草全书幕结构、弧卡与伏笔回收表。
                    </div>
                    <div style={{ display: 'flex', gap: 12, justifyContent: 'center', alignItems: 'flex-end', flexWrap: 'wrap' }}>
                        <label style={{ ...labelStyle, width: 110 }}>
                            目标章数
                            <input style={inputStyle} value={genTarget} onChange={(e) => setGenTarget(e.target.value)} />
                        </label>
                        <label style={{ ...labelStyle, width: 110 }}>
                            每弧章数
                            <input style={inputStyle} value={genPerArc} onChange={(e) => setGenPerArc(e.target.value)} />
                        </label>
                        <button className="btn btn-blue" disabled={busy === 'generate'} onClick={doGenerate}>
                            {busy === 'generate' ? '正在生成…（约 1 分钟）' : '生成路线图草案'}
                        </button>
                    </div>
                </div>
            ) : (
                <>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 15, fontWeight: 700 }}>全书路线图</span>
                        <span className={`badge ${roadmap.status === 'frozen' ? 'badge-green' : 'badge-amber'}`}>
                            {roadmap.status === 'frozen' ? '已冻结' : `v${roadmap.version || 1} 草案 · 未冻结`}
                        </span>
                        <span style={{ fontSize: 12, color: 'var(--ink-sub)' }}>
                            {roadmap.arcs?.length || 0} 弧 · 约 {(roadmap.arcs || []).reduce((n, a) => n + (a.chapters || 0), 0)} 章
                        </span>
                        <div style={{ flex: 1 }} />
                        <button className="btn btn-small ghost" disabled={!!busy} onClick={addArcs}>续写弧卡</button>
                        <button className="btn btn-small ghost" disabled={!!busy} onClick={doGenerate}>
                            {busy === 'generate' ? '重生成中…' : '重生成全部'}
                        </button>
                        <button className="btn btn-small btn-blue" disabled={!!busy} onClick={() => { setFreezeResult(null); setFreezeOpen(true) }}>
                            冻结路线图
                        </button>
                    </div>

                    {/* logline 四要素 */}
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '10px 0' }}>
                        {[['要什么', roadmap.logline?.want], ['谁挡着', roadmap.logline?.obstacle],
                          ['代价', roadmap.logline?.cost], ['终局', roadmap.logline?.ending]].map(([k, v]) => (
                            <span key={k} style={{
                                fontSize: 12, padding: '3px 10px', borderRadius: 999,
                                border: '1px solid var(--line)', background: 'var(--paper)',
                            }}>{k}：{v || '—'}</span>
                        ))}
                    </div>

                    {/* 助手调整路线图 */}
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '4px 0 10px' }}>
                        <input
                            style={{ ...inputStyle, flex: 1 }}
                            placeholder="让助手调整路线图，如：把第 2 弧拆成两弧 / 让第 4 弧回收病人卡"
                            value={aiInput}
                            onChange={(e) => setAiInput(e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Enter') askAssistant() }}
                        />
                        <button className="btn btn-small" disabled={aiBusy} onClick={askAssistant}>
                            {aiBusy ? '思考中…' : '问助手'}
                        </button>
                    </div>
                    {aiReply && (
                        <div style={{ marginBottom: 10, fontSize: 12.5, lineHeight: 1.7, color: 'var(--ink-sub)' }}>
                            助手：{aiReply}
                        </div>
                    )}
                    {aiPending && (
                        <div style={{
                            marginBottom: 10, padding: '8px 12px', borderRadius: 8,
                            border: '1px solid var(--line)', background: 'var(--dai-wash, rgba(61,107,73,.08))',
                            display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5,
                        }}>
                            <span style={{ flex: 1 }}>{aiPending.summary || aiPending.tool}</span>
                            <button className="btn btn-small btn-green" disabled={aiBusy} onClick={applyAiPending}>采纳</button>
                            <button className="btn btn-small ghost" disabled={aiBusy} onClick={() => setAiPending(null)}>取消</button>
                        </div>
                    )}

                    {(errs.length > 0 || warns.length > 0) && (
                        <div style={{ marginBottom: 10, fontSize: 12.5, lineHeight: 1.7 }}>
                            {errs.map((w, i) => <div key={`e${i}`} style={{ color: 'var(--cinnabar-d)' }}>✕ {w}</div>)}
                            {warns.map((w, i) => <div key={`w${i}`} style={{ color: 'var(--amber, #a06a1f)' }}>⚠ {w}</div>)}
                        </div>
                    )}

                    {(roadmap.acts || []).map((act) => {
                        const actArcs = (roadmap.arcs || []).filter((a) => a.act_id === act.id)
                        return (
                            <div key={act.id} style={{ marginBottom: 18 }}>
                                <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 8 }}>
                                    {act.title}
                                    <span style={{ fontWeight: 400, color: 'var(--ink-sub)', marginLeft: 8 }}>
                                        （{actArcs.length} 弧{act.goal ? ` · ${act.goal}` : ''}）
                                    </span>
                                </div>
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
                                    {actArcs.map((a) => (
                                        <div
                                            key={a.id}
                                            onClick={() => { setEditing({ ...a }); setRegenNote('') }}
                                            style={{
                                                border: '1px solid var(--line)', borderRadius: 8, padding: 10,
                                                background: 'var(--paper-raised)', cursor: 'pointer',
                                            }}
                                        >
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                                                <b style={{ fontSize: 13 }}>{a.index}. {a.title}</b>
                                                <span className={`badge ${ROLE_TONE[a.role] || 'badge-neutral'}`}>{a.role}</span>
                                                <span style={{ fontSize: 11, color: 'var(--ink-mute)', marginLeft: 'auto' }}>{a.chapters} 章</span>
                                            </div>
                                            <div style={{ fontSize: 12.5, color: 'var(--ink-sub)', lineHeight: 1.6 }}>
                                                {a.l1 || '（未填一句话剧情）'}
                                            </div>
                                            {(a.foreshadow_open?.length > 0 || a.foreshadow_close?.length > 0) && (
                                                <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                                    {a.foreshadow_open?.map((f) => (
                                                        <span key={`o${f}`} style={{ fontSize: 11, padding: '1px 8px', borderRadius: 999, background: 'var(--cinnabar-wash)', color: 'var(--cinnabar-d)' }}>埋 {f}</span>
                                                    ))}
                                                    {a.foreshadow_close?.map((f) => (
                                                        <span key={`c${f}`} style={{ fontSize: 11, padding: '1px 8px', borderRadius: 999, background: 'var(--green-wash)', color: 'var(--green)' }}>收 {f}</span>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )
                    })}

                    {/* 伏笔台账 */}
                    {(roadmap.foreshadow || []).length > 0 && (
                        <div style={{ border: '1px solid var(--line)', borderRadius: 8, padding: 12, background: 'var(--paper)' }}>
                            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>伏笔台账</div>
                            {(roadmap.foreshadow || []).map((f) => {
                                const arcNo = (id) => (roadmap.arcs || []).find((a) => a.id === id)?.index ?? '?'
                                return (
                                    <div key={f.id} style={{ display: 'flex', gap: 10, fontSize: 12.5, lineHeight: 1.8, alignItems: 'baseline' }}>
                                        <span style={{ minWidth: 140 }}>{f.name}</span>
                                        <span style={{ color: 'var(--ink-sub)' }}>开启：第 {arcNo(f.open_arc)} 弧</span>
                                        <span style={{ color: f.close_arc ? 'var(--green)' : 'var(--cinnabar-d)' }}>
                                            {f.close_arc ? `回收：第 ${arcNo(f.close_arc)} 弧` : '未回收'}
                                        </span>
                                    </div>
                                )
                            })}
                        </div>
                    )}
                </>
            )}

            {/* 弧卡编辑弹层 */}
            {editing && (
                <Overlay onClose={() => setEditing(null)}>
                    <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 10 }}>
                        编辑弧卡 · 第 {editing.index} 弧
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: 10, marginBottom: 10 }}>
                        <label style={labelStyle}>标题
                            <input style={inputStyle} value={editing.title || ''} onChange={(e) => setEditing((s) => ({ ...s, title: e.target.value }))} />
                        </label>
                        <label style={labelStyle}>定位
                            <select style={inputStyle} value={editing.role || '铺垫'} onChange={(e) => setEditing((s) => ({ ...s, role: e.target.value }))}>
                                {['铺垫', '升级', '转折', '高潮', '收束'].map((r) => <option key={r} value={r}>{r}</option>)}
                            </select>
                        </label>
                        <label style={labelStyle}>章数
                            <input style={inputStyle} value={editing.chapters || 1} onChange={(e) => setEditing((s) => ({ ...s, chapters: Number(e.target.value) || 1 }))} />
                        </label>
                    </div>
                    <label style={{ ...labelStyle, marginBottom: 10 }}>一句话剧情
                        <textarea style={{ ...inputStyle, minHeight: 54, fontFamily: 'inherit' }} value={editing.l1 || ''} onChange={(e) => setEditing((s) => ({ ...s, l1: e.target.value }))} />
                    </label>
                    <label style={{ ...labelStyle, marginBottom: 10 }}>情节线（注入 l2）
                        <textarea style={{ ...inputStyle, minHeight: 120, fontFamily: 'inherit' }} value={editing.l2 || ''} onChange={(e) => setEditing((s) => ({ ...s, l2: e.target.value }))} />
                    </label>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginBottom: 10 }}>
                        <label style={labelStyle}>角色（逗号分隔）
                            <input style={inputStyle} value={(editing.characters || []).join('，')}
                                onChange={(e) => setEditing((s) => ({ ...s, characters: e.target.value.split(/[，,]/).map((x) => x.trim()).filter(Boolean) }))} />
                        </label>
                        <label style={labelStyle}>元素（逗号分隔）
                            <input style={inputStyle} value={(editing.elements || []).join('，')}
                                onChange={(e) => setEditing((s) => ({ ...s, elements: e.target.value.split(/[，,]/).map((x) => x.trim()).filter(Boolean) }))} />
                        </label>
                        <label style={labelStyle}>伏笔埋（逗号分隔）
                            <input style={inputStyle} value={(editing.foreshadow_open || []).join('，')}
                                onChange={(e) => setEditing((s) => ({ ...s, foreshadow_open: e.target.value.split(/[，,]/).map((x) => x.trim()).filter(Boolean) }))} />
                        </label>
                    </div>
                    <label style={{ ...labelStyle, marginBottom: 12 }}>伏笔收（逗号分隔）
                        <input style={inputStyle} value={(editing.foreshadow_close || []).join('，')}
                            onChange={(e) => setEditing((s) => ({ ...s, foreshadow_close: e.target.value.split(/[，,]/).map((x) => x.trim()).filter(Boolean) }))} />
                    </label>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <input style={{ ...inputStyle, flex: 1 }} placeholder="让助手重写的要求（可选），如：冲突更狠、回收病人卡"
                            value={regenNote} onChange={(e) => setRegenNote(e.target.value)} />
                        <button className="btn btn-small ghost" disabled={!!busy} onClick={() => regenerate(editing)}>
                            {busy === 'regen' ? '重写中…' : '助手重写此卡'}
                        </button>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 14 }}>
                        <button className="btn ghost" disabled={!!busy} onClick={() => setEditing(null)}>取消</button>
                        <button className="btn btn-blue" disabled={!!busy} onClick={saveEditing}>
                            {busy === 'save' ? '保存中…' : '保存弧卡'}
                        </button>
                    </div>
                </Overlay>
            )}

            {/* 冻结确认弹层 */}
            {freezeOpen && (
                <Overlay onClose={() => { setFreezeOpen(false); setFreezeResult(null) }}>
                    <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 10 }}>冻结路线图</div>
                    <div style={{ fontSize: 13, color: 'var(--ink-sub)', marginBottom: 12 }}>
                        冻结后路线图作为批量生成的弧卡队列与 l2 上下文。校验错误将拦截冻结。
                    </div>
                    {freezeResult && (
                        <div style={{ marginBottom: 10, fontSize: 12.5, lineHeight: 1.8 }}>
                            {(freezeResult.errors || []).map((w, i) => <div key={i} style={{ color: 'var(--cinnabar-d)' }}>✕ {w}</div>)}
                            {(freezeResult.warnings || []).map((w, i) => <div key={i} style={{ color: 'var(--amber, #a06a1f)' }}>⚠ {w}</div>)}
                        </div>
                    )}
                    {!freezeResult && (errs.length > 0 || warns.length > 0) && (
                        <div style={{ marginBottom: 10, fontSize: 12.5, lineHeight: 1.8 }}>
                            {errs.map((w, i) => <div key={i} style={{ color: 'var(--cinnabar-d)' }}>✕ {w}</div>)}
                            {warns.map((w, i) => <div key={i} style={{ color: 'var(--amber, #a06a1f)' }}>⚠ {w}</div>)}
                        </div>
                    )}
                    {((freezeResult?.errors || errs).length > 0) && (
                        <label style={{ display: 'flex', gap: 6, fontSize: 12.5, marginBottom: 12, alignItems: 'center' }}>
                            <input type="checkbox" checked={forceFreeze} onChange={(e) => setForceFreeze(e.target.checked)} />
                            仍要强制冻结（不推荐：悬空伏笔/结构问题会带入生产线）
                        </label>
                    )}
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                        <button className="btn ghost" disabled={!!busy} onClick={() => { setFreezeOpen(false); setFreezeResult(null) }}>取消</button>
                        <button className="btn btn-blue" disabled={!!busy || (((freezeResult?.errors || errs).length > 0) && !forceFreeze)} onClick={doFreeze}>
                            {busy === 'freeze' ? '冻结中…' : '确认冻结'}
                        </button>
                    </div>
                </Overlay>
            )}
        </div>
    )
}
