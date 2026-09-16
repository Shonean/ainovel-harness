/**
 * 确认弹层（v9 设计稿 cm-* 类；替代 window.confirm，Electron 安全）。
 */
export default function ConfirmModal({ open, title, body, okText = '确认', onOk, onCancel }) {
    if (!open) return null
    return (
        <div className="cm-mask on" onClick={e => { if (e.target === e.currentTarget) onCancel?.() }}>
            <div className="cm">
                <div className="t">{title}</div>
                <div className="d">{body}</div>
                <div className="cm-foot">
                    <button className="btn btn-small" onClick={onCancel}>取消</button>
                    <button className="btn btn-blue" onClick={onOk}>{okText}</button>
                </div>
            </div>
        </div>
    )
}
