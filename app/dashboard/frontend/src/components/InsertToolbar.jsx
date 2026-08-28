/**
 * 插入工具栏（右栏底部，点选对象下方、输入框上方）
 *
 * 8 个按钮：动作 / 对白 / 细节 / 冲突 / 留空 / 物品卡 / 角色卡 / 设定卡
 *
 * Props:
 *  - cursor: { sceneIdx, beatIdx, rowIdx, kind } | null  当前光标位置
 *  - onInsert: (kind) => void  点击插入按钮时触发
 *  - disabled: boolean  是否禁用（没有光标位置时禁用）
 *  - sceneName: string  当前场景名（用于显示位置提示）
 */
export default function InsertToolbar({ cursor, onInsert, disabled, sceneName }) {
    const buttons = [
        { kind: 'action', icon: '', label: '动作' },
        { kind: 'dialogue', icon: '', label: '对白' },
        { kind: 'detail', icon: '', label: '细节' },
        { kind: 'conflict', icon: '', label: '冲突' },
        { kind: 'blank', icon: '', label: '留空' },
        { kind: 'item_card', icon: '', label: '物品卡' },
        { kind: 'char_card', icon: '', label: '角色卡' },
        { kind: 'setting_card', icon: '', label: '设定卡' },
    ]

    return (
        <div style={{
            flexShrink: 0,
            padding: '8px 0',
            borderTop: '1px solid var(--line-soft)',
            borderBottom: '1px solid var(--line-soft)',
            background: 'var(--paper-raised)',
        }}>
            {/* 位置指示 */}
            <div style={{
                fontSize: 10.5,
                color: disabled ? 'var(--ink-mute)' : 'var(--ink-sub)',
                marginBottom: 6,
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                lineHeight: 1.3,
            }}>
                {disabled ? (
                    <span style={{ color: 'var(--ink-mute)' }}>
                        点击中栏内容行定位插入位置
                    </span>
                ) : (
                    <>
                        <span style={{
                            background: 'var(--dai)',
                            color: 'var(--paper-raised)',
                            padding: '1px 6px',
                            borderRadius: 2,
                            fontSize: 10,
                            fontWeight: 600,
                        }}>光标</span>
                        <span style={{ color: 'var(--dai-dark)', fontWeight: 600, fontSize: 11 }}>
                            {sceneName || '场景'}
                            {cursor?.beatIdx >= 0 ? ` · 节拍${cursor.beatIdx + 1}` : ''}
                        </span>
                    </>
                )}
            </div>

            {/* 按钮组 */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                {buttons.map(btn => (
                    <button
                        key={btn.kind}
                        onClick={() => !disabled && onInsert(btn.kind)}
                        disabled={disabled}
                        style={{
                            padding: '2px 8px',
                            borderRadius: 3,
                            fontSize: 11,
                            border: '1px solid var(--line-soft)',
                            background: disabled ? 'var(--paper)' : 'var(--paper)',
                            color: disabled ? 'var(--ink-mute)' : 'var(--ink-sub)',
                            cursor: disabled ? 'not-allowed' : 'pointer',
                            fontFamily: 'inherit',
                            transition: 'all 0.15s',
                            display: 'flex',
                            alignItems: 'center',
                            gap: 3,
                            opacity: disabled ? 0.5 : 1,
                        }}
                        onMouseEnter={(e) => {
                            if (!disabled) {
                                e.currentTarget.style.borderColor = 'var(--dai)'
                                e.currentTarget.style.background = 'var(--dai-wash)'
                                e.currentTarget.style.color = 'var(--dai-dark)'
                            }
                        }}
                        onMouseLeave={(e) => {
                            e.currentTarget.style.borderColor = 'var(--line-soft)'
                            e.currentTarget.style.background = 'var(--paper)'
                            e.currentTarget.style.color = disabled ? 'var(--ink-mute)' : 'var(--ink-sub)'
                        }}
                    >
<span>{btn.label}</span>
                    </button>
                ))}
            </div>
        </div>
    )
}
