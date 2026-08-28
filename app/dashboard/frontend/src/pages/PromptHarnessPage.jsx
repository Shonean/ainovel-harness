import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchApiLibrary, applyApiPreset, phConnectionsOverview } from '../api.js'
import PromptLogsPage from './PromptLogsPage.jsx'
import PlotExtractHub from '../components/PlotExtractHub.jsx'
import TemplateLibraryPage from './TemplateLibraryPage.jsx'
import ConnectionPanel from '../components/ConnectionPanel.jsx'

/**
 * 文字模型预设选择器（下拉切换多组预设）
 */
function TextPresetSelector() {
    const [open, setOpen] = useState(false)
    const [presets, setPresets] = useState([])
    const [currentId, setCurrentId] = useState(null)
    const [switching, setSwitching] = useState(false)
    const ref = useRef(null)

    useEffect(() => {
        const handler = (e) => {
            if (ref.current && !ref.current.contains(e.target)) setOpen(false)
        }
        document.addEventListener('mousedown', handler)
        return () => document.removeEventListener('mousedown', handler)
    }, [])

    const loadPresets = useCallback(() => {
        fetchApiLibrary().then(r => {
            setPresets(r.text_presets || [])
            setCurrentId(r.current_text_id || null)
        }).catch(() => {})
    }, [])

    const handleApply = async (presetId) => {
        setSwitching(true)
        try {
            await applyApiPreset(presetId)
            setCurrentId(presetId)
            setOpen(false)
        } catch (e) {
            alert('切换文字模型预设失败：' + e.message)
        }
        setSwitching(false)
    }

    const currentPreset = presets.find(p => p.id === currentId)

    return (
        <div className="ph-api-preset-selector" ref={ref}>
            <button
                className="btn btn-small"
                style={{ background: 'var(--cinnabar)', color: 'var(--paper-raised)', border: 'none' }}
                onClick={() => { setOpen(!open); if (!open) loadPresets() }}
                disabled={switching}
                title="切换文字模型预设"
            >
                {switching ? '⏳' : ''} {currentPreset?.name || '文字模型'} ▾
            </button>
            {open && (
                <div className="prompt-harness-menu" style={{ right: 0, left: 'auto', minWidth: 220 }}>
                    <div className="prompt-harness-item" style={{
                        fontWeight: 600, color: 'var(--ink-sub)', fontSize: '0.75rem',
                        padding: '4px 12px', cursor: 'default',
                    }}>
                        文字模型预设
                    </div>
                    {presets.length === 0 ? (
                        <div className="prompt-harness-item" style={{ color: 'var(--ink-mute)', cursor: 'default' }}>
                            暂无预设
                        </div>
                    ) : (
                        presets.map(p => (
                            <div
                                key={p.id}
                                className="prompt-harness-item"
                                onClick={() => handleApply(p.id)}
                                style={{
                                    flexDirection: 'column',
                                    alignItems: 'flex-start',
                                    gap: 2,
                                    background: p.id === currentId ? 'var(--cinnabar-wash)' : undefined,
                                }}
                            >
                                <span style={{ fontWeight: 600 }}>{p.id === currentId ? '' : ''}{p.name}</span>
                                <span style={{ fontSize: '0.7rem', color: 'var(--ink-mute)' }}>
                                    {p.fields?.ARK_MODEL_PRO || '(未设模型)'}
                                </span>
                            </div>
                        ))
                    )}
                    <a
                        href="#/api-presets"
                        className="prompt-harness-item"
                        style={{ borderTop: '1px solid var(--line)', color: 'var(--dai)' }}
                        onClick={() => setOpen(false)}
                    >
                        管理预设…
                    </a>
                </div>
            )}
        </div>
    )
}

/**
 * 向量模型指示器（显示当前模型，点击跳转到管理页）
 */
function EmbedPresetIndicator() {
    const [embedModel, setEmbedModel] = useState('')
    const [loaded, setLoaded] = useState(false)

    const load = useCallback(() => {
        fetchApiLibrary().then(r => {
            const fields = (r.embed_config && r.embed_config.fields) || {}
            setEmbedModel(fields.EMBED_MODEL || '')
            setLoaded(true)
        }).catch(() => {})
    }, [])

    useEffect(() => { load() }, [load])

    return (
        <a
            href="#/api-presets"
            className="btn btn-small"
            style={{
                background: 'var(--green-wash)', color: 'var(--green)',
                border: '1px solid var(--green)', cursor: 'pointer',
            }}
            title="向量模型配置（全局共用，点此去修改）"
        >
            {loaded ? (embedModel || '未配置') : '...'}
        </a>
    )
}

/**
 * 炼工台 · 统一界面（v7.8）
 * 一屏四卷：榨干提取 | 情节模板库 | 连接·主系统 | 日志中心
 * 顶部流水线横条 = 训练→模板→主系统 连接总览（常驻）
 */
export default function PromptHarnessPage() {
    const [overview, setOverview] = useState(null)
    const [overviewErr, setOverviewErr] = useState('')
    const [logsOpen, setLogsOpen] = useState(false)

    const loadOverview = useCallback(() => {
        phConnectionsOverview().then(r => {
            setOverview(r.overview || null)
            setOverviewErr('')
        }).catch(e => setOverviewErr(String(e.message || e)))
    }, [])
    useEffect(() => { loadOverview() }, [loadOverview])

    const o = overview || {}
    const counts = o.counts || {}
    const logSum = o.log_summary || {}

    return (
        <div className="prompt-system-layout lintai">
            <nav className="prompt-system-nav">
                <a href="#/" className="prompt-system-back">⇦ 返回主页</a>
                <div className="ph-brand"><span>Prompt Harness</span> <em>· 炼工台</em></div>
                <span className="prompt-system-spacer" />
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="ph-health" title="服务状态">● 服务就绪</span>
                    <EmbedPresetIndicator />
                    <TextPresetSelector />
                </div>
            </nav>

            {/* ── 训练流水线横条（连接总览 · 常驻）── */}
            <div className="ph-pipe">
                <span className="ph-pipe-lbl">连接总览</span>
                <span className="st">语料库 <b>{counts.corpus_file_count ?? '—'}</b></span>
                                <span className="st">榨干提取 <b>达标入库</b></span>
                                <span className="st">模板库 <b>{counts.template_count ?? '—'}</b> 条</span>
                                <span className="st hl">主系统消费 <b>{counts.consumption_count ?? '—'}</b> 情节</span>
                                <span className="st">五级阶梯 <b>l1-l5</b></span>
                <span className="ph-pipe-note">模板库命中 → 自动注入 step_ladder（格式 + 策略）</span>
                {overviewErr && <span className="ph-pipe-note" style={{ color: 'var(--cinnabar-d)' }}>⚠ {overviewErr}</span>}
            </div>

            {/* ── 主体三栏 ── */}
            <div className="ph-cols">
                <section className="ph-col">
                    <div className="ph-col-hd"><span>榨干提取</span><span className="n">复现原文 · 达标入库</span><span className="sp" /><span className="ph-chip green">自动提取风格/角色</span></div>
                    <div className="ph-col-bd"><PlotExtractHub /></div>
                </section>

                <section className="ph-col">
                    <div className="ph-col-hd"><span>情节模板库</span><span className="n">唯一与主系统连接的内容</span><span className="sp" /><span className="ph-chip">{counts.template_count ?? '—'} 条</span></div>
                    <div className="ph-col-bd"><TemplateLibraryPage /></div>
                </section>

                <section className="ph-col ph-col-conn">
                    <div className="ph-col-hd ph-col-hd-conn"><span>连接 · 主系统</span><span className="n">prompt → 主系统 可视化</span><span className="sp" /><span className="ph-chip dai">新模块</span></div>
                    <div className="ph-col-bd"><ConnectionPanel overview={o} /></div>
                </section>
            </div>

            {/* ── 底部日志条 ── */}
            <div className="ph-logbar">
                <div className="ph-logbar-hd" onClick={() => setLogsOpen(v => !v)}>
                    <span>日志中心</span>
                    <span className="dim">{logSum.total_calls ?? 0} 调用 / {logSum.total_tokens ?? 0} tokens / {logSum.error_count ?? 0} 错误</span>
                    <span className="sp" />
                    <span className="dim">{logsOpen ? '▾ 收起' : '▸ 展开完整日志中心（任务 / LLM / 会话）'}</span>
                </div>
                {logsOpen && (
                    <div className="ph-logbar-bd"><PromptLogsPage /></div>
                )}
            </div>
        </div>
    )
}
