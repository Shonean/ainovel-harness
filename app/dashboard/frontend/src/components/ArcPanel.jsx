import { useState } from 'react'

export default function ArcPanel({
    arcs,
    activeArcId,
    elements,
    settings,
    bookRoot,
    bookTitle,
    busy,
    onArcSelect,
    onArcCreate,
    onArcDelete,
    onArcRename,
    onNotice,
    onError,
}) {
    const [arcView, setArcView] = useState('cards') // 'cards' | 'list'
    const [newL1, setNewL1] = useState('')
    const [newN, setNewN] = useState(1)
    const [newCarryPrev, setNewCarryPrev] = useState(true)
    const [newArcOpen, setNewArcOpen] = useState(false)

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

    const list = arcs?.arcs || []
    const arcList = [...list].sort((a, b) => {
        const no = (name) => { const m = String(name || '').match(/(\d+)/); return m ? parseInt(m[1], 10) : 0 }
        return no(a.name) - no(b.name)
    })

    // 创建情节
    const handleCreate = async () => {
        if (!newL1.trim()) return
        try {
            await onArcCreate?.({
                l1: newL1.trim(),
                n_chapters: Number(newN) || 1,
                carry_prev: newCarryPrev,
            })
            setNewL1('')
            setNewN(1)
            setNewArcOpen(false)
        } catch (e) {
            onError?.(`创建失败: ${e.message || e}`)
        }
    }

    // 删除情节
    const handleDelete = async (arc) => {
        if (!window.confirm(`确定删除情节「${arc.name}」？`)) return
        try {
            await onArcDelete?.(arc.id)
        } catch (e) {
            onError?.(`删除失败: ${e.message || e}`)
        }
    }

    // 重命名情节
    const handleRename = async (arc) => {
        const newName = prompt('输入新名称：', arc.name)
        if (!newName || newName === arc.name) return
        try {
            await onArcRename?.(arc.id, newName)
        } catch (e) {
            onError?.(`重命名失败: ${e.message || e}`)
        }
    }

    // 获取元素名称
    const nameById = {}
    Object.values(elements || {}).flat().forEach(e => { if (e?.id) nameById[e.id] = e.name })

    // 获取情节参与元素
    const getPartNames = (arc) => {
        const sel = arc.selected || {}
        return [
            ...(sel.characters || []).map(id => nameById[id] || id),
            ...(sel.items || []).map(id => nameById[id] || id),
            ...(sel.settings || []).map(id => nameById[id] || id),
        ].filter(Boolean)
    }

    // 获取阶梯进度
    const getLevelProgress = (arc) => {
        const st = arc.state || {}
        const levels = st.levels || {}
        const result = []
        for (const lv of ['l1', 'l2', 'l3', 'l4', 'l5']) {
            const l = levels[lv] || {}
            const filled = lv === 'l3' ? (l.data || l.text) : lv === 'l4' ? (l.scenes?.length) : l.text
            result.push({
                level: lv,
                status: l.confirmed ? 'ok' : filled ? 'half' : 'no',
            })
        }
        return result
    }

    return (
        <div>
            {/* 新建情节表单 */}
            <div style={{ marginBottom: 12 }}>
                <button
                    style={btnStyle(false, !newArcOpen)}
                    onClick={() => setNewArcOpen(!newArcOpen)}
                >
                    {newArcOpen ? '取消' : '新建情节'}
                </button>
                {newArcOpen && (
                    <div style={{
                        marginTop: 8,
                        padding: 12,
                        border: '1px solid var(--line)',
                        borderRadius: 6,
                        background: 'var(--paper)',
                    }}>
                        <textarea
                            value={newL1}
                            onChange={e => setNewL1(e.target.value)}
                            rows={2}
                            placeholder="本情节一句话极简剧情（l1）"
                            style={{
                                width: '100%',
                                boxSizing: 'border-box',
                                padding: 8,
                                border: '1px solid var(--line-soft)',
                                borderRadius: 6,
                                fontSize: 13,
                                lineHeight: 1.7,
                                fontFamily: 'inherit',
                            }}
                        />
                        <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center' }}>
                            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
                                章数：
                                <input
                                    type="number"
                                    min={1}
                                    max={20}
                                    value={newN}
                                    onChange={e => setNewN(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
                                    style={{ width: 60, padding: '4px 6px', border: '1px solid var(--line-soft)', borderRadius: 4, fontSize: 13 }}
                                />
                            </label>
                            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
                                <input
                                    type="checkbox"
                                    checked={newCarryPrev}
                                    onChange={e => setNewCarryPrev(e.target.checked)}
                                />
                                承接上一情节
                            </label>
                            <button
                                style={btnStyle(busy || !newL1.trim(), true)}
                                disabled={busy || !newL1.trim()}
                                onClick={handleCreate}
                            >
                                {busy ? '创建中…' : '新建情节'}
                            </button>
                        </div>
                    </div>
                )}
            </div>

            {/* 情节列表 */}
            {arcList.length === 0 ? (
                <div style={{
                    fontSize: 13,
                    color: 'var(--ink-mute)',
                    padding: 14,
                    textAlign: 'center',
                    border: '1px dashed var(--line)',
                    borderRadius: 8,
                }}>
                    还没有情节——在上方填 l1 一句话极简剧情 + 章数，点「新建情节」
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {arcList.map((arc) => {
                        const isActive = arc.id === activeArcId
                        const partNames = getPartNames(arc)
                        const levelProgress = getLevelProgress(arc)
                        return (
                            <div
                                key={arc.id}
                                style={{
                                    border: `1px solid ${isActive ? 'var(--dai)' : 'var(--line)'}`,
                                    borderRadius: 8,
                                    padding: 12,
                                    background: isActive ? 'var(--dai-wash)' : 'var(--paper)',
                                    cursor: 'pointer',
                                    transition: 'all 0.15s',
                                }}
                                onClick={() => onArcSelect?.(arc.id)}
                            >
                                {/* 情节头部 */}
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                                    <span style={{ fontWeight: 700, fontSize: 14, flex: 1 }}>{arc.name}</span>
                                    <button
                                        style={{ ...btnStyle(false, false), padding: '2px 6px', fontSize: 11 }}
                                        onClick={(e) => { e.stopPropagation(); handleRename(arc) }}
                                    >
                                        
                                    </button>
                                    <button
                                        style={{ ...btnStyle(false, false), padding: '2px 6px', fontSize: 11, borderColor: 'var(--cinnabar)', color: 'var(--cinnabar)' }}
                                        onClick={(e) => { e.stopPropagation(); handleDelete(arc) }}
                                    >
                                        
                                    </button>
                                </div>

                                {/* l1 */}
                                <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 6 }}>
                                    {arc.l1 || '（无 l1）'}
                                </div>

                                {/* 参与元素 */}
                                {partNames.length > 0 && (
                                    <div style={{ fontSize: 11, color: 'var(--ink-mute)', marginBottom: 6 }}>
                                        参与：{partNames.slice(0, 5).join('、')}
                                        {partNames.length > 5 && ` 等${partNames.length}个`}
                                    </div>
                                )}

                                {/* 阶梯进度 */}
                                <div style={{ display: 'flex', gap: 4 }}>
                                    {levelProgress.map(lv => (
                                        <span
                                            key={lv.level}
                                            style={{
                                                padding: '2px 6px',
                                                borderRadius: 4,
                                                fontSize: 10,
                                                fontWeight: 600,
                                                background: lv.status === 'ok' ? 'var(--green-wash)' : lv.status === 'half' ? 'var(--amber-wash)' : 'var(--bg-card-2)',
                                                color: lv.status === 'ok' ? 'var(--green)' : lv.status === 'half' ? 'var(--amber)' : 'var(--ink-mute)',
                                            }}
                                        >
                                            {lv.level}
                                        </span>
                                    ))}
                                </div>
                            </div>
                        )
                    })}
                </div>
            )}
        </div>
    )
}
