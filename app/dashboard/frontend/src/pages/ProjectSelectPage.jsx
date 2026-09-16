import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchProjects, switchProject, initializeProject, deleteProject, fetchAdaptationOverview } from '../api.js'
import ProjectCard from '../components/ProjectCard.jsx'
import PromptModal, { askText } from '../components/PromptModal.jsx'
import ModePickerModal from '../components/ModePickerModal.jsx'

const SKELETON_COUNT = 4

function ProjectCardSkeleton() {
    return (
        <div className="project-card project-card-loading">
            <div className="project-card-cover">
                <span className="project-card-icon">◻</span>
            </div>
            <div className="project-card-body">
                <div className="project-card-name">&nbsp;</div>
                <div className="project-card-meta">&nbsp;</div>
            </div>
        </div>
    )
}

function EmptyState({ onCreate }) {
    return (
        <div className="project-empty-state">
            <h2>还没有书</h2>
            <p>开始创作你的第一本小说吧！</p>
            <div className="project-empty-actions">
                <button className="btn btn-blue" onClick={onCreate}>
                    ＋ 新建书
                </button>
                <a href="#/api-presets" className="btn btn-purple">
                    API 预设
                </a>
                <a href="#/prompt-harness" className="btn btn-purple">
                    Prompt Harness
                </a>
                <a href="#/ai-creation" className="btn btn-purple">
                    AI 创作
                </a>
            </div>
        </div>
    )
}

export default function ProjectSelectPage() {
    const navigate = useNavigate()
    const [projects, setProjects] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [switchingRoot, setSwitchingRoot] = useState(null)
    const [modePickerOpen, setModePickerOpen] = useState(false)
    const [adapt, setAdapt] = useState(null)

    useEffect(() => {
        // 改编中心总览（容错：失败不阻断书架）
        fetchAdaptationOverview()
            .then(r => setAdapt(r || null))
            .catch(() => setAdapt(null))
    }, [])

    const load = useCallback(() => {
        setLoading(true)
        setError(null)
        fetchProjects()
            .then(r => setProjects(r.projects || []))
            .catch(e => {
                setError(e.message || '加载项目列表失败')
                setProjects([])
            })
            .finally(() => setLoading(false))
    }, [])

    useEffect(() => {
        load()
    }, [load])

    const handleSelect = useCallback(async (project) => {
        if (switchingRoot) return
        setSwitchingRoot(project.project_root)
        try {
            await switchProject(project.project_root)
            // 量产书直接进量产工作台（路线图/生产线）；精品书进完整工作台
            if (project.book_mode === 'mass') {
                navigate('/mass', { replace: true })
            } else {
                navigate('/ai-creation', { replace: true })
            }
        } catch (e) {
            alert(`切换项目失败: ${e.message}`)
            setSwitchingRoot(null)
        }
    }, [navigate, switchingRoot])

    const handleAdapt = useCallback(async (project, kind) => {
        if (switchingRoot) return
        try { await switchProject(project.project_root) } catch { /* 切换失败也继续导航 */ }
        if (kind === 'drama') navigate('/ai-creation?tab=adapt')
        else navigate('/adaptation')
    }, [navigate, switchingRoot])

    const handleCreate = useCallback(() => setModePickerOpen(true), [])

    const handlePickMode = useCallback((mode) => {
        setModePickerOpen(false)
        navigate(`/create-book?mode=${mode === 'mass' ? 'mass' : 'premium'}`)
    }, [navigate])

    const handleInitialize = useCallback(async (project) => {
        const confirmed = window.confirm(
            `确定初始化《${project.name}》？\n\n` +
            `将清空所有运行时数据（正文、大纲、审查报告、AI生成内容等），\n` +
            `只保留设定集和项目配置。\n\n此操作不可恢复！`
        )
        if (!confirmed) return
        const nameConfirm = await askText(`请输入书名「${project.name}」以确认初始化：`)
        if (nameConfirm !== project.name) {
            if (nameConfirm !== null) alert('书名不匹配，已取消初始化。')
            return
        }
        try {
            await initializeProject(project.project_root)
            load()
        } catch (e) {
            alert(`初始化失败: ${e.message}`)
        }
    }, [load])

    const handleDelete = useCallback(async (project) => {
        const confirmed = window.confirm(
            `确定删除《${project.name}》？\n\n` +
            `将永久删除这本书的所有数据：\n` +
            `正文、大纲、设定、AI 生成内容、审查报告等全部文件。\n\n` +
            `此操作不可恢复！`
        )
        if (!confirmed) return
        const nameConfirm = await askText(`请输入书名「${project.name}」以确认删除：`)
        if (nameConfirm !== project.name) {
            if (nameConfirm !== null) alert('书名不匹配，已取消删除。')
            return
        }
        try {
            await deleteProject(project.project_root)
            load()
        } catch (e) {
            alert(`删除失败: ${e.message}`)
        }
    }, [load])

    return (
        <div className="project-select-page">
            <header className="project-select-header">
                <div className="project-select-brand">
                    <div className="project-select-logo">AInovel <span style={{ color: 'var(--cinnabar)' }}>·</span> Harness</div>
                    <div className="project-select-subtitle">选择一本书，开始创作</div>
                </div>
                <div className="project-select-actions">
                    <button className="btn btn-blue" onClick={handleCreate}>＋ 新建书</button>
                    <button className="btn btn-purple" onClick={() => navigate('/ai-creation')}>AI 创作</button>
                    <button className="btn btn-purple" onClick={() => navigate('/adaptation')}>改编中心</button>
                    <button className="btn btn-purple" onClick={() => navigate('/prompt-harness')}>Prompt Harness</button>
                    <button className="btn btn-purple" onClick={() => navigate('/api-presets')}>API 预设</button>
                </div>
            </header>

            <main className="project-select-main">
                {!loading && !error && projects.length > 0 && (
                    <div className="volhead">
                        <div className="kicker">项目库 · 卷一</div>
                        <div className="title">全部藏书</div>
                        <div className="dek">书数据不在 git · 读 小说系统/&lt;书名&gt;/ 下运行时产物</div>
                        <div className="head-rule"><span className="folio">{projects.length} 卷 · 全部藏书</span></div>
                    </div>
                )}
                {loading ? (
                    <div className="project-card-grid">
                        {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
                            <ProjectCardSkeleton key={i} />
                        ))}
                    </div>
                ) : error ? (
                    <div className="project-select-error">
                        <p>{error}</p>
                        <button className="btn" onClick={load}>重试</button>
                    </div>
                ) : projects.length === 0 ? (
                    <EmptyState onCreate={handleCreate} />
                ) : (
                    <>
                        {/* 统计条：书/总章/总字/平均意图（前端求和） */}
                        {(() => {
                            const valid = projects.filter(p => !p.incomplete)
                            const totCh = valid.reduce((n, p) => n + (p.chapters || 0), 0)
                            const totW = valid.reduce((n, p) => n + (p.words || 0), 0)
                            const scored = valid.filter(p => p.avg_score != null)
                            const avg = scored.length
                                ? (scored.reduce((s, p) => s + (p.avg_score || 0), 0) / scored.length).toFixed(3)
                                : '—'
                            return (
                                <div className="stat-grid-ed lib-statgrid" style={{ marginBottom: 36 }}>
                                    <div className="stat-ed"><div className="sl">藏书</div><div className="sv">{projects.length}</div><div className="ss">卷</div></div>
                                    <div className="stat-ed"><div className="sl">总章数</div><div className="sv">{totCh}</div><div className="ss">已落盘</div></div>
                                    <div className="stat-ed"><div className="sl">总字数</div><div className="sv">{(totW / 10000).toFixed(1)}万</div><div className="ss">估算</div></div>
                                    <div className="stat-ed"><div className="sl">平均意图</div><div className="sv acc">{avg}</div><div className="ss">{scored.length} 本有评分</div></div>
                                    {/* 改编产能（T35：数据源 /api/adaptation/overview，失败时隐藏） */}
                                    {adapt?.totals && (
                                        <>
                                            <div className="stat-ed"><div className="sl">改编 Pack</div><div className="sv">{adapt.totals.packs}</div><div className="ss">{adapt.totals.ok} 可用</div></div>
                                            <div className="stat-ed"><div className="sl">成品</div><div className="sv">{adapt.totals.drama_videos} / 0</div><div className="ss">漫剧 / 游戏</div></div>
                                        </>
                                    )}
                                </div>
                            )
                        })()}

                        <div className="sec-h" style={{ margin: '40px 0 14px' }}>
                            <span className="no">壹</span>
                            <span className="t">藏书</span>
                            <span className="hint">{projects.filter(p => !p.incomplete).length} 卷 · 全部藏书</span>
                        </div>
                        <div className="project-card-grid">
                            {projects.map(p => (
                                <ProjectCard
                                    key={p.project_root}
                                    project={p}
                                    onClick={() => handleSelect(p)}
                                    onInitialize={handleInitialize}
                                    onDelete={handleDelete}
                                    onAdapt={handleAdapt}
                                />
                            ))}
                        </div>

                        {/* 最近创作 */}
                        {(() => {
                            const recent = projects
                                .filter(p => !p.incomplete && p.updated)
                                .sort((a, b) => (b.updated || 0) - (a.updated || 0))
                                .slice(0, 4)
                            if (recent.length === 0) return null
                            const fmt = ts => {
                                const d = new Date(ts)
                                return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
                            }
                            return (
                                <>
                                    <div className="sec-h" style={{ margin: '40px 0 14px' }}>
                                        <span className="no">贰</span>
                                        <span className="t">最近创作</span>
                                        <span className="hint">按最近修改排序</span>
                                    </div>
                                    <div className="arcrows" style={{ maxWidth: 1480, margin: '0 auto' }}>
                                        {recent.map(p => (
                                            <div key={p.project_root} className="arcrow" onClick={() => handleSelect(p)}>
                                                <span className="ano">笔</span>
                                                <span className="aname">{p.name}</span>
                                                <span className="elems">
                                                    <span className="elem">{p.genre || '未设定题材'}</span>
                                                    {(p.chapters || 0) > 0 && <span className="elem">{p.arcs} 情节 · {p.chapters} 章</span>}
                                                </span>
                                                <span className="meta">{fmt(p.updated)}</span>
                                            </div>
                                        ))}
                                    </div>
                                </>
                            )
                        })()}
                    </>
                )}

            </main>

            <footer className="project-select-footer">
                <span>AInovel Harness v6.7.2</span>
            </footer>
            {/* Electron 无 window.prompt：文本输入弹窗统一走 PromptModal */}
            <PromptModal />
            <ModePickerModal
                open={modePickerOpen}
                onPick={handlePickMode}
                onClose={() => setModePickerOpen(false)}
            />
        </div>
    )
}
