import { useCallback } from 'react'

/**
 * 基本设定表单（测试书可编辑字段源，6 组 ~16 字段）。
 *
 * 按 prompt-harness 方法论设计（非正式书老表单）：
 * ① 书与基调 ② 主角与人物 ③ 世界与设定 ④ 核心剧情骨架 ⑤ 关键道具 ⑥ 创意约束。
 * 每字段映射到 AI 创作链路产出（elements / style / 设定集；硬约束复用 l1-l5 不变prompt）。
 *
 * Props:
 *  - settings: object（16 字段结构，见 ai_creation._empty_basic_settings）
 *  - onChange: (nextSettings: object) => void
 */
function Field({ label, value, onChange, placeholder, rows }) {
    const style = {
        width: '100%', boxSizing: 'border-box', padding: 8, borderRadius: 6,
        border: '1px solid var(--line-soft)', fontSize: 13, marginTop: 4,
    }
    return (
        <label className="field-label" style={{ width: '100%' }}>
            {label}：
            {rows ? (
                <textarea className="field-input" style={{ ...style, minHeight: rows * 18 + 16 }} rows={rows}
                    value={value || ''} placeholder={placeholder}
                    onChange={e => onChange(e.target.value)} />
            ) : (
                <input className="field-input" style={style} value={value || ''} placeholder={placeholder}
                    onChange={e => onChange(e.target.value)} />
            )}
        </label>
    )
}

function Section({ title, hint, children }) {
    return (
        <div className="create-book-section">
            <div className="create-book-section-title">
                {title}
                {hint && <span style={{ fontSize: 12, color: 'var(--ink-sub)', marginLeft: 8, fontWeight: 400 }}>{hint}</span>}
            </div>
            <div className="create-book-section-body">{children}</div>
        </div>
    )
}

const rowInputStyle = {
    padding: 6, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13, boxSizing: 'border-box',
}

export default function BasicSettingsForm({ settings, onChange }) {
    const s = settings || {}
    const p = s.protagonist || {}
    const ps = s.power_system || {}

    const set = useCallback((path, value) => {
        const next = JSON.parse(JSON.stringify(s))
        let obj = next
        for (let i = 0; i < path.length - 1; i++) {
            if (!obj[path[i]] || typeof obj[path[i]] !== 'object') obj[path[i]] = {}
            obj = obj[path[i]]
        }
        obj[path[path.length - 1]] = value
        onChange(next)
    }, [s, onChange])

    const setListItem = useCallback((key, idx, field, value) => {
        const next = JSON.parse(JSON.stringify(s))
        if (!Array.isArray(next[key])) next[key] = []
        if (!next[key][idx]) next[key][idx] = {}
        next[key][idx][field] = value
        onChange(next)
    }, [s, onChange])

    const addListItem = useCallback((key) => {
        const next = JSON.parse(JSON.stringify(s))
        if (!Array.isArray(next[key])) next[key] = []
        next[key].push({})
        onChange(next)
    }, [s, onChange])

    const delListItem = useCallback((key, idx) => {
        const next = JSON.parse(JSON.stringify(s))
        next[key] = (next[key] || []).filter((_, i) => i !== idx)
        onChange(next)
    }, [s, onChange])

    // 通用「名称-一句话」清单行（locations / items）
    const renderNameDescList = (key, nameLabel) => (
        <div>
            {(s[key] || []).map((item, i) => (
                <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 4 }}>
                    <input style={{ ...rowInputStyle, width: 140 }} value={item.name || ''}
                        placeholder={nameLabel}
                        onChange={e => setListItem(key, i, 'name', e.target.value)} />
                    <input style={{ ...rowInputStyle, flex: 1 }} value={item.desc || ''}
                        placeholder="一句话说明"
                        onChange={e => setListItem(key, i, 'desc', e.target.value)} />
                    <button type="button" style={{ border: '1px solid var(--line-soft)', borderRadius: 6, background: 'var(--paper-raised)', cursor: 'pointer' }}
                        onClick={() => delListItem(key, i)}>✕</button>
                </div>
            ))}
            <button type="button" style={{ padding: '3px 10px', borderRadius: 6, border: '1px solid var(--line-soft)', background: 'var(--paper-raised)', fontSize: 12, cursor: 'pointer' }}
                onClick={() => addListItem(key)}>+ 添加{nameLabel}</button>
        </div>
    )

    return (
        <div className="book-brief-form">
            <Section title="① 书与基调" hint="→ genre 模板；风格基调由系统按类型模板自动提取">
                <Field label="书名/文件夹名" value={s.name} onChange={v => set(['name'], v)} placeholder="我的新书" />
                <Field label="类型" value={s.genre} onChange={v => set(['genre'], v)} placeholder="玄幻+修仙 / 都市 / 悬疑" />
            </Section>

            <Section title="② 主角与人物" hint="→ 角色元素（隔离单元）">
                <Field label="主角姓名" value={p.name} onChange={v => set(['protagonist', 'name'], v)} placeholder="主角" />
                <div style={{ marginTop: 6 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-sub)', marginBottom: 3 }}>出场人物（每行或每项：名字/身份/与主角关系）</div>
                    {(s.cast || []).map((c, i) => (
                        <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 4 }}>
                            <input style={{ ...rowInputStyle, width: 120 }} value={c.name || ''} placeholder="名字"
                                onChange={e => setListItem('cast', i, 'name', e.target.value)} />
                            <input style={{ ...rowInputStyle, flex: 1 }} value={c.role || ''} placeholder="身份"
                                onChange={e => setListItem('cast', i, 'role', e.target.value)} />
                            <input style={{ ...rowInputStyle, flex: 1 }} value={c.relation || ''} placeholder="与主角关系"
                                onChange={e => setListItem('cast', i, 'relation', e.target.value)} />
                            <button type="button" style={{ border: '1px solid var(--line-soft)', borderRadius: 6, background: 'var(--paper-raised)', cursor: 'pointer' }}
                                onClick={() => delListItem('cast', i)}>✕</button>
                        </div>
                    ))}
                    <button type="button" style={{ padding: '3px 10px', borderRadius: 6, border: '1px solid var(--line-soft)', background: 'var(--paper-raised)', fontSize: 12, cursor: 'pointer' }}
                        onClick={() => addListItem('cast')}>+ 添加人物</button>
                </div>
            </Section>

            <Section title="③ 世界与设定" hint="→ 设定元素（隔离单元）">
                <Field label="时间背景" value={s.time_setting} onChange={v => set(['time_setting'], v)} placeholder="时代/年代/世界观一句话，如：王朝末年 / 近未来 2047" />
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <div style={{ flex: 1, minWidth: 140 }}>
                        <Field label="力量/金手指·类型" value={ps.type} onChange={v => set(['power_system', 'type'], v)} placeholder="系统/传承/低武/异能…" />
                    </div>
                    <div style={{ flex: 1, minWidth: 140 }}>
                        <Field label="力量/金手指·名称" value={ps.name} onChange={v => set(['power_system', 'name'], v)} placeholder="如：百官楷模系统" />
                    </div>
                    <div style={{ flex: 1, minWidth: 180 }}>
                        <Field label="力量/金手指·规则" value={ps.rules} onChange={v => set(['power_system', 'rules'], v)} placeholder="一句话规则/代价" />
                    </div>
                </div>
                <div style={{ marginTop: 6 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-sub)', marginBottom: 3 }}>关键地点/势力（名称/一句话）</div>
                    {renderNameDescList('locations', '地点/势力')}
                </div>
            </Section>

            <Section title="④ 核心剧情骨架" hint="→ 设定集核心剧情 + 意图评分基准">
                <Field label="核心冲突" value={s.conflict} onChange={v => set(['conflict'], v)} placeholder="推动全书的核心矛盾" />
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <div style={{ flex: 1, minWidth: 180 }}>
                        <Field label="目标" value={s.goal} onChange={v => set(['goal'], v)} placeholder="主角最终要达成什么" />
                    </div>
                    <div style={{ flex: 1, minWidth: 180 }}>
                        <Field label="结局悬念" value={s.ending} onChange={v => set(['ending'], v)} placeholder="书级走向/留的钩子" />
                    </div>
                </div>
                <Field label="转折（可选）" value={s.twist} onChange={v => set(['twist'], v)} placeholder="关键的意外/反转" />
            </Section>

            <Section title="⑤ 关键道具" hint="→ 物品元素（隔离单元）">
                {renderNameDescList('items', '道具')}
            </Section>

            <Section title="⑥ 创意约束" hint="→ 反套路/钩子注入设定；硬约束复用 l1-l5 不变prompt">
                <Field label="反套路规则" value={s.anti_trope} onChange={v => set(['anti_trope'], v)} placeholder="反套路设计，如：主角金手指有代价，不无脑碾压" />
                <Field label="开篇钩子" value={s.opening_hook} onChange={v => set(['opening_hook'], v)} placeholder="第一段/第一章的钩子" />
                <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginTop: 4 }}>
                    硬约束：复用 l1-l5 不变prompt（fixed_prompts.json 运行时可编辑，每级自动注入），无需手填。
                </div>
            </Section>
        </div>
    )
}
