import { useState } from 'react'
import { phFragmentsPut, phNotesPut } from '../api.js'

export default function L4Editor({
    l4Edit,
    setL4Edit,
    l4SceneExpanded,
    setL4SceneExpanded,
    l4FragDraft,
    setL4FragDraft,
    l4BeatSelected,
    setL4BeatSelected,
    bookRoot,
    activeArcId,
    fragments,
    notes,
    onNotice,
    onError,
    onRefresh,
}) {
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

    const inp = {
        width: '100%',
        boxSizing: 'border-box',
        padding: 6,
        borderRadius: 6,
        border: '1px solid var(--line-soft)',
        fontSize: 13,
        marginTop: 4,
    }

    const setSc = (i, field, val) => {
        setL4Edit(arr => {
            const c = JSON.parse(JSON.stringify(arr))
            c[i][field] = val
            return c
        })
    }

    const listText = (v) => Array.isArray(v) ? v.join('\n') : ''
    const lines = (s) => s.split('\n').map(x => x.trim()).filter(Boolean)

    // 留空插入
    const insertBlank = async (sceneIdx) => {
        const sel = window.getSelection()
        const selectedText = sel?.toString()?.trim()
        if (!selectedText) {
            onError?.('请先在素材文本中选中要留空的内容')
            return
        }
        // 纯 [] 自由插入：把选中文本包成 [意图]，不建独立 blanks 记录
        const draft = l4FragDraft[sceneIdx] || { type: 'detail', content: '' }
        const newContent = (draft.content || '').replace(selectedText, `[${selectedText}]`)
        setL4FragDraft(d => ({ ...d, [sceneIdx]: { ...draft, content: newContent } }))
        onNotice?.(`已标记留空：[${selectedText}]（l5 时 LLM 按此意图填充）`)
    }

    // 素材类型快捷添加
    const addFragFromToolbar = (sceneIdx, ftype) => {
        if (ftype === 'blank') {
            insertBlank(sceneIdx)
            return
        }
        const draft = l4FragDraft[sceneIdx] || { type: 'detail', content: '' }
        const prefix = {
            dialogue: '【对白】',
            detail: '【细节】',
            action: '【动作】',
            item_card: '【物品】',
            char_card: '【角色】',
            setting_card: '【设定】',
        }[ftype] || ''
        const newType = ftype.replace('_card', '')
        setL4FragDraft(d => ({
            ...d,
            [sceneIdx]: {
                type: newType,
                content: (draft.content || '') + (draft.content ? '\n' : '') + prefix,
            },
        }))
    }

    // 保存素材到场景
    const saveFrag = async (sceneIdx) => {
        const draft = l4FragDraft[sceneIdx]
        if (!draft?.content?.trim()) {
            onError?.('素材内容不能为空')
            return
        }
        try {
            const r = await phFragmentsPut({
                book_root: bookRoot,
                arc_id: activeArcId,
                ftype: draft.type || 'detail',
                content: draft.content.trim(),
                scene_idx: sceneIdx,
            })
            if (r?.ok) {
                onNotice?.('素材已保存')
                setL4FragDraft(d => {
                    const n = { ...d }
                    delete n[sceneIdx]
                    return n
                })
                await onRefresh?.()
            }
        } catch (e) {
            onError?.(`素材保存失败：${e.message || e}`)
        }
    }

    // 段级备注
    const addBeatNote = async (sceneIdx) => {
        const sel = l4BeatSelected[sceneIdx]
        if (!sel || sel.size === 0) {
            onError?.('请先勾选要备注的节拍')
            return
        }
        const content = prompt('请输入段级备注内容：')
        if (!content?.trim()) return
        const beatStr = Array.from(sel).sort().join(',')
        try {
            const r = await phNotesPut({
                book_root: bookRoot,
                scope: `arc:${activeArcId}:scene${sceneIdx}:beats:${beatStr}`,
                content: content.trim(),
            })
            if (r?.ok) {
                onNotice?.('段级备注已保存')
                setL4BeatSelected(d => ({ ...d, [sceneIdx]: new Set() }))
                await onRefresh?.()
            }
        } catch (e) {
            onError?.(`备注保存失败：${e.message || e}`)
        }
    }

    // 切换节拍勾选
    const toggleBeat = (sceneIdx, beatIdx) => {
        setL4BeatSelected(d => {
            const prev = d[sceneIdx] || new Set()
            const next = new Set(prev)
            if (next.has(beatIdx)) next.delete(beatIdx)
            else next.add(beatIdx)
            return { ...d, [sceneIdx]: next }
        })
    }

    // 获取当前场景的素材
    const sceneFrags = (sceneIdx) => (fragments || []).filter(f => f.scene_idx === sceneIdx)
    // 获取当前场景的备注
    const sceneNotes = (sceneIdx) => (notes || []).filter(n => (n.scope || '').includes(`scene${sceneIdx}`))

    if (!l4Edit || l4Edit.length === 0) {
        return (
            <div style={{ marginTop: 8, padding: 12, textAlign: 'center', color: 'var(--ink-mute)' }}>
                暂无场景数据
            </div>
        )
    }

    return (
        <div style={{ marginTop: 8, borderTop: '1px dashed var(--line-soft)', paddingTop: 8 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
                编辑 l4 场景（每行一条）
            </div>
            {l4Edit.map((sc, i) => {
                const expanded = l4SceneExpanded === i
                const beats = sc.beats || []
                const selSet = l4BeatSelected[i] || new Set()
                return (
                    <div key={i} style={{
                        border: '1px solid var(--line)',
                        borderRadius: 6,
                        padding: 8,
                        marginBottom: 6,
                        background: 'var(--paper)',
                    }}>
                        {/* 场景头部 */}
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                            <b
                                style={{ fontSize: 12, flexShrink: 0, cursor: 'pointer' }}
                                onClick={() => setL4SceneExpanded(expanded ? null : i)}
                            >
                                {expanded ? '▼' : '▶'} 场景{i + 1}
                            </b>
                            <input
                                style={{ ...inp, marginTop: 0, flex: 1 }}
                                value={sc.name || ''}
                                onChange={e => setSc(i, 'name', e.target.value)}
                                placeholder="场景名"
                            />
                            <input
                                style={{ ...inp, marginTop: 0, flex: 1 }}
                                value={sc.environment || ''}
                                onChange={e => setSc(i, 'environment', e.target.value)}
                                placeholder="环境"
                            />
                            <button
                                style={btnStyle(false, false)}
                                onClick={() => setL4Edit(a => a.filter((_, j) => j !== i))}
                            >
                                ✕
                            </button>
                        </div>

                        {/* 动作 */}
                        <div style={{ marginTop: 4 }}>
                            <label style={{ fontSize: 11, color: 'var(--ink-sub)' }}>
                                动作 {(sc.actions || []).length > 0 && `(${(sc.actions || []).length} 条)`}
                            </label>
                            <textarea
                                value={listText(sc.actions)}
                                onChange={e => setSc(i, 'actions', lines(e.target.value))}
                                rows={2}
                                placeholder="动作（每行一条）"
                                style={inp}
                            />
                        </div>

                        {/* 对白 */}
                        <div style={{ marginTop: 4 }}>
                            <label style={{ fontSize: 11, color: 'var(--ink-sub)' }}>
                                对白 {(sc.dialogues || []).length > 0 && `(${(sc.dialogues || []).length} 轮)`}
                            </label>
                            <div style={{ marginTop: 4, border: '1px solid var(--line-soft)', borderRadius: 4, overflow: 'hidden' }}>
                                {(sc.dialogues || []).length === 0 ? (
                                    <div style={{ padding: '8px 12px', fontSize: 12, color: 'var(--ink-mute)', textAlign: 'center' }}>
                                        暂无对白，点击下方添加
                                    </div>
                                ) : (
                                    (sc.dialogues || []).map((d, di) => (
                                        <div key={di} style={{
                                            padding: '4px 8px',
                                            fontSize: 12,
                                            background: di % 2 === 0 ? 'var(--paper)' : 'var(--bg-card-2)',
                                            borderBottom: di < (sc.dialogues || []).length - 1 ? '1px solid var(--line-soft)' : 'none',
                                        }}>
                                            <span style={{ fontSize: 10, color: 'var(--ink-mute)' }}>{di + 1}. </span>
                                            {d}
                                        </div>
                                    ))
                                )}
                            </div>
                            <button
                                style={{ ...btnStyle(false, false), marginTop: 4, fontSize: 11 }}
                                onClick={() => {
                                    const nd = [...(sc.dialogues || []), '角色：台词']
                                    setSc(i, 'dialogues', nd)
                                }}
                            >
                                + 添加对白
                            </button>
                        </div>

                        {/* 冲突 */}
                        <div style={{ marginTop: 4 }}>
                            <label style={{ fontSize: 11, color: 'var(--ink-sub)' }}>冲突</label>
                            <textarea
                                value={listText(sc.conflicts)}
                                onChange={e => setSc(i, 'conflicts', lines(e.target.value))}
                                rows={2}
                                placeholder="冲突（每行一条）"
                                style={inp}
                            />
                        </div>

                        {/* 细节 */}
                        <div style={{ marginTop: 4 }}>
                            <label style={{ fontSize: 11, color: 'var(--ink-sub)' }}>细节</label>
                            <textarea
                                value={listText(sc.details)}
                                onChange={e => setSc(i, 'details', lines(e.target.value))}
                                rows={2}
                                placeholder="细节（每行一条）"
                                style={inp}
                            />
                        </div>

                        {/* 节拍（展开时显示） */}
                        {expanded && beats.length > 0 && (
                            <div style={{ marginTop: 4 }}>
                                <label style={{ fontSize: 11, color: 'var(--ink-sub)' }}>节拍</label>
                                <div style={{ marginTop: 4, padding: '6px 8px', background: 'var(--bg-card-2)', borderRadius: 4 }}>
                                    {beats.map((beat, bi) => (
                                        <label key={bi} style={{
                                            display: 'flex',
                                            alignItems: 'flex-start',
                                            gap: 6,
                                            padding: '2px 0',
                                            cursor: 'pointer',
                                            fontSize: 12,
                                        }}>
                                            <input
                                                type="checkbox"
                                                checked={selSet.has(bi)}
                                                onChange={() => toggleBeat(i, bi)}
                                                style={{ marginTop: 2 }}
                                            />
                                            <span style={{ color: 'var(--ink-mute)', flexShrink: 0 }}>{bi + 1}.</span>
                                            <span>{beat}</span>
                                        </label>
                                    ))}
                                    {selSet.size > 0 && (
                                        <button
                                            style={{ ...btnStyle(false, false), marginTop: 4, fontSize: 11 }}
                                            onClick={() => addBeatNote(i)}
                                        >
                                            段级备注
                                        </button>
                                    )}
                                </div>
                            </div>
                        )}

                        {/* 素材编辑（展开时显示） */}
                        {expanded && (
                            <div style={{ marginTop: 4 }}>
                                <label style={{ fontSize: 11, color: 'var(--ink-sub)' }}>素材</label>
                                <div style={{ marginTop: 4, padding: '6px 8px', background: 'var(--bg-card-2)', borderRadius: 4 }}>
                                    {/* 已有素材 */}
                                    {sceneFrags(i).length > 0 && (
                                        <div style={{ marginBottom: 6 }}>
                                            {sceneFrags(i).map((f, fi) => (
                                                <div key={fi} style={{ fontSize: 12, padding: '2px 0', borderBottom: '1px solid var(--line-soft)' }}>
                                                    <span style={{ color: 'var(--dai)', fontWeight: 600 }}>[{f.type}]</span> {f.content}
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                    {/* 素材输入 */}
                                    <textarea
                                        value={l4FragDraft[i]?.content || ''}
                                        onChange={e => setL4FragDraft(d => ({
                                            ...d,
                                            [i]: { type: l4FragDraft[i]?.type || 'detail', content: e.target.value },
                                        }))}
                                        rows={2}
                                        placeholder="输入素材内容..."
                                        style={{ ...inp, marginTop: 0 }}
                                    />
                                    <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
                                        {['blank', 'dialogue', 'detail', 'action'].map(ft => (
                                            <button
                                                key={ft}
                                                style={{ ...btnStyle(false, false), fontSize: 10, padding: '2px 6px' }}
                                                onClick={() => addFragFromToolbar(i, ft)}
                                            >
                                                {{ blank: '留空', dialogue: '对白', detail: '细节', action: '动作' }[ft]}
                                            </button>
                                        ))}
                                        <button
                                            style={{ ...btnStyle(false, true), fontSize: 10, padding: '2px 6px' }}
                                            onClick={() => saveFrag(i)}
                                        >
                                            保存素材
                                        </button>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* 备注（展开时显示） */}
                        {expanded && sceneNotes(i).length > 0 && (
                            <div style={{ marginTop: 4 }}>
                                <label style={{ fontSize: 11, color: 'var(--ink-sub)' }}>备注</label>
                                <div style={{ marginTop: 4, padding: '6px 8px', background: 'var(--amber-wash)', borderRadius: 4 }}>
                                    {sceneNotes(i).map((n, ni) => (
                                        <div key={ni} style={{ fontSize: 12, padding: '2px 0' }}>
                                            {n.content}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                )
            })}
        </div>
    )
}
