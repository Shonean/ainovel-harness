import { useCallback, useState } from 'react'
import { createProject } from '../api.js'

/**
 * 新建书弹窗：收集完整 init brief，调用 /api/project/create 创建目录并启动 init。
 *
 * Props:
 *  - open: boolean
 *  - onClose: () => void
 *  - onCreated: ({ taskId, projectRoot, name }) => void
 */

const EMPTY_BRIEF = {
    project: {
        title: '',
        genre: '',
        target_words: 0,
        target_chapters: 0,
        one_liner: '',
        core_conflict: '',
        target_reader: '',
        platform: '',
    },
    protagonist: {
        name: '',
        desire: '',
        flaw: '',
        archetype: '',
        structure: '单主角',
    },
    relationship: {
        heroine_config: '无女主',
        antagonist_tiers: '',
        antagonist_mirror: '',
    },
    golden_finger: {
        type: '',
        name: '',
        style: '',
        visibility: '',
        irreversible_cost: '',
        growth_rhythm: '',
    },
    world: {
        scale: '',
        factions: '',
        power_system_type: '',
        social_class: '',
        resource_distribution: '',
    },
    constraints: {
        anti_trope: '',
        hard_constraints: '',
        opening_hook: '',
    },
}

function Field({ label, value, onChange, placeholder, type = 'text' }) {
    return (
        <label className="field-label">
            {label}：
            <input
                className="field-input"
                type={type}
                value={value}
                placeholder={placeholder}
                onChange={e => onChange(e.target.value)}
            />
        </label>
    )
}

function Section({ title, children }) {
    return (
        <div className="create-book-section">
            <div className="create-book-section-title">{title}</div>
            <div className="create-book-section-body">{children}</div>
        </div>
    )
}

export default function CreateBookModal({ open, onClose, onCreated }) {
    const [name, setName] = useState('')
    const [brief, setBrief] = useState(() => JSON.parse(JSON.stringify(EMPTY_BRIEF)))
    const [referenceTextPath, setReferenceTextPath] = useState('')
    const [submitting, setSubmitting] = useState(false)
    const [err, setErr] = useState('')

    const setGroup = useCallback((group, key, val) => {
        setBrief(prev => ({ ...prev, [group]: { ...prev[group], [key]: val } }))
    }, [])

    const buildBrief = useCallback(() => {
        const out = JSON.parse(JSON.stringify(brief))
        out.project.title = out.project.title || name
        out.project.target_words = Number(out.project.target_words) || 0
        out.project.target_chapters = Number(out.project.target_chapters) || 0
        out.constraints.hard_constraints = out.constraints.hard_constraints
            .split('\n').map(s => s.trim()).filter(Boolean)
        return out
    }, [brief, name])

    const handleSubmit = useCallback(async () => {
        setErr('')
        const folderName = name.trim()
        if (!folderName) {
            setErr('请输入文件夹名/书名')
            return
        }
        if (!brief.project.genre.trim()) {
            setErr('请输入题材')
            return
        }

        setSubmitting(true)
        try {
            const res = await createProject(
                folderName,
                buildBrief(),
                referenceTextPath.trim() || null,
            )
            onCreated?.({
                taskId: res.task_id,
                projectRoot: res.project_root,
                name: res.name,
            })
        } catch (e) {
            setErr(e.message || '创建失败')
        } finally {
            setSubmitting(false)
        }
    }, [name, brief, referenceTextPath, buildBrief, onCreated])

    if (!open) return null

    return (
        <div className="askuser-overlay" onClick={onClose}>
            <div className="askuser-modal create-book-modal" onClick={e => e.stopPropagation()}>
                <div className="askuser-question">新建书</div>

                <div className="create-book-form">
                    <Section title="① 故事核与商业定位">
                        <div className="create-book-section-body">
                            <Field label="文件夹名/书名" value={name} onChange={setName} placeholder="我的新书" />
                            <Field label="显示书名（可选）" value={brief.project.title} onChange={v => setGroup('project', 'title', v)} placeholder="留空同文件夹名" />
                            <Field label="题材" value={brief.project.genre} onChange={v => setGroup('project', 'genre', v)} placeholder="玄幻+修仙" />
                            <Field label="目标字数" type="number" value={brief.project.target_words} onChange={v => setGroup('project', 'target_words', v)} placeholder="2000000" />
                            <Field label="目标章数" type="number" value={brief.project.target_chapters} onChange={v => setGroup('project', 'target_chapters', v)} placeholder="600" />
                            <Field label="一句话故事" value={brief.project.one_liner} onChange={v => setGroup('project', 'one_liner', v)} />
                            <Field label="核心冲突" value={brief.project.core_conflict} onChange={v => setGroup('project', 'core_conflict', v)} />
                            <Field label="目标读者" value={brief.project.target_reader} onChange={v => setGroup('project', 'target_reader', v)} />
                            <Field label="平台" value={brief.project.platform} onChange={v => setGroup('project', 'platform', v)} placeholder="起点/番茄" />
                        </div>
                    </Section>

                    <Section title="② 角色骨架与关系">
                        <div className="create-book-section-body">
                            <Field label="主角姓名" value={brief.protagonist.name} onChange={v => setGroup('protagonist', 'name', v)} />
                            <Field label="主角欲望" value={brief.protagonist.desire} onChange={v => setGroup('protagonist', 'desire', v)} />
                            <Field label="主角缺陷" value={brief.protagonist.flaw} onChange={v => setGroup('protagonist', 'flaw', v)} />
                            <Field label="主角原型" value={brief.protagonist.archetype} onChange={v => setGroup('protagonist', 'archetype', v)} placeholder="成长型/复仇型" />
                            <Field label="感情线配置" value={brief.relationship.heroine_config} onChange={v => setGroup('relationship', 'heroine_config', v)} placeholder="无女主/单女主/多女主" />
                            <Field label="反派分层" value={brief.relationship.antagonist_tiers} onChange={v => setGroup('relationship', 'antagonist_tiers', v)} placeholder="小/中/大" />
                            <Field label="反派镜像" value={brief.relationship.antagonist_mirror} onChange={v => setGroup('relationship', 'antagonist_mirror', v)} />
                        </div>
                    </Section>

                    <Section title="③ 金手指">
                        <div className="create-book-section-body">
                            <Field label="类型" value={brief.golden_finger.type} onChange={v => setGroup('golden_finger', 'type', v)} placeholder="系统/传承/重生/无" />
                            <Field label="名称" value={brief.golden_finger.name} onChange={v => setGroup('golden_finger', 'name', v)} />
                            <Field label="风格" value={brief.golden_finger.style} onChange={v => setGroup('golden_finger', 'style', v)} placeholder="硬核/诙谐/黑暗" />
                            <Field label="可见度" value={brief.golden_finger.visibility} onChange={v => setGroup('golden_finger', 'visibility', v)} />
                            <Field label="不可逆代价" value={brief.golden_finger.irreversible_cost} onChange={v => setGroup('golden_finger', 'irreversible_cost', v)} />
                            <Field label="成长节奏" value={brief.golden_finger.growth_rhythm} onChange={v => setGroup('golden_finger', 'growth_rhythm', v)} placeholder="慢热/中速/快节奏" />
                        </div>
                    </Section>

                    <Section title="④ 世界观与力量规则">
                        <div className="create-book-section-body">
                            <Field label="世界规模" value={brief.world.scale} onChange={v => setGroup('world', 'scale', v)} placeholder="单城/大陆/多界" />
                            <Field label="力量体系类型" value={brief.world.power_system_type} onChange={v => setGroup('world', 'power_system_type', v)} />
                            <Field label="势力格局" value={brief.world.factions} onChange={v => setGroup('world', 'factions', v)} />
                            <Field label="社会阶层" value={brief.world.social_class} onChange={v => setGroup('world', 'social_class', v)} />
                            <Field label="资源分配" value={brief.world.resource_distribution} onChange={v => setGroup('world', 'resource_distribution', v)} />
                        </div>
                    </Section>

                    <Section title="⑤ 创意约束包">
                        <div className="create-book-section-body">
                            <Field label="反套路规则" value={brief.constraints.anti_trope} onChange={v => setGroup('constraints', 'anti_trope', v)} />
                            <Field label="开篇钩子" value={brief.constraints.opening_hook} onChange={v => setGroup('constraints', 'opening_hook', v)} />
                            <label className="field-label" style={{ width: '100%' }}>
                                硬约束（每行一条）：
                                <textarea
                                    className="field-input"
                                    rows={3}
                                    value={brief.constraints.hard_constraints}
                                    placeholder={'主角不得开挂碾压\n每卷必须有一次重大挫折'}
                                    onChange={e => setGroup('constraints', 'hard_constraints', e.target.value)}
                                />
                            </label>
                        </div>
                    </Section>

                    <Section title="⑥ 灵感来源（可选）">
                        <div className="create-book-section-body">
                            <Field
                                label="参考书文本路径"
                                value={referenceTextPath}
                                onChange={setReferenceTextPath}
                                placeholder="留空=纯原创"
                            />
                        </div>
                    </Section>
                </div>

                {err && <div className="askuser-error">{err}</div>}

                <div className="create-book-actions">
                    <button className="btn" onClick={onClose} disabled={submitting}>取消</button>
                    <button className="btn btn-green" onClick={handleSubmit} disabled={submitting}>
                        {submitting ? '创建中…' : '创建并初始化'}
                    </button>
                </div>
            </div>
        </div>
    )
}
