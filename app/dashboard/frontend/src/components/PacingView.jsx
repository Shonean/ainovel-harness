import ChartWrapper from './ChartWrapper.jsx'

export default function PacingView({ arcs }) {
    const list = arcs?.arcs || []
    const chapters = list.flatMap(a => (a.chapters || []).map(c => ({ ...c, arcName: a.name })))
    const words = chapters.map(c => c.text ? c.text.length : 0)
    const avgW = words.length ? words.reduce((a, b) => a + b, 0) / words.length : 0

    const wordOption = {
        tooltip: { trigger: 'axis' },
        grid: { left: 40, right: 16, top: 20, bottom: 40 },
        xAxis: { type: 'category', data: chapters.map(c => `第${c.num}章`) },
        yAxis: { type: 'value' },
        series: [{
            type: 'bar',
            data: words,
            itemStyle: { color: 'var(--dai)' },
            label: { show: true, position: 'top', fontSize: 10 }
        }],
    }

    return (
        <div>
            <div className="stat-grid">
                {[
                    ['章数', String(chapters.length)],
                    ['平均字数', Math.round(avgW) || '—'],
                    ['最长章', String(Math.max(0, ...words))],
                    ['最短章', String(Math.min(...words))]
                ].map(([label, value]) => (
                    <article key={label} className="card stat-card">
                        <span className="stat-label">{label}</span>
                        <span className="stat-value">{value}</span>
                    </article>
                ))}
            </div>
            {chapters.length ? (
                <article className="card" style={{ marginTop: 12 }}>
                    <div className="card-header">
                        <div className="card-title">每章字数分布</div>
                    </div>
                    <ChartWrapper option={wordOption} height={300} />
                </article>
            ) : (
                <div className="empty-state" style={{ margin: '20px 0' }}>
                    <p>暂无章节字数数据</p>
                </div>
            )}
            <div className="section-label" style={{ marginTop: 16 }}>
                情节进度甘特（情节 → 章）
            </div>
            {list.length ? (
                list.map(a => (
                    <div key={a.id} style={{ marginBottom: 10, border: '1px solid var(--line)', borderRadius: 8, padding: 10 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                            {a.name} {a.status === 'done' ? '' : a.status === 'active' ? '' : '○'}
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                            {(a.chapters || []).map(c => (
                                <span
                                    key={c.num}
                                    title={`第${c.num}章 ${c.title} · 综合${c.overall ?? '—'}`}
                                    style={{
                                        padding: '3px 8px',
                                        borderRadius: 8,
                                        fontSize: 12,
                                        background: c.polluted ? 'var(--amber-wash)' : 'var(--green-wash)',
                                        color: 'var(--ink)',
                                        border: '1px solid var(--line)'
                                    }}
                                >
                                    {c.num}{c.overall != null ? ` ${(c.overall).toFixed(2)}` : ''}
                                </span>
                            ))}
                        </div>
                    </div>
                ))
            ) : (
                <div className="empty-state">
                    <p>还没有情节</p>
                </div>
            )}
        </div>
    )
}
