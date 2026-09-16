import { useCallback, useEffect, useState } from 'react'
import { phListPlotTemplates, phDeletePlotTemplate, phPlotTemplatePerf } from '../api.js'
import { templateFields } from '../plotFields.js'

const card = { border: '1px solid var(--line)', borderRadius: 8, padding: 12, background: 'var(--paper-raised)', marginBottom: 10 }
const btn = (primary) => ({
    padding: '8px 16px', borderRadius: 6, border: '1px solid var(--line-soft)',
    background: primary ? 'var(--green)' : 'var(--paper-raised)', color: primary ? 'var(--paper-raised)' : 'var(--ink)',
    cursor: 'pointer', fontSize: 14, fontWeight: primary ? 600 : 400,
})

/**
 * 情节模板库（独立页）：与主系统 AI 创作相连的内容。
 * 模板来源 = 榨干提取 达标后的情节入库（去实体化槽位模板）。
 */
export default function TemplateLibraryPage() {
    const [templates, setTemplates] = useState([])
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')
    const [perf, setPerf] = useState(null)
    const [showPerf, setShowPerf] = useState(false)

    const loadTemplates = useCallback(async () => {
        setLoading(true)
        setError('')
        try {
            const r = await phListPlotTemplates()
            setTemplates(Array.isArray(r) ? r : [])
        } catch (e) {
            setError(String(e.message || e))
        }
        setLoading(false)
    }, [])

    const loadPerf = useCallback(async () => {
        try {
            const r = await phPlotTemplatePerf()
            setPerf(r)
        } catch (e) {
            setError(`绩效加载失败：${e.message || e}`)
        }
    }, [])
    useEffect(() => { loadTemplates() }, [loadTemplates])

    const handleDelete = async (id) => {
        try {
            await phDeletePlotTemplate(id)
            loadTemplates()
        } catch (e) {
            setError(`删除失败：${e.message || e}`)
        }
    }

    return (
        <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                <div style={{ fontSize: 15, fontWeight: 700 }}>情节模板库（唯一能与主系统连接的内容）</div>
                <span style={{ fontSize: 12, color: 'var(--ink-sub)' }}>{templates.length} 条</span>
                <button style={{ marginLeft: 'auto', ...btn(false) }} onClick={loadTemplates} disabled={loading}>
                    {loading ? '刷新中…' : '↻ 刷新'}
                </button>
            </div>
            <div style={{ marginBottom: 10 }}>
                <button
                    style={{ fontSize: 13, color: 'var(--dai)', background: 'transparent', border: 'none', cursor: 'pointer', padding: '2px 0' }}
                    onClick={() => { if (!perf) loadPerf(); setShowPerf(!showPerf) }}
                >
                    {showPerf ? '▾ 收起绩效' : '▸ 模板绩效面板'}
                </button>
                {showPerf && perf && (
                    <div style={{ border: '1px solid var(--line)', borderRadius: 8, padding: 10, marginTop: 6, background: 'var(--paper-raised)' }}>
                        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                            总 {perf.total} 条 ｜ 被使用 {perf.used} ｜ 从未使用 <b style={{ color: perf.never_used > perf.total * 0.5 ? 'var(--cinnabar-d)' : 'inherit' }}>{perf.never_used}</b>
                        </div>
                        {perf.by_usage?.length > 0 && (
                            <div style={{ marginBottom: 8 }}>
                                <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 3 }}>使用排行（创作时最常被套用）</div>
                                {perf.by_usage.slice(0, 10).map(u => (
                                    <div key={u.id} style={{ fontSize: 12, padding: '2px 0', display: 'flex', gap: 8 }}>
                                        <b>{u.use_count}次</b>
                                        <span>{u.name}</span>
                                        {u.avg_quality != null && <span style={{ color: 'var(--green)' }}>avg {u.avg_quality.toFixed?.(3)}</span>}
                                    </div>
                                ))}
                            </div>
                        )}
                        {perf.by_quality?.length > 0 && (
                            <div style={{ marginBottom: 8 }}>
                                <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 3 }}>质量排行（落盘后效果最好）</div>
                                {perf.by_quality.slice(0, 8).map(u => (
                                    <div key={u.id} style={{ fontSize: 12, padding: '2px 0', display: 'flex', gap: 8 }}>
                                        <b>{(u.avg_quality ?? 0).toFixed(3)}</b>
                                        <span>{u.name}</span>
                                        <span style={{ color: 'var(--ink-mute)' }}>{u.n_finalized}次落盘</span>
                                    </div>
                                ))}
                            </div>
                        )}
                        {perf.by_archetype?.length > 0 && (
                            <div style={{ marginBottom: 8 }}>
                                <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 3 }}>题材分布（情节类型 × 模板数/使用）</div>
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 4 }}>
                                    {perf.by_archetype.slice(0, 12).map(a => (
                                        <div key={a.archetype} style={{ fontSize: 12, padding: '3px 6px', background: 'var(--bg-card-2)', borderRadius: 4 }}>
                                            <b>{a.archetype}</b> <span style={{ color: 'var(--ink-mute)' }}>{a.templates}条/用{a.use_count}</span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                        {perf.match_stats?.length > 0 && (
                            <div>
                                <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 3 }}>命中统计（new_arc 建情节时被匹配）</div>
                                {perf.match_stats.slice(0, 10).map(m => (
                                    <div key={m.name} style={{ fontSize: 12, padding: '2px 0' }}>· {m.name} <span style={{ color: 'var(--ink-mute)' }}>×{m.hits}</span></div>
                                ))}
                            </div>
                        )}
                        {!perf.by_usage?.length && !perf.by_quality?.length && !perf.match_stats?.length && (
                            <div style={{ fontSize: 12, color: 'var(--ink-mute)' }}>（尚无使用记录——模板被 new_arc 命中/工作台套用后，使用次数与质量分会自动累计到这里）</div>
                        )}
                    </div>
                )}
            </div>
            {error && <div style={{ marginBottom: 8, padding: 8, borderRadius: 6, background: 'var(--cinnabar-wash)', color: 'var(--cinnabar-d)', fontSize: 13 }}>⚠ {error}</div>}
            {templates.length === 0 && !loading && (
                <div style={{ color: 'var(--ink-mute)', fontSize: 13, padding: 8 }}>
                    （库为空：先到「榨干提取」从语料中榨取模板，达标情节 整情节入库后出现在这里）
                </div>
            )}
            {templates.map(t => (
                <div key={t.id} style={card}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <b style={{ fontSize: 14 }}>{t.name}</b>
                        <span style={{ fontSize: 12, color: 'var(--ink-sub)' }}>
                            score {t.qualified?.score?.toFixed?.(3)}｜s_char {t.qualified?.s_char?.toFixed?.(3)}
                        </span>
                        <button style={{ marginLeft: 'auto', ...btn(false) }} onClick={() => handleDelete(t.id)}>删除</button>
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--ink-sub)', marginTop: 2 }}>{t.description}</div>
                    <div style={{ fontSize: 12, color: 'var(--dai)', marginTop: 2 }}>
                        来源：{t.source?.corpus
                            ? `${t.source.corpus} · 第 ${t.source.chapter_start && t.source.chapter_end ? `${t.source.chapter_start}-${t.source.chapter_end}` : (t.source.chapter ?? '?')} 章`
                            : '（未记录来源书）'}
                        {t.archetype ? ` ｜ 原型：${t.archetype}` : ''}
                    </div>
                    <details style={{ marginTop: 4 }}>
                        <summary style={{ fontSize: 13, color: 'var(--cinnabar)', cursor: 'pointer' }}>
                            专业字段表单（9 字段 · 主系统填这个）
                        </summary>
                        <div style={{ background: 'var(--cinnabar-wash)', padding: 8, borderRadius: 6, marginTop: 4 }}>
                            {templateFields(t).map(f => (
                                <div key={f.key} style={{ fontSize: 13, marginBottom: 3 }}>
                                    <b>{f.label}：</b>{f.hint || '（无）'}
                                </div>
                            ))}
                        </div>
                    </details>
                    <details style={{ marginTop: 4 }}>
                        <summary style={{ fontSize: 13, color: 'var(--dai)', cursor: 'pointer' }}>槽位化模板（4 级）</summary>
                        <div style={{ background: 'var(--bg-card-2)', padding: 8, borderRadius: 6, marginTop: 4 }}>
                            {(['l1', 'l2', 'l3', 'l4']).map(k => (
                                <div key={k} style={{ fontSize: 13, marginBottom: 4 }}>
                                    <b>{k}:</b> {(t.levels_text?.[k] || '')}
                                </div>
                            ))}
                        </div>
                    </details>
                    <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginTop: 4 }}>策略：{JSON.stringify(t.strategy || {})}</div>
                </div>
            ))}
            {loading && <div style={{ color: 'var(--ink-mute)', fontSize: 13, padding: 8 }}>加载中…</div>}
        </div>
    )
}
