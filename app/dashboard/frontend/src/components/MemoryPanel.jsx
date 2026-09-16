import { useCallback, useEffect, useState } from 'react'
import { askText } from './PromptModal.jsx'
import {
    phAiMemoryTiers, phAiMemoryAdd, phAiMemoryDelete,
    phAiMemoryCardResolve, phAiMemoryWorkingAdd, phAiMemoryWorkingDelete,
} from '../api.js'

const _ts = (t) => {
    const n = Number(t) * 1000
    if (!n) return ''
    const d = new Date(n)
    const p = (x) => String(x).padStart(2, '0')
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/**
 * 【Phase 3】三级记忆面板（D1）：全局记忆 / 情节记忆 / 单对话记忆 + 未决冲突卡处置。
 * 数据走 GET /ai-creation/memory/tiers；删除走既有 DELETE /ai-creation/memory/{mid}
 * （v2 语义节点归档）；会话卡走 /ai-creation/memory/session；冲突卡处置走
 * /ai-creation/memory/card/resolve（四动作：分层并存/版本链改写/撤销/加槽）。
 *
 * Props:
 *  - bookRoot: 书根路径
 *  - arcId: 当前情节 id（空 = 未选情节，情节层显示引导）
 *  - sessionId: 当前会话 id（空 = 无活动会话，单对话层显示引导）
 *  - onChanged: 任意增删/处置后回调（父组件同步刷新自己的记忆列表）
 */
export default function MemoryPanel({ bookRoot, arcId = '', sessionId = '', onChanged }) {
    const [tiers, setTiers] = useState(null)
    const [loading, setLoading] = useState(false)
    const [err, setErr] = useState('')
    const [busyCard, setBusyCard] = useState('')

    const reload = useCallback(async () => {
        if (!bookRoot) return
        setLoading(true)
        setErr('')
        try {
            setTiers(await phAiMemoryTiers(bookRoot, { arc_id: arcId, session_id: sessionId }))
        } catch (e) { setErr(e.message || '加载记忆失败') }
        setLoading(false)
    }, [bookRoot, arcId, sessionId])

    useEffect(() => { reload() }, [reload])

    const notify = () => { if (onChanged) onChanged() }

    // ── 增删 ──────────────────────────────────────────────
    const addTier = async (scope) => {
        if (scope === 'arc' && !arcId) return
        const key = await askText('记忆标题（简短，可空）', '')
        if (key === null) return
        const text = await askText('记忆内容')
        if (!text?.trim()) return
        try {
            await phAiMemoryAdd({
                book_root: bookRoot, text: text.trim(), key: (key || '').trim(),
                scope, arc_id: scope === 'arc' ? arcId : '',
            })
            await reload(); notify()
        } catch (e) { setErr(e.message || '添加失败') }
    }
    const delTier = async (m) => {
        if (!window.confirm('删除这条记忆？（v2 节点归档，账本可溯）')) return
        try {
            await phAiMemoryDelete(bookRoot, m.id)
            await reload(); notify()
        } catch (e) { setErr(e.message || '删除失败') }
    }
    const addSessionCard = async () => {
        if (!sessionId) return
        const text = await askText('本会话约束（每轮都注入给 AI）')
        if (!text?.trim()) return
        try {
            await phAiMemoryWorkingAdd(bookRoot, sessionId, text.trim())
            await reload(); notify()
        } catch (e) { setErr(e.message || '添加失败') }
    }
    const delSessionCard = async (c) => {
        try {
            await phAiMemoryWorkingDelete(bookRoot, sessionId, c.id)
            await reload(); notify()
        } catch (e) { setErr(e.message || '删除失败') }
    }

    // ── 冲突卡处置（四动作） ───────────────────────────────
    const resolve = async (card, action) => {
        let note = ''
        let slot = ''
        if (action === 'coexist_layered') {
            note = await askText('分层说明（如：主线绿、回忆线灰）', '')
            if (note === null) return
        } else if (action === 'add_slot') {
            slot = await askText('槽名（如：回忆线）', '')
            if (!slot?.trim()) return
        }
        setBusyCard(card.id)
        try {
            await phAiMemoryCardResolve(bookRoot, card.id, action, note, slot)
            await reload(); notify()
        } catch (e) { setErr(e.message || '处置失败') }
        setBusyCard('')
    }

    const openCards = (tiers?.cards || []).filter(c => c.status === 'open')
    const stats = tiers?.stats || {}

    const renderItem = (m, onDel) => (
        <div key={m.id} className="wb-memitem">
            <div className="mkey">{m.key || '（未命名）'}{m.layered ? ' ⛓' : ''}</div>
            <div className="mval">{m.text}</div>
            {m.at ? <div className="mtime">{String(m.at).slice(5, 16).replace('T', ' ')}</div> : null}
            <span className="mdel" onClick={() => onDel(m)}>删除</span>
        </div>
    )

    return (
        <div className="wb-mempanel">
            {/* ⚠ 未决冲突卡（需要作者仲裁的语义矛盾） */}
            <div className="wb-memsec">
                <div className="wb-memsec-title">
                    ⚠ 未决冲突卡
                    <span className="cnt">{openCards.length} 张</span>
                </div>
                {openCards.length === 0 ? (
                    <div style={{ fontSize: 11, color: 'var(--ink-mute)', textAlign: 'center', padding: 8 }}>
                        暂无——同一设定被写成不同说法时会在这里等你仲裁
                    </div>
                ) : openCards.map(c => (
                    <div key={c.id} className="wb-memitem" style={{ display: 'block' }}>
                        <div className="mkey">{c.entity} · {c.attr}{c.scope && c.scope !== 'book' ? `（${c.scope.replace('arc:', '情节 ')}）` : ''}</div>
                        <div className="mval" style={{ color: 'var(--cinnabar-d)' }}>
                            旧说「{c.old}」 vs 新说「{c.new}」
                        </div>
                        {c.axis && c.axis !== '无显式维度差异，需作者仲裁' && (
                            <div className="mtime">线索：{c.axis}</div>
                        )}
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 4 }}>
                            {[
                                { a: 'coexist_layered', t: '分层并存' },
                                { a: 'version_rewrite', t: '新值定版' },
                                { a: 'revert', t: '撤销新值' },
                                { a: 'add_slot', t: '加槽' },
                            ].map(x => (
                                <button key={x.a} disabled={busyCard === c.id}
                                    onClick={() => resolve(c, x.a)}
                                    style={{
                                        fontSize: 10.5, padding: '2px 7px', borderRadius: 3, cursor: 'pointer',
                                        border: '1px solid var(--dai)', background: 'var(--paper)', color: 'var(--dai-dark)',
                                        opacity: busyCard === c.id ? 0.5 : 1,
                                    }}>
                                    {x.t}
                                </button>
                            ))}
                        </div>
                    </div>
                ))}
            </div>

            {/* 全局记忆（book 语义库） */}
            <div className="wb-memsec">
                <div className="wb-memsec-title">
                    全局记忆
                    <span className="cnt">{(tiers?.global || []).length} 条</span>
                </div>
                {loading && !tiers ? (
                    <div style={{ fontSize: 11, color: 'var(--ink-mute)', textAlign: 'center', padding: 8 }}>加载中…</div>
                ) : (
                    <>
                        {(tiers?.global || []).map(m => renderItem(m, delTier))}
                        {(tiers?.global || []).length === 0 && (
                            <div style={{ fontSize: 11, color: 'var(--ink-mute)', textAlign: 'center', padding: 6 }}>暂无全书记忆</div>
                        )}
                        <div className="wb-memadd" onClick={() => addTier('book')}>＋ 添加全书记忆</div>
                    </>
                )}
            </div>

            {/* 情节记忆（arc 语义库） */}
            <div className="wb-memsec">
                <div className="wb-memsec-title">
                    情节记忆
                    <span className="cnt">{(tiers?.arc || []).length} 条</span>
                </div>
                {!arcId ? (
                    <div style={{ fontSize: 11, color: 'var(--ink-mute)', textAlign: 'center', padding: 8 }}>先选情节</div>
                ) : (
                    <>
                        {(tiers?.arc || []).map(m => renderItem(m, delTier))}
                        {(tiers?.arc || []).length === 0 && (
                            <div style={{ fontSize: 11, color: 'var(--ink-mute)', textAlign: 'center', padding: 6 }}>暂无本情节记忆</div>
                        )}
                        <div className="wb-memadd" onClick={() => addTier('arc')}>＋ 添加本情节记忆</div>
                    </>
                )}
            </div>

            {/* 单对话记忆（session 工作记忆：每轮必注入，不参与召回） */}
            <div className="wb-memsec">
                <div className="wb-memsec-title">
                    单对话记忆
                    <span className="cnt">{(tiers?.session || []).length} 条</span>
                </div>
                {!sessionId ? (
                    <div style={{ fontSize: 11, color: 'var(--ink-mute)', textAlign: 'center', padding: 8 }}>
                        无活动会话——发一条消息或新建对话后可加本会话约束
                    </div>
                ) : (
                    <>
                        {(tiers?.session || []).map(c => (
                            <div key={c.id} className="wb-memitem">
                                <div className="mkey">本会话约束</div>
                                <div className="mval">{c.text}</div>
                                {c.ts ? <div className="mtime">{_ts(c.ts)}</div> : null}
                                <span className="mdel" onClick={() => delSessionCard(c)}>删除</span>
                            </div>
                        ))}
                        {(tiers?.session || []).length === 0 && (
                            <div style={{ fontSize: 11, color: 'var(--ink-mute)', textAlign: 'center', padding: 6 }}>
                                暂无——如「这一轮只改对白」这类本会话铁律
                            </div>
                        )}
                        <div className="wb-memadd" onClick={addSessionCard}>＋ 添加本会话约束</div>
                    </>
                )}
            </div>

            {err && <div style={{ fontSize: 11, color: 'var(--cinnabar-d)', padding: '4px 8px' }}>{err}</div>}
            <div style={{ fontSize: 10.5, color: 'var(--ink-mute)', textAlign: 'center', padding: '2px 0 6px' }}>
                账本 {stats.episodes ?? 0} 条 · 语义节点 {stats.nodes ?? 0} 个 · 冲突卡 {stats.cards ?? 0} 张
            </div>
        </div>
    )
}
