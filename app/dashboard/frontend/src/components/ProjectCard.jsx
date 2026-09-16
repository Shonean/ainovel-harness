/**
 * 项目卡片：在项目库中展示单本书。
 *
 * Props:
 *  - project: { name, project_root, active, test_book, book_mode, adapt_packs?, adapt_ok?, drama_videos? }
 *  - onClick: () => void
 *  - onInitialize: (project) => void  (仅测试书)
 *  - onDelete: (project) => void      (永久删除)
 *  - onAdapt: (project, kind: 'hub'|'drama'|'game') => void  (改编出口行点击；不传则整行只读)
 *
 * 根节点用 div role="button"：改编出口行内含可点 chip，button 内不能再嵌按钮。
 */

import Badge from './Badge.jsx'

function AdaptRow({ project, onAdapt }) {
    if (project.adapt_packs == null) return null
    const items = []
    if (project.adapt_packs > 0) {
        items.push({
            key: 'drama', kind: 'drama',
            title: '漫剧工作台（书级改编页 → 情节树 → 工作台）',
            node: <span className={'chipx' + ((project.drama_videos || 0) > 0 ? ' on' : '')}>▶ 漫剧{project.drama_videos > 0 ? ` · ${project.drama_videos} 集` : ''}</span>,
        })
        items.push({
            key: 'pack', kind: 'hub',
            title: '改编中心（跨书 Pack / 成片 / 发布）',
            node: <span className="chipx on">pack {project.adapt_packs}</span>,
        })
        items.push({
            key: 'game', kind: null,
            title: '游戏线暂停（隐藏保留，后续期恢复）',
            node: <span className="chipx dim">游戏 · 暂停</span>,
        })
    } else {
        items.push({
            key: 'none', kind: 'drama',
            title: '先推进情节到 l4 再改编',
            node: <span className="chipx dim">无可打包弧 · 先推进 l4</span>,
        })
    }
    return (
        <div className="pc-adapt">
            <span className="pc-adapt-label">改编</span>
            {items.map(it => (
                <span key={it.key} className="pc-adapt-item"
                    onClick={onAdapt && it.kind ? (e) => { e.stopPropagation(); onAdapt(project, it.kind) } : undefined}>
                    <span title={it.title}>{it.node}</span>
                </span>
            ))}
        </div>
    )
}

export default function ProjectCard({ project, onClick, onInitialize, onDelete, onAdapt }) {
    const handleDelete = (e) => {
        e.stopPropagation()
        onDelete && onDelete(project)
    }
    const handleKey = (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onClick && onClick()
        }
    }
    if (project.incomplete) {
        return (
            <div className={`project-card incomplete`} style={{ opacity: 0.6 }} role="button" tabIndex={0}>
                <div className="project-card-cover">
                    <span className="project-card-icon">⚠</span>
                    {onDelete && (
                        <button
                            className="project-card-delete"
                            onClick={handleDelete}
                            title="删除这本书"
                        >✕</button>
                    )}
                </div>
                <div className="project-card-body">
                    <div className="project-card-name">{project.name}</div>
                    <div className="project-card-meta">
                        <Badge tone="red">初始化失败</Badge>
                    </div>
                </div>
              </div>
        )
    }
    const chs = project.chapters ?? 0
    const arcs = project.arcs ?? 0
    const score = project.avg_score
    const polluted = project.polluted ?? 0
    const words = project.words ?? 0
    return (
        <div
            className={`project-card ${project.active ? 'active' : ''}`}
            role="button"
            tabIndex={0}
            onClick={onClick}
            onKeyDown={handleKey}
        >
            <div className="project-card-cover">
                {onDelete && (
                    <button
                        className="project-card-delete"
                        onClick={handleDelete}
                        title="删除这本书"
                    >✕</button>
                )}
            </div>
            <div className="project-card-body">
                <div className="project-card-name">{project.name}</div>
                <div className="project-card-meta">
                    {project.genre && <Badge tone="neutral">{project.genre}</Badge>}
                    {project.book_mode === 'mass'
                        ? <Badge tone="amber">量产</Badge>
                        : <Badge tone="cyan">精品</Badge>}
                    <Badge tone="purple">创作中</Badge>
                    {polluted > 0 && <Badge tone="red">⚠ 污染 {polluted}</Badge>}
                </div>
                {/* 统计行：情节 / 章 / 评分 / 字数 */}
                <div className="pc-stats">
                    {chs > 0 || arcs > 0 ? (
                        <>
                            <span>{arcs} 情节</span>
                            <span className="pc-dot">·</span>
                            <span>{chs} 章</span>
                            <span className="pc-dot">·</span>
                            <span className={score != null ? 'pc-score' : ''}>{score != null ? score.toFixed(3) : '—'}</span>
                            {words > 0 && <><span className="pc-dot">·</span><span>{(words / 10000).toFixed(1)} 万字</span></>}
                        </>
                    ) : (
                        <span className="pc-empty">空书 · 待创作</span>
                    )}
                </div>
                {/* 改编出口行（T35）：pack / 漫剧 / 游戏 直达 */}
                <AdaptRow project={project} onAdapt={onAdapt} />
                {onInitialize && (
                    <button
                        className="btn btn-amber btn-small"
                        style={{ marginTop: 8, width: '100%' }}
                        onClick={(e) => { e.stopPropagation(); onInitialize(project); }}
                        title="清空运行时数据，保留设定"
                    >
                        初始化
                    </button>
                )}
            </div>
        </div>
    )
}
