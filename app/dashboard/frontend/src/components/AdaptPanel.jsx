/**
 * 书级改编页签（T36 v9）：情节树三态 + 该情节漫剧产物 + 新建改编 + 作品详情（3 Tab）。
 *
 * 数据源（prompt-harness server.py）：
 *  - POST /ai-creation/adaptation/packs / pack / build / pack/delete
 *  - GET  /ai-creation/adaptation/preview（inkjs 调试页）
 *  - GET  /ai-creation/drama/media（pack 内成片）
 *
 * Props: { bookRoot, arcs?: {arcs:[...]}, onAdvance?(arcId) }
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
    dramaMediaUrl, fetchAdaptationOverview, phAdaptBuild, phAdaptPack, phAdaptPackDelete, phAdaptPacks, phAiState,
} from '../api.js'
import ConfirmModal from './ConfirmModal.jsx'
import { toast } from '../lib/toast.js'

function l4Count(arc) {
    const scenes = arc?.state?.levels?.l4?.scenes
    return Array.isArray(scenes) ? scenes.length : 0
}

function fmtDur(sec) {
    const s = Math.round(Number(sec) || 0)
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

const ASSET_ICON = { background: '背景 · 待生成', portrait: '立绘 · 待生成', voice: '语音 · 待生成', bgm: 'BGM', sfx: '音效', video: '镜头视频' }

export default function AdaptPanel({ bookRoot, arcs: arcsProp, onAdvance }) {
    const navigate = useNavigate()
    const [packs, setPacks] = useState(null)
    const [arcs, setArcs] = useState(arcsProp?.arcs || null)
    const [bookExtras, setBookExtras] = useState({ drama_videos: 0, packs: 0 })
    const [selectedArc, setSelectedArc] = useState('')
    const [pickOpen, setPickOpen] = useState(false)
    const [pickSel, setPickSel] = useState('')
    const [building, setBuilding] = useState(false)
    const [buildName, setBuildName] = useState('')
    const [detail, setDetail] = useState(null)
    const [detailTab, setDetailTab] = useState('drama')
    const [delTarget, setDelTarget] = useState(null)
    const [error, setError] = useState('')

    const loadPacks = useCallback(() => {
        if (!bookRoot) return
        setError('')
        phAdaptPacks(bookRoot).then(r => setPacks(r?.packs || [])).catch(e => setError(e.message))
        fetchAdaptationOverview().then(ov => {
            const rows = (ov?.packs || []).filter(p => p.book_root === bookRoot)
            setBookExtras({
                drama_videos: rows.reduce((a, p) => a + (p.drama_videos || 0), 0),
                packs: rows.filter(p => !p.failed).length,
            })
        }).catch(() => { /* 忽略 */ })
    }, [bookRoot])

    useEffect(() => { loadPacks() }, [loadPacks])

    useEffect(() => {
        if (arcsProp?.arcs) { setArcs(arcsProp.arcs); return }
        if (!bookRoot || arcs) return
        phAiState(bookRoot).then(r => setArcs(r?.arcs?.arcs || [])).catch(() => setArcs([]))
    }, [bookRoot, arcsProp, arcs])

    // 默认选中第一个有 pack 的弧 / 有 l4 的弧
    useEffect(() => {
        if (!arcs || !arcs.length || selectedArc) return
        const withPack = arcs.find(a => (packs || []).some(p => arcOfPack(p) === a.id))
        const withL4 = arcs.find(a => l4Count(a) > 0)
        const hit = withPack || withL4 || arcs[0]
        if (hit) setSelectedArc(hit.id)
    }, [arcs, packs, selectedArc])

    const arcOfPack = (p) => {
        const ids = p?.pack?.source?.arc_ids
        if (Array.isArray(ids) && ids.length) return ids[0]
        if (p?.validation?.stats?.arc_id) return p.validation.stats.arc_id
        return ''
    }

    const openDetail = async (packId, tab = 'drama') => {
        setDetailTab(tab)
        try {
            const r = await phAdaptPack(bookRoot, packId, ['story', 'characters', 'world', 'assets'])
            setDetail({ packId, data: r })
        } catch (e) { toast(`详情加载失败：${e.message}`) }
    }

    const doBuild = async () => {
        if (!pickSel || building) return
        const arc = (arcs || []).find(a => a.id === pickSel)
        setBuilding(true)
        setBuildName(arc?.name || pickSel)
        setPickOpen(false)
        try {
            const r = await phAdaptBuild(bookRoot, [pickSel])
            const ok = (r.results || []).some(x => x.ok)
            if (ok) {
                toast(`改编完成：${arc?.name || pickSel}（漫剧已加入该书，可进工作台精调）`)
                loadPacks()
            } else {
                const first = (r.results || [])[0] || {}
                toast(`构建未完成：${first.error || '未知错误'}`)
            }
        } catch (e) { toast(`构建失败：${e.message}`) }
        finally { setBuilding(false); setBuildName('') }
    }

    const doDelete = async () => {
        const packId = delTarget
        setDelTarget(null)
        try { await phAdaptPackDelete(bookRoot, packId); toast(`已删除：${packId}`); if (detail?.packId === packId) setDetail(null); loadPacks() }
        catch (e) { toast(`删除失败：${e.message}`) }
    }

    if (!bookRoot) return <div className="zsec-empty" style={{ margin: 20 }}>先选一本书。</div>

    const list = packs || []
    const selected = (arcs || []).find(a => a.id === selectedArc)
    const selPacks = list.filter(p => arcOfPack(p) === selectedArc)
    const selectedL4 = l4Count(selected)
    const selName = selected?.name || '—'
    const adaptedCount = (arcs || []).filter(a => list.some(p => arcOfPack(p) === a.id)).length

    const openWorkbench = (packId) => navigate(`/adaptation/workbench?book=${encodeURIComponent(bookRoot)}&pack=${encodeURIComponent(packId)}`)

    return (
        <div className="z-page" style={{ minHeight: '100%' }}>
            <header className="z-head">
                <div className="z-title" style={{ fontSize: 22 }}>改编</div>
                <div className="z-sub">情节树三态：未就绪 → 去推进情节；可改编 → 新建改编；已改编 → 漫剧工作台 / 再生成一版</div>
                <span className="grow" />
                <button className="btn btn-small" onClick={() => navigate('/adaptation/films')}>成片管理</button>
                <button className="btn btn-small" onClick={loadPacks}>刷新</button>
                <button className="btn btn-blue btn-small" disabled={selectedL4 <= 0}
                    onClick={() => { setPickSel(selectedArc); setPickOpen(true) }}>＋ 新建改编</button>
            </header>

            <div className="z-stats">
                <div className="z-stat"><b>{list.filter(p => !p.failed).length}</b><span>个改编包</span></div>
                <div className="z-stat"><b>{bookExtras.drama_videos}</b><span>部漫剧</span></div>
                <div className="z-stat"><b>{(arcs || []).length}</b><span>个情节</span></div>
                <div className="z-stat"><b>{adaptedCount}</b><span>已改编情节</span></div>
            </div>

            <div className="z-body">
                {error && <div className="zsec-empty" style={{ marginBottom: 12 }}>{error}</div>}
                <div className="bk-split">
                    <div className="bk-tree">
                        <div className="bk-tree-h">
                            <span className="t">情节树</span>
                            <span className="s">与书级讨论同一棵树 · <b>{(arcs || []).length}</b> 个情节 · 点情节看它的改编产物</span>
                        </div>
                        <div className="bo-timeline">
                            {(arcs || []).map((a, idx) => {
                                const arcPacks = list.filter(p => arcOfPack(p) === a.id)
                                const has = arcPacks.length > 0
                                const l4ok = l4Count(a) > 0
                                const sel = a.id === selectedArc
                                return (
                                    <div className={'bo-arc-node ' + (has ? 'done' : 'todo') + (sel ? ' sel' : '')}
                                        key={a.id} onClick={() => setSelectedArc(a.id)}>
                                        <div className="bo-arc-left">
                                            <div className="bo-arc-idx">第 {idx + 1} 章</div>
                                            <div className={'bo-arc-badge ' + (has ? 'done' : 'todo')}>{has ? '已改编' : l4ok ? '可改编' : '未就绪'}</div>
                                        </div>
                                        <div className="bo-arc-marker" />
                                        <div className="bo-arc-right">
                                            <div className="bo-arc-card">
                                                <div className="bo-arc-title">{a.name || a.id}</div>
                                                <div className="bo-arc-summary">
                                                    {has ? `漫剧 ${arcPacks.length} 集 · 可进工作台`
                                                        : l4ok ? `l4 快照 ×${l4Count(a)} 场景 · 可生成竖屏漫剧`
                                                            : '还没有 l4 场景快照 · 改编需先生成'}
                                                </div>
                                                <div className="bo-element-cloud">
                                                    {has
                                                        ? <><span className="bo-cloud-tag char">漫剧</span><span className="bo-cloud-tag setting">l4 ×{l4Count(a)} 场景</span></>
                                                        : <span className="bo-cloud-tag">未改编</span>}
                                                </div>
                                                <div className="bo-arc-tools">
                                                    {has && <button className="bo-arc-tool enter" onClick={e => { e.stopPropagation(); openWorkbench(arcPacks[0].pack_id) }}>漫剧工作台</button>}
                                                    {has && <button className="bo-arc-tool chat" onClick={e => { e.stopPropagation(); setSelectedArc(a.id); setPickSel(a.id); setPickOpen(true) }}>＋ 再生成一版</button>}
                                                    {!has && l4ok && <button className="bo-arc-tool enter" onClick={e => { e.stopPropagation(); setSelectedArc(a.id); setPickSel(a.id); setPickOpen(true) }}>＋ 新建改编</button>}
                                                    {!has && !l4ok && (
                                                        <>
                                                            <button className="bo-arc-tool enter" onClick={e => { e.stopPropagation(); onAdvance ? onAdvance(a.id) : toast('已切到创作：推进生成 l4 后即可改编') }}>去推进情节</button>
                                                            <button className="lock-tool" disabled title="需先生成 l4 场景快照">＋ 新建改编</button>
                                                        </>
                                                    )}
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                )
                            })}
                            {!(arcs || []).length && <div className="zsec-empty">弧列表加载中…</div>}
                        </div>
                    </div>

                    <div className="bk-products">
                        <div className="bk-ph">
                            <span className="t">{selName}</span>
                            <span className={'badge ' + (selectedL4 > 0 ? 'badge-green' : 'badge-neutral')}>
                                {selectedL4 > 0 ? `l4 ×${selectedL4} 场景` : '无 l4 快照'}</span>
                            <span className="grow" />
                            <button className="btn btn-blue btn-small" disabled={selectedL4 <= 0}
                                onClick={() => { setPickSel(selectedArc); setPickOpen(true) }}>
                                {selectedL4 <= 0 ? '🔒 新建改编（需先有 l4）' : (selPacks.length ? '＋ 再生成一版' : '＋ 为该情节新建改编')}
                            </button>
                        </div>
                        <div className="causal">
                            <span className="st">情节 l4 快照</span><i>→</i><span className="st">＋新建改编</span><i>→</i>
                            <span className="st">Pack（竖屏漫剧）</span><i>→</i><span className="st">漫剧工作台</span><i>→</i>
                            <span className="st">成片 · 发布</span>
                        </div>

                        {building && (
                            <div className="dmc build" style={{ pointerEvents: 'none' }}>
                                <div style={{ padding: '34px 22px', textAlign: 'center' }}>
                                    <div style={{ fontSize: 15, fontWeight: 700 }}>正在渲染漫剧 · {buildName}</div>
                                    <div style={{ fontSize: 12.5, color: 'var(--ink-mute)', marginTop: 6 }}>
                                        确定性投影分镜 + 字幕/BGM 占位（约 1–2 分钟）</div>
                                    <div style={{ width: '64%', height: 8, borderRadius: 6, background: 'var(--paper)', border: '1px solid var(--line-soft)', overflow: 'hidden', margin: '14px auto 0' }}>
                                        <i style={{ display: 'block', height: '100%', width: '60%', background: 'var(--dai)', transition: 'width .25s linear' }} /></div>
                                </div>
                            </div>
                        )}

                        <div className="dmc-wall">
                            {selPacks.map(p => {
                                const g = p.pack?.game || {}
                                const v = p.validation || {}
                                const hasVideo = (p.drama_videos || 0) > 0
                                return (
                                    <div className="dmc" key={p.pack_id}>
                                        <div className="dmc-cover" onClick={() => openWorkbench(p.pack_id)}>
                                            <div className="flag">
                                                {p.failed ? <span className="badge badge-red">校验失败</span>
                                                    : v.ok === true ? <span className="badge badge-green">校验通过</span>
                                                        : <span className="badge badge-neutral">未校验</span>}
                                            </div>
                                            <div className="bigplay">▶</div>
                                            <div className="title-card">
                                                <div className="tc-k">{bookRoot.split(/[\\/]/).pop()} · 漫剧</div>
                                                <div className="tc-t">{g.title || p.pack_id}</div>
                                                <div className="tc-s">{g.logline ? String(g.logline).slice(0, 30) : '竖屏漫剧'}</div>
                                            </div>
                                            {hasVideo && <div className="dur">已合成</div>}
                                        </div>
                                        <div className="dmc-body">
                                            <div className="dmc-title">{g.title || p.pack_id}
                                                <span className="badge badge-purple">漫剧成片</span></div>
                                            <div className="dmc-meta">
                                                <span>{v.stats?.scenes ?? '—'} 场景 · {v.stats?.nodes ?? '—'} 节点</span>
                                                <span>{v.stats?.endings ?? '—'} 结局</span>
                                                <span className="mono">{p.pack_id}</span>
                                            </div>
                                            <div className="dmc-actions">
                                                <button className="btn btn-blue btn-small" onClick={() => openWorkbench(p.pack_id)}>▶ 进入漫剧工作台</button>
                                                <button className="btn btn-small" onClick={() => openDetail(p.pack_id)}>作品详情</button>
                                                <span className="grow" />
                                                <button className="btn btn-small btn-warnlike" onClick={() => setDelTarget(p.pack_id)}>删除</button>
                                            </div>
                                        </div>
                                    </div>
                                )
                            })}
                        </div>

                        {!building && !selPacks.length && (
                            <div className="zsec-empty">
                                {selectedL4 > 0
                                    ? `该情节已具备 l4 快照（×${selectedL4} 场景）——点「＋ 新建改编」，约 1–2 分钟产出竖屏漫剧（含字幕 / BGM / 配音位）。`
                                    : '该情节还没有 l4 场景快照——改编基于最新完成章的 l4 场景编译，先把它推进生成到 l4。'}
                                {selectedL4 <= 0 && (
                                    <div><button className="btn btn-blue btn-small" style={{ marginTop: 12 }}
                                        onClick={() => onAdvance ? onAdvance(selectedArc) : toast('已切到创作：推进生成 l4 后即可改编')}>去推进情节（创作）</button></div>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {/* 新建改编弹层 */}
            {pickOpen && (
                <div className="pkm-mask on" onClick={e => { if (e.target === e.currentTarget) setPickOpen(false) }}>
                    <div className="pkm">
                        <div className="pkm-h">
                            <span className="t">新建改编</span>
                            <span className="s">选一个情节 · 一弧一集 · 产出竖屏漫剧（字幕/BGM/配音位） · 约 1–2 分钟</span>
                            <span className="grow" />
                            <button className="pkm-close" onClick={() => setPickOpen(false)}>✕</button>
                        </div>
                        <div className="pkm-list">
                            {(arcs || []).map(a => {
                                const l4ok = l4Count(a) > 0
                                const sel = pickSel === a.id
                                return (
                                    <div key={a.id} className={'pkm-arc ' + (sel ? 'sel ' : '') + (l4ok ? '' : 'dis')}
                                        onClick={() => l4ok && setPickSel(a.id)}>
                                        <span className="cb">{sel ? '✓' : ''}</span>
                                        <div>
                                            <div className="nm">{a.name || a.id} {l4ok && <span className="badge badge-green">l4 ×{l4Count(a)} 场景</span>}</div>
                                            <div className="id">{a.id}</div>
                                        </div>
                                        <span className="grow" />
                                        <span className={'badge ' + (l4ok ? 'badge-cyan' : 'badge-neutral')}>
                                            {l4ok ? '推荐 · 有完整场景快照' : 'l4 快照空 · 先推进情节'}</span>
                                    </div>
                                )
                            })}
                        </div>
                        <div className="pkm-foot">
                            <div className="note">产出：竖屏漫剧成片位（约 3 分钟，含字幕/BGM）· 资产与关键镜头随后在 ark 控制台生成 · 预计成本 &lt;$0.001</div>
                            <span className="grow" />
                            <button className="btn btn-small" onClick={() => setPickOpen(false)}>取消</button>
                            <button className="btn btn-blue" disabled={!pickSel || building} onClick={doBuild}>
                                {building ? '构建中…' : '开始改编（1）'}</button>
                        </div>
                    </div>
                </div>
            )}

            {/* 作品详情（3 Tab overlay） */}
            {detail && (
                <div className="dt on">
                    <div className="dt-top">
                        <button className="dt-back" onClick={() => setDetail(null)}>← 返回</button>
                        <span className="dt-name">{detail.data?.pack?.game?.title || detail.packId}</span>
                        <span className="badge badge-purple">漫剧</span>
                        {detail.data?.validation?.ok === true && <span className="badge badge-green">校验通过</span>}
                        {(detail.data?.validation?.warnings || []).length > 0 &&
                            <span className="badge badge-amber">警告 {detail.data.validation.warnings.length}</span>}
                        <span className="grow" />
                        <button className="dt-wbtn" onClick={() => openWorkbench(detail.packId)}>漫剧工作台</button>
                        <button className="dt-wbtn" onClick={() => navigate('/adaptation/films')}>成片管理</button>
                        <button className="btn btn-small" onClick={() => window.open(dramaMediaUrl(bookRoot, detail.packId, 'drama/drama_preview.mp4'), '_blank')}>打开成片</button>
                        <button className="btn btn-small btn-warnlike" onClick={() => setDelTarget(detail.packId)}>删除</button>
                    </div>
                    <div className="dt-body">
                        <div className="dt-media">
                            <video className="dt-stage" controls preload="metadata"
                                src={dramaMediaUrl(bookRoot, detail.packId, 'drama/drama_preview.mp4')} />
                        </div>
                        <div className="dt-info">
                            <div className="dt-tabs">
                                <button className={'dt-tab ' + (detailTab === 'drama' ? 'on' : '')} onClick={() => setDetailTab('drama')}>漫剧成片</button>
                                <button className={'dt-tab ' + (detailTab === 'assets' ? 'on' : '')} onClick={() => setDetailTab('assets')}>资产图集</button>
                                <button className={'dt-tab ' + (detailTab === 'story' ? 'on' : '')} onClick={() => setDetailTab('story')}>剧情与角色</button>
                            </div>
                            {detailTab === 'drama' && (
                                <div className="dt-panel on">
                                    <div className="dt-h1">{detail.data?.pack?.game?.title || detail.packId}</div>
                                    <p className="dt-logline">{detail.data?.pack?.game?.logline || '—'}</p>
                                    <div className="dt-chips">
                                        <span className="dt-chip hot">竖屏 1080×1920 · 24fps</span>
                                        <span className="dt-chip">{detail.data?.validation?.stats?.nodes ?? '—'} 节点</span>
                                        <span className="dt-chip">{detail.data?.validation?.stats?.endings ?? '—'} 结局</span>
                                        <span className="dt-chip">LLM {detail.data?.validation?.stats?.llm_calls ?? '—'} 次 · ${detail.data?.validation?.stats?.cost_est ?? '—'}</span>
                                    </div>
                                    <div className="dt-sec">
                                        <div className="dt-sec-t">校验报告 <span className="hint">validation.json</span></div>
                                        <div className="dt-list">
                                            {(detail.data?.validation?.warnings || []).map((w, i) => (
                                                <div className="dt-li" key={i}><span className="k">警告</span><span className="v">{w.code} · {w.detail}</span></div>
                                            ))}
                                            {!(detail.data?.validation?.warnings || []).length && <div className="dt-li"><span className="k">校验</span><span className="v">无警告</span></div>}
                                        </div>
                                    </div>
                                </div>
                            )}
                            {detailTab === 'assets' && (
                                <div className="dt-panel on">
                                    <div className="dt-h1">资产图集 <span style={{ fontSize: 13, fontWeight: 400, color: 'var(--ink-mute)' }}>
                                        {(detail.data?.files?.assets?.assets || []).length} 项 · 生成走 ark 控制台</span></div>
                                    <div className="dt-sec">
                                        <div className="dt-assets">
                                            {(detail.data?.files?.assets?.assets || []).map(a => (
                                                <div className={'dt-asset ' + (a.kind === 'portrait' ? 'portrait' : '')} key={a.asset_id}>
                                                    <div className="frame"><span className="ico">{ASSET_ICON[a.kind] || a.kind}</span></div>
                                                    <div className="meta">
                                                        <span className="aid">{a.asset_id}</span><span className="kind">{a.kind}</span>
                                                        <div className="prompt">{a.prompt}</div>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                            )}
                            {detailTab === 'story' && (
                                <div className="dt-panel on">
                                    <div className="dt-h1">剧情与角色</div>
                                    <p className="dt-logline">{detail.data?.pack?.game?.logline || '—'}</p>
                                    <div className="dt-sec">
                                        <div className="dt-sec-t">场景 <span className="hint">story.json</span></div>
                                        <div className="dt-list">
                                            {(detail.data?.files?.story?.nodes || []).map(n => (
                                                <div className="dt-li" key={n.id}>
                                                    <span className="k">{n.id}</span>
                                                    <span className="v">{n.title || n.type || ''} · {(n.lines || []).length} 行
                                                        {n.choices?.length ? ` · ${n.choices.length} 个选择` : ''}</span>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                    <div className="dt-sec">
                                        <div className="dt-sec-t">角色</div>
                                        <div className="dt-list">
                                            {(detail.data?.files?.characters?.characters || []).map(c => (
                                                <div className="dt-li" key={c.id}>
                                                    <span className="k">{c.name || c.id}</span>
                                                    <span className="v">{(c.description || c.desc || '').slice(0, 120)}</span>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                    <div className="dt-sec">
                                        <div className="dt-sec-t">世界观 <span className="hint">world.json</span></div>
                                        <div className="dt-list">
                                            {Object.entries(detail.data?.files?.world?.settings || detail.data?.files?.world || {}).slice(0, 8).map(([k, v]) => (
                                                <div className="dt-li" key={k}><span className="k">{k}</span><span className="v">{typeof v === 'string' ? v : JSON.stringify(v).slice(0, 140)}</span></div>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}

            <ConfirmModal
                open={!!delTarget}
                title="删除改编产物？"
                body={<>将删除 <span className="mono">{delTarget}</span> 的整个目录（含漫剧成片、字幕与资产），不可恢复。</>}
                okText="确认删除"
                onOk={doDelete}
                onCancel={() => setDelTarget(null)}
            />
        </div>
    )
}
