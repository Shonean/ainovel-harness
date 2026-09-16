/**
 * 量产书创建流程（P2）：快速表单 → 生成元素卡（不自动批准）→ 卡片确认。
 *
 * 与精品流程（CreateBookPage）区别：无对话助手，表单一次成型；
 * 卡片逐张可改可驳回，或一键全部采纳后入库进入工作台。
 *
 * 设计原型：docs/计划稿/2026-09-11-量产模式-UI原型.html（03 快速表单 / 04 卡片确认）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
    createProject, registerProject,
    phAiInitQuick, phAiOptimizeStatus,
    phAiPendingGet, phAiPendingApprove, phAiPendingReject, phAiPendingApproveAll,
} from '../api.js'

const KIND_LABEL = { characters: '角色', items: '物品', settings: '设定' }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const inputStyle = {
    width: '100%', boxSizing: 'border-box', padding: '8px 10px', fontSize: 13,
    border: '1px solid var(--line)', borderRadius: 6,
    background: 'var(--paper)', color: 'var(--ink)',
}
const labelStyle = { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13, fontWeight: 600 }

function Field({ label, value, onChange, placeholder, full }) {
    return (
        <label style={{ ...labelStyle, gridColumn: full ? '1 / -1' : 'auto' }}>
            {label}
            <input style={inputStyle} value={value} onChange={onChange} placeholder={placeholder} />
        </label>
    )
}

function StepNav({ step }) {
    const items = [
        { key: 'form', label: '1 基础信息' },
        { key: 'cards', label: '2 卡片确认' },
        { key: 'roadmap', label: '3 路线图' },
    ]
    const idx = step === 'form' || step === 'generating' ? 0 : step === 'cards' ? 1 : 2
    return (
        <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
            {items.map((it, i) => (
                <span key={it.key} style={{
                    padding: '4px 12px', borderRadius: 999, fontSize: 12.5,
                    border: '1px solid var(--line)',
                    background: i === idx ? 'var(--dai-wash, rgba(61,107,73,.12))' : 'transparent',
                    color: i === idx ? 'var(--dai)' : 'var(--ink-sub)',
                    fontWeight: i === idx ? 700 : 400,
                }}>{it.label}</span>
            ))}
        </div>
    )
}

export default function MassCreateFlow() {
    const navigate = useNavigate()
    const [step, setStep] = useState('form') // form | generating | cards
    const [form, setForm] = useState({
        name: '', genre: '', protagonist: '', style: '',
        oneLiner: '', targetChapters: 30, chaptersPerArc: 3,
    })
    const [bookRoot, setBookRoot] = useState('')
    const [msgs, setMsgs] = useState([])
    const [error, setError] = useState('')
    const [cards, setCards] = useState([])
    const [editingId, setEditingId] = useState('')
    const [draft, setDraft] = useState({ name: '', desc: '' })
    const [busy, setBusy] = useState(false)
    const aliveRef = useRef(true)
    useEffect(() => () => { aliveRef.current = false }, [])

    const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))

    const refreshCards = useCallback(async (root) => {
        const r = await phAiPendingGet(root)
        setCards((r?.items || []).filter((c) => c && c.type === 'card'))
    }, [])

    const handleCreate = useCallback(async () => {
        if (!form.name.trim()) { setError('请先填书名'); return }
        setBusy(true)
        setError('')
        setStep('generating')
        setMsgs(['正在创建书目录…'])
        try {
            const res = await createProject(form.name.trim(), {
                project: {
                    title: form.name.trim(), genre: form.genre,
                    target_chapters: Number(form.targetChapters) || 0,
                },
            }, null, null, true, 'mass')
            const root = res?.project_root
            if (!root) throw new Error('建书失败：未返回书目录')
            setBookRoot(root)
            setMsgs((m) => [...m, '正在生成元素卡与设定集…'])
            const { task_id } = await phAiInitQuick(root, {
                title: form.name.trim(), genre: form.genre,
                protagonist: form.protagonist, style: form.style,
                one_liner: form.oneLiner,
                target_chapters: Number(form.targetChapters) || 0,
            })
            if (!task_id) throw new Error('初始化任务未启动')
            let done = false
            for (let i = 0; i < 400; i++) {
                await sleep(1500)
                if (!aliveRef.current) return
                const st = await phAiOptimizeStatus(task_id)
                const pm = st?.progress?.messages
                if (Array.isArray(pm) && pm.length) setMsgs(pm)
                if (st?.status === 'done') { done = true; break }
                if (st?.status === 'failed' || st?.status === 'cancelled') {
                    throw new Error(st?.error || '初始化失败')
                }
            }
            if (!done) throw new Error('初始化超时（10 分钟）')
            await refreshCards(root)
            setStep('cards')
        } catch (e) {
            setError(e?.message || String(e))
            setStep('form')
        } finally {
            setBusy(false)
        }
    }, [form, refreshCards])

    const approve = useCallback(async (p, edits = {}) => {
        setBusy(true)
        try {
            await phAiPendingApprove(bookRoot, p.id, edits)
            setEditingId('')
            await refreshCards(bookRoot)
        } catch (e) {
            setError(e?.message || String(e))
        } finally {
            setBusy(false)
        }
    }, [bookRoot, refreshCards])

    const reject = useCallback(async (p) => {
        setBusy(true)
        try {
            await phAiPendingReject(bookRoot, p.id)
            await refreshCards(bookRoot)
        } catch (e) {
            setError(e?.message || String(e))
        } finally {
            setBusy(false)
        }
    }, [bookRoot, refreshCards])

    const adoptAll = useCallback(async () => {
        setBusy(true)
        setError('')
        try {
            await phAiPendingApproveAll(bookRoot)
            await registerProject(bookRoot)
            navigate('/mass', { replace: true })
        } catch (e) {
            setError(e?.message || String(e))
            setBusy(false)
        }
    }, [bookRoot, navigate])

    const enterLater = useCallback(async () => {
        setBusy(true)
        setError('')
        try {
            await registerProject(bookRoot)
            navigate('/ai-creation', { replace: true })
        } catch (e) {
            setError(e?.message || String(e))
            setBusy(false)
        }
    }, [bookRoot, navigate])

    const settingCards = cards.filter((c) => c.kind === 'settings')
    const elementCards = cards.filter((c) => c.kind !== 'settings')

    const renderCard = (p) => {
        const editing = editingId === p.id
        return (
            <div key={p.id} style={{
                border: '1px solid var(--line)', borderRadius: 8, padding: 12,
                background: 'var(--paper-raised)', marginBottom: 10,
            }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    {editing ? (
                        <input
                            style={{ ...inputStyle, flex: 1, fontWeight: 700 }}
                            value={draft.name}
                            onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                        />
                    ) : (
                        <span style={{ fontSize: 14, fontWeight: 700 }}>{p.name || '（未命名）'}</span>
                    )}
                    <span style={{ fontSize: 11, color: 'var(--ink-mute)', border: '1px solid var(--line)', borderRadius: 999, padding: '1px 8px' }}>
                        {KIND_LABEL[p.kind] || p.kind || '卡片'}
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--amber, #a06a1f)', border: '1px dashed var(--line)', borderRadius: 999, padding: '1px 8px' }}>待审</span>
                </div>
                {editing ? (
                    <textarea
                        style={{ ...inputStyle, minHeight: 70, resize: 'vertical', fontFamily: 'inherit' }}
                        value={draft.desc}
                        onChange={(e) => setDraft((d) => ({ ...d, desc: e.target.value }))}
                    />
                ) : (
                    <div style={{ fontSize: 13, color: 'var(--ink-sub)', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
                        {p.desc || '（无描述）'}
                    </div>
                )}
                <div style={{ display: 'flex', gap: 8, marginTop: 10, justifyContent: 'flex-end' }}>
                    {editing ? (
                        <>
                            <button className="btn btn-small ghost" disabled={busy} onClick={() => setEditingId('')}>取消编辑</button>
                            <button className="btn btn-small btn-green" disabled={busy} onClick={() => approve(p, { name: draft.name, desc: draft.desc })}>保存并采纳</button>
                        </>
                    ) : (
                        <>
                            <button className="btn btn-small ghost" disabled={busy} onClick={() => { setEditingId(p.id); setDraft({ name: p.name || '', desc: p.desc || '' }) }}>编辑</button>
                            <button className="btn btn-small btn-green" disabled={busy} onClick={() => approve(p)}>采纳</button>
                            <button className="btn btn-small ghost" disabled={busy} onClick={() => reject(p)}>驳回</button>
                        </>
                    )}
                </div>
            </div>
        )
    }

    return (
        <div className="wizard-page" style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
            <header style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px',
                paddingRight: 276, borderBottom: '1px solid var(--line)',
                background: 'var(--paper-raised)', flexShrink: 0,
            }}>
                <button className="btn btn-small" onClick={() => navigate('/', { replace: true })}>⇦ 返回书架</button>
                <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>新建量产书</h1>
                <span className="badge badge-amber">量产</span>
                <div style={{ flex: 1 }} />
            </header>

            <main style={{ flex: 1, overflowY: 'auto', padding: '20px 24px', maxWidth: 980, width: '100%', margin: '0 auto', boxSizing: 'border-box' }}>
                <StepNav step={step} />

                {step === 'form' && (
                    <div style={{ border: '1px solid var(--line)', borderRadius: 10, padding: 16, background: 'var(--paper-raised)' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                            <Field label="书名" value={form.name} onChange={set('name')} placeholder="如：茌山一中·原料书" />
                            <Field label="题材" value={form.genre} onChange={set('genre')} placeholder="如：中式恐怖 / 悬疑" />
                            <Field label="主角名" value={form.protagonist} onChange={set('protagonist')} placeholder="如：林川" />
                            <Field label="文风（可选）" value={form.style} onChange={set('style')} placeholder="如：压抑、悬疑、循环感" />
                            <Field label="预计章数" value={form.targetChapters} onChange={set('targetChapters')} />
                            <Field label="每情节章数" value={form.chaptersPerArc} onChange={set('chaptersPerArc')} />
                            <label style={{ ...labelStyle, gridColumn: '1 / -1' }}>
                                一句话简介
                                <textarea
                                    style={{ ...inputStyle, minHeight: 80, resize: 'vertical', fontFamily: 'inherit' }}
                                    value={form.oneLiner}
                                    onChange={set('oneLiner')}
                                    placeholder="用一两句话说明故事：谁，在什么地方，遇到什么，代价是什么。"
                                />
                            </label>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                            <button className="btn ghost" onClick={() => navigate('/')} disabled={busy}>返回</button>
                            <button className="btn btn-blue" onClick={handleCreate} disabled={busy}>创建并生成设定卡</button>
                        </div>
                    </div>
                )}

                {step === 'generating' && (
                    <div style={{ border: '1px solid var(--line)', borderRadius: 10, padding: 20, background: 'var(--paper-raised)', textAlign: 'center' }}>
                        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 10 }}>正在生成元素卡与设定集…</div>
                        {msgs.map((m, i) => (
                            <div key={i} style={{ fontSize: 13, color: 'var(--ink-sub)', lineHeight: 1.8 }}>{m}</div>
                        ))}
                        <div style={{ fontSize: 12, color: 'var(--ink-mute)', marginTop: 12 }}>通常 30-90 秒；请勿关闭页面</div>
                    </div>
                )}

                {step === 'cards' && (
                    <>
                        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 12 }}>
                            <h3 style={{ margin: 0, fontSize: 15 }}>初始化 · 卡片确认</h3>
                            <span style={{ fontSize: 12.5, color: 'var(--ink-sub)' }}>
                                共 {cards.length} 张（设定 {settingCards.length} · 元素 {elementCards.length}）——可逐张改，也可一键全部采纳
                            </span>
                        </div>
                        {cards.length === 0 ? (
                            <div style={{ padding: 20, textAlign: 'center', color: 'var(--ink-mute)' }}>
                                没有待审卡片（可直接进入工作台）
                            </div>
                        ) : (
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, alignItems: 'start' }}>
                                <div>
                                    <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8, color: 'var(--ink-sub)' }}>
                                        设定卡（{settingCards.length} 张）
                                    </div>
                                    {settingCards.map(renderCard)}
                                </div>
                                <div>
                                    <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8, color: 'var(--ink-sub)' }}>
                                        元素卡（{elementCards.length} 张）
                                    </div>
                                    {elementCards.map(renderCard)}
                                </div>
                            </div>
                        )}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                            <button className="btn ghost" disabled={busy} onClick={enterLater}>稍后确认，先进入工作台</button>
                            <button className="btn btn-blue" disabled={busy || cards.length === 0} onClick={adoptAll}>
                                一键全部采纳，规划路线图
                            </button>
                        </div>
                    </>
                )}

                {error && (
                    <div style={{ marginTop: 14, padding: '10px 14px', background: 'var(--cinnabar-wash)', color: 'var(--cinnabar-d)', borderRadius: 8, fontSize: 13 }}>
                        {error}
                    </div>
                )}
            </main>
        </div>
    )
}
