/**
 * 项目卡片：在项目库中展示单本书。
 *
 * Props:
 *  - project: { name, project_root, active, test_book }
 *  - onClick: () => void
 *  - onInitialize: (project) => void  (仅测试书)
 *  - onDelete: (project) => void      (永久删除)
 */

import Badge from './Badge.jsx'

export default function ProjectCard({ project, onClick, onInitialize, onDelete }) {
    const handleDelete = (e) => {
        e.stopPropagation()
        onDelete && onDelete(project)
    }
    if (project.incomplete) {
        return (
            <div className={`project-card incomplete`} style={{ opacity: 0.6 }}>
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
        <button
            type="button"
            className={`project-card ${project.active ? 'active' : ''}`}
            onClick={onClick}
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
        </button>
    )
}
