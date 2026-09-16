/**
 * 发布导出（T36 v9）：清单 / 预告片 / 多语言 / 平台规格 / 审核预检 → 发布包。
 *
 * 路由：/adaptation/publish?book=<book_root>&pack=<pack_id>（缺省取第一部成片）
 * 动作：POST /ai-creation/drama/publish（checklist/cover/trailer/i18n/audit/specs/package）
 */
import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { dramaMediaUrl, fetchAdaptationFilms, phDramaPublish } from '../api.js'
import { toast } from '../lib/toast.js'

function fmtBytes(n) {
    if (!n) return '—'
    if (n > 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
    return `${Math.round(n / 1024)} KB`
}

export default function PublishPage() {
    const navigate = useNavigate()
    const location = useLocation()
    const query = new URLSearchParams(location.search)
    const [bookRoot, setBookRoot] = useState(query.get('book') || '')
    const [packId, setPackId] = useState(query.get('pack') || '')
    const [checklist, setChecklist] = useState(null)
    const [specs, setSpecs] = useState([])
    const [error, setError] = useState('')
    const [busy, setBusy] = useState('')

    useEffect(() => {
        if (bookRoot && packId) return
        fetchAdaptationFilms()
            .then(d => {
                const e = (d.episodes || [])[0]
                if (e) { setBookRoot(e.book_root); setPackId(e.pack_id) }
                else setError('还没有成片——先到漫剧工作台合成一集。')
            })
            .catch(e => setError(e.message))
    }, [bookRoot, packId])

    const load = useCallback(async () => {
        if (!bookRoot || !packId) return
        try {
            const r = await phDramaPublish(bookRoot, packId, 'checklist')
            setChecklist(r)
            if ((r.items || []).some(i => i.key === 'specs' && i.done)) {
                const s = await phDramaPublish(bookRoot, packId, 'specs')
                setSpecs(s.specs || [])
            }
        } catch (e) { setError(e.message || '加载发布清单失败') }
    }, [bookRoot, packId])

    useEffect(() => { load() }, [load])

    const act = async (action, key) => {
        if (busy) return
        setBusy(key || action)
        try {
            const r = await phDramaPublish(bookRoot, packId, action)
            if (r.ok) {
                toast(action === 'package' ? `发布包已导出（${fmtBytes(r.bytes)}）`
                    : action === 'i18n' ? `字幕已翻译 ${r.lines || 0} 行`
                        : action === 'audit' ? `审核预检完成：${r.hit_count} 命中`
                            : action === 'cover' ? '封面已生成（成片首帧）'
                                : action === 'trailer' ? `预告片已生成${r.duration ? `（${Math.round(r.duration)}s）` : ''}`
                                    : '完成')
                if (action === 'specs') setSpecs(r.specs || [])
                await load()
            } else {
                toast(`未完成：${r.error || '未知错误'}`)
            }
        } catch (e) { toast(`失败：${e.message}`) }
        finally { setBusy('') }
    }

    const items = checklist?.items || []
    const done = checklist?.done || 0
    const total = checklist?.total || 6
    const pub = items.find(i => i.key === 'package')?.done

    return (
        <div className="z-page">
            <header className="z-head">
                <button className="z-back" onClick={() => navigate('/adaptation/films')}>← 成片管理</button>
                <div className="z-title">发布导出</div>
                <div className="z-sub">竖屏正片 + 横屏预告片 + 多语言 + 平台规格 + 审核预检 → 一键发布包</div>
                <span className="grow" />
                <button className="btn btn-small" disabled={!!busy} onClick={() => act('trailer', 'trailer')}>生成预告片</button>
                <button className="btn btn-blue btn-small" disabled={!!busy || done < total} onClick={() => act('package', 'package')}>导出发布包</button>
            </header>

            <div className="z-body">
                {error && <div className="zsec-empty">{error}</div>}
                {!error && (
                    <div className="ak-grid2">
                        <div>
                            <section className="zsec" style={{ marginTop: 10 }}>
                                <div className="zsec-h">
                                    <div className="zsec-t">发布清单</div>
                                    <div className="zsec-meta">{done} / {total} 项就绪</div>
                                </div>
                                <div style={{ display: 'grid', gap: 8 }}>
                                    {items.map(it => (
                                        <div className="pl-row" key={it.key}>
                                            <span>{it.name}</span>
                                            <span className="d">{it.d}</span>
                                            <span className={'st ' + (it.done ? 'ok' : 'wait')}>{it.done ? '就绪' : '未就绪'}</span>
                                            <button className="mini-btn" disabled={!!busy}
                                                onClick={() => act(it.key === 'i18nEn' ? 'i18n' : it.key, it.key)}>
                                                {busy === it.key ? '执行中…' : it.done ? '重做' : (it.key === 'cover' ? '生成封面' : it.key === 'trailer' ? '生成预告片' : it.key === 'i18nEn' ? '翻译字幕' : it.key === 'audit' ? '执行预检' : it.key === 'specs' ? '导出规格' : '打包导出')}
                                            </button>
                                        </div>
                                    ))}
                                    {!items.length && <div className="zsec-empty">加载中…</div>}
                                </div>
                            </section>

                            <section className="zsec">
                                <div className="zsec-h"><div className="zsec-t">平台规格</div>
                                    <div className="zsec-meta">按平台导出，正片不重编码（仅裁切/封装）</div></div>
                                <div className="dt-list">
                                    {(specs.length ? specs : [
                                        ['抖音 / 快手', '1080×1920 · H.264 · ≤ 5 分钟'],
                                        ['B站（竖屏）', '1080×1920 · H.264 · 封面 1146×717'],
                                        ['合辑（长片）', '1080×1920 · 10 分钟级 · 章节标记'],
                                        ['预告片（横屏）', '1920×1080 · 30–60s'],
                                    ]).map((r, i) => (
                                        <div className="dt-li" key={i}><span className="k">{r[0]}</span><span className="v">{r[1]}</span></div>
                                    ))}
                                </div>
                            </section>

                            <section className="zsec">
                                <div className="zsec-h"><div className="zsec-t">多语言</div>
                                    <div className="zsec-meta">字幕先行（srt）· 配音第二期 · R3 spec 框架 + zh→en 一版</div></div>
                                <div className="dt-list">
                                    {[
                                        ['zh-CN', '正片字幕 + 配音', true],
                                        ['en', '字幕翻译（真实 LLM，点击「翻译字幕」生成）', !!items.find(i => i.key === 'i18nEn')?.done],
                                        ['其他语言', '同管线扩展（内容生产，无代码改动）', false],
                                    ].map((r, i) => (
                                        <div className="dt-li" key={i}>
                                            <span className="k">{r[0]}</span><span className="v">{r[1]}</span>
                                            <span className={'badge ' + (r[2] ? 'badge-green' : 'badge-neutral')}>{r[2] ? '已就绪' : '待做'}</span>
                                        </div>
                                    ))}
                                </div>
                            </section>
                        </div>

                        <div>
                            <section className="zsec" style={{ marginTop: 10 }}>
                                <div className="zsec-h"><div className="zsec-t">预告片（横屏）</div>
                                    <div className="zsec-meta">复用分镜前段（占位素材），关键镜头生成后替换</div></div>
                                <div className="dk-panel">
                                    <div className="dw-cfg">
                                        <div className="row"><span className="k">规格</span><span>1920×1080 · 30–60s · H.264</span></div>
                                        <div className="row"><span className="k">素材</span><span>{items.find(i => i.key === 'trailer')?.done ? '分镜前段镜头（已生成）' : '分镜前段 + 结局片段（待生成）'}</span></div>
                                        <div className="row"><span className="k">状态</span><span>{items.find(i => i.key === 'trailer')?.done ? '已生成' : '未生成'}</span></div>
                                    </div>
                                    <div className="ak-actions">
                                        <button className="mini-btn primary" disabled={!!busy} onClick={() => act('trailer', 'trailer')}>用分镜生成</button>
                                        <button className="mini-btn" onClick={() => navigate(`/adaptation/workbench?book=${encodeURIComponent(bookRoot)}&pack=${encodeURIComponent(packId)}`)}>进工作台</button>
                                    </div>
                                    {items.find(i => i.key === 'trailer')?.done && (
                                        <video style={{ width: '100%', marginTop: 8, borderRadius: 10 }} controls
                                            src={dramaMediaUrl(bookRoot, packId, 'drama/trailer_preview.mp4')} />
                                    )}
                                    <div className="gp-note">预告片会自动登记进发布清单；素材复用漫剧管线（R3 §7.2 口径）。</div>
                                </div>
                            </section>

                            <section className="zsec">
                                <div className="zsec-h"><div className="zsec-t">审核预检</div>
                                    <div className="zsec-meta">本地敏感词 + 规则（离线，不联网）</div></div>
                                <div className="dk-panel">
                                    <div className="ak-kv">
                                        <span>结果</span><b>{items.find(i => i.key === 'audit')?.done ? '已执行' : '未执行'}</b>
                                        <span>范围</span><span>正片字幕 / 预告片 / pack 文案</span>
                                        <span>口径</span><span>命中项列出位置并给替换建议；发布前必须清零（R3 §6 层 1）</span>
                                    </div>
                                    <div className="ak-actions">
                                        <button className="mini-btn" disabled={!!busy} onClick={() => act('audit', 'audit')}>执行预检</button>
                                    </div>
                                    {pub && <div className="gp-note">发布包已导出：{checklist?.package_path || '见 .ainovel/films/'}</div>}
                                </div>
                            </section>
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}
