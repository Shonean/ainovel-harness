import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

/**
 * 全书思维导图（书 → 情节 → 章）· 高密度版（A 折叠聚合情节环 + C 密度阈值降级）。
 *
 * 解决「情节多 / 章多时放射状平铺叶子会乱」：
 *  - 默认 map 模式 = 折叠聚合情节环：书居中为根，每个情节一张聚合卡绕圈排布
 *    （情节名 + 状态印章 + 章点阵），章叶子收进卡里——悬停看章列表，点击选中情节。
 *    无论多少章，环上始终只有情节这一层，密度恒定。
 *  - 自动降级：情节数 > 12（环上放不下）→ 自动切到 outline 两级大纲（情节折叠行 → 章列表）。
 *  - 手动切换：工具栏「思维导图 / 两级大纲」可随时覆盖自动决策。
 *
 * 保留语义：阶梯进度 l1-l5（✓ 已确认 / ● 已生成待确认 / ○ 未生成）、
 * 章评分（综合分 + 污染）、参与元素、草稿章；点击选中/查看/切换交互不变。
 *
 * Props（接口不变）：
 *  - bookTitle, arcs, elements, activeArcId, onSelectArc, onViewChapter, onSelectDraft
 */
export default function StoryMindMap({ bookTitle, arcs, elements, activeArcId, onSelectArc, onViewChapter, onSelectDraft }) {
    const LEVEL_ORDER = ['l1', 'l2', 'l3', 'l4', 'l5']
    const stripCh = (t) => (t || '').replace(/^第\s*\d+\s*章[\s：:、．.，,]?/, '')
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
    const trunc = (s, n) => (s || '').length > n ? (s || '').slice(0, n) + '…' : (s || '')

    const elm = elements || {}
    const elementName = (id) => {
        for (const k of ['characters', 'items', 'settings']) {
            const e = (elm[k] || []).find(x => x && x.id === id)
            if (e) return e.name
        }
        return id
    }

    // 阶梯进度标记：✓=已确认 / ●=已生成待确认 / ○=未生成
    const ladderOf = (arc) => LEVEL_ORDER.map(lv => {
        const l = (arc?.state?.levels || {})[lv] || {}
        const filled = lv === 'l3' ? (l.data || l.text) : lv === 'l4' ? (l.scenes?.length) : l.text
        return { lv, confirmed: !!l.confirmed, filled: !!filled }
    })

    // 该情节的章叶子：已落盘 + 草稿（去重：草稿标题与已落盘同则跳过）
    const arcLeaves = (arc) => {
        const fins = (arc.chapters || []).map((c, i) => ({
            kind: 'fin', idx: i, num: c.num, title: c.title,
            overall: c.overall, polluted: !!c.polluted,
        }))
        const finTitles = new Set((arc.chapters || []).map(c => stripCh(c.title)))
        const l3d = (arc?.state?.levels?.l3?.data) || {}
        let drafts = []
        if (Array.isArray(l3d.chapters)) drafts = l3d.chapters
        else if (l3d && l3d.title) drafts = [l3d]
        const pend = drafts.map((ch, i) => ({ kind: 'draft', idx: i, title: ch.title || '' }))
            .filter(d => d.title && !finTitles.has(stripCh(d.title)))
        return [...fins, ...pend]
    }

    const list = useMemo(() => arcs.map(a => ({ arc: a, leaves: arcLeaves(a) })), [arcs])
    const hasPolluted = (arc) => (arc.chapters || []).some(c => c.polluted)

    // 统计
    let fins = 0, drafts = 0, sum = 0, n = 0
    list.forEach(({ leaves }) => leaves.forEach(l => {
        if (l.kind === 'fin') { fins++; sum += l.overall || 0; n++ } else drafts++
    }))
    const avg = n ? (sum / n) : null
    const scoreColor = (v) => v >= 0.7 ? 'var(--green)' : v >= 0.6 ? 'var(--amber)' : 'var(--ink-mute)'

    // ── 密度阈值 → 自动降级（C 方案） ──
    const AUTO_DENSE_ARC = 12          // 情节数超此值，环上放不下 → 自动降级两级大纲
    const [mode, setMode] = useState('auto')   // 'auto' | 'map' | 'outline'
    const effectiveMode = mode === 'auto' ? (list.length > AUTO_DENSE_ARC ? 'outline' : 'map') : mode

    // ── 视口交互：缩放 / 平移 / 适应（map 模式用） ──
    const [view, setView] = useState({ scale: 0.6, tx: 0, ty: 0 })
    const [dragging, setDragging] = useState(false)
    const viewportRef = useRef(null)
    const dragRef = useRef(null)
    const sizeRef = useRef({ w: 0, h: 0 })
    const viewRef = useRef(view)
    useEffect(() => { viewRef.current = view })

    const zoomAt = useCallback((mx, my, factor) => {
        setView(v => {
            const ns = clamp(v.scale * factor, 0.18, 3)
            const k = ns / v.scale
            return { scale: ns, tx: mx - (mx - v.tx) * k, ty: my - (my - v.ty) * k }
        })
    }, [])

    const fit = useCallback(() => {
        const el = viewportRef.current
        if (!el) return
        const vw = el.clientWidth || 900, vh = el.clientHeight || 600
        const cw = sizeRef.current.w, ch = sizeRef.current.h
        if (!cw || !ch) return
        const s = clamp(Math.min(vw / cw, vh / ch), 0.15, 1.1)
        setView({ scale: s, tx: (vw - cw * s) / 2, ty: (vh - ch * s) / 2 })
    }, [])

    // ── map 模式布局：折叠聚合情节环（书居中 + 情节卡绕圈，无叶子平铺） ──
    const R1 = 210, PAD = 60
    const ringSize = useMemo(() => {
        const d = (R1 + 130) * 2 + PAD * 2
        return { w: d, h: d }
    }, [])
    useEffect(() => { sizeRef.current = ringSize })

    const arcAngle = (i) => (i / Math.max(1, list.length)) * Math.PI * 2 - Math.PI / 2

    // 环上情节卡位置
    const ringNodes = list.map(({ arc, leaves }, i) => {
        const a = arcAngle(i)
        return {
            arc, leaves,
            x: R1 * Math.cos(a), y: R1 * Math.sin(a),
            angle: a,
            active: arc.id === activeArcId,
            hasPolluted: hasPolluted(arc),
        }
    })

    // 情节集合变化（新增/删除）后自动适应窗口
    const arcsKey = arcs.map(a => a.id).join('|')
    useEffect(() => { fit() }, [fit, arcsKey])

    // 滚轮缩放（原生非 passive 监听，避免页面跟着滚）
    useEffect(() => {
        const el = viewportRef.current
        if (!el) return
        const onWheel = (e) => {
            e.preventDefault()
            const rect = el.getBoundingClientRect()
            zoomAt(e.clientX - rect.left, e.clientY - rect.top, e.deltaY < 0 ? 1.12 : 0.89)
        }
        el.addEventListener('wheel', onWheel, { passive: false })
        return () => el.removeEventListener('wheel', onWheel)
    }, [zoomAt])

    const onMouseDown = (e) => {
        const v = viewRef.current
        dragRef.current = { x: e.clientX, y: e.clientY, tx: v.tx, ty: v.ty }
        setDragging(true)
    }
    useEffect(() => {
        if (!dragging) return
        const mm = (e) => {
            const d = dragRef.current
            if (!d) return
            setView(v => ({ ...v, tx: d.tx + (e.clientX - d.x), ty: d.ty + (e.clientY - d.y) }))
        }
        const mu = () => { dragRef.current = null; setDragging(false) }
        window.addEventListener('mousemove', mm)
        window.addEventListener('mouseup', mu)
        return () => { window.removeEventListener('mousemove', mm); window.removeEventListener('mouseup', mu) }
    }, [dragging])

    const zoomBtn = (factor) => {
        const el = viewportRef.current
        zoomAt((el?.clientWidth || 900) / 2, (el?.clientHeight || 600) / 2, factor)
    }

    // ── 状态印章（合格 / 待审 / 污染） ──
    const sealStyle = {
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: '"Noto Serif SC","Songti SC",SimSun,serif', fontWeight: 700,
        fontSize: 10, letterSpacing: '0.14em', textIndent: '0.14em',
        padding: '1px 7px', borderRadius: 3, whiteSpace: 'nowrap', lineHeight: 1.4,
    }
    const arcSeal = (nd) => {
        if (nd.hasPolluted) return (
            <span style={{ ...sealStyle, color: 'var(--cinnabar-d)', background: 'var(--cinnabar-wash)', border: '1.5px solid var(--cinnabar)', transform: 'rotate(-8deg)' }}>污染</span>
        )
        if (nd.arc.status === 'done') return (
            <span style={{ ...sealStyle, color: '#f8e9e0', background: 'linear-gradient(150deg,var(--cinnabar),var(--cinnabar-d))', border: '1px solid var(--cinnabar-d)', boxShadow: '0 1px 2px rgba(163,44,32,.3)' }}>合格</span>
        )
        return (
            <span style={{ ...sealStyle, color: 'var(--cinnabar)', background: 'var(--paper-raised)', border: '1.5px dashed var(--cinnabar)', opacity: 0.92 }}>待审</span>
        )
    }

    // 章点阵：每章一个小方块
    const dotsOf = (leaves) => leaves.map((l, i) => (
        <i key={i} title={l.title} style={{
            width: 8, height: 8, borderRadius: 2, flexShrink: 0,
            background: l.kind === 'fin'
                ? (l.polluted ? 'var(--cinnabar)' : 'var(--green)')
                : 'transparent',
            border: l.kind === 'fin'
                ? 'none'
                : '1.5px solid var(--amber)',
        }} />
    ))

    // 章列表（map popover / outline 展开共用渲染）
    const chRow = (l, nd) => {
        if (l.kind === 'fin') return (
            <div key={'f' + l.idx} onClick={(e) => { e.stopPropagation(); onViewChapter(nd.arc.id, l.idx) }}
                style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 2px', fontSize: 12, cursor: 'pointer', borderRadius: 3 }}>
                <i style={{ width: 7, height: 7, borderRadius: 2, flexShrink: 0, background: l.polluted ? 'var(--cinnabar)' : 'var(--green)' }} />
                <span style={{ color: 'var(--ink)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    第{String(l.num).padStart(4, '0')}章 {trunc(l.title, 10)}
                </span>
                <span style={{ color: scoreColor(l.overall), fontWeight: 600 }}>{l.overall != null ? l.overall.toFixed(2) : '—'}</span>
                {l.polluted && <span style={{ color: 'var(--cinnabar-d)', fontSize: 10 }}>⚠</span>}
            </div>
        )
        return (
            <div key={'d' + l.idx} onClick={(e) => { e.stopPropagation(); onSelectDraft(nd.arc.id, l.idx) }}
                style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 2px', fontSize: 12, cursor: 'pointer', borderRadius: 3 }}>
                <i style={{ width: 7, height: 7, borderRadius: 2, flexShrink: 0, border: '1.5px solid var(--amber)' }} />
                <span style={{ color: 'var(--amber)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{trunc(l.title, 10)}</span>
                <span style={{ color: 'var(--amber)', fontSize: 10 }}>草稿</span>
            </div>
        )
    }

    // 阶梯进度条（popover / outline 共用）
    const ladderBar = (arc) => (
        <span style={{ fontFamily: 'monospace', fontSize: 11, display: 'inline-flex', gap: 3, color: 'var(--ink-sub)' }}>
            {ladderOf(arc).map(l => (
                <span key={l.lv} style={{ color: l.confirmed ? 'var(--green)' : l.filled ? 'var(--amber)' : 'var(--ink-mute)' }}>
                    {l.confirmed ? '✓' : l.filled ? '●' : '○'}{l.lv[1]}
                </span>
            ))}
        </span>
    )

    // ── map 模式：聚合情节环（HTML 绝对定位 + transform 视口） ──
    const renderMap = () => (
        <div ref={viewportRef}
            onMouseDown={onMouseDown}
            style={{ position: 'relative', height: 560, overflow: 'hidden', cursor: dragging ? 'grabbing' : 'grab', userSelect: 'none', touchAction: 'none', background: 'var(--paper)' }}>
            <div style={{
                position: 'absolute', left: 0, top: 0, width: ringSize.w, height: ringSize.h,
                transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.scale})`,
                transformOrigin: '0 0',
            }}>
                {/* 书（根）居中 */}
                <div style={{
                    position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%,-50%)',
                    zIndex: 3,
                }}>
                    <div style={{
                        background: 'var(--dai)', color: 'var(--paper-raised)', borderRadius: 4,
                        padding: '14px 24px', textAlign: 'center', boxShadow: '0 6px 18px rgba(66,55,35,.14)',
                    }}>
                        <div style={{ fontFamily: '"Noto Serif SC","Songti SC",SimSun,serif', fontSize: 16, fontWeight: 700, letterSpacing: '.05em' }}>{trunc(bookTitle, 12)}</div>
                        <div style={{ fontSize: 10.5, opacity: 0.75, marginTop: 3 }}>{list.length} 情节 · {fins} 章已落盘</div>
                    </div>
                </div>
                {/* 情节卡绕圈 */}
                {ringNodes.map((nd, i) => {
                    const w = 150
                    const h = nd.leaves.length ? 92 : 74
                    const x = nd.x - w / 2 + ringSize.w / 2
                    const y = nd.y - h / 2 + ringSize.h / 2
                    const parts = [...(nd.arc.selected?.characters || []), ...(nd.arc.selected?.items || []), ...(nd.arc.selected?.settings || [])]
                        .map(id => elementName(id))
                    return (
                        <div key={nd.arc.id}
                            className="smm-arc"
                            onClick={() => onSelectArc(nd.arc.id)}
                            title="点击选中此情节"
                            style={{
                                position: 'absolute', left: x, top: y, width: w, height: h, zIndex: 2,
                                background: nd.active ? 'var(--dai-wash)' : 'var(--paper)',
                                border: nd.active ? '2px solid var(--dai)' : '1px solid var(--line)',
                                borderRadius: 4, cursor: 'pointer', padding: '8px 10px',
                                boxShadow: nd.active ? '0 0 0 3px rgba(56,82,92,.15)' : '0 1px 2px rgba(66,55,35,.05)',
                                transition: 'box-shadow .13s, transform .13s',
                            }}
                            onMouseEnter={(e) => { e.currentTarget.style.boxShadow = '0 4px 12px rgba(66,55,35,.12)'; e.currentTarget.style.transform = 'translateY(-1px)' }}
                            onMouseLeave={(e) => { e.currentTarget.style.boxShadow = nd.active ? '0 0 0 3px rgba(56,82,92,.15)' : '0 1px 2px rgba(66,55,35,.05)'; e.currentTarget.style.transform = 'none' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                                <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--ink)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{trunc(nd.arc.name || '未命名情节', 9)}</span>
                                {arcSeal(nd)}
                            </div>
                            {nd.leaves.length > 0 && (
                                <div style={{ display: 'flex', gap: 3, marginTop: 6, flexWrap: 'wrap' }}>{dotsOf(nd.leaves)}</div>
                            )}
                            <div style={{ marginTop: 5, fontSize: 9.5, color: 'var(--ink-mute)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 4 }}>
                                <span>{parts.length ? `参与 ${parts.slice(0, 2).join('·')}${parts.length > 2 ? ` +${parts.length - 2}` : ''}` : (nd.leaves.length ? `${nd.leaves.length} 章` : '未选元素')}</span>
                                <span style={{ color: 'var(--ink-sub)' }}>{nd.leaves.length} 章{nd.hasPolluted ? ' · ⚠' : ''}</span>
                            </div>
                            {/* 悬停 popover：章列表 */}
                            {nd.leaves.length > 0 && (
                                <div className="smm-pop" style={{
                                    display: 'none', position: 'absolute', left: '50%', top: '100%', transform: 'translateX(-50%)',
                                    marginTop: 6, minWidth: 190, maxWidth: 230, zIndex: 20,
                                    background: 'var(--paper-raised)', border: '1px solid var(--line)', borderRadius: 4,
                                    boxShadow: '0 6px 20px rgba(66,55,35,.16)', padding: '7px 9px',
                                }}>
                                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6, marginBottom: 3 }}>
                                        <span style={{ fontSize: 10.5, color: 'var(--ink-sub)', fontWeight: 600 }}>阶梯</span>
                                        {ladderBar(nd.arc)}
                                    </div>
                                    <div style={{ borderTop: '1px solid var(--line-soft)', margin: '3px 0 4px' }} />
                                    {nd.leaves.map(l => chRow(l, nd))}
                                </div>
                            )}
                        </div>
                    )
                })}
                {/* 环轨迹（淡到几乎不可见，给「圈」一个锚） */}
                <svg style={{ position: 'absolute', left: 0, top: 0, width: ringSize.w, height: ringSize.h, pointerEvents: 'none', zIndex: 1 }}>
                    <circle cx={ringSize.w / 2} cy={ringSize.h / 2} r={R1} fill="none" stroke="var(--line)" strokeWidth="1" strokeDasharray="3 5" opacity="0.35" />
                </svg>
            </div>
            {/* 图例 */}
            <div style={{ position: 'absolute', left: 8, bottom: 8, display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: 10, color: 'var(--ink-sub)', background: 'rgba(250,248,241,.94)', padding: '4px 9px', borderRadius: 4, border: '1px solid var(--line)', pointerEvents: 'none' }}>
                <span><i style={{ width: 8, height: 8, borderRadius: 2, background: 'var(--green)', display: 'inline-block' }} />已落盘</span>
                <span><i style={{ width: 8, height: 8, borderRadius: 2, background: 'var(--cinnabar)', display: 'inline-block' }} />污染</span>
                <span><i style={{ width: 8, height: 8, borderRadius: 2, border: '1.5px solid var(--amber)', display: 'inline-block' }} />草稿</span>
                <span style={{ color: 'var(--dai)' }}>蓝框=当前情节</span>
                <span style={{ color: 'var(--cinnabar)' }}>印章=状态</span>
            </div>
        </div>
    )

    // ── outline 模式：两级大纲（情节折叠行 → 章列表） ──
    const [openArcs, setOpenArcs] = useState({})
    const toggleArc = (id) => setOpenArcs(p => ({ ...p, [id]: !p[id] }))
    useEffect(() => {
        // 情节变化时保留未展开状态（不主动收）
    }, [arcsKey])

    const renderOutline = () => (
        <div style={{ padding: 12, maxHeight: 560, overflowY: 'auto', background: 'var(--paper)' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {list.map(({ arc, leaves }) => {
                    const open = !!openArcs[arc.id]
                    const nd = { arc, leaves, hasPolluted: hasPolluted(arc) }
                    const finCnt = leaves.filter(l => l.kind === 'fin').length
                    return (
                        <div key={arc.id} style={{ border: '1px solid var(--line)', borderRadius: 4, background: 'var(--paper)', overflow: 'hidden' }}>
                            <div onClick={() => toggleArc(arc.id)}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', cursor: 'pointer',
                                    background: arc.id === activeArcId ? 'var(--dai-wash)' : 'transparent',
                                }}>
                                <span style={{ fontSize: 9, color: 'var(--ink-mute)', transition: 'transform .15s', transform: open ? 'rotate(90deg)' : 'none', width: 10 }}>▶</span>
                                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{arc.name || '未命名情节'}</span>
                                <span style={{ display: 'flex', gap: 3 }}>{dotsOf(leaves.slice(0, 16))}</span>
                                {leaves.length > 16 && <span style={{ fontSize: 9, color: 'var(--ink-mute)' }}>+{leaves.length - 16}</span>}
                                {arcSeal(nd)}
                                <span style={{ fontSize: 10, color: 'var(--ink-sub)', fontVariantNumeric: 'tabular-nums' }}>{finCnt}/{leaves.length}</span>
                            </div>
                            {open && (
                                <div style={{ padding: '4px 12px 8px 30px', borderTop: '1px solid var(--line-soft)', background: 'var(--paper-raised)' }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '4px 0 6px' }}>
                                        <span style={{ fontSize: 10.5, color: 'var(--ink-sub)', fontWeight: 600 }}>阶梯</span>
                                        {ladderBar(arc)}
                                    </div>
                                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                                        {leaves.length === 0 ? (
                                            <div style={{ fontSize: 12, color: 'var(--ink-mute)', padding: '4px 0' }}>（还没有章）</div>
                                        ) : leaves.map(l => chRow(l, nd))}
                                    </div>
                                </div>
                            )}
                        </div>
                    )
                })}
            </div>
        </div>
    )

    // ── 工具栏 ──
    const tbBtn = { padding: '2px 9px', borderRadius: 4, border: '1px solid var(--line)', background: 'var(--paper)', cursor: 'pointer', fontSize: 12, color: 'var(--ink)' }
    const modeBtn = (m, label) => (
        <button type="button" onClick={() => setMode(m)} style={{
            padding: '3px 10px', borderRadius: 4, fontSize: 12, cursor: 'pointer',
            background: effectiveMode === m ? 'var(--dai)' : 'transparent',
            border: effectiveMode === m ? '1px solid var(--dai)' : '1px solid var(--line)',
            color: effectiveMode === m ? 'var(--paper-raised)' : 'var(--ink-sub)', fontWeight: effectiveMode === m ? 600 : 500,
        }}>{label}</button>
    )

    return (
        <div style={{ border: '1px solid var(--line)', borderRadius: 4, overflow: 'hidden', background: 'var(--paper)', marginBottom: 10 }}>
            <style>{`
                .smm-arc:hover { z-index: 15 !important; }
                .smm-arc:hover .smm-pop { display: block !important; }
            `}</style>
            {/* 顶部说明 + 统计 + 模式切换 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '8px 12px', borderBottom: '1px solid var(--line)', fontSize: 12, color: 'var(--ink)', background: 'var(--paper)' }}>
                <span style={{ fontWeight: 700, fontSize: 13 }}>全书思维导图</span>
                <span style={{ color: 'var(--ink-sub)' }}>
                    {effectiveMode === 'map' ? '书 → 情节 · 悬停看章 · 滚轮缩放 · 拖拽平移' : '情节 → 章 · 两级大纲 · 点情节展开'}
                    {mode === 'auto' && list.length > AUTO_DENSE_ARC && <span style={{ color: 'var(--cinnabar-d)', marginLeft: 6 }}>· 情节多已自动降级</span>}
                </span>
                <span style={{ marginLeft: 'auto', color: 'var(--ink-sub)', display: 'flex', gap: 6, alignItems: 'center' }}>
                    {list.length} 情节 · {fins} 已落盘 · {drafts} 草稿{avg != null && <span> · 平均综合 <b style={{ color: scoreColor(avg) }}>{avg.toFixed(2)}</b></span>}
                </span>
                <span style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                    {modeBtn('map', '思维导图')}
                    {modeBtn('outline', '两级大纲')}
                    {mode !== 'auto' && <button type="button" onClick={() => setMode('auto')} style={{ ...tbBtn, color: 'var(--ink-sub)' }} title="跟随密度自动选择">自动</button>}
                </span>
                <span style={{ display: 'flex', gap: 4 }}>
                    <button type="button" onClick={() => zoomBtn(1.2)} title="放大" style={tbBtn}>＋</button>
                    <button type="button" onClick={() => zoomBtn(0.83)} title="缩小" style={tbBtn}>－</button>
                    <button type="button" onClick={fit} title="适应窗口" style={tbBtn}>适应</button>
                </span>
            </div>
            {effectiveMode === 'map' ? renderMap() : renderOutline()}
        </div>
    )
}
