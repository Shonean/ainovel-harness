/**
 * 新建书模式选择弹层：精品 / 量产 两卡。
 *
 * Props:
 *  - open: boolean
 *  - onPick: (mode: 'premium' | 'mass') => void
 *  - onClose: () => void
 *
 * 设计原型：docs/计划稿/2026-09-11-量产模式-UI原型.html（02 模式选择）。
 */

const maskStyle = {
    position: 'fixed', inset: 0, background: 'rgba(20,16,10,.4)', zIndex: 9998,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
}

const modalStyle = {
    background: 'var(--paper-raised, #fdfaf2)', border: '1px solid var(--line, #cfc6b2)',
    borderRadius: 12, padding: '18px 20px 14px', width: 640, maxWidth: '92vw',
    boxShadow: '0 8px 30px rgba(0,0,0,.18)',
}

const cardStyle = {
    flex: 1, display: 'flex', flexDirection: 'column', gap: 8, textAlign: 'left',
    padding: '16px 16px 14px', borderRadius: 10, cursor: 'pointer',
    border: '1px solid var(--line, #cfc6b2)', background: 'var(--paper, #faf7f0)',
    color: 'var(--ink, #1a1712)', font: 'inherit',
}

export default function ModePickerModal({ open, onPick, onClose }) {
    if (!open) return null
    return (
        <div style={maskStyle} onMouseDown={onClose}>
            <div style={modalStyle} onMouseDown={(e) => e.stopPropagation()}>
                <div style={{
                    display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
                    fontSize: 15, fontWeight: 700, marginBottom: 14,
                }}>
                    <span>新建书 · 选择模式</span>
                    <span style={{ fontWeight: 400, color: 'var(--ink-sub, #555)', fontSize: 12 }}>
                        建书后可在工作台设置里切换
                    </span>
                </div>
                <div style={{ display: 'flex', gap: 12 }}>
                    <button type="button" style={cardStyle} onClick={() => onPick('premium')}>
                        <h4 style={{ margin: 0, fontSize: 15 }}>精品书</h4>
                        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6 }}>
                            五级阶梯逐级确认 · 全量评分 · 完整编辑与书级讨论
                        </p>
                        <p style={{ margin: 0, fontSize: 12, color: 'var(--ink-sub, #555)' }}>
                            适合精雕细琢的一本书
                        </p>
                        <span className="btn ghost" style={{ marginTop: 'auto', alignSelf: 'flex-start' }}>
                            创建精品书
                        </span>
                    </button>
                    <button type="button" style={{ ...cardStyle, borderColor: 'var(--ink, #1a1712)' }} onClick={() => onPick('mass')}>
                        <h4 style={{ margin: 0, fontSize: 15, display: 'flex', alignItems: 'center', gap: 6 }}>
                            量产书 <span className="badge badge-amber">量产</span>
                        </h4>
                        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6 }}>
                            快速开局 · 路线图规划 · 流水线批量 · 跳过评分可补评
                        </p>
                        <p style={{ margin: 0, fontSize: 12, color: 'var(--ink-sub, #555)' }}>
                            适合低质量原料书批量产出
                        </p>
                        <span className="btn btn-blue" style={{ marginTop: 'auto', alignSelf: 'flex-start' }}>
                            创建量产书
                        </span>
                    </button>
                </div>
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}>
                    <button className="btn ghost" onClick={onClose}>取消</button>
                </div>
            </div>
        </div>
    )
}
