import { useEffect, useRef, useState } from 'react'

// 提案可编辑字段（按工具/args 推断；返回 null = 无内容可改，只确认/取消）
const getPendingFields = (pc) => {
    if (!pc || !pc.args || typeof pc.args !== 'object') return null
    const args = pc.args
    const t = pc.tool
    const f = []
    if (t === 'update_element') {
        const lbl = args.field === 'desc' ? '新描述' : args.field === 'name' ? '新名称' : `内容（${args.field || ''}）`
        f.push({ key: 'value', label: lbl, multi: true, def: args.value ?? args.desc ?? '' })
    } else if (t === 'add_element') {
        f.push({ key: 'name', label: '名称', multi: false, def: args.name || '' })
        f.push({ key: 'desc', label: '描述', multi: true, def: args.desc || '' })
    } else if (t === 'new_arc') {
        f.push({ key: 'l1', label: 'l1 一句话剧情', multi: true, def: args.l1 || '' })
        f.push({ key: 'n_chapters', label: '章数', multi: false, def: args.n_chapters || 1 })
    } else if (t === 'set_level') {
        f.push({ key: 'text', label: `${args.level || '该级'} 内容`, multi: true, def: args.text || '' })
    } else if (t === 'modify_level') {
        f.push({ key: 'instruction', label: `改写指令（${args.level || ''}）`, multi: true, def: args.instruction || '' })
    } else if (t === 'remember') {
        f.push({ key: 'content', label: '记忆内容', multi: true, def: args.content || '' })
    }
    return f.length ? f : null
}

/**
 * 共享对话窗口（Codex 式：工具事件流 + 待确认动作）。
 * 大纲/正文页签都用它；对话状态由父组件持有（切页保留）。
 *
 * Props:
 *  - messages: [{role, content, tool_events?}]
 *  - onSend: (text) => void
 *  - busy: boolean（对话请求中）
 *  - disabled: boolean（未选情节时禁用发送）
 *  - pendingConfirm: {summary, detail} | null（finalize 待确认）
 *  - onConfirm / onCancel: 确认/取消待确认动作
 *  - compact: boolean（新工作台右栏用：隐藏自带输入框，空状态显示引导卡片）
 */
export default function ChatWindow({ messages, onSend, busy, disabled, pendingConfirm, onConfirm, onCancel, compact, placeholder, inputValue, onInputChange, historyKey = 'ainovel-chat-history' }) {
    const [internalInput, setInternalInput] = useState('')
    const boxRef = useRef(null)
    // 使用外部控制的input或内部状态
    const input = inputValue !== undefined ? inputValue : internalInput
    const setInput = onInputChange || setInternalInput
    // 提案可编辑内容草稿（同意前可改，同意后应用改过的版本）
    const [pendDraft, setPendDraft] = useState({})
    // 输入历史（上/下箭头翻历史；localStorage 持久化，按书/场景区分用 historyKey）
    const histRef = useRef(null)
    const histIdxRef = useRef(-1)
    const draftBeforeHistRef = useRef('')

    useEffect(() => {
        if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
    }, [messages])

    // 打开提案时初始化可编辑草稿
    useEffect(() => {
        if (!pendingConfirm) { setPendDraft({}); return }
        const d = {}
        const pf = getPendingFields(pendingConfirm)
        ;(pf || []).forEach(x => { d[x.key] = x.def })
        setPendDraft(d)
    }, [pendingConfirm])

    // 懒加载输入历史（localStorage，跨会话保留最近 100 条）
    const loadHist = () => {
        if (histRef.current === null) {
            try { histRef.current = JSON.parse(localStorage.getItem(historyKey) || '[]') }
            catch { histRef.current = [] }
            histIdxRef.current = -1
        }
        return histRef.current
    }
    const saveHist = (h) => {
        histRef.current = h
        try { localStorage.setItem(historyKey, JSON.stringify(h.slice(-100))) } catch { /* 隐私模式等 */ }
    }

    const send = () => {
        const text = input.trim()
        if (!text || busy || disabled) return
        onSend(text)
        setInput('')
        // 入历史（去连续重复，重置浏览指针）
        const h = loadHist()
        if (h[h.length - 1] !== text) saveHist([...h, text])
        histIdxRef.current = -1
        draftBeforeHistRef.current = ''
    }

    // 上/下箭头翻历史：空输入时（光标在首行）上箭头取上一条，下箭头回退
    const onHistKey = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); return }
        const ta = e.target
        const atTop = ta.selectionStart === 0 && ta.selectionEnd === 0
        const atBottom = ta.selectionStart === ta.value.length
        if (e.key === 'ArrowUp' && atTop) {
            const h = loadHist()
            if (!h.length) return
            e.preventDefault()
            if (histIdxRef.current === -1) draftBeforeHistRef.current = input
            histIdxRef.current = histIdxRef.current === -1
                ? h.length - 1
                : Math.max(0, histIdxRef.current - 1)
            setInput(h[histIdxRef.current] || '')
        } else if (e.key === 'ArrowDown' && atBottom && histIdxRef.current !== -1) {
            const h = loadHist()
            e.preventDefault()
            histIdxRef.current += 1
            if (histIdxRef.current >= h.length) {
                histIdxRef.current = -1
                setInput(draftBeforeHistRef.current)
            } else {
                setInput(h[histIdxRef.current])
            }
        }
    }

    const preStyle = { whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.7, margin: 0 }

    // 确认按钮文字（根据工具类型动态显示）
    const confirmLabel = (() => {
        const t = pendingConfirm?.tool
        if (t === 'finalize') return '确认保存本章到书'
        if (t === 'finish_arc') return '确认标记情节完成'
        if (t === 'save_ladder') return '确认保存阶梯'
        if (t === 'save_element') return '确认保存元素'
        if (t === 'add_memory') return '确认添加记忆'
        return '同意执行'
    })()

    // 快捷提问（空状态引导）
    const quickPrompts = [
        { icon: '', text: '润色一下正文', action: '润色一下正文' },
        { icon: '', text: '评一下这章质量', action: '评一下这章' },
        { icon: '⚠', text: '检查元素污染', action: '查下污染' },
        { icon: '', text: '搜索相关参考', action: '搜点参考资料' },
        { icon: '', text: '帮我想个转折', action: '帮我想个情节转折' },
        { icon: '', text: '扩写这段场景', action: '扩写一下当前场景' },
    ]

    if (compact) {
        // 新工作台右栏版：占满空间，空状态引导卡片
        const empty = messages.length === 0
        return (
            <div ref={boxRef} className="cw-compact" style={{
                flexShrink: 0,
                display: 'flex',
                flexDirection: 'column',
                gap: 10,
                padding: '2px 2px 2px 0',
            }}>
                {empty && <div style={{ height: 8 }} />}

                {!empty && messages.map((m, i) => (
                    <div key={i} style={{ marginBottom: 4 }}>
                        <div style={{
                            fontSize: 10.5,
                            fontWeight: 600,
                            color: m.role === 'user' ? 'var(--dai-dark)' : 'var(--green-dark)',
                            marginBottom: 3,
                            fontFamily: 'var(--font-sans)',
                        }}>
                            {m.role === 'user' ? '你' : 'AI'}
                        </div>
                        {Array.isArray(m.tool_events) && m.tool_events.length > 0 && (
                            <div style={{ margin: '4px 0 6px', padding: 6, background: 'var(--cinnabar-wash)', border: '1px solid var(--cinnabar-border)', borderRadius: 4, fontSize: 11 }}>
                                {m.tool_events.map((ev, j) => (
                                    <div key={j} style={{ marginBottom: 2 }}>
                                        {ev.summary}
                                        {ev.detail && (
                                            <details>
                                                <summary style={{ color: 'var(--cinnabar)', cursor: 'pointer', fontSize: 10.5 }}>查看详情</summary>
                                                <pre style={{ ...preStyle, fontSize: 10.5, color: 'var(--ink-sub)', background: 'var(--paper)', padding: 6, borderRadius: 3, maxHeight: 140, overflowY: 'auto', marginTop: 4 }}>{ev.detail}</pre>
                                            </details>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}
                        <pre style={{ ...preStyle, color: 'var(--ink)', fontSize: 12.5 }}>{m.content}</pre>
                    </div>
                ))}

                {pendingConfirm && (
                    <div style={{
                        padding: 10,
                        borderRadius: 4,
                        border: '1px solid var(--cinnabar-border)',
                        background: 'var(--cinnabar-wash)',
                        flexShrink: 0,
                    }}>
                        <div style={{ fontSize: 11.5, color: 'var(--ink)', marginBottom: 8, fontWeight: 600 }}>
                            {pendingConfirm.summary}
                        </div>
                        <div style={{ fontSize: 10.5, color: 'var(--ink-sub)', marginBottom: 8 }}>
                            {pendingConfirm.detail}
                        </div>
                        <div style={{ display: 'flex', gap: 6 }}>
                            <button
                                onClick={onConfirm}
                                style={{
                                    padding: '5px 10px', borderRadius: 3,
                                    border: '1px solid var(--cinnabar-d)', background: 'var(--cinnabar-d)',
                                    color: 'var(--paper-raised)', cursor: 'pointer', fontSize: 11,
                                }}>
                                {confirmLabel}
                            </button>
                            <button
                                onClick={onCancel}
                                style={{
                                    padding: '5px 10px', borderRadius: 3,
                                    border: '1px solid var(--line-soft)', background: 'var(--paper-raised)',
                                    color: 'var(--ink)', cursor: 'pointer', fontSize: 11,
                                }}>
                                取消
                            </button>
                        </div>
                    </div>
                )}

                {/* 输入框（compact 模式底部常驻） */}
                <div style={{
                    display: 'flex', gap: 6, flexShrink: 0,
                    padding: '6px 2px 4px 0', borderTop: '1px solid var(--line-soft)',
                    marginTop: 'auto',
                }}>
                    <textarea
                        value={input}
                        onChange={e => setInput(e.target.value)}
                        rows={5}
                        placeholder={placeholder || (disabled ? '请先选择讨论对象…' : '说点什么…（Enter 发送，Shift+Enter 换行）')}
                        onKeyDown={onHistKey}
                        disabled={disabled || busy}
                        style={{
                            flex: 1, boxSizing: 'border-box', padding: '8px 10px',
                            borderRadius: 4, border: '1px solid var(--line-soft)',
                            fontSize: 12.5, resize: 'none',
                            background: 'var(--paper)', color: 'var(--ink)',
                            outline: 'none', lineHeight: 1.6,
                            minHeight: 100, maxHeight: 200,
                            fontFamily: 'inherit',
                            opacity: disabled ? 0.5 : 1,
                        }}
                    />
                    <button
                        onClick={send}
                        disabled={busy || !input.trim() || disabled}
                        style={{
                            padding: '0 14px', borderRadius: 4,
                            border: '1px solid var(--dai)', background: 'var(--dai)',
                            color: 'var(--paper-raised)',
                            cursor: (busy || !input.trim() || disabled) ? 'not-allowed' : 'pointer',
                            opacity: (busy || !input.trim() || disabled) ? 0.5 : 1,
                            fontSize: 12, height: 100, alignSelf: 'flex-end',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            flexShrink: 0,
                        }}>
                        {busy ? '…' : '发送'}
                    </button>
                </div>
            </div>
        )
    }

    // 旧版完整模式（带输入框，留作兼容）
    return (
        <div className="section-block" style={{ borderLeft: '3px solid var(--cinnabar)', marginTop: 10 }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>与 AI 对话（工具循环：预检索 + 可执行动作）</div>
            <div ref={boxRef} style={{ maxHeight: 220, overflowY: 'auto', border: '1px solid var(--line)', borderRadius: 6, padding: 8, marginBottom: 8 }}>
                {messages.length === 0 && (
                    <div style={{ color: 'var(--ink-mute)', fontSize: 13 }}>
                        可以问：这段转折是不是太突兀？/ 没思路帮我搜点参考 / 评点当前内容…
                    </div>
                )}
                {messages.map((m, i) => (
                    <div key={i} style={{ marginBottom: 6 }}>
                        <b style={{ fontSize: 12, color: m.role === 'user' ? 'var(--dai)' : 'var(--green)' }}>
                            {m.role === 'user' ? '你' : 'AI'}：
                        </b>
                        {Array.isArray(m.tool_events) && m.tool_events.length > 0 && (
                            <div style={{ margin: '4px 0', padding: 6, background: 'var(--cinnabar-wash)', borderRadius: 6, fontSize: 12 }}>
                                {m.tool_events.map((ev, j) => (
                                    <div key={j} style={{ marginBottom: 2 }}>
                                        {ev.summary}
                                        {ev.detail && (
                                            <details>
                                                <summary style={{ color: 'var(--cinnabar)', cursor: 'pointer', fontSize: 11 }}>查看详情</summary>
                                                <pre style={{ ...preStyle, fontSize: 11, color: 'var(--ink-sub)', background: 'var(--paper)', padding: 6, borderRadius: 4, maxHeight: 140, overflowY: 'auto' }}>{ev.detail}</pre>
                                            </details>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}
                        <pre style={{ ...preStyle, color: 'var(--ink)' }}>{m.content}</pre>
                    </div>
                ))}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
                <textarea value={input} onChange={e => setInput(e.target.value)} rows={2}
                    placeholder={placeholder || (disabled ? '先在大纲页签创建/选择情节，再跟 AI 讨论' : '跟 AI 讨论当前情节内容、要修改意见、没思路搜参考…（Enter 发送）')}
                    onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
                    style={{ flex: 1, boxSizing: 'border-box', padding: 8, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13 }} />
                <button style={{
                    padding: '0 14px', borderRadius: 6, border: '1px solid var(--dai)', background: 'var(--dai)',
                    color: 'var(--paper-raised)', cursor: busy || !input.trim() || disabled ? 'not-allowed' : 'pointer',
                    opacity: busy || !input.trim() || disabled ? 0.5 : 1, fontSize: 13,
                    alignSelf: 'stretch', display: 'flex', alignItems: 'center', justifyContent: 'center',
                }} disabled={busy || !input.trim() || disabled} onClick={send}>
                    {busy ? '…' : '发送'}
                </button>
            </div>
            {pendingConfirm && (() => {
                const pendingFields = getPendingFields(pendingConfirm)
                const edited = pendingFields && pendingFields.some(f => {
                    const v = pendDraft[f.key]
                    return v !== undefined && String(v) !== String(f.def)
                })
                return (
                <div style={{ marginTop: 8, padding: 8, borderRadius: 6, border: '1px solid var(--cinnabar-border)', background: 'var(--cinnabar-wash)' }}>
                    <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 6 }}>
                        助手想执行：<b style={{ color: 'var(--ink)' }}>{pendingConfirm.summary}</b>
                        {pendingConfirm.detail && pendingConfirm.detail !== '用户同意后执行。' && (
                            <div style={{ marginTop: 2 }}>{pendingConfirm.detail}</div>
                        )}
                    </div>
                    {pendingFields && (
                        <div style={{ marginTop: 4, marginBottom: 8, padding: 8, border: '1px dashed var(--dai)', borderRadius: 6, background: 'var(--paper)' }}>
                            <div style={{ fontSize: 11, color: 'var(--ink-sub)', marginBottom: 4 }}>
                                可在同意前修改以下内容{edited ? '（已改，将应用你的版本）' : ''}
                            </div>
                            {pendingFields.map(f => (
                                <div key={f.key} style={{ marginBottom: 6 }}>
                                    <div style={{ fontSize: 11, color: 'var(--ink-sub)', marginBottom: 2 }}>{f.label}</div>
                                    {f.multi ? (
                                        <textarea value={pendDraft[f.key] ?? f.def}
                                            onChange={e => setPendDraft(p => ({ ...p, [f.key]: e.target.value }))}
                                            rows={3}
                                            style={{ width: '100%', boxSizing: 'border-box', fontFamily: 'inherit', fontSize: 12.5, lineHeight: 1.6, padding: 6, border: '1px solid var(--line-soft)', borderRadius: 6 }} />
                                    ) : (
                                        <input value={pendDraft[f.key] ?? f.def}
                                            onChange={e => setPendDraft(p => ({ ...p, [f.key]: e.target.value }))}
                                            style={{ width: '100%', boxSizing: 'border-box', fontFamily: 'inherit', fontSize: 12.5, padding: 5, border: '1px solid var(--line-soft)', borderRadius: 6 }} />
                                    )}
                                </div>
                            ))}
                        </div>
                    )}
                    <button style={{
                        padding: '6px 12px', borderRadius: 6, border: '1px solid var(--cinnabar-d)', background: 'var(--cinnabar-d)', color: 'var(--paper-raised)',
                        cursor: 'pointer', fontSize: 13,
                    }} onClick={() => onConfirm(pendingFields ? { ...pendDraft } : undefined)}>
                        {confirmLabel}
                    </button>
                    <button style={{
                        padding: '6px 12px', borderRadius: 6, border: '1px solid var(--line-soft)', background: 'var(--paper-raised)', color: 'var(--ink)',
                        cursor: 'pointer', fontSize: 13, marginLeft: 8,
                    }} onClick={onCancel}>
                        取消
                    </button>
                </div>
                )
            })()}
        </div>
    )
}
