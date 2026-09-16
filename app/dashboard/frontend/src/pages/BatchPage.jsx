/**
 * 批量出片（T36 v9）：量产书按弧排队出片——每弧一集，失败重试不阻塞后续。
 *
 * 数据/动作：POST /ai-creation/drama/batch（list/queue/run/pause/retry）
 * 运行中轮询队列状态（后台任务：构建 pack → 投影 → 合成）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchCurrentProject, fetchProjects, phDramaBatch } from '../api.js'
import { toast } from '../lib/toast.js'

function StatusBadge({ status }) {
    if (status === 'locked') return <span className="badge badge-neutral">缺 l4 · 先推进</span>
    if (status === 'ready') return <span className="badge badge-cyan">待运行</span>
    if (status === 'running') return <span className="badge badge-amber">出片中…</span>
    if (status === 'paused') return <span className="badge badge-amber">已暂停</span>
    if (status === 'done') return <span className="badge badge-green">已完成</span>
    return <span className="badge badge-red">失败</span>
}

export default function BatchPage() {
    const navigate = useNavigate()
    const [bookRoot, setBookRoot] = useState('')
    const [books, setBooks] = useState([])
    const [items, setItems] = useState([])
    const [running, setRunning] = useState(false)
    const [paused, setPaused] = useState(false)
    const [error, setError] = useState('')
    const [busy, setBusy] = useState(false)

    useEffect(() => {
        fetchProjects().then(r => {
            const list = (r.projects || []).filter(p => !p.incomplete)
            setBooks(list)
            if (!bookRoot) {
                fetchCurrentProject().then(cur => {
                    if (cur?.project_root && list.some(b => b.project_root === cur.project_root)) setBookRoot(cur.project_root)
                    else if (list.length) setBookRoot(list[0].project_root)
                }).catch(() => { if (list.length) setBookRoot(list[0].project_root) })
            }
        }).catch(e => setError(e.message))
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    const load = useCallback(async () => {
        if (!bookRoot) return
        try {
            const r = await phDramaBatch(bookRoot, 'queue')
            setItems(r.items || [])
            setRunning(!!r.running)
            setPaused(!!r.paused)
        } catch (e) { setError(e.message || '加载队列失败') }
    }, [bookRoot])

    useEffect(() => { load() }, [load])

    useEffect(() => {
        if (!running) return
        const iv = setInterval(load, 1500)
        return () => clearInterval(iv)
    }, [running, load])

    const stats = useMemo(() => ({
        total: items.length,
        done: items.filter(i => i.status === 'done').length,
        failed: items.filter(i => i.status === 'failed').length,
        cost: items.reduce((a, i) => a + (i.cost || 0), 0),
    }), [items])

    const run = async () => {
        if (busy) return
        setBusy(true)
        try {
            const r = await phDramaBatch(bookRoot, 'run')
            if (r.ok) { toast(`批量出片开始（${r.queued} 个弧）`); setRunning(true); load() }
            else toast(r.error)
        } finally { setBusy(false) }
    }
    const pause = async () => {
        const r = await phDramaBatch(bookRoot, 'pause')
        if (r.ok) { toast('已暂停（可续跑）'); setRunning(false); setPaused(true); load() }
        else toast(r.error)
    }
    const retry = async () => {
        const r = await phDramaBatch(bookRoot, 'retry')
        if (r.ok) { toast(r.retried ? `已重试 ${r.retried} 个失败弧` : '没有失败的弧'); load() }
        else toast(r.error)
    }

    return (
        <div className="z-page">
            <header className="z-head">
                <button className="z-back" onClick={() => navigate('/adaptation/films')}>← 成片管理</button>
                <div className="z-title">批量出片</div>
                <div className="z-sub">量产书按弧排队出片：每弧一集，失败重试不阻塞后续；成本逐弧可见</div>
                <span className="grow" />
                <select className="btn btn-small" value={bookRoot} onChange={e => setBookRoot(e.target.value)}
                    style={{ padding: '4px 8px' }}>
                    {books.map(b => <option key={b.project_root} value={b.project_root}>{b.name}{b.book_mode === 'mass' ? '（量产）' : ''}</option>)}
                    {!books.length && <option value="">无可用书</option>}
                </select>
                <button className="btn btn-small" disabled={busy || !bookRoot || running} onClick={run}>开始批量</button>
                <button className="btn btn-small" disabled={!running} onClick={pause}>暂停</button>
                <button className="btn btn-blue btn-small" onClick={retry}>失败重试</button>
            </header>

            <div className="z-stats">
                <div className="z-stat"><b>{stats.total}</b><span>队列弧数</span></div>
                <div className="z-stat"><b>{stats.done}</b><span>已完成</span></div>
                <div className="z-stat"><b>{stats.failed}</b><span>失败</span></div>
                <div className="z-stat"><b>¥{stats.cost.toFixed(1)}</b><span>累计成本（估算）</span></div>
                <div className="z-stat"><b>{stats.done}</b><span>产出单集</span></div>
            </div>

            <div className="z-body">
                {error && <div className="zsec-empty">{error}</div>}
                {!error && (
                    <section className="zsec" style={{ marginTop: 10 }}>
                        <div className="zsec-h"><div className="zsec-t">队列</div>
                            <div className="zsec-meta">仅列出有 l4 快照的弧可运行；无快照弧灰态并给「去推进」</div></div>
                        <div className="bt-row" style={{ background: 'var(--paper)', fontWeight: 700 }}>
                            <span className="nm">情节</span><span style={{ flex: '0 0 120px' }}>模式</span>
                            <span className="grow">进度</span><span className="st">状态</span>
                            <span style={{ flex: '0 0 84px', textAlign: 'right' }}>成本</span>
                        </div>
                        <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
                            {items.map((b, i) => (
                                <div className="bt-row" key={b.arc_id || i}>
                                    <span className="nm">{b.name || b.arc_id}</span>
                                    <span style={{ flex: '0 0 120px', color: 'var(--ink-mute)' }}>{b.l4 > 0 ? '量产 fast' : '—'}</span>
                                    <span className="prog"><i style={{ width: `${b.progress || 0}%` }} /></span>
                                    <span className="st"><StatusBadge status={b.status} /></span>
                                    <span style={{ flex: '0 0 84px', textAlign: 'right' }}>{b.cost ? `¥${Number(b.cost).toFixed(2)}` : '—'}</span>
                                    {b.status === 'locked' && (
                                        <button className="mini-btn" onClick={async () => {
                                            try { await fetch(`/api/project/switch`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project_root: bookRoot }) }) } catch { /* 忽略 */ }
                                            navigate('/ai-creation?tab=create')
                                        }}>去推进</button>
                                    )}
                                    {b.status === 'failed' && (
                                        <button className="mini-btn primary" onClick={retry}>重试</button>
                                    )}
                                </div>
                            ))}
                            {!items.length && <div className="zsec-empty">该书还没有弧——先到创作里生成情节。</div>}
                        </div>
                        {items.some(i => i.status === 'failed') && (
                            <div className="gp-note">
                                失败原因：{items.filter(i => i.status === 'failed').map(i => `${i.name}: ${i.error || '未知'}`).join('；')}
                            </div>
                        )}
                    </section>
                )}
            </div>
        </div>
    )
}
