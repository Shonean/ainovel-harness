import { useCallback, useEffect, useRef, useState } from 'react'
import {
    phGetCorpusFiles, phExtractPlotTemplates, phTaskStatus, phStoreExtractTemplate,
} from '../api.js'

const btn = (disabled, primary) => ({
    padding: '8px 16px', borderRadius: 6, border: '1px solid var(--line-soft)',
    background: primary ? 'var(--green)' : 'var(--paper-raised)', color: primary ? 'var(--paper-raised)' : 'var(--ink)',
    cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1,
    fontSize: 14, fontWeight: primary ? 600 : 400,
})
const card = { border: '1px solid var(--line)', borderRadius: 8, padding: 12, background: 'var(--paper-raised)', marginBottom: 10 }
const pre = { whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.7, margin: 0 }
const badge = (ok) => ({
    padding: '2px 8px', borderRadius: 10, fontSize: 12,
    background: ok ? 'var(--green-wash)' : 'var(--cinnabar-wash)', color: ok ? 'var(--green)' : 'var(--cinnabar-d)',
})

export default function PlotExtractHub() {
    const [files, setFiles] = useState([])
    const [filepath, setFilepath] = useState('')
    const [startChapter, setStartChapter] = useState(1)
    const [endChapter, setEndChapter] = useState('')
    const [minScore, setMinScore] = useState(0.65)

    const [taskId, setTaskId] = useState(null)
    const [running, setRunning] = useState(false)
    const [status, setStatus] = useState(null)     // {status, progress, result, error}
    const [error, setError] = useState('')

    const [storing, setStoring] = useState({})   // {arc: bool} 入库中
    const pollTimer = useRef(null)

    const loadFiles = useCallback(() => {
        phGetCorpusFiles('flat').then(r => {
            const arr = r?.files || []
            setFiles(arr)
            if (!filepath && arr.length) setFilepath(arr[0]?.path || arr[0]?.name || '')
        }).catch(() => setFiles([]))
    }, [filepath])
    useEffect(() => { loadFiles() }, []) // eslint-disable-line react-hooks/exhaustive-deps

    // 任务轮询
    useEffect(() => {
        if (!taskId || !running) return
        pollTimer.current = setInterval(async () => {
            try {
                const s = await phTaskStatus(taskId)
                setStatus(s)
                if (s?.status === 'done' || s?.status === 'failed') {
                    setRunning(false)
                    clearInterval(pollTimer.current)
                }
            } catch { /* 继续轮询 */ }
        }, 4000)
        return () => clearInterval(pollTimer.current)
    }, [taskId, running])

    // 单按钮榨干（overrides 可覆盖起止章；全文档 = {start_chapter:1, end_chapter:null}）
    const handleExtract = async (overrides = {}) => {
        if (!filepath) { setError('请选择要榨干的文档'); return }
        setError(''); setRunning(true); setStatus(null)
        try {
            const r = await phExtractPlotTemplates({
                filepath,
                start_chapter: overrides.start_chapter ?? startChapter,
                end_chapter: overrides.end_chapter !== undefined
                    ? overrides.end_chapter
                    : (endChapter ? Number(endChapter) : null),
                min_score: minScore,
            })
            setTaskId(r?.task_id)
        } catch (e) {
            setError(`启动榨干失败：${e.message || e}`)
            setRunning(false)
        }
    }

    // 【v5.34】全文档一键榨干：start=1、end=末尾（整本 txt 全榨，按情节分组入库）
    const handleExtractAll = async () => {
        if (!filepath) { setError('请选择要榨干的文档'); return }
        const ok = window.confirm('全文档一键榨干：将从第 1 章榨到整本末尾（几百章可能耗时十几小时）。\n确认继续？')
        if (!ok) return
        setStartChapter(1)
        setEndChapter('')
        await handleExtract({ start_chapter: 1, end_chapter: null })
    }

    // 【v5.33.7】用户确认后整情节入库：按 task_id + arc 去实体化存库（情节内全部章内容）
    const handleStore = async (arc) => {
        if (!taskId) { setError('无榨干任务（先点「榨干提取」）'); return }
        setError('')
        setStoring(prev => ({ ...prev, [arc]: true }))
        try {
            const r = await phStoreExtractTemplate(taskId, arc)
            setStatus(prev => {
                if (!prev) return prev
                const rep = (prev.result?.report || []).map(rr =>
                    rr.arc === arc ? { ...rr, template_id: r?.template?.id, template_name: r?.template?.name } : rr)
                return { ...prev, result: { ...(prev.result || {}), report: rep } }
            })
        } catch (e) {
            setError(`入库失败：${e.message || e}`)
        } finally {
            setStoring(prev => ({ ...prev, [arc]: false }))
        }
    }

    // 【v5.33.4】完整 l4 展示：每场景含环境 + 全部叶子（动作/对白/narration/心理/冲突/细节）
    const leafLabels = [
        ['actions', '动作'], ['dialogues', '对白'], ['narration', '原文叙述原句'],
        ['psychologies', '心理'], ['conflicts', '冲突'], ['details', '细节'],
    ]
    const renderScene = (sc) => {
        if (!sc) return null
        return (
            <div style={{ marginTop: 4, padding: 6, background: 'var(--bg-main)', borderRadius: 6 }}>
                <b>{sc.name || '场景'}</b>
                {sc.environment && <div style={{ marginTop: 2 }}>环境：{sc.environment}</div>}
                {leafLabels.map(([key, label]) => {
                    const items = Array.isArray(sc[key]) ? sc[key].filter(Boolean) : []
                    if (!items.length) return null
                    return (
                        <div key={key} style={{ marginTop: 2 }}>
                            <b>{label}（{items.length}）：</b>
                            {items.map((it, j) => <div key={j} style={{ marginLeft: 10 }}>· {it}</div>)}
                        </div>
                    )
                })}
            </div>
        )
    }
    const renderSkeleton = (sk) => {
        if (!sk) return null
        const l3 = sk.l3 || {}
        return (
            <div style={{ fontSize: 13 }}>
                {sk.l1 && <div><b>l1 极简：</b>{sk.l1}</div>}
                {sk.l2 && <div style={{ marginTop: 4 }}><b>l2 情节概要：</b>{sk.l2}</div>}
                {(l3.title || l3.core) && (
                    <div style={{ marginTop: 4 }}>
                        <b>l3 章核心：</b>
                        {l3.title && <span>{l3.title}｜</span>}
                        {l3.core}
                        {Array.isArray(l3.beats) && l3.beats.length > 0 && <div>拍：{l3.beats.join('；')}</div>}
                    </div>
                )}
                {Array.isArray(sk.l4) && sk.l4.length > 0 && (
                    <div style={{ marginTop: 6 }}>
                        <b>l4 场景分解（{sk.l4.length}）：</b>
                        {sk.l4.map((sc, i) => (
                            <div key={i} style={{ marginTop: 4 }}>
                                <b>{i + 1}.</b>
                                {renderScene(sc)}
                            </div>
                        ))}
                    </div>
                )}
            </div>
        )
    }

    const result = status?.result || {}
    const report = result.report || []

    return (
        <div>
            {/* 配置区：文档 + 章节范围 + 合格分 + 单按钮（风格/角色由系统自动提取） */}
            <div style={card}>
                <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>
                    单按钮榨干：选中文档 → 逐章提取压缩阶梯 → 重建校验 → 合格去实体化入库
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                    <select value={filepath} onChange={e => setFilepath(e.target.value)}
                        style={{ padding: 8, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13, flex: 1, minWidth: 240 }}>
                        <option value="">选择语料文档…</option>
                        {files.map(f => (
                            <option key={f.path || f.name} value={f.path || f.name}>{f.name || f.path}</option>
                        ))}
                    </select>
                    <input type="number" value={startChapter} min={1} onChange={e => setStartChapter(Number(e.target.value))}
                        placeholder="起始章" style={{ width: 70, padding: 8, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13 }} />
                    <input type="number" value={endChapter} min={1} onChange={e => setEndChapter(e.target.value)}
                        placeholder="结束章(空=末尾)" style={{ width: 120, padding: 8, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13 }} />
                    <input type="number" step={0.05} value={minScore} onChange={e => setMinScore(Number(e.target.value))}
                        placeholder="合格分" style={{ width: 80, padding: 8, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13 }} />
                </div>
                <button style={btn(running || !filepath, true)} disabled={running || !filepath} onClick={() => handleExtract()}>
                    {running ? '⏳ 榨干中…' : '榨干提取（逐章 → 骨架+正文 → 合格入库）'}
                </button>
                <button style={{ ...btn(running || !filepath, true), background: 'var(--cinnabar)', marginLeft: 8 }}
                    disabled={running || !filepath} onClick={handleExtractAll}
                    title="整本 txt 从第 1 章榨到末尾（按剧情分组，情节内全章达标可整情节入库）">
                    {running ? '⏳ 榨干中…' : '全文档一键榨干'}
                </button>
                <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--ink-sub)' }}>
                    每章约 2-4 分钟（build_ladder 提取 + verify_ladder 重建校验）
                </span>
                {error && <div style={{ marginTop: 8, padding: 8, borderRadius: 6, background: 'var(--cinnabar-wash)', color: 'var(--cinnabar-d)', fontSize: 13 }}>⚠ {error}</div>}
            </div>

            {/* 任务进度 */}
            {running && status?.status === 'running' && (
                <div style={card}>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>⏳ 榨干进行中…</div>
                    <div style={{ fontSize: 13, color: 'var(--ink-sub)' }}>
                        进度：第 {status?.progress?.chapter || 0}/{status?.progress?.total || '?'} 章
                        {status?.progress?.phase ? `（${status?.progress?.phase}）` : ''}
                    </div>
                    {(status?.progress?.messages || []).filter(Boolean).slice(-3).map((m, i) => (
                        <div key={i} style={{ fontSize: 12, color: 'var(--ink-sub)' }}>{m}</div>
                    ))}
                </div>
            )}
            {status?.status === 'failed' && (
                <div style={{ ...card, borderColor: 'var(--cinnabar-border)', background: 'var(--cinnabar-wash)' }}>
                    <b style={{ color: 'var(--cinnabar-d)' }}>榨干失败：</b>
                    <pre style={{ ...pre, color: 'var(--cinnabar-d)', fontSize: 12 }}>{status.error}</pre>
                </div>
            )}
            {status?.status === 'done' && (
                <div style={{ ...card, borderColor: 'var(--green)', background: 'var(--green-wash)' }}>
                    <b style={{ color: 'var(--green)' }}>
                        榨干完成：{result.qualified ?? 0}/{result.arc_count ?? report.length} 个剧情达标
                        （情节内全部章 ≥ 合格分才可整情节入库），{result.skipped?.length ?? 0} 章跳过
                    </b>
                    {(result.style || result.role_setting) && (
                        <div style={{ marginTop: 6, fontSize: 12, color: 'var(--ink-sub)' }}>
                            自动提取：风格「{result.style || '—'}」｜角色「{result.role_setting || '—'}」
                        </div>
                    )}
                </div>
            )}

            {/* 【v5.33.7】剧情分组：每情节一个模板（含情节内全部章骨架 + 正文） */}
            {report.length > 0 && (
                <div>
                    <div style={{ fontSize: 15, fontWeight: 700, margin: '10px 0 6px' }}>
                        榨干产物：剧情分组（每情节一个模板，点「整情节入库」含情节内全部章）
                    </div>
                    {report.map((arc, i) => (
                        <div key={i} style={{ ...card, borderLeft: arc.qualified || arc.template_id ? '3px solid var(--green)' : '3px solid var(--line-soft)' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                                <b style={{ fontSize: 14 }}>剧情 {arc.arc}：{arc.name}</b>
                                {arc.archetype && <span style={{ fontSize: 12, color: 'var(--cinnabar)' }}>原型：{arc.archetype}</span>}
                                <span style={{ fontSize: 12, color: 'var(--ink-sub)' }}>第 {arc.start_chapter}-{arc.end_chapter} 章</span>
                                <span style={badge(arc.qualified || arc.template_id)}>
                                    {arc.template_id ? `已入库（${arc.template_name || arc.name}）` : (arc.qualified ? '达标（待入库）' : '未合格')}
                                </span>
                                {arc.qualified && !arc.template_id && (
                                    <button style={{ ...btn(storing[arc.arc], true) }} disabled={storing[arc.arc]}
                                        onClick={() => handleStore(arc.arc)}>
                                        {storing[arc.arc] ? '入库中…' : '整情节入库'}
                                    </button>
                                )}
                            </div>
                            {(arc.l1 || arc.l2) && (
                                <div style={{ fontSize: 13, background: 'var(--bg-card-2)', padding: 8, borderRadius: 6, marginBottom: 6 }}>
                                    {arc.l1 && <div><b>情节 l1 极简：</b>{arc.l1}</div>}
                                    {arc.l2 && <div style={{ marginTop: 2 }}><b>情节 l2 概要：</b>{arc.l2}</div>}
                                </div>
                            )}
                            {arc.chapters?.map((ch, j) => (
                                <div key={j} style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--line)' }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                        <b style={{ fontSize: 13 }}>第 {ch.chapter_num ?? ch.chapter} 章</b>
                                        <span style={badge(ch.qualified)}>{ch.qualified ? '达标' : '未合格'}</span>
                                        <span style={{ fontSize: 12, color: 'var(--ink-sub)' }}>
                                            score {ch.score?.toFixed?.(3) ?? ch.score}｜正文 {ch.prose?.length ?? 0} 字
                                        </span>
                                    </div>
                                    <details>
                                        <summary style={{ fontSize: 13, color: 'var(--dai)', cursor: 'pointer', marginTop: 2 }}>压缩阶梯（骨架）</summary>
                                        <div style={{ background: 'var(--bg-card-2)', padding: 8, borderRadius: 6, marginTop: 4 }}>
                                            {renderSkeleton(ch.skeleton)}
                                        </div>
                                    </details>
                                    <details>
                                        <summary style={{ fontSize: 13, color: 'var(--green)', cursor: 'pointer', marginTop: 4 }}>重建正文（{ch.prose?.length ?? 0} 字）</summary>
                                        <pre style={{ ...pre, background: 'var(--bg-card-2)', padding: 8, borderRadius: 6, marginTop: 4 }}>{ch.prose}</pre>
                                    </details>
                                </div>
                            ))}
                        </div>
                    ))}
                </div>
            )}

        </div>
    )
}
