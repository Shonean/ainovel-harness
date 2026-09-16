/**
 * 成片管理（T36 v9）：单集 1–3 分钟 · 合辑 10 分钟级；封面 / 状态 / 合成与导出。
 *
 * 数据：GET /api/adaptation/films（跨书单集 + 合辑）；动作走 /ai-creation/drama/*。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { dramaMediaUrl, fetchAdaptationFilms, phDramaCompilation, phDramaFilmExport } from '../api.js'
import { toast } from '../lib/toast.js'

function fmtDur(sec) {
    const s = Math.round(Number(sec) || 0)
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export default function FilmManagerPage() {
    const navigate = useNavigate()
    const [films, setFilms] = useState(null)
    const [error, setError] = useState('')

    const load = useCallback(() => {
        setError('')
        fetchAdaptationFilms().then(setFilms).catch(e => setError(e.message || '加载成片失败'))
    }, [])
    useEffect(() => { load() }, [load])

    const episodes = films?.episodes || []
    const comps = films?.compilations || []
    const duration = episodes.reduce((a, e) => a + (e.duration || 0), 0)
    const exported = episodes.filter(e => e.exported).length
    const published = episodes.filter(e => e.published).length

    const epByKey = useMemo(() => {
        const m = new Map()
        for (const e of episodes) m.set(`${e.book_root}::${e.pack_id}`, e)
        return m
    }, [episodes])

    const play = (e) => window.open(dramaMediaUrl(e.book_root, e.pack_id, 'drama/drama_preview.mp4'), '_blank')

    const exportEp = async (e) => {
        const r = await phDramaFilmExport(e.book_root, e.pack_id)
        if (r.ok) { toast(`已导出：${e.title}`); load() } else toast(`导出失败：${r.error}`)
    }

    const addComp = async (e) => {
        const r = await phDramaCompilation(e.book_root, 'add', { pack_id: e.pack_id })
        if (r.ok) { toast('已加入合辑'); load() } else toast(`失败：${r.error}`)
    }

    const exportComp = async (c) => {
        toast('合辑导出中（PyAV 拼接）…')
        const r = await phDramaCompilation(c.book_root, 'export')
        if (r.ok) { toast('合辑已导出'); load() } else toast(`导出失败：${r.error}`)
    }

    const removeCompItem = async (c, packId) => {
        const r = await phDramaCompilation(c.book_root, 'remove', { pack_id: packId })
        if (r.ok) load(); else toast(`失败：${r.error}`)
    }

    return (
        <div className="z-page">
            <header className="z-head">
                <button className="z-back" onClick={() => navigate('/adaptation')}>← 改编中心</button>
                <div className="z-title">成片管理</div>
                <div className="z-sub">单集 1–3 分钟 · 合辑 10 分钟级；封面 / 状态 / 合成与导出都在这里</div>
                <span className="grow" />
                <button className="btn btn-small" onClick={() => navigate('/adaptation/batch')}>批量出片</button>
                <button className="btn btn-small" onClick={() => navigate('/adaptation')}>漫剧工作台入口</button>
                <button className="btn btn-blue btn-small" disabled={!episodes.length}
                    onClick={() => { const e = episodes[0]; navigate(`/adaptation/publish?book=${encodeURIComponent(e.book_root)}&pack=${encodeURIComponent(e.pack_id)}`) }}>发布导出</button>
            </header>

            <div className="z-stats">
                <div className="z-stat"><b>{episodes.length}</b><span>单集</span></div>
                <div className="z-stat"><b>{fmtDur(duration)}</b><span>总时长</span></div>
                <div className="z-stat"><b>{comps.length}</b><span>合辑</span></div>
                <div className="z-stat"><b>{exported}</b><span>已导出</span></div>
                <div className="z-stat"><b>{published}</b><span>已发布</span></div>
            </div>

            <div className="z-body">
                {error && <div className="zsec-empty">{error}<br /><button className="btn btn-small" style={{ marginTop: 10 }} onClick={load}>重试</button></div>}
                {!error && (
                    <>
                        <section className="zsec">
                            <div className="zsec-h">
                                <div className="zsec-t">单集</div>
                                <div className="zsec-meta">来自书级改编的 Pack · 一章一集 · 可播放 / 导出 / 加入合辑</div>
                            </div>
                            {episodes.length === 0 ? (
                                <div className="zsec-empty">还没有成片——到「漫剧工作台 → 合成导出」出一集。</div>
                            ) : (
                                <div className="ep-list">
                                    {episodes.map(e => (
                                        <div className="ep" key={`${e.book_root}::${e.pack_id}`}>
                                            <div className="ep-cover">{e.blurb || e.title}</div>
                                            <div className="ep-main">
                                                <div className="ep-t">{e.title}
                                                    <span className={'badge ' + (e.exported ? 'badge-green' : 'badge-amber')}>{e.exported ? '已导出' : '已合成'}</span>
                                                    {e.published && <span className="badge badge-cyan">已发布</span>}
                                                </div>
                                                <div className="ep-meta">
                                                    <span>{e.duration_text || fmtDur(e.duration)}</span>
                                                    <span>1080×1920 · 24fps</span>
                                                    <span>来源：{e.book} · {e.pack_id}</span>
                                                    <span>{e.exported ? 'MP4 已导出' : '待导出'}</span>
                                                </div>
                                            </div>
                                            <div className="ep-act">
                                                <button className="mini-btn primary" onClick={() => play(e)}>播放</button>
                                                <button className="mini-btn"
                                                    onClick={() => navigate(`/adaptation/workbench?book=${encodeURIComponent(e.book_root)}&pack=${encodeURIComponent(e.pack_id)}`)}>进工作台</button>
                                                <button className="mini-btn" onClick={() => addComp(e)}>加入合辑</button>
                                                <button className="mini-btn" onClick={() => exportEp(e)}>导出</button>
                                                <button className="mini-btn"
                                                    onClick={() => navigate(`/adaptation/publish?book=${encodeURIComponent(e.book_root)}&pack=${encodeURIComponent(e.pack_id)}`)}>发布</button>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </section>

                        <section className="zsec">
                            <div className="zsec-h">
                                <div className="zsec-t">合辑编排</div>
                                <div className="zsec-meta">多弧合成长片（10 分钟级）· 排序 / 一键导出</div>
                            </div>
                            {comps.length === 0 ? (
                                <div className="comp-row" style={{ borderStyle: 'dashed', color: 'var(--ink-mute)' }}>
                                    还没有合辑——从单集点「加入合辑」开始编排。
                                </div>
                            ) : comps.map(c => {
                                const items = (c.items || []).map(pid => ({ pid, ep: epByKey.get(`${c.book_root}::${pid}`) }))
                                return (
                                    <div key={c.id + c.book_root} style={{ marginBottom: 14 }}>
                                        <div className="comp-row" style={{ borderStyle: 'dashed' }}>
                                            <span className="grow">{c.title || '合集'} · {c.book}</span>
                                            <span className={'badge ' + (c.exported ? 'badge-green' : 'badge-neutral')}>
                                                {c.exported ? '已导出' : items.length ? '已编排' : '未编排'}</span>
                                            <button className="mini-btn primary" disabled={!items.length} onClick={() => exportComp(c)}>导出合辑</button>
                                        </div>
                                        {items.map((x, i) => (
                                            <div className="comp-row" style={{ marginTop: 8 }} key={x.pid}>
                                                <span className="no">{i + 1}</span>
                                                <span className="grow">{x.ep?.title || x.pid}</span>
                                                <span className="mono" style={{ fontSize: 11, color: 'var(--ink-mute)' }}>{x.ep?.duration_text || ''}</span>
                                                <button className="mini-btn" onClick={() => removeCompItem(c, x.pid)}>移除</button>
                                            </div>
                                        ))}
                                        {items.length > 0 && (
                                            <div className="comp-row" style={{ marginTop: 8, borderStyle: 'dashed' }}>
                                                <span className="grow">合计时长</span>
                                                <b>{fmtDur(items.reduce((a, x) => a + (x.ep?.duration || 0), 0))}</b>
                                            </div>
                                        )}
                                    </div>
                                )
                            })}
                        </section>
                    </>
                )}
            </div>
        </div>
    )
}
