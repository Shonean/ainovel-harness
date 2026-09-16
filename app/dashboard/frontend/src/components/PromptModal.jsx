import { useState, useEffect, useRef } from 'react'

/**
 * Electron 兼容的 window.prompt 替代。
 * Electron 渲染进程不支持 window.prompt()（调用即抛异常），
 * 页面内所有文本输入弹窗一律走 askText()（Promise 化，取消返回 null）。
 *
 * 用法：
 *   const name = await askText('情节新名字：', a.name)
 * 组件挂载：在页面根节点渲染一次 <PromptModal />。
 */
let _resolve = null

export function askText(title, defaultValue = '') {
    return new Promise((resolve) => {
        window.dispatchEvent(new CustomEvent('ainovel-ask', {
            detail: { title, defaultValue, resolve },
        }))
    })
}

export default function PromptModal() {
    const [st, setSt] = useState(null) // {title, value, resolve}
    const inputRef = useRef(null)

    useEffect(() => {
        const h = (e) => setSt({ title: e.detail.title, value: e.detail.defaultValue || '', resolve: e.detail.resolve })
        window.addEventListener('ainovel-ask', h)
        return () => window.removeEventListener('ainovel-ask', h)
    }, [])

    useEffect(() => {
        if (st && inputRef.current) {
            inputRef.current.focus()
            inputRef.current.select()
        }
    }, [st])

    if (!st) return null

    const close = (val) => {
        setSt(null)
        st.resolve(val === undefined ? null : val)
    }

    return (
        <div
            style={{
                position: 'fixed', inset: 0, background: 'rgba(20,16,10,.4)', zIndex: 9999,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
            onMouseDown={() => close(null)}
        >
            <div
                style={{
                    background: 'var(--paper-raised, #fdfaf2)', border: '1px solid var(--line, #cfc6b2)',
                    borderRadius: 10, padding: '18px 20px', minWidth: 340, maxWidth: '80vw',
                    boxShadow: '0 8px 30px rgba(0,0,0,.18)',
                }}
                onMouseDown={(e) => e.stopPropagation()}
            >
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink, #1a1712)', marginBottom: 10 }}>{st.title}</div>
                <input
                    ref={inputRef}
                    defaultValue={st.value}
                    style={{
                        width: '100%', boxSizing: 'border-box', padding: '8px 10px', fontSize: 13,
                        border: '1px solid var(--line, #cfc6b2)', borderRadius: 6,
                        background: 'var(--paper, #faf7f0)', color: 'var(--ink, #1a1712)',
                    }}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter') close(e.currentTarget.value)
                        if (e.key === 'Escape') close(null)
                    }}
                />
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 14 }}>
                    <button
                        style={{
                            padding: '6px 16px', borderRadius: 6, fontSize: 13, cursor: 'pointer',
                            border: '1px solid var(--line, #cfc6b2)', background: 'transparent', color: 'var(--ink-sub, #555)',
                        }}
                        onClick={() => close(null)}
                    >取消</button>
                    <button
                        style={{
                            padding: '6px 16px', borderRadius: 6, fontSize: 13, cursor: 'pointer',
                            border: 'none', background: 'var(--dai, #3d6b49)', color: '#fff',
                        }}
                        onClick={() => close(inputRef.current ? inputRef.current.value : null)}
                    >确定</button>
                </div>
            </div>
        </div>
    )
}
