import { useState } from 'react'

/**
 * 连接 · 主系统（炼工台右栏 · v7.8）
 * 可视化 prompt-harness 训练产出 → 主系统消费的整条链路：
 *   ① 五级不变 prompt 注入链  ② 链路图  ③ 模板消费  ④ 最近命中记录
 * 数据源：GET /connections/overview（PromptHarnessPage 一次性拉取）
 */
const K = {
    l1: { bg: '#E3D9C4', fg: '#6B4F1D' },
    l2: { bg: '#D7CDD8', fg: '#5A3E63' },
    l3: { bg: '#CDE0D9', fg: '#2F5E4A' },
    l4: { bg: '#D4E0E9', fg: '#2D4E6B' },
    l5: { bg: '#F0D6D0', fg: '#7A2E1D' },
}
const tStyle = { color: 'var(--ink-mute)', fontSize: 11, lineHeight: 1.5 }
const sec = (color) => ({
    fontSize: 10.5, fontWeight: 700, letterSpacing: '.14em', textTransform: 'uppercase',
    color: color || 'var(--ink-mute)', margin: '2px 0 5px',
})

function InvChain({ invariants, baseline }) {
    const [open, setOpen] = useState({})
    const rows = [...(invariants || [])]
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {rows.map(iv => {
                const kc = K[iv.level] || K.l1
                const o = !!open[iv.level]
                return (
                    <div key={iv.level} style={{ display: 'flex', alignItems: 'flex-start', gap: 7, padding: '5px 7px', border: '1px solid var(--line-faint)', borderRadius: 6, background: 'var(--paper)' }}>
                        <div style={{ flexShrink: 0, width: 88 }}>
                            <div style={{ fontWeight: 700, fontSize: 11.5 }}>
                                <span style={{ display: 'inline-block', minWidth: 22, textAlign: 'center', borderRadius: 4, padding: '1px 4px', fontSize: 10.5, background: kc.bg, color: kc.fg }}>{iv.level}</span>
                                {' '}{iv.name || ''}
                            </div>
                            <div style={{ fontSize: 10, color: 'var(--ink-mute)' }}>{iv.injection}</div>
                        </div>
                        <div style={{ flex: 1, fontSize: 11, color: 'var(--ink-sub)', lineHeight: 1.5 }}>
                            {o ? iv.text : (iv.text.slice(0, 52) + (iv.text.length > 52 ? '…' : ''))}
                            {iv.text.length > 52 && (
                                <span style={{ color: 'var(--dai)', cursor: 'pointer', marginLeft: 4 }} onClick={() => setOpen(p => ({ ...p, [iv.level]: !p[iv.level] }))}>
                                    {o ? '△ 收起' : '▾ 展开'}
                                </span>
                            )}
                        </div>
                        <div style={{ flexShrink: 0, fontSize: 10.5, color: 'var(--ink-mute)' }}>{iv.length} 字</div>
                    </div>
                )
            })}
            {baseline && (
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 7, padding: '5px 7px', border: '1px dashed var(--cin-border)', borderRadius: 6, background: 'var(--cin-wash)' }}>
                    <div style={{ flexShrink: 0, width: 88 }}>
                        <div style={{ fontWeight: 700, fontSize: 11.5, color: 'var(--cin-d)' }}>防线</div>
                        <div style={{ fontSize: 10, color: 'var(--ink-mute)' }}>baseline_guard</div>
                    </div>
                    <div style={{ flex: 1, fontSize: 11, color: 'var(--ink-sub)', lineHeight: 1.5 }}>{baseline.text.slice(0, 60)}{baseline.text.length > 60 ? '…' : ''}</div>
                    <div style={{ flexShrink: 0, fontSize: 10.5, color: 'var(--ink-mute)' }}>{baseline.length} 字</div>
                </div>
            )}
        </div>
    )
}

function Chain() {
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {[
                { ic: '', t: '语料库', d: '参考小说 · 示例', r: '2 本' },
                { ic: '', t: '榨干提取', d: '逐章压缩阶梯 → 重建校验', r: '达标≥0.65' },
                { ic: '', t: '模板库', d: '去实体化槽位模板 l1-l4', r: '嵌入索引' },
                { ic: '', t: '命中 → state.template', d: 'new_arc 自动 match（相似≥0.50）', r: '格式+策略注入' },
                { ic: '', t: '主系统 · 逐情节创作', d: 'l1→l5 → 元素隔离 → 双评分', r: '落盘' },
            ].map((s, i) => (
                <div key={i}>
                    {i > 0 && <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--ink-faint)', fontSize: 11, paddingLeft: 26 }}>
                        <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--dai)' }} />{s.linkHint || '↓'}
                    </div>}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', border: '1px solid var(--line-soft)', borderRadius: 6, background: 'var(--paper)' }}>
                        <span style={{ fontSize: 15, flexShrink: 0 }}>{s.ic}</span>
                        <div style={{ flex: 1 }}>
                            <div style={{ fontWeight: 600, fontSize: 12.5 }}>{s.t}</div>
                            <div style={{ fontSize: 11, color: 'var(--ink-mute)' }}>{s.d}</div>
                        </div>
                        <div style={{ flexShrink: 0, textAlign: 'right', fontSize: 11, color: 'var(--ink-mute)' }}>{s.r}</div>
                    </div>
                </div>
            ))}
        </div>
    )
}

function Consumption({ items }) {
    const list = items || []
    return (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
            <thead>
                <tr style={{ textAlign: 'left' }}>
                    <th style={{ padding: '3px 6px', borderBottom: '1px solid var(--line-soft)', color: 'var(--ink-mute)', fontSize: 10.5 }}>书 / 情节</th>
                    <th style={{ padding: '3px 6px', borderBottom: '1px solid var(--line-soft)', color: 'var(--ink-mute)', fontSize: 10.5 }}>模板</th>
                    <th style={{ padding: '3px 6px', borderBottom: '1px solid var(--line-soft)', color: 'var(--ink-mute)', fontSize: 10.5 }}>相似</th>
                </tr>
            </thead>
            <tbody>
                {list.length === 0 && (
                    <tr><td colSpan="3" style={{ padding: '6px 6px', color: 'var(--ink-faint)', fontSize: 11 }}>
                        空 — 在主系统工作台新建情节、命中模板后自动显示（数据源：主系统 arcs.json state.template）
                    </td></tr>
                )}
                {list.slice(0, 8).map((c, i) => (
                    <tr key={i} style={{ borderBottom: '1px dashed var(--line-faint)' }}>
                        <td style={{ padding: '3px 6px' }}>{c.book} · {c.arc_name}</td>
                        <td style={{ padding: '3px 6px', color: 'var(--dai-dark)' }}>{c.template_name}</td>
                        <td style={{ padding: '3px 6px', color: 'var(--green)', fontWeight: 700 }}>
                            {c.similarity != null ? (+c.similarity).toFixed(3) : '—'}
                        </td>
                    </tr>
                ))}
            </tbody>
        </table>
    )
}

function Matches({ items }) {
    const list = items || []
    if (list.length === 0) {
        return <div style={tStyle}>（暂无 plot_template_match 记录）</div>
    }
    return (
        <div>
            {list.slice(0, 4).map((m, i) => (
                <div key={i} style={{ marginBottom: 7 }}>
                    <div style={{ fontSize: 11.5, background: 'var(--dai-wash)', padding: '5px 7px', borderRadius: 6, marginBottom: 4, color: 'var(--ink-sub)' }}>
                        <span style={{ fontSize: 10, color: 'var(--ink-mute)', marginRight: 6 }}>{m.date}</span>
                        {m.user.slice(0, 70)}{m.user.length > 70 ? '…' : ''}
                    </div>
                    {m.matched.length > 0 ? (
                        <div style={{ fontSize: 11.5, padding: '4px 7px', borderLeft: '3px solid var(--green)', background: 'var(--green-wash)', borderRadius: '0 5px 5px 0' }}>
                            候选 {m.matched.length}：{m.matched.slice(0, 2).join('；')}
                            <span style={{ color: 'var(--ink-mute)', fontSize: 10.5, marginLeft: 4 }}>（LLM 仲裁候选 · 是否套用看相似度 ≥0.50）</span>
                        </div>
                    ) : (
                        <div style={{ fontSize: 11.5, padding: '4px 7px', borderLeft: '3px solid var(--amber)', background: 'var(--amber-wash)', borderRadius: '0 5px 5px 0' }}>无候选</div>
                    )}
                </div>
            ))}
        </div>
    )
}

export default function ConnectionPanel({ overview }) {
    const o = overview || {}
    const counts = o.counts || {}
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {/* 计数摘要 */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {[
                    ['语料', counts.corpus_file_count ?? '—', '本'],
                    ['模板库', counts.template_count ?? '—', '条'],
                    ['主系统消费', counts.consumption_count ?? '—', '情节'],
                ].map(([k, v, u], i) => (
                    <div key={k} style={{ flex: 1, minWidth: 70, textAlign: 'center', padding: '6px 4px', border: '1px solid var(--line-soft)', borderRadius: 6, background: 'var(--paper-raised)' }}>
                        <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--dai-dark)' }}>{v}</div>
                        <div style={{ fontSize: 10.5, color: 'var(--ink-mute)' }}>{k}（{u}）</div>
                    </div>
                ))}
            </div>

            <div>
                <div style={sec('var(--dai-dark)')}>① 五级不变 prompt → 生成链路</div>
                <InvChain invariants={o.invariants} baseline={o.baseline_guard} />
            </div>

            <div>
                <div style={{ ...sec('var(--dai-dark)'), marginTop: 8 }}>② 模板 → 主系统 链路</div>
                <Chain />
            </div>

            <div>
                <div style={{ ...sec('var(--dai-dark)'), marginTop: 8 }}>③ 模板消费（书 → 情节 → 模板）</div>
                <Consumption items={o.consumption} />
            </div>

            <div>
                <div style={{ ...sec('var(--dai-dark)'), marginTop: 8 }}>④ 最近命中记录（workbench 日志）</div>
                <Matches items={o.recent_matches} />
            </div>
        </div>
    )
}
