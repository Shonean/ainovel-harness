import { useEffect, useRef, useState } from 'react'
import {
    phAiElementAdd, phAiElementPut, phAiElementDelete,
    phAiPendingGet, phAiPendingApprove, phAiPendingReject, phAiPendingApproveAll, phAiPendingRejectAll,
    phAiMemoryGet, phAiMemoryAdd, phAiMemoryDelete,
    phAiInspireChat, fetchApiLibrary, applyApiPreset,
} from '../api.js'

/**
 * 灵感工坊（接入测试书工作台）
 * 三区布局：左 表格式卡片 + 关系面板 | 中 多机制对话 | 右 记忆 + 待审批
 * 数据源 = 主系统 8765（当前书 elements.json/arcs.json），走 /ai-creation/* 端点
 */
const K_LABEL = { c: '角色', i: '物品', s: '设定', map: '地图', arcs: '情节' }
const K_EMOJI = { c: '', i: '', s: '', map: '', arcs: '' }
const K_COLOR = { c: 'var(--cinnabar)', i: 'var(--amber)', s: 'var(--dai)', map: 'var(--dai)', arcs: 'var(--dai)' }
// 四挡位元数据：id / 图标 / 名称 / 一句话定位 / 适用场景（与后端 _inspire_mech_system 一致）
const MECHS = [
    { id: 'seed', icon: '', name: '种子推演', desc: '从「一个点」按因果链条展开', scene: '有灵感没展开 / 新书起点' },
    { id: 'analog', icon: '', name: '类比迁移', desc: '把现实结构骨架移植到小说', scene: '搭设定体系 / 要真实感' },
    { id: 'invert', icon: '', name: '约束反转', desc: '列烂套路→逐条反转→找空白', scene: '大纲卡住 / 反套路' },
    { id: 'free', icon: '', name: '自由对话', desc: '不套框架，想到什么聊什么', scene: '泛讨论 / 兜底' },
]
const KIND_TO_ELEM = { c: 'characters', i: 'items', s: 'settings', map: 'maps' }

function est(s) { return String(s == null ? '' : s) }

export default function InspireWorkspace({ bookRoot, elements, arcs, onRefresh }) {
    // 选中卡 ctx
    const [sel, setSel] = useState(null)   // {kind, id} kind 用 c/i/s
    // 对话
    const [chatHist, setChatHist] = useState([])
    const [mech, setMech] = useState('seed')
    const [chatInput, setChatInput] = useState('')
    const [chatBusy, setChatBusy] = useState(false)
    // 模型预设（api_library 文字模型）
    const [presets, setPresets] = useState([])
    const [currentId, setCurrentId] = useState(null)
    const [presetOpen, setPresetOpen] = useState(false)
    const [presetSwitching, setPresetSwitching] = useState(false)
    const presetRef = useRef(null)
    // 待审批
    const [pending, setPending] = useState([])
    const [pendingOpen, setPendingOpen] = useState(false)
    const [editingPid, setEditingPid] = useState(null)   // 正在编辑的候选 id
    const [editingText, setEditingText] = useState('')   // mem 编辑文本
    const [editingName, setEditingName] = useState('')   // card 编辑名称
    const [editingDesc, setEditingDesc] = useState('')   // card 编辑描述
    const [editingFields, setEditingFields] = useState([])  // card 编辑字段
    // 记忆
    const [memory, setMemory] = useState([])
    // 编辑建议/建卡建议（对话内）
    const [suggestions, setSuggestions] = useState([])
    const chatBoxRef = useRef(null)
    const abortRef = useRef(null)   // 当前对话请求的 AbortController（支持「停止生成」）

    // 加载 pending + memory
    const loadPending = async () => {
        if (!bookRoot) return
        const r = await phAiPendingGet(bookRoot).catch(() => ({ items: [] }))
        setPending(r.items || [])
    }
    const loadMemory = async () => {
        if (!bookRoot) return
        const r = await phAiMemoryGet(bookRoot).catch(() => ({ items: [] }))
        setMemory(r.items || [])
    }
    useEffect(() => { loadPending(); loadMemory() }, [bookRoot])

    // 模型预设：加载 + 应用（切预设即时生效，inspire_chat 用 SETTINGS.model）
    const loadPresets = async () => {
        const r = await fetchApiLibrary().catch(() => null)
        if (r) { setPresets(r.text_presets || []); setCurrentId(r.current_text_id || null) }
    }
    const applyPreset = async (id) => {
        setPresetSwitching(true)
        try {
            await applyApiPreset(id)
            setCurrentId(id); setPresetOpen(false)
        } catch (e) { alert('切换模型失败：' + e.message) }
        setPresetSwitching(false)
    }
    const currentPreset = presets.find(p => p.id === currentId)
    // 点外部关闭下拉
    useEffect(() => {
        const handler = (e) => { if (presetRef.current && !presetRef.current.contains(e.target)) setPresetOpen(false) }
        document.addEventListener('mousedown', handler)
        return () => document.removeEventListener('mousedown', handler)
    }, [])

    // 全元素列表（c/i/s 转 kind）
    const allCards = () => {
        const out = []
        for (const [kind, coll] of Object.entries(KIND_TO_ELEM)) {
            for (const e of (elements?.[coll] || [])) {
                out.push({ kind, id: e.id, name: e.name, desc: e.desc, fields: e.fields || [], relations: e.relations || [], layout: e.layout || [] })
            }
        }
        return out
    }
    const findCard = (kind, id) => allCards().find(c => c.kind === kind && c.id === id)

    // 滚动到底
    useEffect(() => {
        if (chatBoxRef.current) chatBoxRef.current.scrollTop = chatBoxRef.current.scrollHeight
    }, [chatHist])

    // ── 卡片 CRUD ──
    const addCard = async (kind, { name, desc, fields, relations }) => {
        if (!bookRoot || !name) return
        await phAiElementAdd(bookRoot, kind, { name, desc, fields, relations }).catch(() => {})
        onRefresh?.()
    }
    const updateCard = async (kind, id, patch) => {
        if (!bookRoot) return
        await phAiElementPut(bookRoot, kind, id, patch).catch(() => {})
        onRefresh?.()
    }
    const deleteCard = async (kind, id) => {
        if (!bookRoot) return
        await phAiElementDelete(bookRoot, kind, id).catch(() => {})
        onRefresh?.()
    }

    // ── 对话 ──
    const send = async () => {
        const text = chatInput.trim()
        if (!text || chatBusy || !bookRoot) return
        const controller = new AbortController()
        abortRef.current = controller
        setChatBusy(true)
        const hist = [...chatHist, { role: 'user', content: text }]
        setChatHist(hist)
        setChatInput('')
        // ctx = 选中卡
        let ctx = null
        if (sel) {
            const c = findCard(sel.kind, sel.id)
            if (c) ctx = { kind: sel.kind, name: c.name, desc: c.desc, fields: c.fields, relations: c.relations }
        }
        const r = await phAiInspireChat(bookRoot, hist, mech, ctx, controller.signal)
            .catch(err => err?.name === 'AbortError' ? { ok: false, aborted: true } : { ok: false, error: '网络错误' })
        if (r.aborted) { setChatBusy(false); abortRef.current = null; return }   // 用户点了停止
        if (r.ok) {
            setChatHist([...hist, { role: 'assistant', content: r.reply }])
            if (r.suggested_edits?.length) setSuggestions(r.suggested_edits.map(e => ({ type: 'edit', ...e })))
            if (r.pending_count > 0) loadPending()
        } else {
            setChatHist([...hist, { role: 'assistant', content: '⚠ ' + (r.error || '调用失败') }])
        }
        setChatBusy(false)
        abortRef.current = null
    }
    // 停止当前生成（中断未完成的对话请求）
    const stopChat = () => { if (abortRef.current) abortRef.current.abort() }

    // ── 渲染：表格式卡片 ──
    const renderCard = (c) => {
        return (
            <div key={c.id} className={`insp-card ${sel?.kind === c.kind && sel?.id === c.id ? 'sel' : ''}`}
                onClick={() => setSel({ kind: c.kind, id: c.id })} style={{ borderLeftColor: K_COLOR[c.kind] }}>
                <div className="insp-card-head">
                    <span className="insp-k" style={{ background: K_COLOR[c.kind] }}>{K_LABEL[c.kind]}</span>
                    <span className="insp-card-name">{K_EMOJI[c.kind]} {est(c.name)}</span>
                </div>
                <div className="insp-card-fields">
                    {(c.fields || []).length === 0 && <div className="insp-frow" style={{ color: 'var(--ink-mute)' }}>（无字段）</div>}
                    {(c.fields || []).map((f, i) => (
                        <div className="insp-frow" key={i}><span className="fn">{est(f.name)}</span><span className="fv">{est(f.value)}</span></div>
                    ))}
                </div>
                <div className="insp-card-meta">{c.relations?.length || 0} 条关系</div>
                <div className="insp-card-actions">
                    <button className="insp-mini" onClick={(e) => { e.stopPropagation(); editCardModal(c) }}>编辑</button>
                    <button className="insp-mini rel" onClick={(e) => { e.stopPropagation(); setSel({ kind: c.kind, id: c.id }); openRelModal(c) }}>关系</button>
                    <button className="insp-mini del" onClick={(e) => { e.stopPropagation(); if (confirm(`删除「${c.name}」？`)) deleteCard(c.kind, c.id) }}></button>
                </div>
            </div>
        )
    }

    // ── 卡片编辑模态 ──
    const [editCard, setEditCard] = useState(null)
    const [editFields, setEditFields] = useState([])
    const editCardModal = (c) => {
        setEditCard(c)
        setEditFields((c.fields || []).map(f => ({ name: f.name || '', value: f.value || '' })))
    }
    const saveEditCard = async () => {
        const c = editCard
        const fields = editFields.filter(f => f.name && f.value)
        await updateCard(c.kind, c.id, { name: c.name, desc: c.desc, fields, relations: c.relations })
        setEditCard(null)
    }

    // ── 建关系模态 ──
    const [relFrom, setRelFrom] = useState(null)
    const [relTo, setRelTo] = useState('')
    const [relName, setRelName] = useState('')
    const [relMult, setRelMult] = useState('1:1')
    const openRelModal = (c) => {
        setRelFrom(c)
        setRelTo('')
        setRelName('')
    }
    const saveRel = async () => {
        const from = relFrom
        const target = findCardByAny(relTo)
        if (!target || !from) return
        const relations = [...(from.relations || []), { to_kind: target.kind, to_id: target.id, name: relName || '关联', mult: relMult }]
        await updateCard(from.kind, from.id, { name: from.name, desc: from.desc, fields: from.fields, relations })
        setRelFrom(null)
    }
    const findCardByAny = (key) => allCards().find(c => `${c.kind}:${c.id}` === key)

    // ── 地图布局视图（把地图卡的 layout 画成宅邸/城池平面示意）──
    const MapLayout = ({ layout }) => {
        if (!layout || !layout.length) return null
        const find = (z) => layout.find(x => x.zone === z)
        // 城池嵌套（含 palace/imperial zone → 京城四重方城示意）
        if (find('palace') || find('imperial')) {
            return (
                <div className="map-nest">
                    {layout.map(z => (
                        <div key={z.zone} className={`map-zone nest-${z.zone}`} title={z.desc}>{z.name}</div>
                    ))}
                </div>
            )
        }
        // 宅邸平面（gate/first/second/third/rear/east/west 栅格）
        const box = (z, cls) => {
            const it = find(z)
            return it ? <div className={`map-zone ${cls}`} title={it.desc}>{it.name}</div>
                : <div className={`map-zone ${cls} empty`} />
        }
        return (
            <div className="map-house">
                <div className="map-row">{box('rear', 'main')}</div>
                <div className="map-row">{box('west', 'side')}{box('third', 'main')}{box('east', 'side')}</div>
                <div className="map-row"><div className="map-side-spacer" />{box('second', 'main')}<div className="map-side-spacer" /></div>
                <div className="map-row">{box('first', 'main')}</div>
                <div className="map-row">{box('gate', 'gate')}</div>
            </div>
        )
    }

    // ── 关系面板 ──
    const relPanel = () => {
        const c = sel ? findCard(sel.kind, sel.id) : null
        if (!c) return <div className="insp-empty">点一张素材卡<br />这里显示它的关系</div>
        const out = (c.relations || []).map(r => {
            const t = findCard(r.to_kind, r.to_id)
            return (
                <div className="insp-rel" key={`${r.to_kind}:${r.to_id}`} onClick={() => t && setSel({ kind: t.kind, id: t.id })}>
                    <span className="insp-rel-name">{est(r.name)}</span>
                    <span className="insp-rel-arrow">→</span>
                    <span className="insp-rel-target">{t ? `${K_EMOJI[t.kind]} ${est(t.name)}` : est(r.to_id)}</span>
                    <span className="insp-rel-mult">{est(r.mult)}</span>
                </div>
            )
        })
        // 入向
        const incoming = allCards().filter(x => x.kind !== c.kind || x.id !== c.id).filter(x =>
            (x.relations || []).some(r => r.to_kind === c.kind && r.to_id === c.id)
        ).map(x => {
            const r = x.relations.find(rr => rr.to_kind === c.kind && rr.to_id === c.id)
            return (
                <div className="insp-rel incoming" key={x.id}>
                    <span className="insp-rel-name">{est(r.name)}</span>
                    <span className="insp-rel-arrow">←</span>
                    <span className="insp-rel-target">{K_EMOJI[x.kind]} {est(x.name)}</span>
                    <button className="insp-rel-del" onClick={() => {
                        const rels = (x.relations || []).filter(rr => !(rr.to_kind === c.kind && rr.to_id === c.id))
                        updateCard(x.kind, x.id, { name: x.name, desc: x.desc, fields: x.fields, relations: rels })
                    }}>✕</button>
                </div>
            )
        })
        return (
            <div>
                {c.kind === 'map' && (c.layout || []).length > 0 && (
                    <div className="insp-map-block">
                        <div className="insp-panel-title">布局</div>
                        <MapLayout layout={c.layout} />
                    </div>
                )}
                <div className="insp-rel-head">{K_EMOJI[c.kind]} {est(c.name)}</div>
                <button className="insp-btn-add" onClick={() => openRelModal(c)}>建关系</button>
                {out.length === 0 && incoming.length === 0 && <div className="insp-empty">还没有关系</div>}
                {out.length > 0 && <div className="insp-rel-group">出向（→）{out}</div>}
                {incoming.length > 0 && <div className="insp-rel-group">入向（←）{incoming}</div>}
            </div>
        )
    }

    // 编辑候选后同意（mem 带 text；card 带 name/desc/fields，后端已支持）
    const saveEdit = async (p) => {
        if (p.type === 'mem') {
            const text = editingText.trim()
            if (!text) return
            await phAiPendingApprove(bookRoot, p.id, { text })
        } else {
            const fields = editingFields.filter(f => f.name && f.value)
            await phAiPendingApprove(bookRoot, p.id, { name: editingName.trim(), desc: editingDesc, fields })
        }
        setEditingPid(null)
        loadPending(); loadMemory(); onRefresh?.()
    }
    const startEdit = (p) => {
        setEditingPid(p.id)
        if (p.type === 'mem') {
            setEditingText(p.text || '')
        } else {
            setEditingName(p.name || '')
            setEditingDesc(p.desc || '')
            setEditingFields((p.fields || []).map(f => ({ name: f.name || '', value: f.value || '' })))
        }
    }

    // ── 待审批面板 ──
    const pendingPanel = () => (
        <div className={`insp-pending-overlay ${pendingOpen ? 'show' : ''}`} onClick={() => setPendingOpen(false)}>
            <div className="insp-pending-box" onClick={e => e.stopPropagation()}>
                <div className="insp-pending-head">
                    <span>候选审批</span><span className="insp-pc">{pending.length}</span>
                    <button className="insp-close" onClick={() => setPendingOpen(false)}>✕</button>
                </div>
                <div className="insp-pending-batch">
                    <button className="insp-batch all" onClick={async () => { await phAiPendingApproveAll(bookRoot); loadPending(); loadMemory(); onRefresh?.(); }}>✓ 全部同意</button>
                    <button className="insp-batch none" onClick={async () => { await phAiPendingRejectAll(bookRoot); loadPending(); }}>✕ 全部拒绝</button>
                </div>
                <div className="insp-pending-list">
                    {pending.length === 0 && <div className="insp-empty">没有待审批项 </div>}
                    {pending.map(p => (
                        <div className="insp-pend" key={p.id}>
                            <div className="insp-pend-head">
                                <span className="insp-k" style={{ background: p.type === 'mem' ? 'var(--cinnabar)' : 'var(--dai)' }}>{p.type === 'mem' ? '记忆' : '卡片'}</span>
                                {p.type === 'card' && <span>{est(p.name)}</span>}
                            </div>
                            <div className="insp-pend-body">
                                {p.type === 'mem' ? (
                                    editingPid === p.id ? (
                                        <textarea className="insp-pend-edit" value={editingText} rows={2}
                                            onChange={e => setEditingText(e.target.value)}
                                            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit(p) } }} />
                                    ) : (
                                        <div className="insp-pend-text">{est(p.text)}</div>
                                    )
                                ) : editingPid === p.id ? (
                                    <div className="insp-card-edit">
                                        <input className="insp-pend-input" value={editingName} placeholder="卡片名称"
                                            onChange={e => setEditingName(e.target.value)} />
                                        <textarea className="insp-pend-edit" value={editingDesc} rows={2} placeholder="描述"
                                            onChange={e => setEditingDesc(e.target.value)} />
                                        {editingFields.map((f, i) => (
                                            <div className="insp-fedit-row" key={i}>
                                                <input value={f.name} placeholder="字段名" onChange={e => {
                                                    const fs = [...editingFields]; fs[i] = { ...fs[i], name: e.target.value }; setEditingFields(fs)
                                                }} />
                                                <input value={f.value} placeholder="值" onChange={e => {
                                                    const fs = [...editingFields]; fs[i] = { ...fs[i], value: e.target.value }; setEditingFields(fs)
                                                }} />
                                                <button className="insp-mini del" onClick={() => setEditingFields(editingFields.filter((_, j) => j !== i))}>✕</button>
                                            </div>
                                        ))}
                                        <button className="insp-btn-add" onClick={() => setEditingFields([...editingFields, { name: '', value: '' }])}>＋ 字段</button>
                                    </div>
                                ) : (
                                    <div className="insp-pend-text">{est(p.desc)}<div className="insp-pend-reason">{est(p.reason)}</div></div>
                                )}
                            </div>
                            <div className="insp-pend-actions">
                                {editingPid === p.id ? (
                                    <>
                                        <button className="insp-pok" onClick={() => saveEdit(p)}>保存</button>
                                        <button className="insp-pno" onClick={() => setEditingPid(null)}>取消</button>
                                    </>
                                ) : (
                                    <>
                                        <button className="insp-pno" onClick={() => startEdit(p)}>编辑</button>
                                        <button className="insp-pok" onClick={async () => {
                                            await phAiPendingApprove(bookRoot, p.id, {})
                                            loadPending(); loadMemory(); onRefresh?.()
                                        }}>同意</button>
                                        <button className="insp-pno" onClick={async () => { await phAiPendingReject(bookRoot, p.id); loadPending() }}>✕ 拒绝</button>
                                    </>
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    )

    // ── 记忆面板 ──
    const [memInput, setMemInput] = useState('')
    const memoryPanel = () => (
        <div className="insp-memory">
            <div className="insp-panel-title">记忆（{memory.length}）</div>
            <div className="insp-mem-add">
                <input value={memInput} onChange={e => setMemInput(e.target.value)} placeholder="手动记一条…"
                    onKeyDown={e => { if (e.key === 'Enter' && memInput.trim()) { phAiMemoryAdd(bookRoot, memInput.trim()); setMemInput(''); loadMemory() } }} />
                <button onClick={async () => { if (memInput.trim()) { await phAiMemoryAdd(bookRoot, memInput.trim()); setMemInput(''); loadMemory() } }}>+</button>
            </div>
            {memory.length === 0 && <div className="insp-empty">AI 会自动记住设定/偏好</div>}
            {memory.map(m => (
                <div className="insp-mem-item" key={m.id}>
                    <span className="insp-mem-text">{est(m.text)}</span>
                    <button className="insp-mem-del" onClick={async () => { await phAiMemoryDelete(bookRoot, m.id); loadMemory() }}>✕</button>
                </div>
            ))}
        </div>
    )

    // ── 对话区 ──
    const chatPanel = () => (
        <div className="insp-chat">
            <div className="insp-chat-top">
                <div className="ph-api-preset-selector" ref={presetRef}>
                    <button className="insp-preset-btn" disabled={presetSwitching}
                        onClick={() => { if (!presetOpen) loadPresets(); setPresetOpen(!presetOpen) }}>
                        {presetSwitching ? '⏳' : ''} {currentPreset?.name || '模型预设'} ▾
                    </button>
                    {presetOpen && (
                        <div className="prompt-harness-menu" style={{ right: 0, left: 'auto', minWidth: 200 }}>
                            <div className="prompt-harness-item" style={{ fontWeight: 600, color: 'var(--ink-sub)', fontSize: '0.75rem', padding: '4px 12px', cursor: 'default' }}>文字模型预设</div>
                            {presets.length === 0 ? (
                                <div className="prompt-harness-item" style={{ color: 'var(--ink-mute)', cursor: 'default' }}>暂无预设</div>
                            ) : presets.map(p => (
                                <div key={p.id} className="prompt-harness-item" style={{ cursor: 'pointer' }}
                                    onClick={() => applyPreset(p.id)}>
                                    <span style={{ color: p.id === currentId ? 'var(--cinnabar)' : 'inherit', fontWeight: p.id === currentId ? 700 : 400 }}>{p.name}</span>
                                    {p.id === currentId && <span style={{ color: 'var(--cinnabar)' }}>✓</span>}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
            <div className="insp-ctx-bar">
                <span className="insp-ctx-label">对话上下文</span>
                <span className="insp-ctx-val">
                    {sel ? `${K_EMOJI[sel.kind]} ${est(findCard(sel.kind, sel.id)?.name || '')}` : '点左侧卡设为上下文'}
                </span>
            </div>
            <div className="insp-chat-body" ref={chatBoxRef}>
                {chatHist.length === 0 && <div className="insp-empty" style={{ padding: 30 }}>和 AI 讨论灵感…<br />点左侧卡片设为上下文，或直接抛想法</div>}
                {chatHist.map((m, i) => (
                    <div className={`insp-msg ${m.role}`} key={i}>
                        <div className="insp-msg-b">{est(m.content)}</div>
                        <div className="insp-msg-meta">{m.role === 'user' ? '你 · ' + mechName(mech) : '灵感工坊 · ' + mechName(mech)}</div>
                    </div>
                ))}
                {chatBusy && (
                    <div className="insp-msg assistant">
                        <div className="insp-msg-b insp-thinking"><i></i><i></i><i></i></div>
                        <div className="insp-msg-meta">灵感工坊 · {mechName(mech)} 思考中</div>
                    </div>
                )}
                {suggestions.length > 0 && (
                    <div className="insp-sug-list">
                        {suggestions.map((s, i) => (
                            <div className="insp-sug" key={i}>
                                <div className="insp-sug-head">建议改进「{est(s.card_name || s.name)}」</div>
                                <div className="insp-sug-reason">{est(s.reason)}</div>
                                <div className="insp-sug-actions">
                                    <button className="insp-pok" onClick={async () => {
                                        const c = findCard(s.kind, s.id)
                                        if (c) {
                                            await updateCard(c.kind, c.id, {
                                                name: c.name,
                                                desc: s.desc ?? c.desc,
                                                fields: s.fields || c.fields,
                                                relations: s.relations || c.relations,
                                            })
                                        }
                                        setSuggestions(suggestions.filter((_, j) => j !== i))
                                    }}>同意</button>
                                    <button className="insp-pno" onClick={() => setSuggestions(suggestions.filter((_, j) => j !== i))}>✕ 拒绝</button>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
            <div className="insp-chat-input">
                <div className="insp-mech">
                    {MECHS.map(m => (
                        <button key={m.id} className={`insp-mech-btn ${mech === m.id ? 'active' : ''}`}
                            onClick={() => setMech(m.id)}
                            title={`${m.name}：${m.desc}｜适合：${m.scene}`}>
                            {m.name}
                        </button>
                    ))}
                </div>
                {(() => { const cur = MECHS.find(m => m.id === mech); return cur && (
                    <div className="insp-mech-hint">{cur.name} · {cur.desc} · <span className="scene">适合：{cur.scene}</span></div>
                ) })()}
                <div className="insp-input-row">
                    <textarea value={chatInput} onChange={e => setChatInput(e.target.value)} rows={2}
                        placeholder="和 AI 讨论灵感…（Enter 发送，Shift+Enter 换行）"
                        onKeyDown={e => {
                            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send() }
                        }} />
                    <button className={`insp-send ${chatBusy ? 'stop' : ''}`} onClick={chatBusy ? stopChat : send}
                        title={chatBusy ? '停止生成' : '发送'}>{chatBusy ? '⏹' : ''}</button>
                </div>
            </div>
        </div>
    )

    const mechName = (m) => (MECHS.find(x => x.id === m) || {}).name || m

    return (
        <div className="insp-workspace">
            {/* 待审批轻提示 */}
            {pending.length > 0 && (
                <div className="insp-pending-toast" onClick={() => setPendingOpen(true)}>
                    <span className="insp-dot"></span><b>{pending.length}</b> 条候选待审批 → 点此查看
                </div>
            )}
            <div className="insp-grid">
                {/* 左：卡片 + 关系 */}
                <div className="insp-left">
                    <div className="insp-cards">
                        {['c', 'i', 's', 'map'].map(kind => (
                            <div className="insp-group" key={kind}>
                                <div className="insp-group-title">{K_EMOJI[kind]} {K_LABEL[kind]}
                                    <button className="insp-add" onClick={async () => {
                                        const name = prompt(`新建${K_LABEL[kind]}卡名称：`)
                                        if (name) await addCard(kind, { name })
                                    }}>+</button>
                                </div>
                                <div className="insp-cards-grid">
                                    {allCards().filter(c => c.kind === kind).map(renderCard)}
                                    {allCards().filter(c => c.kind === kind).length === 0 && <div className="insp-empty">还没有{K_LABEL[kind]}卡</div>}
                                </div>
                            </div>
                        ))}
                    </div>
                    <div className="insp-relpanel">{relPanel()}</div>
                </div>
                {/* 中：对话 */}
                <div className="insp-chat-wrap">{chatPanel()}</div>
                {/* 右：记忆 */}
                <div className="insp-right">{memoryPanel()}</div>
            </div>

            {/* 建关系模态 */}
            {relFrom && (
                <div className="insp-overlay" onClick={() => setRelFrom(null)}>
                    <div className="insp-modal" onClick={e => e.stopPropagation()}>
                        <div className="insp-modal-title">建关系：{K_EMOJI[relFrom.kind]} {est(relFrom.name)} → ?</div>
                        <label>目标素材<select value={relTo} onChange={e => setRelTo(e.target.value)}>
                            <option value="">请选择…</option>
                            {allCards().filter(c => c.kind !== relFrom.kind || c.id !== relFrom.id).map(c => (
                                <option key={`${c.kind}:${c.id}`} value={`${c.kind}:${c.id}`}>{K_EMOJI[c.kind]} {est(c.name)}</option>
                            ))}
                        </select></label>
                        <label>关系名<input value={relName} onChange={e => setRelName(e.target.value)} placeholder="持有 / 宿敌 / 属于" /></label>
                        <label>重数<select value={relMult} onChange={e => setRelMult(e.target.value)}>
                            <option value="1:1">1:1 一对一</option><option value="1:N">1:N 一对多</option>
                            <option value="N:1">N:1 多对一</option><option value="N:M">N:M 多对多</option>
                        </select></label>
                        <div className="insp-modal-actions">
                            <button className="insp-pno" onClick={() => setRelFrom(null)}>取消</button>
                            <button className="insp-pok" onClick={saveRel}>建立关系</button>
                        </div>
                    </div>
                </div>
            )}

            {/* 卡片编辑模态 */}
            {editCard && (
                <div className="insp-overlay" onClick={() => setEditCard(null)}>
                    <div className="insp-modal" onClick={e => e.stopPropagation()}>
                        <div className="insp-modal-title">编辑「{est(editCard.name)}」</div>
                        <label>名称<input value={editCard.name} onChange={e => setEditCard({ ...editCard, name: e.target.value })} /></label>
                        <label>描述<textarea rows={3} value={editCard.desc} onChange={e => setEditCard({ ...editCard, desc: e.target.value })} /></label>
                        <div className="insp-modal-title">字段</div>
                        {editFields.map((f, i) => (
                            <div className="insp-fedit-row" key={i}>
                                <input value={f.name} placeholder="字段名" onChange={e => {
                                    const fs = [...editFields]; fs[i] = { ...fs[i], name: e.target.value }; setEditFields(fs)
                                }} />
                                <input value={f.value} placeholder="值" onChange={e => {
                                    const fs = [...editFields]; fs[i] = { ...fs[i], value: e.target.value }; setEditFields(fs)
                                }} />
                                <button className="insp-mini del" onClick={() => setEditFields(editFields.filter((_, j) => j !== i))}>✕</button>
                            </div>
                        ))}
                        <button className="insp-btn-add" onClick={() => setEditFields([...editFields, { name: '', value: '' }])}>＋ 字段</button>
                        <div className="insp-modal-actions">
                            <button className="insp-pno" onClick={() => setEditCard(null)}>取消</button>
                            <button className="insp-pok" onClick={saveEditCard}>保存</button>
                        </div>
                    </div>
                </div>
            )}

            {/* 待审批 overlay */}
            {pendingPanel()}
        </div>
    )
}
