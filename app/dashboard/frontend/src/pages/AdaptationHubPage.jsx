/**
 * 改编中心（T36 v9 · 首页=书墙）。
 *
 * 数据：GET /api/adaptation/overview（跨书 Pack / 资产库 / 账本 / 预算）
 *      GET /api/adaptation/films（跨书成片与合辑）
 * 书墙按书聚合（情节再多只加书内条目，首页不膨胀）；书内情节树在「书级改编」页签。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchAdaptationFilms, fetchAdaptationOverview, saveAdaptationBudget, switchProject } from '../api.js'
import { toast } from '../lib/toast.js'

function fmtDur(sec) {
    const s = Math.round(Number(sec) || 0)
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function fmtTime(ms) {
    if (!ms) return '—'
    const d = new Date(Number(ms))
    return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export default function AdaptationHubPage() {
    const navigate = useNavigate()
    const [data, setData] = useState(null)
    const [films, setFilms] = useState(null)
    const [error, setError] = useState('')
    const [budget, setBudget] = useState(null)
    const [saving, setSaving] = useState(false)

    const load = useCallback(() => {
        setError('')
        Promise.all([
            fetchAdaptationOverview().catch(e => { throw e }),
            fetchAdaptationFilms().catch(() => ({ episodes: [], compilations: [] })),
        ]).then(([ov, fl]) => {
            setData(ov || {})
            setFilms(fl || { episodes: [], compilations: [] })
            setBudget(ov?.budget || null)
        }).catch(e => setError(e.message || '加载改编中心失败'))
    }, [])

    useEffect(() => { load() }, [load])

    const episodes = films?.episodes || []
    const comps = films?.compilations || []
    const packs = data?.packs || []
    const totals = data?.totals || {}

    const byBook = useMemo(() => {
        const map = new Map()
        for (const p of packs) {
            if (!map.has(p.book_root)) map.set(p.book_root, { name: p.book, book_root: p.book_root, packs: [], episodes: [] })
            map.get(p.book_root).packs.push(p)
        }
        for (const e of episodes) {
            if (!map.has(e.book_root)) map.set(e.book_root, { name: e.book, book_root: e.book_root, packs: [], episodes: [] })
            map.get(e.book_root).episodes.push(e)
        }
        return Array.from(map.values())
    }, [packs, episodes])

    const publishedCount = episodes.filter(e => e.published).length

    const openBook = async (bookRoot, kind) => {
        try { await switchProject(bookRoot) } catch { /* 切换失败也继续导航 */ }
        if (kind === 'workbench' && episodes.length) {
            const ep = episodes.find(e => e.book_root === bookRoot)
            if (ep) { navigate(`/adaptation/workbench?book=${encodeURIComponent(bookRoot)}&pack=${encodeURIComponent(ep.pack_id)}`); return }
        }
        const pack = packs.find(p => p.book_root === bookRoot && !p.failed)
        if (kind === 'workbench' && pack) {
            navigate(`/adaptation/workbench?book=${encodeURIComponent(bookRoot)}&pack=${encodeURIComponent(pack.pack_id)}`)
            return
        }
        navigate('/ai-creation?tab=adapt')
    }

    const saveBudget = async () => {
        if (!budget || saving) return
        setSaving(true)
        try { await saveAdaptationBudget(budget); toast('预算已保存'); load() }
        catch (e) { toast(`保存失败：${e.message}`) }
        finally { setSaving(false) }
    }

    return (
        <div className="z-page">
            <header className="z-head">
                <button className="z-back" onClick={() => navigate('/')}>← 书架</button>
                <div className="z-title">改编中心</div>
                <div className="z-sub">按书管理改编——点开一本书看它改编成了什么；成片与发布在成品层</div>
                <span className="grow" />
                <button className="btn btn-small" onClick={() => navigate('/adaptation/films')}>成片管理</button>
                <button className="btn btn-small" onClick={load}>刷新</button>
            </header>

            <div className="z-stats">
                <div className="z-stat"><b>{episodes.length}</b><span>部漫剧</span></div>
                <div className="z-stat"><b>{fmtDur(episodes.reduce((a, e) => a + (e.duration || 0), 0))}</b><span>漫剧总时长</span></div>
                <div className="z-stat"><b>{episodes.length}</b><span>单集</span></div>
                <div className="z-stat"><b>{comps.length}</b><span>合辑</span></div>
                <div className="z-stat"><b>{publishedCount}</b><span>已发布</span></div>
            </div>

            <div className="z-body">
                {error && (
                    <div className="zsec-empty">{error}<br />
                        <button className="btn btn-small" style={{ marginTop: 10 }} onClick={load}>重试</button>
                    </div>
                )}
                {!error && (
                    <section className="zsec">
                        <div className="zsec-h">
                            <div className="zsec-t"><span className="ico">书</span>有改编的书</div>
                            <div className="zsec-meta">按书聚合——书内情节再多只增加条目，首页的书不会膨胀</div>
                            <span className="grow" />
                        </div>
                        {byBook.length === 0 ? (
                            <div className="zsec-empty">还没有改编产物——到书的「改编」页签选弧新建改编，约 1–2 分钟出竖屏漫剧。</div>
                        ) : (
                            <div className="bmc-wall">
                                {byBook.map(b => {
                                    const ok = b.packs.filter(p => !p.failed).length
                                    const exported = b.episodes.filter(e => e.exported).length
                                    const dur = b.episodes.reduce((a, e) => a + (e.duration || 0), 0)
                                    const arcs = Array.from(new Set(b.packs.map(p => p.pack_id.split('_')[3] || p.pack_id))).slice(0, 6)
                                    return (
                                        <div className="bmc" key={b.book_root} onClick={() => openBook(b.book_root, 'book')}>
                                            <div className="bmc-cover">
                                                <div className="k">书 · 改编</div>
                                                <div className="t">{b.name}</div>
                                                <div className="s">{b.packs.length} 个 Pack · {b.episodes.length} 集漫剧</div>
                                                <div className="bmc-counts">
                                                    <span className="badge badge-purple">漫剧 {b.episodes.length} 集</span>
                                                    {exported > 0
                                                        ? <span className="badge badge-green">已导出 {exported}</span>
                                                        : <span className="badge badge-neutral">待合成</span>}
                                                </div>
                                            </div>
                                            <div className="bmc-body">
                                                <div className="bmc-row">
                                                    <span>总时长 {fmtDur(dur)}</span>
                                                    <span>{ok} 个校验通过</span>
                                                    <span>{b.episodes.length || b.packs.length} 个成品条目</span>
                                                </div>
                                                <div className="bmc-arcs">
                                                    {arcs.map((a, i) => <span className="bmc-arc" key={i}>{a} · 已改编</span>)}
                                                </div>
                                                <div className="bmc-actions">
                                                    <button className="btn btn-blue btn-small" onClick={e => { e.stopPropagation(); openBook(b.book_root, 'book') }}>进入改编</button>
                                                    <button className="btn btn-small" disabled={!ok && !b.episodes.length}
                                                        onClick={e => { e.stopPropagation(); openBook(b.book_root, 'workbench') }}>▶ 漫剧工作台</button>
                                                    <span className="grow" />
                                                    <span className="z-sub" style={{ fontSize: 11.5 }}>点卡也可进入</span>
                                                </div>
                                            </div>
                                        </div>
                                    )
                                })}
                            </div>
                        )}
                    </section>
                )}

                <details className="z-fold">
                    <summary>制作台（跨书 Pack 明细 · 账本 · 预算闸）</summary>
                    <div className="inner">
                        <div className="adh-panel">
                            <div className="adh-panel-t">跨书 Pack（{totals.packs ?? 0}）</div>
                            <div className="adh-list" style={{ border: '1px solid var(--line-soft)', borderRadius: 10 }}>
                                {packs.map(p => (
                                    <div className="adh-row" key={p.book_root + p.pack_id}>
                                        <span className="adh-book">{p.book}</span>
                                        <span className="adh-pid mono">{p.pack_id}</span>
                                        {p.failed ? <span className="badge badge-red">校验失败</span>
                                            : p.ok === true ? <span className="badge badge-green">校验通过</span>
                                                : <span className="badge badge-neutral">未校验</span>}
                                        {(p.warnings > 0) && <span className="badge badge-amber">警告 {p.warnings}</span>}
                                        <span className="adh-meta mono">{p.nodes ?? '—'} 节点 · {p.endings ?? '—'} 结局</span>
                                        <span className="adh-grow" />
                                        <span className="adh-meta">{p.drama_videos > 0 ? `漫剧 ${p.drama_videos} 部` : '未合成'}</span>
                                        <span className="adh-meta">{fmtTime(p.updated)}</span>
                                    </div>
                                ))}
                                {!packs.length && <div className="adh-empty-inline" style={{ padding: '10px 14px' }}>暂无 Pack</div>}
                            </div>
                            <div className="adh-panel-t" style={{ marginTop: 14 }}>最近账本（≤50 条）</div>
                            {data?.ledger_rows?.length ? (
                                <div className="adh-ledger mono">
                                    {data.ledger_rows.slice(0, 12).map((r, i) => (
                                        <div className="adh-lrow" key={i}>
                                            <span>{(r.ts || '').slice(5, 16)}</span><span>{r.driver}</span>
                                            <span>{r.capability}</span><span>{r.status || ''}</span>
                                            <span>{r.est_cost != null ? `${r.est_cost} ${r.currency || ''}` : '—'}</span>
                                        </div>
                                    ))}
                                </div>
                            ) : <div className="adh-empty-inline">暂无记录（生成回填后累计）</div>}
                        </div>
                        <div className="adh-panel">
                            <div className="adh-panel-t">预算闸（月度上限）</div>
                            {budget && (
                                <>
                                    <label className="adh-field"><span>生图 ¥</span>
                                        <input type="number" min="0" value={budget.image_month_cny ?? 0}
                                            onChange={e => setBudget({ ...budget, image_month_cny: Number(e.target.value) })} /></label>
                                    <label className="adh-field"><span>视频 ¥</span>
                                        <input type="number" min="0" value={budget.video_month_cny ?? 0}
                                            onChange={e => setBudget({ ...budget, video_month_cny: Number(e.target.value) })} /></label>
                                    <label className="adh-field"><span>在线导演 $</span>
                                        <input type="number" min="0" value={budget.director_month_usd ?? 0}
                                            onChange={e => setBudget({ ...budget, director_month_usd: Number(e.target.value) })} /></label>
                                    <label className="adh-field adh-switch"><span>启用熔断</span>
                                        <input type="checkbox" checked={budget.enabled !== false}
                                            onChange={e => setBudget({ ...budget, enabled: e.target.checked })} /></label>
                                    <button className="btn btn-blue" style={{ marginTop: 10 }} disabled={saving} onClick={saveBudget}>
                                        {saving ? '保存中…' : '保存预算'}</button>
                                    <div className="adh-hint">熔断动作：对应驱动返回 budget_exhausted → 自动走降级（占位底 / 静帧 / 离线），任务不失败。</div>
                                </>
                            )}
                        </div>
                    </div>
                </details>
            </div>

            <footer className="project-select-footer" style={{ marginTop: 'auto' }}>
                <span>AInovel Harness · 改编中心（漫剧线）</span>
            </footer>
        </div>
    )
}
