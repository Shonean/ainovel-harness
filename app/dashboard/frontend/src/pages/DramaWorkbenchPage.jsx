/**
 * 漫剧工作台（T36 · v9 六步）：分镜 / 资产 / 配音 / 关键镜头 / 字幕 / 合成导出。
 *
 * 路由：/adaptation/workbench?book=<book_root>&pack=<pack_id>
 * 数据：POST /ai-creation/drama/workbench（分镜/字幕/资产/配音/关键镜头/成片/任务/预算）
 * 生成类动作 → 内嵌 ark 控制台（任务队列 → 驱动 → 回填 + 记账）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
    dramaMediaUrl, phArk, phArkChat, phDramaCompilation, phDramaCompose, phDramaFilmExport,
    phDramaKeyshot, phDramaWorkbench, phTasks, switchProject,
} from '../api.js'
import ArkConsole from '../components/ArkConsole.jsx'
import { toast } from '../lib/toast.js'

const STEPS = [
    { key: 'shot', no: '①', label: '分镜' },
    { key: 'asset', no: '②', label: '资产' },
    { key: 'voice', no: '③', label: '配音' },
    { key: 'key', no: '④', label: '关键镜头' },
    { key: 'srt', no: '⑤', label: '字幕' },
    { key: 'compose', no: '⑥', label: '合成导出' },
]

const STEP_HINTS = {
    shot: '分镜 = 确定性投影；改时长会联动全片',
    asset: '资产生成 → ark 控制台（seedream）；入库即记账',
    voice: '离线 Kokoro 预生成 · 在线可切云 TTS',
    key: 'Seedance 白名单 · draft 两段式控本',
    srt: '字幕随成片烧录，也可导出独立 srt',
    compose: '合成导出：BGM / 转场 / 画幅 / 成片管理',
}

function fmtSec(v) {
    const n = Number(v) || 0
    return n % 1 ? n.toFixed(1) : String(n)
}

function assetStateBadge(state) {
    if (state === 'done') return <span className="badge badge-green">已生成</span>
    if (state === 'gen') return <span className="badge badge-amber">生成中</span>
    if (state === 'failed') return <span className="badge badge-red">失败</span>
    return <span className="badge badge-neutral">待生成</span>
}

function StepPanels({ data, step, onOpenArk, onKeyAction, onCompose, onExport, onAddComp, composeCfg, setComposeCfg, busy, onPreviewVoice, mediaUrl }) {
    if (step === 'shot') {
        return (
            <div className="dk-panel dw-panel on">
                <div className="dk-panel-t">镜头明细 <span style={{ fontWeight: 400, fontSize: 11.5, color: 'var(--ink-mute)' }}>
                    story → shots 确定性投影（{data.shots.shots} 镜）</span></div>
                <div className="dt-list">
                    {data.shots.timeline.map(row => (
                        <div className="dt-li" key={row.node_id}>
                            <span className="k">{row.node_id}{row.is_ending ? ' · 结局' : ''}</span>
                            <span className="v">{row.shots} 镜 · {fmtSec(row.duration)}s
                                <br /><span style={{ color: 'var(--ink-mute)' }}>旁白 {row.narration} / 对白 {row.dialogue} · {(row.sample || '').slice(0, 40)}</span>
                            </span>
                        </div>
                    ))}
                </div>
                <div className="step-note">1 个 l4 场景 = N 个镜头：narration→推镜 / inner→特写 / dialogue→正反打 / stage→演出提示。</div>
            </div>
        )
    }
    if (step === 'asset') {
        return (
            <div className="dk-panel dw-panel on">
                <div className="dk-panel-t">资产清单 <span style={{ fontWeight: 400, fontSize: 11.5, color: 'var(--ink-mute)' }}>
                    {data.assets.length} 项 · 内容寻址入库 · 跨 Pack 复用</span></div>
                <div className="dt-assets">
                    {data.assets.map(a => (
                        <div className={'dt-asset ' + (a.kind === 'portrait' ? 'portrait' : '')} key={a.asset_id}>
                            <div className="frame"><span className="ico">{a.state === 'done' ? '已生成 ✓' : (a.kind === 'portrait' ? '立绘 · 待生成' : a.kind === 'voice' ? '语音 · 待生成' : '背景 · 待生成')}</span></div>
                            <div className="meta">
                                <span className="aid">{a.asset_id}</span><span className="kind">{a.kind}</span>
                                <div className="prompt">{a.prompt}</div>
                            </div>
                        </div>
                    ))}
                    {!data.assets.length && <div className="zsec-empty">该 pack 没有资产清单（assets.json 为空）。</div>}
                </div>
                <div className="ak-actions">
                    <button className="mini-btn primary" onClick={() => onOpenArk('gen', {
                        kind: 'image', size: '768x1024', label: '生成立绘',
                        prompt: (data.assets.find(a => a.kind === 'portrait') || {}).prompt || '',
                        entry_asset_id: (data.assets.find(a => a.kind === 'portrait' && a.state !== 'done') || {}).asset_id || '',
                    })}>去生成立绘</button>
                    <button className="mini-btn" onClick={() => onOpenArk('gen', {
                        kind: 'image', size: '1080x1920', label: '生成背景',
                        prompt: (data.assets.find(a => a.kind === 'background') || {}).prompt || '',
                        entry_asset_id: (data.assets.find(a => a.kind === 'background' && a.state !== 'done') || {}).asset_id || '',
                    })}>去生成背景</button>
                    <button className="mini-btn" onClick={() => onOpenArk('understanding')}>理解 · 字幕对齐</button>
                </div>
                <div className="step-note">一致性：先出三视图参考图，再出表情差分；生成走 ark 控制台 seedream，入库自动记账。</div>
            </div>
        )
    }
    if (step === 'voice') {
        return (
            <div className="dk-panel dw-panel on">
                <div className="dk-panel-t">配音 <span style={{ fontWeight: 400, fontSize: 11.5, color: 'var(--ink-mute)' }}>
                    Kokoro 离线 ↔ 云 TTS · 同一音色表</span></div>
                <div className="dw-cfg" style={{ marginBottom: 8 }}>
                    <div className="row"><span className="k">音色</span><span>默认（中性 · 压抑）· 语速 0.92</span></div>
                </div>
                <div>
                    {data.voice.map((v, i) => (
                        <div className="vc-row" key={v.asset_id || i}>
                            <span className="who">旁白</span>
                            <span className="txt">{(v.prompt || '').replace(/^内心独白：/, '')}</span>
                            <span className="tag">{v.state === 'done' ? '已生成' : '待生成'}</span>
                            <span className="act">
                                {v.state === 'done'
                                    ? <button onClick={() => onPreviewVoice(v)}>试听</button>
                                    : <button onClick={() => onOpenArk('gen', {
                                        kind: 'voice', label: '生成语音', prompt: (v.prompt || '').replace(/^内心独白：/, ''),
                                        entry_asset_id: v.asset_id || '',
                                    })}>生成</button>}
                            </span>
                        </div>
                    ))}
                    {!data.voice.length && <div className="zsec-empty">没有可配音的旁白行。</div>}
                </div>
                <div className="step-note">离线成品的配音在制作期全部预生成（游戏/漫剧共用）；在线模式可切云 TTS 流式。</div>
            </div>
        )
    }
    if (step === 'key') {
        return (
            <div className="dk-panel dw-panel on">
                <div className="dk-panel-t">关键镜头（Seedance） <span style={{ fontWeight: 400, fontSize: 11.5, color: 'var(--ink-mute)' }}>
                    白名单 · draft 控本 · 预算可见</span></div>
                <div style={{ display: 'grid', gap: 8 }}>
                    {data.keyshots.map((k, i) => (
                        <div className={'ks-row' + (k.stage === 2 ? ' done' : '')} key={k.shot_id || i}>
                            <span className="n">{k.shot_id}</span>
                            <span className="d">{k.desc}</span>
                            <span className="cost">draft ¥{Number(k.cost_draft || 0).toFixed(1)} · 正式 ¥{Number(k.cost_final || 0).toFixed(1)}</span>
                            {k.stage === 0 && <span className="badge badge-neutral">未生成</span>}
                            {k.stage === 1 && <span className="badge badge-amber">draft 已出 · 待审片</span>}
                            {k.stage === 2 && <span className="badge badge-green">正式完成</span>}
                            <span className="act">
                                {k.stage === 0 && <button className="mini-btn primary" disabled={busy} onClick={() => onKeyAction('draft', i)}>出 draft</button>}
                                {k.stage === 1 && <button className="mini-btn primary" disabled={busy} onClick={() => onKeyAction('final', i)}>通过 · 出正式</button>}
                                {k.stage === 2 && <button className="mini-btn" onClick={() => onKeyAction('reset', i)}>重置</button>}
                            </span>
                        </div>
                    ))}
                </div>
                <div className="ak-kv" style={{ marginTop: 10, gridTemplateColumns: '96px 1fr' }}>
                    <span>视频余额</span><b>¥{Number(data.budget?.video_month_cny || 0).toFixed(1)}</b>
                    <span>策略</span><span>draft 两段式：先出 5s draft 审片 → 通过才出正式；失败降级静帧（旁白托底）。未开通视觉资源时为「降级 skipped」并说明原因。</span>
                </div>
            </div>
        )
    }
    if (step === 'srt') {
        return (
            <div className="dk-panel dw-panel on">
                <div className="dk-panel-t">字幕（srt） <span style={{ fontWeight: 400, fontSize: 11.5, color: 'var(--ink-mute)' }}>
                    TTS 时间戳优先 · 缺则强制对齐</span></div>
                <div className="dt-sub">
                    {data.srt.map((r, i) => (
                        <div className="dt-subrow" key={i}><span className="t">{r.t}</span><span>{r.x}</span></div>
                    ))}
                    {!data.srt.length && <div className="dt-subrow"><span>暂无字幕（先生成分镜）</span></div>}
                </div>
                <div className="ak-actions">
                    <button className="mini-btn" onClick={() => onOpenArk('understanding')}>faster-whisper 强制对齐</button>
                    <button className="mini-btn" onClick={() => onOpenArk('understanding')}>理解 · 语音转写</button>
                </div>
                <div className="step-note">字幕轨随成片烧录，也可导出独立 srt（发布多语言用）。</div>
            </div>
        )
    }
    // compose
    return (
        <div className="dk-panel dw-panel on">
            <div className="dk-panel-t">竖屏成片 <span style={{ fontWeight: 400, fontSize: 11.5, color: 'var(--ink-mute)' }}>
                pack 内真实文件</span></div>
            <video className="dw-video" controls preload="metadata" src={mediaUrl} key={mediaUrl} />
            <div className="dk-panel-t" style={{ marginTop: 14 }}>合成配置</div>
            <div className="dw-cfg">
                <div className="row"><span className="k">BGM</span>
                    <select value={composeCfg.bgm} onChange={e => setComposeCfg(c => ({ ...c, bgm: e.target.value }))}>
                        <option value="">无（暂无可选曲库）</option>
                    </select>
                </div>
                <div className="row"><span className="k">BGM 音量</span>
                    <input type="number" style={{ width: 70 }} value={composeCfg.bgm_gain}
                        onChange={e => setComposeCfg(c => ({ ...c, bgm_gain: Number(e.target.value) }))} /> % · ducking（对白自动压低）
                </div>
                <div className="row"><span className="k">字幕</span>
                    <select value={composeCfg.burn_subtitles ? 'burn' : 'srt'}
                        onChange={e => setComposeCfg(c => ({ ...c, burn_subtitles: e.target.value === 'burn' }))}>
                        <option value="burn">烧录进画面</option><option value="srt">独立 srt</option>
                    </select>
                </div>
                <div className="row"><span className="k">画幅</span>
                    <select value={composeCfg.orientation} onChange={e => setComposeCfg(c => ({ ...c, orientation: e.target.value }))}>
                        <option value="portrait">竖屏 1080×1920（正片）</option>
                        <option value="landscape">横屏 1920×1080（预告片）</option>
                    </select>
                </div>
                <div className="row"><span className="k">输出</span><span>24fps · H.264 · 音轨 AAC</span></div>
            </div>
            <div className="ak-actions">
                <button className="mini-btn" disabled={busy} onClick={onCompose}>重新合成</button>
                <button className="mini-btn primary" onClick={onExport}>导出成片</button>
                <button className="mini-btn" onClick={onAddComp}>加入合辑</button>
            </div>
            <div className="gp-note">引擎侧已实现：projector 分镜投影 · compose（PyAV + Ken Burns + 字幕烧录 + BGM/ducking/crossfade）· align（faster-whisper）。</div>
        </div>
    )
}

function WorkbenchAssistant({ bookRoot, packId, packTitle }) {
    const [msgs, setMsgs] = useState([{ who: 'ai', text: `我是导演助手（漫剧 · ${packTitle || '当前集'}）。点步骤看数据，生成类动作走 ark 控制台并逐笔记账。` }])
    const [input, setInput] = useState('')
    const quick = [
        '背景提示词：加深夜湿气',
        '结局怎么改更狠一点？',
        '场景一节奏加快（压缩镜头）',
        '这部片子怎么发布到多平台？',
    ]
    const send = async (text) => {
        const txt = (text || input).trim()
        if (!txt) return
        setInput('')
        const history = msgs.slice(-6).map(m => ({ role: m.who === 'me' ? 'user' : 'assistant', content: m.text }))
        setMsgs(m => [...m, { who: 'me', text: txt }])
        try {
            const r = await phArkChat(txt, history)
            setMsgs(m => [...m, { who: 'ai', text: r.ok ? r.reply : `（失败：${r.error}）` }])
        } catch (e) {
            setMsgs(m => [...m, { who: 'ai', text: `（失败：${e.message}）` }])
        }
    }
    return (
        <div className="aa-host">
            <div className="aa">
                <div className="aa-head"><span className="dot" /><span className="t">改编助手</span>
                    <span className="ctx">漫剧 · {packTitle || '—'}</span><span className="grow" /></div>
                <div className="aa-body on">
                    <div className="aa-msgs">
                        {msgs.map((m, i) => (
                            <div className={'aa-msg ' + (m.who === 'me' ? 'me' : 'ai')} key={i}>
                                <span className="who">{m.who === 'me' ? '你' : '改编助手'}</span>{m.text}
                            </div>
                        ))}
                    </div>
                    <div className="aa-quick">
                        {quick.map(q => <span className="aa-q" key={q} onClick={() => send(q)}>{q}</span>)}
                    </div>
                    <div className="aa-input">
                        <textarea value={input} onChange={e => setInput(e.target.value)} placeholder="说说这部片子怎么改…（镜头 / 资产 / 集 / 发布）" />
                        <button onClick={() => send()}>发送</button>
                    </div>
                </div>
                <div className="aa-base">生成 = 本项目 ark 控制台（ark-cli 自建前端）· 本地编辑 = pack 文件 · 账本 = usage_ledger。</div>
            </div>
        </div>
    )
}

export default function DramaWorkbenchPage() {
    const navigate = useNavigate()
    const location = useLocation()
    const params = useMemo(() => new URLSearchParams(location.search), [location.search])
    const bookRoot = params.get('book') || ''
    const packId = params.get('pack') || ''

    const [data, setData] = useState(null)
    const [error, setError] = useState('')
    const [step, setStep] = useState('shot')
    const [arkOpen, setArkOpen] = useState(false)
    const [arkDomain, setArkDomain] = useState('gen')
    const [arkPrefill, setArkPrefill] = useState(null)
    const [busy, setBusy] = useState('')
    const [composeTask, setComposeTask] = useState('')
    const [composeCfg, setComposeCfg] = useState({ bgm: '', bgm_gain: 18, burn_subtitles: true, orientation: 'portrait' })
    const composePoll = useRef(null)

    const load = useCallback(async () => {
        if (!bookRoot || !packId) return
        setError('')
        try { setData(await phDramaWorkbench(bookRoot, packId)) }
        catch (e) { setError(e.message || '加载工作台失败') }
    }, [bookRoot, packId])

    useEffect(() => { load() }, [load])

    useEffect(() => {
        if (!composeTask) return
        const iv = setInterval(async () => {
            try {
                const r = await phTasks()
                const t = (r.tasks || []).find(x => x.task_id === composeTask)
                if (t && ['done', 'failed', 'cancelled'].includes(t.status)) {
                    clearInterval(iv); setComposeTask(''); setBusy('')
                    if (t.status === 'done') { toast('合成完成'); load() }
                    else toast(`合成${t.status === 'failed' ? '失败' : '已取消'}${t.error ? '：' + t.error : ''}`)
                }
            } catch { /* 忽略轮询错误 */ }
        }, 2000)
        composePoll.current = iv
        return () => clearInterval(iv)
    }, [composeTask, load])

    if (!bookRoot || !packId) {
        return (
            <div className="z-page">
                <header className="z-head">
                    <button className="z-back" onClick={() => navigate('/adaptation')}>← 改编中心</button>
                    <div className="z-title">漫剧工作台</div>
                </header>
                <div className="z-body"><div className="zsec-empty">缺少 book / pack 参数——请从改编中心或书卡进入。</div></div>
            </div>
        )
    }
    if (error) {
        return (
            <div className="z-page">
                <header className="z-head">
                    <button className="z-back" onClick={() => navigate('/adaptation')}>← 改编中心</button>
                    <div className="z-title">漫剧工作台</div>
                </header>
                <div className="z-body">
                    <div className="zsec-empty">{error}<br />
                        <button className="btn btn-small" style={{ marginTop: 10 }} onClick={load}>重试</button>
                    </div>
                </div>
            </div>
        )
    }
    if (!data) return <div className="z-page"><div className="z-body"><div className="zsec-empty">加载中…</div></div></div>

    const packTitle = data.pack?.game?.title || packId
    const mediaUrl = dramaMediaUrl(bookRoot, packId, 'drama/drama_preview.mp4')
    const summary = data.shots
    const typeCounts = summary.type_counts || {}
    const typeRows = [
        ['still_push 推镜', typeCounts.still_push || 0, '2.9–3.0s/镜'],
        ['closeup 特写', typeCounts.closeup || 0, '3.0s/镜'],
        ['正反打', typeCounts.shot_reverse_shot || 0, '3.0s/镜'],
        ['结束卡', typeCounts.end_card || 0, '3.5s/镜'],
    ]

    const openArk = (domain, prefill) => { setArkDomain(domain || 'gen'); setArkPrefill(prefill || null); setArkOpen(true) }

    const keyAction = async (action, index) => {
        setBusy('key')
        try {
            const r = await phDramaKeyshot(bookRoot, packId, action, index)
            if (r.ok) toast(action === 'draft' ? 'draft 已出——审片通过后再出正式' : action === 'final' ? '正式镜头完成' : '已重置')
            else toast(`未产出（${r.driver_status}）：${(r.reason || '').slice(0, 80)}`)
            load()
        } finally { setBusy('') }
    }

    const compose = async () => {
        setBusy('compose')
        try {
            const cfg = {
                burn_subtitles: composeCfg.burn_subtitles,
                bgm: composeCfg.bgm || null,
                bgm_gain: composeCfg.bgm_gain / 100,
                ...(composeCfg.orientation === 'landscape' ? { width: 1920, height: 1080 } : {}),
            }
            const r = await phDramaCompose(bookRoot, packId, cfg)
            if (!r.ok) { toast(`合成提交失败：${r.error || ''}`); setBusy(''); return }
            setComposeTask(r.task_id)
            toast('合成任务已排队（PyAV 真合成，完成后自动刷新）')
        } catch (e) { toast(`合成失败：${e.message}`); setBusy('') }
    }

    const exportFilm = async () => {
        const r = await phDramaFilmExport(bookRoot, packId)
        toast(r.ok ? '成片已导出到 .ainovel/films/' : `导出失败：${r.error}`)
    }
    const addComp = async () => {
        const r = await phDramaCompilation(bookRoot, 'add', { pack_id: packId })
        toast(r.ok ? '已加入合辑（成片管理里可导出）' : `失败：${r.error}`)
    }
    const previewVoice = (v) => {
        const out = (v.state === 'done') ? '试听：离线 WAV 产物（未集成播放器）' : '尚未生成'
        toast(out)
    }
    const backToBook = async () => {
        try { await switchProject(bookRoot) } catch { /* 忽略 */ }
        navigate('/ai-creation?tab=adapt')
    }

    return (
        <div className="dk-page">
            <header className="dk-head">
                <button className="z-back" onClick={backToBook}>← 书级改编</button>
                <div className="dk-title">漫剧工作台</div>
                <div className="dk-sub">{data.pack?.source?.book_title || ''} · {packTitle} · {summary.duration_text} · {summary.shots} 镜头 ·
                    {summary.size?.width}×{summary.size?.height} · 旁白 {summary.tracks.narration} / 对白 {summary.tracks.dialogue}</div>
                <span className="grow" />
                <button className="btn btn-small" onClick={() => openArk('overview')}>ark 控制台</button>
                <button className="btn btn-small" onClick={() => navigate('/adaptation/films')}>成片管理</button>
                <button className="btn btn-blue btn-small" onClick={exportFilm}>导出成片</button>
            </header>

            <div className="dk-steps">
                {STEPS.map(s => (
                    <button key={s.key} className={'dk-step' + (step === s.key ? ' on' : '')} onClick={() => setStep(s.key)}>
                        <span className="no">{s.no}</span>{s.label}
                    </button>
                ))}
                <span className="grow" />
                <span className="hint">{STEP_HINTS[step]}</span>
            </div>

            <div className="dk-body three">
                <div className="dk-panel">
                    <div className="dk-panel-t">分镜时间线 <span style={{ fontWeight: 400, fontSize: 11.5, color: 'var(--ink-mute)' }}>
                        灰=旁白轨 · 深=对白轨</span></div>
                    <div className="tl">
                        {summary.timeline.map(row => {
                            const total = Math.max(1, row.narration + row.dialogue)
                            return (
                                <div className="tl-row" key={row.node_id}>
                                    <span className="tl-node">{row.node_id}</span>
                                    <div className="tl-track">
                                        {row.narration > 0 && <i className="blk n" style={{ flex: row.narration }} />}
                                        {row.dialogue > 0 && <i className="blk d" style={{ flex: row.dialogue }} />}
                                    </div>
                                    <span className="tl-sec">{fmtSec(row.duration)}s</span>
                                </div>
                            )
                        })}
                    </div>
                    <div className="tl-legend">
                        <span><i className="blk n" />旁白轨 <b>{summary.tracks.narration}</b> 镜</span>
                        <span><i className="blk d" />对白轨 <b>{summary.tracks.dialogue}</b> 镜</span>
                    </div>
                    <div className="dk-panel-t" style={{ marginTop: 18 }}>镜头类型分布</div>
                    <div className="dt-bars">
                        {typeRows.map(([n, v, s]) => (
                            <div className="dt-bar" key={n}><div className="n">{n}</div><div className="v">{v}</div><div className="s">{s}</div></div>
                        ))}
                    </div>
                </div>

                <StepPanels
                    data={data} step={step} busy={busy}
                    onOpenArk={openArk} onKeyAction={keyAction} onCompose={compose}
                    onExport={exportFilm} onAddComp={addComp} onPreviewVoice={previewVoice}
                    composeCfg={composeCfg} setComposeCfg={setComposeCfg} mediaUrl={mediaUrl}
                />

                <div style={{ display: 'grid', gap: 14, alignContent: 'start' }}>
                    <div className="dk-panel">
                        <div className="dk-panel-t">生成任务（ark 控制台）</div>
                        {(data.tasks || []).length === 0
                            ? <div className="aa-note">还没有任务——在「资产 / 配音」步骤点生成入口。</div>
                            : (data.tasks || []).slice(0, 5).map(t => (
                                <div className="task" key={t.id}>
                                    <div className="task-h"><span className="t">{t.label}</span>
                                        {t.status === 'queued' && <span className="badge badge-neutral">排队</span>}
                                        {t.status === 'running' && <span className="badge badge-amber">生成中…</span>}
                                        {t.status === 'succeeded' && <span className="badge badge-green">成功</span>}
                                        {(t.status === 'skipped' || t.status === 'not_installed') && <span className="badge badge-amber">降级</span>}
                                        {t.status === 'failed' && <span className="badge badge-red">失败</span>}
                                        {t.filled && <span className="badge badge-green">已回填</span>}
                                    </div>
                                    <div className="task-cmd">{t.cmd}</div>
                                    {(t.status === 'skipped' || t.status === 'not_installed') && <div className="task-cmd">原因：{(t.reason || '').slice(0, 90)}</div>}
                                    <div className="task-btns">
                                        <button onClick={() => openArk('gen')}>在 ark 控制台查看</button>
                                    </div>
                                </div>
                            ))}
                        <div className="aa-budget" style={{ marginTop: 8 }}>
                            生图余额 <b>¥{Number(data.budget?.image_month_cny || 0).toFixed(1)}</b> ·
                            视频余额 <b>¥{Number(data.budget?.video_month_cny || 0).toFixed(1)}</b>
                        </div>
                    </div>
                    <WorkbenchAssistant bookRoot={bookRoot} packId={packId} packTitle={packTitle} />
                </div>
            </div>

            <ArkConsole
                open={arkOpen} onClose={() => setArkOpen(false)}
                bookRoot={bookRoot} packId={packId} ctx="drama"
                initialDomain={arkDomain} prefill={arkPrefill}
            />
        </div>
    )
}
