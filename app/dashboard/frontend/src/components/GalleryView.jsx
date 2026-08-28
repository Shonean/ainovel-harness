import { Fragment } from 'react'

// 地图布局渲染
function MapLayout({ layout }) {
    if (!layout || !layout.length) return null
    const find = (z) => layout.find(x => x.zone === z)
    if (find('palace') || find('imperial')) {
        return (
            <div className="map-nest">
                {layout.map(z => <div key={z.zone} className={`map-zone nest-${z.zone}`} title={z.desc}>{z.name}</div>)}
            </div>
        )
    }
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

export default function GalleryView({ arcs, elements, galleryExpanded, onGalleryExpandedChange }) {
    const list = arcs?.arcs || []
    const kinds = [
        ['characters', '角色', ''],
        ['items', '物品', ''],
        ['settings', '设定', ''],
        ['locations', '地点', ''],
        ['maps', '地图', '']
    ]

    const rows = []
    for (const [coll, label, emoji] of kinds) {
        for (const e of (elements[coll] || [])) {
            const usedArcs = list.filter(a => (a.selected?.[coll] || []).includes(e.id))
            const chapNums = usedArcs.flatMap(a => (a.chapters || []).map(c => c.num))
            rows.push({ coll, label, emoji, elem: e, usedArcs, chapNums })
        }
    }

    const totalCh = list.reduce((s, a) => s + (a.chapters || []).length, 0)

    // 关联地图卡
    const relatedMaps = (e) => {
        const out = []
        for (const m of (elements.maps || [])) {
            if (m.id === e.id) { out.push(m); continue }
            const byRel = (m.relations || []).some(r => r.to_id === e.id)
            const toRel = (e.relations || []).some(r => r.to_kind === 'map' && r.to_id === m.id)
            if (byRel || toRel) out.push(m)
        }
        return out
    }

    const relName = (r) => {
        for (const [coll] of kinds) {
            for (const x of (elements[coll] || [])) {
                if (x.id === r.to_id) return `${x.name}`
            }
        }
        return r.to_id
    }

    return (
        <div>
            <div className="stat-grid">
                {[
                    ['角色', String((elements.characters || []).length)],
                    ['物品', String((elements.items || []).length)],
                    ['设定', String((elements.settings || []).length)],
                    ['地图', String((elements.maps || []).length)],
                    ['情节', String(list.length)],
                    ['章', String(totalCh)]
                ].map(([label, value]) => (
                    <article key={label} className="card stat-card">
                        <span className="stat-label">{label}</span>
                        <span className="stat-value">{value}</span>
                    </article>
                ))}
            </div>
            <article className="card" style={{ marginTop: 12 }}>
                <div className="card-header">
                    <div className="card-title">角色 / 物品 / 设定 / 地图 · 点开看详情与地图</div>
                </div>
                {rows.length ? (
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>名称</th>
                                <th>参与情节</th>
                                <th>出场章</th>
                                <th>描述</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map(r => (
                                <Fragment key={r.elem.id}>
                                    <tr
                                        onClick={() => onGalleryExpandedChange(galleryExpanded === r.elem.id ? null : r.elem.id)}
                                        style={{ cursor: 'pointer' }}
                                    >
                                        <td style={{ fontWeight: 600 }}>{r.elem.name}</td>
                                        <td>{r.usedArcs.length ? r.usedArcs.map(a => a.name).join('、') : '—'}</td>
                                        <td>
                                            {r.chapNums.length ? (
                                                r.chapNums.join('、')
                                            ) : (
                                                <span style={{ color: 'var(--ink-mute)' }}>未出场</span>
                                            )}
                                        </td>
                                        <td>{(r.elem.desc || '').slice(0, 40) || '—'}</td>
                                    </tr>
                                    {galleryExpanded === r.elem.id && (
                                        <tr className="gallery-detail-row">
                                            <td colSpan={4}>
                                                <div className="gallery-detail">
                                                    <div className="gallery-desc">{r.elem.desc || '（无描述）'}</div>
                                                    {(r.elem.fields || []).length > 0 && (
                                                        <div className="gallery-fields">
                                                            {(r.elem.fields || []).map((f, i) => (
                                                                <div className="gallery-frow" key={i}>
                                                                    <span className="fn">{f.name}</span>
                                                                    <span className="fv">{f.value}</span>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    )}
                                                    {(r.elem.relations || []).length > 0 && (
                                                        <div className="gallery-rellist">
                                                            {(r.elem.relations || []).map((rel, i) => (
                                                                <span key={i} className="gallery-rel">
                                                                    {rel.name} → {relName(rel)}
                                                                </span>
                                                            ))}
                                                        </div>
                                                    )}
                                                    {r.coll === 'maps' && <MapLayout layout={r.elem.layout} />}
                                                    {r.coll !== 'maps' && relatedMaps(r.elem).length > 0 && (
                                                        <div className="gallery-maps">
                                                            <div className="gallery-maps-title">相关地图</div>
                                                            {relatedMaps(r.elem).map(m => (
                                                                <div key={m.id} className="gallery-map-block">
                                                                    <div className="gallery-map-name">{m.name}</div>
                                                                    <MapLayout layout={m.layout} />
                                                                </div>
                                                            ))}
                                                        </div>
                                                    )}
                                                </div>
                                            </td>
                                        </tr>
                                    )}
                                </Fragment>
                            ))}
                        </tbody>
                    </table>
                ) : (
                    <div className="empty-state">
                        <p>还没有元素——先在「大纲与章纲」创建</p>
                    </div>
                )}
            </article>
        </div>
    )
}
