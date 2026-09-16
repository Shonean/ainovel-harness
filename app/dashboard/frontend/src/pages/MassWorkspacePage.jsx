/**
 * 量产工作台：生产线（P4）/ 路线图 / 章节 / 设置（模式切换）。
 *
 * 挂在 /mass；当前书的 book_mode 决定书级模式。精品书可切到量产（批量运行中禁止，后端 409）。
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
    fetchCurrentProject, switchBookMode,
    phAiBatchGenerate, phAiOptimizeStatus, phAiCancelTask, phAiRuns,
    phAiDeferredCount, phAiRescoreDeferred,
} from '../api.js'
import RoadmapBoard from '../components/RoadmapBoard.jsx'
import AdaptPanel from '../components/AdaptPanel.jsx'

const TABS = [
    { key: 'prod', label: '生产线' },
    { key: 'roadmap', label: '路线图' },
    { key: 'chapters', label: '章节' },
    { key: 'adapt', label: '改编' },
    { key: 'settings', label: '设置' },
]

export default function MassWorkspacePage() {
    const navigate = useNavigate()
    const [loading, setLoading] = useState(true)
    const [bookRoot, setBookRoot] = useState('')
    const [title, setTitle] = useState('')
    const [bookMode, setBookMode] = useState('premium')
    const [tab, setTab] = useState('roadmap')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState('')
    const [notice, setNotice] = useState('')

    const load = useCallback(async () => {
        setLoading(true)
        try {
            const cur = await fetchCurrentProject()
            if (cur?.project_root) {
                setBookRoot(cur.project_root)
                setTitle(cur.title || '')
                setBookMode(cur.book_mode === 'mass' ? 'mass' : 'premium')
            }
        } catch (e) {
            setError(e?.message || String(e))
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { load() }, [load])

    const flash = (m) => { setNotice(m); setTimeout(() => setNotice(''), 3000) }

    const onSwitchMode = useCallback(async (mode) => {
        if (mode === bookMode) return
        const label = mode === 'mass' ? '量产' : '精品'
        if (!window.confirm(`切换到「${label}」模式？两种模式数据同构，随时可再切回。\n批量生成运行中时会拒绝切换。`)) return
        setBusy(true)
        setError('')
        try {
            await switchBookMode(bookRoot, mode)
            setBookMode(mode)
            flash(`已切换到${label}模式`)
        } catch (e) {
            setError(e?.message || String(e))
        } finally {
            setBusy(false)
        }
    }, [bookRoot, bookMode])

    if (loading) {
        return <div className="wb-stage" style={{ padding: 40, color: 'var(--ink-sub)' }}>加载当前书…</div>
    }
    if (!bookRoot) {
        return (
            <div className="wb-stage" style={{ padding: 40, textAlign: 'center' }}>
                <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 10 }}>请先选择一本书</div>
                <a className="btn" href="#/">去书架</a>
            </div>
        )
    }

    return (
        <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--paper)' }}>
            <header style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px',
                paddingRight: 276, borderBottom: '1px solid var(--line)',
                background: 'var(--paper-raised)', flexShrink: 0,
            }}>
                <button className="btn btn-small" onClick={() => navigate('/', { replace: true })}>⇦ 书架</button>
                <b style={{ fontSize: 15 }}>{title || '（未命名）'}</b>
                <span className={`badge ${bookMode === 'mass' ? 'badge-amber' : 'badge-cyan'}`}>
                    {bookMode === 'mass' ? '量产' : '精品'}
                </span>
                <div style={{ flex: 1 }} />
                <button className="btn btn-small ghost" onClick={() => navigate('/ai-creation')}>打开完整工作台</button>
            </header>

            <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
                <nav style={{ width: 168, borderRight: '1px solid var(--line)', padding: '12px 8px', flexShrink: 0 }}>
                    {TABS.map((t) => (
                        <button
                            key={t.key}
                            onClick={() => setTab(t.key)}
                            style={{
                                display: 'block', width: '100%', textAlign: 'left',
                                padding: '8px 12px', marginBottom: 4, borderRadius: 6, cursor: 'pointer',
                                border: 'none', font: 'inherit', fontSize: 13.5,
                                background: tab === t.key ? 'var(--dai-wash, rgba(61,107,73,.12))' : 'transparent',
                                color: tab === t.key ? 'var(--dai)' : 'var(--ink)',
                                fontWeight: tab === t.key ? 700 : 400,
                            }}
                        >{t.label}</button>
                    ))}
                </nav>

                <main style={{ flex: 1, minWidth: 0, overflowY: 'auto', padding: '14px 20px' }}>
                    {notice && <div style={{ marginBottom: 10, padding: '6px 12px', borderRadius: 6, background: 'var(--green-wash)', color: 'var(--green)', fontSize: 12.5 }}>{notice}</div>}
                    {error && <div style={{ marginBottom: 10, padding: '6px 12px', borderRadius: 6, background: 'var(--cinnabar-wash)', color: 'var(--cinnabar-d)', fontSize: 12.5 }}>{error}</div>}

                    {bookMode !== 'mass' && tab !== 'settings' && (
                        <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 6, background: 'var(--amber-wash, #fdf3e0)', fontSize: 12.5 }}>
                            当前是精品书。量产工作台（路线图/生产线）建议切到量产模式使用，
                            <button className="btn btn-small" style={{ marginLeft: 8 }} disabled={busy} onClick={() => onSwitchMode('mass')}>切换为量产</button>
                        </div>
                    )}

                    {tab === 'prod' && (
                        <ProductionLine
                            bookRoot={bookRoot}
                            onNotice={flash}
                            onOpenRoadmap={() => setTab('roadmap')}
                        />
                    )}
                    {tab === 'roadmap' && <RoadmapBoard bookRoot={bookRoot} />}
                    {tab === 'adapt' && <AdaptPanel bookRoot={bookRoot} />}
                    {tab === 'chapters' && (
                        <div style={{ padding: 24, textAlign: 'center', color: 'var(--ink-sub)', border: '1px dashed var(--line)', borderRadius: 10 }}>
                            章节浏览请到完整工作台（左侧章节列表）。
                            <div style={{ marginTop: 10 }}>
                                <button className="btn btn-small" onClick={() => navigate('/ai-creation')}>打开完整工作台</button>
                            </div>
                        </div>
                    )}
                    {tab === 'settings' && (
                        <div style={{ maxWidth: 560 }}>
                            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10 }}>书级模式</div>
                            <div style={{ fontSize: 13, color: 'var(--ink-sub)', lineHeight: 1.8, marginBottom: 12 }}>
                                精品：五级阶梯逐级确认、全量评分、完整编辑与书级讨论。<br />
                                量产：快速开局、路线图规划、流水线批量、跳过评分可补评。<br />
                                模式只影响界面与批量默认行为，正文数据同构，可双向切换。
                            </div>
                            <div style={{ display: 'flex', gap: 10 }}>
                                <button className="btn" disabled={busy || bookMode === 'premium'} onClick={() => onSwitchMode('premium')}>切到精品</button>
                                <button className="btn btn-blue" disabled={busy || bookMode === 'mass'} onClick={() => onSwitchMode('mass')}>切到量产</button>
                            </div>
                            <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid var(--line)' }}>
                                <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 8 }}>量产补评</div>
                                <RescoreButton bookRoot={bookRoot} onNotice={flash} />
                            </div>
                        </div>
                    )}
                </main>
            </div>
        </div>
    )
}

const RUN_STATUS_LABEL = {
    run_completed: '完成',
    run_failed: '失败',
    run_aborted: '中止',
}

const inputStyle = {
    width: '100%', boxSizing: 'border-box', padding: '7px 9px', fontSize: 13,
    border: '1px solid var(--line)', borderRadius: 6,
    background: 'var(--paper)', color: 'var(--ink)',
}

function ProductionLine({ bookRoot, onNotice, onOpenRoadmap }) {
    const [target, setTarget] = useState(30)
    const [perArc, setPerArc] = useState(3)
    const [model, setModel] = useState('')
    const [taskId, setTaskId] = useState('')
    const [status, setStatus] = useState('')
    const [progress, setProgress] = useState(null)
    const [runs, setRuns] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')

    const refreshRuns = useCallback(async () => {
        try {
            const r = await phAiRuns(bookRoot, 'batch_generate', 10)
            setRuns(r?.runs || [])
        } catch (e) {
            setError(e?.message || String(e))
        } finally {
            setLoading(false)
        }
    }, [bookRoot])

    useEffect(() => { refreshRuns() }, [refreshRuns])

    // 运行中轮询任务进度（2s）
    useEffect(() => {
        if (!taskId) return undefined
        let alive = true
        const tick = async () => {
            try {
                const st = await phAiOptimizeStatus(taskId)
                if (!alive) return
                setStatus(st?.status || '')
                setProgress(st?.progress || null)
                if (['done', 'failed', 'cancelled', 'stopped'].includes(st?.status)) {
                    setTaskId('')
                    await refreshRuns()
                    if (st?.status === 'done') onNotice?.('批量生成完成')
                }
            } catch (e) {
                if (alive) setError(e?.message || String(e))
            }
        }
        tick()
        const iv = setInterval(tick, 2000)
        return () => { alive = false; clearInterval(iv) }
    }, [taskId, refreshRuns, onNotice])

    const start = useCallback(async (resumeRunId = null) => {
        setError('')
        try {
            const r = await phAiBatchGenerate({
                book_root: bookRoot,
                target_chapters: Number(target) || 30,
                n_chapters_per_arc: Number(perArc) || 3,
                resume_run_id: resumeRunId,
                model: model.trim() || null,
            })
            if (!r?.task_id) throw new Error('未返回 task_id')
            setTaskId(r.task_id)
            setStatus('running')
            setProgress(null)
        } catch (e) {
            setError(e?.message || String(e))
        }
    }, [bookRoot, target, perArc, model])

    const cancel = useCallback(async () => {
        if (!taskId) return
        try {
            await phAiCancelTask(taskId)
        } catch (e) {
            setError(e?.message || String(e))
        }
    }, [taskId])

    const running = !!taskId
    const p = progress || {}
    const done = Number(p.done || 0)
    const tot = Number(p.target || target || 1)
    const pct = Math.max(0, Math.min(100, Math.round((done / Math.max(1, tot)) * 100)))
    const stats = p.stats || null
    const failures = p.failures || []

    return (
        <div style={{ maxWidth: 900 }}>
            {error && <div style={{ marginBottom: 10, padding: '6px 12px', borderRadius: 6, background: 'var(--cinnabar-wash)', color: 'var(--cinnabar-d)', fontSize: 12.5 }}>{error}</div>}

            {/* 配置 / 运行 / 结果 */}
            {!running && !stats && (
                <div style={{ border: '1px solid var(--line)', borderRadius: 10, padding: 16, background: 'var(--paper-raised)' }}>
                    <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>生产线 · 批量生成</div>
                    <div style={{ fontSize: 12.5, color: 'var(--ink-sub)', marginBottom: 12 }}>
                        量产书按冻结路线图的弧卡队列逐弧推进（l2 注入方向），跳过评分可补评；
                        未冻结时回退到一句话 brief 自动建弧。
                        <button className="btn btn-small ghost" style={{ marginLeft: 8 }} onClick={onOpenRoadmap}>去路线图</button>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '110px 110px 1fr', gap: 10, marginBottom: 12 }}>
                        <label style={{ fontSize: 12.5, fontWeight: 600, display: 'flex', flexDirection: 'column', gap: 4 }}>
                            目标章数
                            <input style={inputStyle} value={target} onChange={(e) => setTarget(e.target.value)} />
                        </label>
                        <label style={{ fontSize: 12.5, fontWeight: 600, display: 'flex', flexDirection: 'column', gap: 4 }}>
                            每弧章数
                            <input style={inputStyle} value={perArc} onChange={(e) => setPerArc(e.target.value)} />
                        </label>
                        <label style={{ fontSize: 12.5, fontWeight: 600, display: 'flex', flexDirection: 'column', gap: 4 }}>
                            模型覆盖（可选）
                            <input style={inputStyle} placeholder="留空用当前预设；如 deepseek-v4-flash" value={model} onChange={(e) => setModel(e.target.value)} />
                        </label>
                    </div>
                    <button className="btn btn-blue" onClick={() => start(null)}>开始批量生成</button>
                </div>
            )}

            {running && (
                <div style={{ border: '1px solid var(--line)', borderRadius: 10, padding: 16, background: 'var(--paper-raised)' }}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
                        <b style={{ fontSize: 14 }}>生产线运行中</b>
                        <span style={{ fontSize: 12.5, color: 'var(--ink-sub)' }}>
                            {done}/{tot} 章 · 已用 {p.elapsed_s ?? 0}s{p.eta_s != null ? ` · 预计剩余约 ${Math.round(p.eta_s / 60)} 分钟` : ''}
                        </span>
                        <div style={{ flex: 1 }} />
                        <button className="btn btn-small" onClick={cancel}>取消</button>
                    </div>
                    <div style={{ height: 10, borderRadius: 999, background: 'var(--bg-card-2, #eee)', overflow: 'hidden', marginBottom: 10 }}>
                        <div style={{ width: `${pct}%`, height: '100%', background: 'var(--dai)' }} />
                    </div>
                    <div style={{ fontSize: 12.5, color: 'var(--ink-sub)', lineHeight: 1.8 }}>
                        {(p.messages || []).slice(-6).map((m, i) => <div key={i}>{m}</div>)}
                    </div>
                    {failures.length > 0 && (
                        <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--cinnabar-d)' }}>
                            失败 {failures.length} 章：{failures.map((f) => `第${f.idx}章`).join('、')}
                        </div>
                    )}
                </div>
            )}

            {!running && stats && (
                <div style={{ border: '1px solid var(--line)', borderRadius: 10, padding: 16, background: 'var(--paper-raised)', marginBottom: 14 }}>
                    <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>
                        本轮结束：{stats.chapters}/{stats.target} 章（{stats.mode === 'mass' ? '量产 fast' : '精品'}）
                    </div>
                    <div style={{ fontSize: 12.5, color: 'var(--ink-sub)', lineHeight: 1.9 }}>
                        耗时 {stats.elapsed_s}s ·
                        调用 {stats.usage?.calls || 0} 次 ·
                        token {stats.usage?.total_tokens || 0} ·
                        估算 ${(stats.usage?.cost_usd || 0).toFixed(3)}
                    </div>
                    {(stats.failures || []).length > 0 && (
                        <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--cinnabar-d)', lineHeight: 1.8 }}>
                            <b>失败清单（可续跑重试）：</b>
                            {(stats.failures || []).map((f, i) => (
                                <div key={i}>· 第{f.idx}章（{f.arc}）：{f.error}</div>
                            ))}
                        </div>
                    )}
                    <div style={{ marginTop: 10 }}>
                        <button className="btn btn-small" onClick={() => setProgress(null)}>返回配置</button>
                    </div>
                    <div style={{ marginTop: 10 }}>
                        <RescoreButton bookRoot={bookRoot} onNotice={onNotice} />
                    </div>
                </div>
            )}

            {/* 运行历史 */}
            <div style={{ border: '1px solid var(--line)', borderRadius: 10, padding: 14, background: 'var(--paper-raised)' }}>
                <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 8 }}>运行历史（最近 10 条）</div>
                {loading ? (
                    <div style={{ color: 'var(--ink-sub)', fontSize: 12.5 }}>加载中…</div>
                ) : runs.length === 0 ? (
                    <div style={{ color: 'var(--ink-mute)', fontSize: 12.5 }}>还没有批量运行记录</div>
                ) : (
                    runs.map((r) => (
                        <div key={r.run_id} style={{
                            display: 'flex', gap: 10, alignItems: 'center', padding: '7px 4px',
                            borderBottom: '1px solid var(--line)', fontSize: 12.5,
                        }}>
                            <span style={{ fontFamily: 'monospace' }}>{r.run_id}</span>
                            <span style={{
                                padding: '1px 8px', borderRadius: 999,
                                background: r.stale_running ? 'var(--amber-wash, #fdf3e0)' : 'var(--bg-card-2, #eee)',
                            }}>
                                {r.stale_running ? '可续跑' : (RUN_STATUS_LABEL[r.last_event] || r.last_event || '—')}
                            </span>
                            <span style={{ color: 'var(--ink-sub)' }}>
                                {r.params?.mode === 'mass' ? '量产' : '精品'} · 目标 {r.params?.target_chapters ?? '?'} 章
                            </span>
                            <span style={{ color: 'var(--ink-mute)', marginLeft: 'auto' }}>
                                {(r.started_at || '').replace('T', ' ').slice(0, 16)}
                            </span>
                            {r.stale_running && (
                                <button className="btn btn-small" disabled={running} onClick={() => start(r.run_id)}>续跑</button>
                            )}
                        </div>
                    ))
                )}
            </div>
        </div>
    )
}

function RescoreButton({ bookRoot, onNotice }) {
    const [count, setCount] = useState(null)
    const [taskId, setTaskId] = useState('')
    const [prog, setProg] = useState(null)
    const [error, setError] = useState('')

    const load = useCallback(async () => {
        try {
            const r = await phAiDeferredCount(bookRoot)
            setCount(Number(r?.count ?? 0))
        } catch {
            setCount(null)
        }
    }, [bookRoot])

    useEffect(() => { load() }, [load])

    useEffect(() => {
        if (!taskId) return undefined
        let alive = true
        const tick = async () => {
            try {
                const st = await phAiOptimizeStatus(taskId)
                if (!alive) return
                setProg(st?.progress || null)
                if (['done', 'failed', 'cancelled'].includes(st?.status)) {
                    setTaskId('')
                    await load()
                    if (st?.status === 'done') {
                        const r = st?.result || {}
                        onNotice?.(`补评完成：成功 ${r.rescored || 0} 章，失败 ${r.failed || 0} 章`)
                    }
                }
            } catch (e) {
                if (alive) setError(e?.message || String(e))
            }
        }
        tick()
        const iv = setInterval(tick, 2000)
        return () => { alive = false; clearInterval(iv) }
    }, [taskId, load, onNotice])

    const start = useCallback(async () => {
        setError('')
        try {
            const r = await phAiRescoreDeferred(bookRoot)
            if (!r?.task_id) throw new Error('未返回 task_id')
            setTaskId(r.task_id)
            setProg(null)
        } catch (e) {
            setError(e?.message || String(e))
        }
    }, [bookRoot])

    if (count === null) return null
    if (taskId) {
        const p = prog || {}
        return (
            <div style={{ fontSize: 12.5, color: 'var(--ink-sub)' }}>
                补评中 {p.done || 0}/{p.total || count}…（结果写入审查报告）
            </div>
        )
    }
    return (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5 }}>
            {count === 0 ? (
                <span style={{ color: 'var(--ink-mute)' }}>没有跳过评分的章节</span>
            ) : (
                <>
                    <span>量产期间有 {count} 章跳过评分</span>
                    <button className="btn btn-small" onClick={start}>一键补评</button>
                </>
            )}
            {error && <span style={{ color: 'var(--cinnabar-d)' }}>{error}</span>}
        </div>
    )
}
