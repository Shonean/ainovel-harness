import { useCallback, useEffect, useState } from 'react'
import { phAiSearchSources, phAiSearchRebuild } from '../api.js'

/**
 * 检索体检面板（设定页签）：一眼看清哪些检索源是空的/有问题的。
 * 每源状态卡：健康（数量）/ ⚠空或未配置 / 有问题。
 * 「重建全部索引」→ 后台任务 + 轮询进度（正在 embed 哪本书 / done n/N）。
 */
const SRC_META = {
    book: { label: '本书索引', keys: ['book'] },
    corpus: { label: '语料索引', keys: ['corpus'] },
    csv: { label: '参考资料 CSV', keys: ['csv'] },
    refmd: { label: '参考文档 md', keys: ['refmd'] },
    template: { label: '情节模板库', keys: ['template'] },
    web: { label: '外部网页', keys: ['web'] },
    embed: { label: '向量模型', keys: ['embed'] },
}

function statusOf(key, s) {
    if (s?.issue) {
        // 空库/未配置 → 黄；缺 key/未生成 → 红
        const soft = ['template', 'web', 'corpus']
        return soft.includes(key) ? 'empty' : 'broken'
    }
    return 'healthy'
}

const BADGE = {
    healthy: { bg: 'var(--green-wash)', color: 'var(--green)', label: '健康' },
    empty: { bg: 'var(--amber-wash)', color: 'var(--amber)', label: '⚠空/未配置' },
    broken: { bg: 'var(--cinnabar-wash)', color: 'var(--cinnabar-d)', label: '有问题' },
}

export default function SearchHealthPanel({ bookRoot, compact = false }) {
    const [health, setHealth] = useState(null)
    const [rebuilding, setRebuilding] = useState(false)
    const [error, setError] = useState('')

    const load = useCallback(async () => {
        try {
            const h = await phAiSearchSources(bookRoot)
            setHealth(h)
        } catch (e) {
            setError(e.message || '体检加载失败')
        }
    }, [bookRoot])

    useEffect(() => { load() }, [load])

    // 重建后轮询进度
    useEffect(() => {
        if (!rebuilding) return
        const iv = setInterval(async () => {
            try {
                const h = await phAiSearchSources(bookRoot)
                setHealth(h)
                if (h?.corpus?.index_ready || h?.corpus?.progress?.status === 'done') {
                    setRebuilding(false)
                    clearInterval(iv)
                }
            } catch (e) { /* 继续轮询 */ }
        }, 8000)
        return () => clearInterval(iv)
    }, [rebuilding, bookRoot])

    const rebuild = async () => {
        setRebuilding(true); setError('')
        try {
            await phAiSearchRebuild(bookRoot)
        } catch (e) {
            setError(e.message || '重建失败')
            setRebuilding(false)
        }
    }

    const summary = health?.summary || { total: 0, healthy: 0, empty: 0, broken: 0 }
    const detail = (key, s) => {
        switch (key) {
            case 'book': return `设定${s.settings_generated ? '✓' : '✗'} · 元素${s.elements} · 情节${s.arcs} · 正文${s.prose_chars}字 · 索引${s.chunks}块${s.index_fresh ? '' : '（过期）'}${s.last_built ? ` · ${s.last_built}建` : ''}`
            case 'corpus': return s.index_ready ? `${s.books} 本语料已索引` : `${s.books} 本语料未建持久索引`
            case 'csv': return `${s.tables} 张表${s.names?.length ? '（' + s.names.join('、') + '）' : ''}`
            case 'refmd': return `${s.docs} 个文档 · ${s.sections} 节`
            case 'template': return `${s.count} 条合格模板`
            case 'web': return s.ok ? `内置 ${s.engine} 可用（keyless）` : `内置 ${s.engine}${s.last_error ? '（上次异常）' : '（未验证）'}`
            case 'embed': return `${s.model || '无模型'}${s.key_set ? '' : '（缺 key）'}`
            default: return ''
        }
    }

    // compact 模式：顶部状态条——只显示健康数 + 有问题的源，不铺网格
    if (compact) {
        const rebBtn = (
            <button title="重建全部索引" onClick={rebuild} disabled={rebuilding}
                style={{ padding: '1px 7px', borderRadius: 6, border: '1px solid var(--dai)', background: 'var(--paper-raised)', color: 'var(--dai)',
                    cursor: rebuilding ? 'not-allowed' : 'pointer', opacity: rebuilding ? 0.6 : 1, fontSize: 11 }}>
                {rebuilding ? '重建中…' : '重建'}
            </button>
        )
        if (!health) return (
            <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--ink-mute)' }}>
                检索体检加载中…{rebBtn}
            </span>
        )
        const prob = Object.keys(SRC_META).filter(key => statusOf(key, health[key]) !== 'healthy')
        return (
            <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', fontSize: 12, color: 'var(--ink-sub)' }}>
                <span>检索 <b style={{ color: summary.broken ? 'var(--cinnabar-d)' : summary.empty ? 'var(--amber)' : 'var(--green)' }}>{summary.healthy}/{summary.total}</b> 健康</span>
                {prob.length === 0 && <span style={{ color: 'var(--green)' }}>全健康</span>}
                {prob.map(key => {
                    const s = health[key]
                    const soft = statusOf(key, s) === 'empty'
                    return (
                        <span key={key} title={s?.issue || SRC_META[key].label}
                            style={{ padding: '1px 7px', borderRadius: 8, background: soft ? 'var(--amber-wash)' : 'var(--cinnabar-wash)', color: soft ? 'var(--amber)' : 'var(--cinnabar-d)' }}>
                            {soft ? '⚠' : ''} {SRC_META[key].label}
                        </span>
                    )
                })}
                {rebBtn}
                {error && <span style={{ color: 'var(--cinnabar-d)' }}>⚠ {error}</span>}
            </span>
        )
    }

    return (
        <div className="section-block" style={{ borderLeft: '3px solid var(--dai)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 15, fontWeight: 700 }}>检索体检</span>
                <span style={{ fontSize: 12, color: 'var(--ink-sub)' }}>
                    共 {summary.total} 源 · 健康 {summary.healthy} · ⚠空 {summary.empty} · 有问题 {summary.broken}
                </span>
                <span style={{ marginLeft: 'auto' }}>
                    <button style={{
                        padding: '6px 12px', borderRadius: 6, border: '1px solid var(--dai)', background: 'var(--dai)', color: 'var(--paper-raised)',
                        cursor: rebuilding ? 'not-allowed' : 'pointer', opacity: rebuilding ? 0.6 : 1, fontSize: 13,
                    }} disabled={rebuilding} onClick={rebuild}>
                        {rebuilding ? '重建中…' : '重建全部索引'}
                    </button>
                </span>
            </div>
            {error && <div style={{ fontSize: 12, color: 'var(--cinnabar-d)', marginBottom: 6 }}>⚠ {error}</div>}
            {health?.corpus?.progress?.status === 'building' && (
                <div style={{ fontSize: 12, color: 'var(--dai)', marginBottom: 6 }}>
                    ⏳ 正在构建语料索引：{health.corpus.progress.book || '…'}（已完成 {health.corpus.progress.done}/{health.corpus.progress.books} 本）…
                </div>
            )}
            {!health ? (
                <div style={{ color: 'var(--ink-mute)', fontSize: 12 }}>加载体检…</div>
            ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 8 }}>
                    {Object.keys(SRC_META).map(key => {
                        const s = health[key] || {}
                        const st = statusOf(key, s)
                        const badge = BADGE[st]
                        return (
                            <div key={key} style={{ padding: 8, borderRadius: 6, border: '1px solid var(--line)', background: 'var(--paper-raised)' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                    <span style={{ fontSize: 13, fontWeight: 600 }}>{SRC_META[key].label}</span>
                                    <span style={{ marginLeft: 'auto', fontSize: 11, padding: '1px 8px', borderRadius: 10, background: badge.bg, color: badge.color }}>
                                        {badge.label}
                                    </span>
                                </div>
                                <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginTop: 4 }}>{detail(key, s)}</div>
                                {s.issue && <div style={{ fontSize: 11, color: st === 'broken' ? 'var(--cinnabar-d)' : 'var(--amber)', marginTop: 3 }}>{s.issue}</div>}
                            </div>
                        )
                    })}
                </div>
            )}
        </div>
    )
}
