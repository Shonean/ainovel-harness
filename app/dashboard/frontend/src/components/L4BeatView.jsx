import { useState } from 'react'

/**
 * L4 节拍叙事流视图
 *
 * 核心设计：
 *  - 按叙事顺序排列（场景 → 节拍①②③ → 每拍内混排动作/对白/细节/冲突）
 *  - 干净无插入按钮，点击行出现光标，插入操作全在右栏
 *  - 编辑仍在原位（双击或点击编辑按钮）
 *
 * Props:
 *  - scenes: l4场景数组
 *  - onScenesChange: (newScenes) => void（编辑场景时调用）
 *  - cursor: { sceneIdx, beatIdx, rowIdx, kind } | null  当前光标位置
 *  - onCursorChange: (cursor) => void  点击行时设置光标
 *  - fragments: 素材数组（用于显示留空槽）
 *  - notes: 备注数组
 *  - flatElements: 扁平元素数组（用于出场元素显示）
 *  - btnStyle: 按钮样式函数 (disabled, primary) => styleObj
 *  - busy: boolean 保存中
 *  - onSave: () => void 保存整个l4
 *  - onAddScene: () => void 添加场景
 *  - onAddBeat: (sceneIdx) => void 添加节拍
 */
export default function L4BeatView({
    scenes,
    onScenesChange,
    cursor,
    onCursorChange,
    fragments = [],
    notes = [],
    flatElements = [],
    btnStyle,
    busy,
    onSave,
    onAddScene,
    onAddBeat,
}) {
    const [editMode, setEditMode] = useState(null) // { sceneIdx, field }  正在编辑哪个字段
    const [beatExpanded, setBeatExpanded] = useState({}) // { sceneIdx_beatIdx: true } 展开详情

    const setSc = (i, field, val) => {
        const next = JSON.parse(JSON.stringify(scenes))
        next[i][field] = val
        onScenesChange(next)
    }

    const sceneFrags = (i) => fragments.filter(f => f.scene_idx === i)
    const sceneNotes = (i) => notes.filter(n => (n.scope || '').includes(`scene${i}`))

    // 从素材文本提取留空槽
    const getBlanksFromText = (i) => {
        const blanks = []
        sceneFrags(i).forEach(f => {
            const txt = String(f.content || f.text || '')
            const re = /\[([^\[\]]+)\]/g
            let m
            while ((m = re.exec(txt)) !== null) blanks.push(m[1].trim())
        })
        return blanks
    }

    // 从场景内容构建节拍叙事流
    // 每拍包含：{ beatText, rows: [{ kind, text, idx, dlgIdx? }] }
    const buildBeatFlow = (sc, sceneIdx) => {
        const beats = sc.beats || []
        const actions = sc.actions || []
        const dialogues = sc.dialogues || []
        const conflicts = sc.conflicts || []
        const details = sc.details || []

        if (beats.length === 0) {
            // 无节拍时，所有内容归到一个"无标题"拍
            const rows = []
            actions.forEach((t, i) => rows.push({ kind: 'action', text: t, idx: i }))
            dialogues.forEach((d, i) => rows.push({ kind: 'dialogue', text: d, idx: i }))
            conflicts.forEach((t, i) => rows.push({ kind: 'conflict', text: t, idx: i }))
            details.forEach((t, i) => rows.push({ kind: 'detail', text: t, idx: i }))
            return [{ beatText: null, rows, beatIdx: -1 }]
        }

        // 有节拍：每个节拍分配内容
        // 简化策略：按数量平均分配动作/对白/细节/冲突到各节拍
        return beats.map((beatText, bi) => {
            const rows = []
            const _slice = (arr, per, extra) => {
                const start = bi * per + Math.min(bi, extra)
                const end = start + per + (bi < extra ? 1 : 0)
                return arr.slice(start, end)
            }
            const n = beats.length
            actions.forEach((t, i) => rows.push({ kind: 'action', text: t, idx: i, beatIdx: bi }))
            dialogues.forEach((d, i) => rows.push({ kind: 'dialogue', text: d, idx: i, beatIdx: bi }))
            conflicts.forEach((t, i) => rows.push({ kind: 'conflict', text: t, idx: i, beatIdx: bi }))
            details.forEach((t, i) => rows.push({ kind: 'detail', text: t, idx: i, beatIdx: bi }))

            // 按原索引排序（模拟叙事顺序：先按类型在数组中的位置，然后混合）
            // 更真实的叙事流应该是交错的，但数据结构本身没有拍内顺序
            // 这里用 idx 排序来大致模拟：先到的先出现
            rows.sort((a, b) => a.idx - b.idx)

            return { beatText, rows, beatIdx: bi }
        })
    }

    const kindIcon = { action: '', dialogue: '', conflict: '', detail: '' }
    const kindLabel = { action: '动作', dialogue: '对白', conflict: '冲突', detail: '细节' }

    const handleRowClick = (sceneIdx, beatIdx, rowIdx, kind) => {
        onCursorChange({ sceneIdx, beatIdx, rowIdx, kind })
    }

    const isRowActive = (sceneIdx, beatIdx, rowIdx) => {
        if (!cursor) return false
        return cursor.sceneIdx === sceneIdx && cursor.beatIdx === beatIdx && cursor.rowIdx === rowIdx
    }

    if (!scenes || scenes.length === 0) {
        return <span style={{ color: 'var(--ink-mute)' }}>（未生成 — 在右侧点「生成下一级」）</span>
    }

    return (
        <div style={{ padding: '4px 0' }}>
            {/* 头部 */}
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span>l4 场景（{scenes.length} 个 · 按叙事顺序）</span>
                <div style={{ display: 'flex', gap: 4 }}>
                    <button style={btnStyle(busy, true)} disabled={busy} onClick={onSave}>保存</button>
                </div>
            </div>

            {scenes.map((sc, si) => {
                const blanks = getBlanksFromText(si)
                const beatFlow = buildBeatFlow(sc, si)
                const sFrags = sceneFrags(si)
                const selectedIds = new Set(sc.elements || [])
                const elemGroups = [
                    { key: 'characters', label: '角色', icon: '' },
                    { key: 'items', label: '物品', icon: '' },
                    { key: 'settings', label: '设定', icon: '' },
                ]

                return (
                    <div key={si} style={{
                        border: '1px solid var(--line)',
                        borderRadius: 6,
                        padding: 8,
                        marginBottom: 8,
                        background: 'var(--paper)',
                    }}>
                        {/* 场景头 */}
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6 }}>
                            <span style={{
                                background: 'var(--cinnabar)',
                                color: 'var(--paper-raised)',
                                padding: '2px 8px',
                                borderRadius: 2,
                                fontSize: 10,
                                fontWeight: 700,
                                flexShrink: 0,
                            }}>场景 {si + 1}</span>
                            <input
                                value={sc.name || ''}
                                onChange={e => setSc(si, 'name', e.target.value)}
                                placeholder="场景名"
                                style={{
                                    flex: 1,
                                    padding: '4px 6px',
                                    borderRadius: 4,
                                    border: '1px solid var(--line-soft)',
                                    fontSize: 12.5,
                                    fontWeight: 600,
                                    background: 'var(--paper)',
                                }}
                            />
                            <input
                                value={sc.environment || ''}
                                onChange={e => setSc(si, 'environment', e.target.value)}
                                placeholder="环境"
                                style={{
                                    flex: 1,
                                    padding: '4px 6px',
                                    borderRadius: 4,
                                    border: '1px solid var(--line-soft)',
                                    fontSize: 11.5,
                                    color: 'var(--ink-sub)',
                                    background: 'var(--paper)',
                                }}
                            />
                            <button
                                style={{ ...btnStyle(false, false), fontSize: 11, padding: '2px 6px' }}
                                onClick={() => onScenesChange(scenes.filter((_, j) => j !== si))}
                            >✕</button>
                        </div>

                        {/* 统计数字 */}
                        <div style={{ fontSize: 10, color: 'var(--ink-mute)', display: 'flex', gap: 10, marginBottom: 6 }}>
                            <span>{(sc.beats || []).length} 拍</span>
                            <span>{(sc.actions || []).length}</span>
                            <span>{(sc.dialogues || []).length}</span>
                            <span>{(sc.conflicts || []).length}</span>
                            <span>{(sc.details || []).length}</span>
                            {blanks.length > 0 && <span>{blanks.length} 留空</span>}
                        </div>

                        {/* 节拍叙事流 */}
                        <div style={{ borderTop: '1px dashed var(--line-soft)', paddingTop: 4 }}>
                            {beatFlow.map((beat, bi) => (
                                <div key={bi} style={{
                                    display: 'flex',
                                    gap: 8,
                                    padding: '4px 0',
                                    borderBottom: bi < beatFlow.length - 1 ? '1px dashed var(--line-soft)' : 'none',
                                    position: 'relative',
                                }}>
                                    {/* 左侧节拍编号 */}
                                    <div style={{
                                        width: 20,
                                        flexShrink: 0,
                                        paddingTop: 2,
                                        textAlign: 'center',
                                        fontSize: 10,
                                        fontWeight: 700,
                                        color: beat.beatText ? 'var(--dai)' : 'var(--ink-mute)',
                                    }}>
                                        {beat.beatIdx >= 0 ? beat.beatIdx + 1 : '·'}
                                    </div>

                                    {/* 右侧内容 */}
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        {/* 节拍文本（如果有） */}
                                        {beat.beatText && (
                                            <div
                                                style={{
                                                    fontSize: 11.5,
                                                    fontWeight: 600,
                                                    color: 'var(--dai-dark)',
                                                    marginBottom: 3,
                                                    cursor: 'pointer',
                                                    display: 'flex',
                                                    alignItems: 'center',
                                                    gap: 4,
                                                }}
                                                onClick={() => {
                                                    const key = `${si}_${bi}`
                                                    setBeatExpanded(d => ({ ...d, [key]: !d[key] }))
                                                }}
                                            >
                                                {beat.beatText}
                                                <span style={{ fontSize: 9, color: 'var(--ink-mute)', fontWeight: 400 }}>
                                                    {beatExpanded[`${si}_${bi}`] ? '▲' : '▼'}
                                                </span>
                                            </div>
                                        )}

                                        {/* 内容行（混排） */}
                                        {beat.rows.length > 0 ? (
                                            beat.rows.map((row, ri) => {
                                                const active = isRowActive(si, beat.beatIdx, ri)
                                                return (
                                                    <div
                                                        key={ri}
                                                        onClick={() => handleRowClick(si, beat.beatIdx, ri, row.kind)}
                                                        style={{
                                                            padding: '3px 6px',
                                                            fontSize: 12,
                                                            lineHeight: 1.6,
                                                            cursor: 'text',
                                                            borderRadius: 3,
                                                            display: 'flex',
                                                            gap: 6,
                                                            alignItems: 'flex-start',
                                                            position: 'relative',
                                                            background: active ? 'var(--dai-wash)' : 'transparent',
                                                            borderLeft: active ? '2px solid var(--dai)' : '2px solid transparent',
                                                            marginLeft: active ? -2 : 0,
                                                        }}
                                                        onMouseEnter={(e) => {
                                                            e.currentTarget.style.background = 'var(--bg-card-2)'
                                                        }}
                                                        onMouseLeave={(e) => {
                                                            e.currentTarget.style.background = isRowActive(si, beat.beatIdx, ri) ? 'var(--dai-wash)' : 'transparent'
                                                        }}
                                                    >
                                                        {/* 类型标（极淡，hover才亮） */}
                                                        <span style={{
                                                            fontSize: 10,
                                                            color: active ? 'var(--dai-dark)' : 'var(--ink-mute)',
                                                            opacity: 0.7,
                                                            flexShrink: 0,
                                                            paddingTop: 2,
                                                        }}>
                                                            {kindIcon[row.kind]}
                                                        </span>
                                                        {/* 内容 */}
                                                        <div style={{ flex: 1, minWidth: 0, color: 'var(--ink)' }}>
                                                            {row.kind === 'dialogue' ? (
                                                                <span>{row.text}</span>
                                                            ) : (
                                                                <span>{row.text}</span>
                                                            )}
                                                        </div>
                                                        {/* 编辑按钮（hover 显示） */}
                                                        <button
                                                            onClick={(e) => {
                                                                e.stopPropagation()
                                                                setEditMode({ sceneIdx: si, kind: row.kind, rowIdx: row.idx })
                                                            }}
                                                            style={{
                                                                fontSize: 10,
                                                                color: 'var(--ink-mute)',
                                                                cursor: 'pointer',
                                                                border: 'none',
                                                                background: 'none',
                                                                padding: 0,
                                                                flexShrink: 0,
                                                                opacity: 0,
                                                            }}
                                                            onMouseEnter={(e) => { e.currentTarget.style.opacity = 1 }}
                                                            onMouseLeave={(e) => { e.currentTarget.style.opacity = 0 }}
                                                        ></button>
                                                    </div>
                                                )
                                            })
                                        ) : (
                                            <div style={{
                                                fontSize: 11,
                                                color: 'var(--ink-mute)',
                                                padding: '2px 0',
                                                fontStyle: 'italic',
                                            }}>
                                                （此节拍暂无内容 — 在右栏点插入按钮添加）
                                            </div>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>

                        {/* 添加节拍 */}
                        <div style={{ marginTop: 4 }}>
                            <button
                                style={{ ...btnStyle(false, false), fontSize: 11, padding: '2px 8px' }}
                                onClick={() => onAddBeat?.(si)}
                            >+ 添加节拍</button>
                        </div>

                        {/* 出场元素 chips（简洁一行） */}
                        {(sc.elements || []).length > 0 && (
                            <div style={{
                                marginTop: 6,
                                paddingTop: 4,
                                borderTop: '1px solid var(--line-soft)',
                                fontSize: 10.5,
                                color: 'var(--ink-sub)',
                                display: 'flex',
                                alignItems: 'center',
                                gap: 4,
                                flexWrap: 'wrap',
                            }}>
                                <span style={{ color: 'var(--ink-mute)' }}>出场：</span>
                                {(sc.elements || []).map(eid => {
                                    const e = flatElements.find(x => x.id === eid)
                                    return (
                                        <span key={eid} style={{
                                            padding: '1px 6px',
                                            borderRadius: 10,
                                            fontSize: 10.5,
                                            background: 'var(--dai-wash)',
                                            color: 'var(--dai-dark)',
                                            fontWeight: 500,
                                        }}>
                                            {e?.name || eid}
                                        </span>
                                    )
                                })}
                            </div>
                        )}

                        {/* 编辑弹层（行级编辑） */}
                        {editMode && editMode.sceneIdx === si && (() => {
                            const kind = editMode.kind
                            const arr = sc[kind + 's'] || sc[kind + 'es'] || []
                            const val = arr[editMode.rowIdx] || ''
                            const field = kind === 'dialogue' ? 'dialogues'
                                : kind === 'action' ? 'actions'
                                : kind === 'conflict' ? 'conflicts'
                                : 'details'
                            return (
                                <div style={{
                                    marginTop: 6,
                                    padding: 8,
                                    background: 'var(--bg-card-2)',
                                    borderRadius: 4,
                                    border: '1px solid var(--line-soft)',
                                }}>
                                    <div style={{ fontSize: 11, color: 'var(--ink-sub)', marginBottom: 4 }}>
                                        编辑 {kindIcon[kind]} {kindLabel[kind]}（第 {editMode.rowIdx + 1} 条）
                                    </div>
                                    <textarea
                                        autoFocus
                                        value={val}
                                        onChange={e => {
                                            const next = [...(sc[field] || [])]
                                            next[editMode.rowIdx] = e.target.value
                                            setSc(si, field, next)
                                        }}
                                        rows={3}
                                        style={{
                                            width: '100%',
                                            boxSizing: 'border-box',
                                            padding: 6,
                                            borderRadius: 4,
                                            border: '1px solid var(--line-soft)',
                                            fontSize: 12,
                                            fontFamily: 'inherit',
                                        }}
                                    />
                                    <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                                        <button
                                            style={{ ...btnStyle(false, true), fontSize: 11 }}
                                            onClick={() => setEditMode(null)}
                                        >✓ 完成</button>
                                        <button
                                            style={{ ...btnStyle(false, false), fontSize: 11, color: 'var(--cinnabar)' }}
                                            onClick={() => {
                                                const next = (sc[field] || []).filter((_, i) => i !== editMode.rowIdx)
                                                setSc(si, field, next)
                                                setEditMode(null)
                                            }}
                                        >删除</button>
                                    </div>
                                </div>
                            )
                        })()}
                    </div>
                )
            })}

            {/* 添加场景 */}
            <button style={{ ...btnStyle(false, false), marginBottom: 6 }} onClick={onAddScene}>
                + 添加场景
            </button>

            {/* 说明 */}
            <div style={{
                marginTop: 8,
                padding: '10px 14px',
                background: 'var(--dai-wash)',
                borderRadius: 2,
                fontSize: 12,
                color: 'var(--dai-dark)',
                borderLeft: '3px solid var(--dai)',
            }}>
                <b>l4 → l5：</b>场景的 l4 内容已足够——节拍明确了叙事顺序，素材提供了固定内容，留空标记了 LLM 需要填充的部分。生成 l5 时，按节拍逐段写，有素材用素材，有留空按意图填充，无素材的节拍由 LLM 自由发挥。
            </div>
        </div>
    )
}
