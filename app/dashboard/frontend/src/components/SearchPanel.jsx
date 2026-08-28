import { useState } from 'react'
import { phAiSearch } from '../api.js'

/**
 * 独立检索面板（写书没思路时主动搜）：六源联邦 + 源过滤 + 精确模式（LLM 精排）。
 * 结果逐条带「发给 AI 讨论」→ 把 query+结果拼成消息交给对话窗口。
 *
 * Props:
 *  - bookRoot: 当前书根
 *  - onSendToChat: (text) => void
 *  - title: 面板标题（页签内区分）
 */
const SOURCES = [
    { key: 'book', label: '本书' },
    { key: 'corpus', label: '语料' },
    { key: 'csv', label: '资料' },
    { key: 'refmd', label: '文档' },
    { key: 'template', label: '模板' },
    { key: 'web', label: '外部' },
]
const SRC_COLOR = { book: 'var(--dai)', corpus: 'var(--green)', csv: 'var(--amber)', refmd: 'var(--cinnabar)', template: 'var(--cinnabar-d)', web: 'var(--ink-sub)' }

export default function SearchPanel({ bookRoot, onSendToChat, title = '检索' }) {
    const [query, setQuery] = useState('')
    const [sel, setSel] = useState(new Set(['book', 'corpus', 'csv', 'refmd', 'template', 'web']))
    const [rerank, setRerank] = useState(false)
    const [results, setResults] = useState(null)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState('')

    const toggleSrc = (k) => setSel(prev => {
        const next = new Set(prev)
        if (next.has(k)) next.delete(k); else next.add(k)
        return next
    })

    const doSearch = async () => {
        const q = query.trim()
        if (!q || busy) return
        setBusy(true); setError('')
        try {
            const r = await phAiSearch({
                book_root: bookRoot, query: q,
                sources: [...sel].length ? [...sel] : null,
                top_k: 6, rerank,
            })
            setResults(r)
        } catch (e) {
            setError(e.message || '检索失败')
        } finally {
            setBusy(false)
        }
    }

    const sendToChat = () => {
        const q = query.trim()
        if (!q || !results?.results?.length) return
        const lines = results.results.map(x => `- [${x.source}] ${x.title}：${x.snippet}`).join('\n')
        onSendToChat(`关于「${q}」我搜到这些结果，帮我看看怎么用：\n${lines}`)
    }

    return (
        <div className="section-block" style={{ borderLeft: '3px solid var(--dai)', marginTop: 10 }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>{title}（六源联邦 · 意图路由 · RRF 融合）</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
                {SOURCES.map(s => (
                    <button key={s.key} onClick={() => toggleSrc(s.key)}
                        style={{
                            padding: '3px 10px', borderRadius: 12, fontSize: 12, cursor: 'pointer', border: '1px solid var(--line-soft)',
                            background: sel.has(s.key) ? 'var(--dai-wash)' : 'var(--bg-card-2)', color: sel.has(s.key) ? 'var(--dai)' : 'var(--ink-sub)',
                            fontWeight: sel.has(s.key) ? 600 : 400,
                        }}>
                        {s.label}
                    </button>
                ))}
                <label style={{ fontSize: 12, color: 'var(--ink-sub)', display: 'flex', alignItems: 'center', gap: 4, marginLeft: 'auto' }}>
                    <input type="checkbox" checked={rerank} onChange={e => setRerank(e.target.checked)} />
                    精确模式（LLM 精排，慢一点）
                </label>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
                <textarea value={query} onChange={e => setQuery(e.target.value)} rows={2}
                    placeholder="没思路搜点什么…例：打脸桥段怎么写 / 灭门案冲突怎么加 / 断水剑旧印伏笔"
                    onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doSearch() } }}
                    style={{ flex: 1, boxSizing: 'border-box', padding: 8, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13 }} />
                <button style={{
                    padding: '6px 12px', borderRadius: 6, border: '1px solid var(--dai)', background: 'var(--dai)', color: 'var(--paper-raised)',
                    cursor: busy || !query.trim() ? 'not-allowed' : 'pointer', opacity: busy || !query.trim() ? 0.5 : 1, fontSize: 13,
                }} disabled={busy || !query.trim()} onClick={doSearch}>
                    {busy ? '…' : '检索'}
                </button>
            </div>
            {error && <div style={{ marginTop: 6, fontSize: 12, color: 'var(--cinnabar-d)' }}>⚠ {error}</div>}
            {results && (
                <div style={{ marginTop: 8 }}>
                    <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 4 }}>
                        命中 {results.results?.length || 0} 条 · 意图「{results.intent}」· 命中源 {results.sources_used?.join(', ') || '无'}
                        {results.results?.length > 0 && (
                            <button style={{ marginLeft: 8, padding: '2px 8px', borderRadius: 6, border: '1px solid var(--cinnabar)', background: 'var(--cinnabar-wash)', color: 'var(--cinnabar)', fontSize: 11, cursor: 'pointer' }}
                                onClick={sendToChat}>发给 AI 讨论</button>
                        )}
                    </div>
                    {results.reason && (
                        <div style={{ fontSize: 11, color: 'var(--dai)', marginBottom: 4 }}>
                            自主判别：查 {results.chosen_sources?.join(', ') || '无'} —— {results.reason}
                        </div>
                    )}
                    {results.results?.length === 0 && <div style={{ fontSize: 12, color: 'var(--ink-mute)' }}>（无命中，换个说法或换源试试）</div>}
                    {results.results?.map((x, i) => (
                        <div key={i} style={{ padding: '6px 8px', border: '1px solid var(--line)', borderRadius: 6, marginBottom: 4, fontSize: 12 }}>
                            <div style={{ fontWeight: 600, color: SRC_COLOR[x.source] || 'var(--ink-sub)' }}>
                                {x.title}
                                <span style={{ marginLeft: 6, fontWeight: 400, color: 'var(--ink-mute)' }}>→ {x.anchor}</span>
                            </div>
                            <div style={{ color: 'var(--ink-sub)', marginTop: 2 }}>{x.snippet}</div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}
