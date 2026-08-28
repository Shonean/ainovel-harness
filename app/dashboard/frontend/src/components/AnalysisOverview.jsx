import ChartWrapper from './ChartWrapper.jsx'

export default function AnalysisOverview({ arcs }) {
    const list = arcs?.arcs || []
    const chapters = list.flatMap(a => (a.chapters || []).map(c => ({ ...c, arcName: a.name })))
    const words = chapters.reduce((s, c) => s + (c.text ? c.text.length : 0), 0)
    const polluted = chapters.filter(c => c.polluted).length
    const avg = arr => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0)
    const intAvg = avg(chapters.filter(c => c.intent_score != null).map(c => c.intent_score))
    const quaAvg = avg(chapters.filter(c => c.quality_score != null).map(c => c.quality_score))

    const scoreOption = {
        tooltip: { trigger: 'axis' },
        legend: { bottom: 0, data: ['意图兑现', '纯质量', '综合'] },
        grid: { left: 40, right: 16, top: 30, bottom: 40 },
        xAxis: { type: 'category', data: chapters.map(c => `第${c.num}章`) },
        yAxis: { type: 'value', min: 0, max: 1 },
        series: [
            { name: '意图兑现', type: 'line', data: chapters.map(c => c.intent_score), symbolSize: 6 },
            { name: '纯质量', type: 'line', data: chapters.map(c => c.quality_score), symbolSize: 6 },
            { name: '综合', type: 'line', data: chapters.map(c => c.overall), symbolSize: 6 },
        ],
    }

    return (
        <div>
            <div className="stat-grid">
                {[
                    ['情节', String(list.length)],
                    ['已落盘章', String(chapters.length)],
                    ['总字数', String(words)],
                    ['⚠ 污染章', String(polluted)],
                    ['意图均分', intAvg ? intAvg.toFixed(3) : '—'],
                    ['质量均分', quaAvg ? quaAvg.toFixed(3) : '—']
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
                        <div className="card-title">每章双评分走势</div>
                    </div>
                    <ChartWrapper option={scoreOption} height={300} />
                </article>
            ) : (
                <div className="empty-state" style={{ margin: '20px 0' }}>
                    <p>还没有落盘章节——先在「章节生成」逐情节创作，或用「批量生成」。</p>
                </div>
            )}
        </div>
    )
}
