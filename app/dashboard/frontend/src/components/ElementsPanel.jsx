/**
 * ElementsPanel.jsx - 共享元素面板组件
 *
 * 提供两种模式：
 * 1. Simple（初始化页面）：左侧列表，点击插入聊天
 * 2. Full（工作台）：统计+表格+画廊（由 AICreationPage 自己实现）
 *
 * 共享逻辑：
 * - 元素状态管理
 * - 从 API 加载元素
 * - 元素类型标签和图标
 */
import { useState, useEffect, useCallback } from 'react'
import { phAiState } from '../api'

// 元素类型配置（与工作台 AICreationPage 保持一致）
export const ELEMENT_KINDS = {
    characters: { label: '角色', icon: '', color: '#5a4a30' },
    items: { label: '物品', icon: '', color: '#6b5a3a' },
    settings: { label: '设定', icon: '', color: '#7a6a4a' },
    maps: { label: '地点', icon: '', color: '#8a7a5a' },
}

// 获取元素总数
export function getElementCount(elements) {
    return Object.values(elements).flat().length
}

// 扁平化元素列表（带 kind 和 icon）
export function flattenElements(elements) {
    return [
        ...(elements.characters || []).map(e => ({ ...e, kind: 'characters', icon: '' })),
        ...(elements.items || []).map(e => ({ ...e, kind: 'items', icon: '' })),
        ...(elements.settings || []).map(e => ({ ...e, kind: 'settings', icon: '' })),
        ...(elements.maps || []).map(e => ({ ...e, kind: 'maps', icon: '' })),
    ]
}

/**
 * useElements - 元素状态 Hook
 *
 * @param {string} bookRoot - 书目录路径
 * @returns {{ elements, setElements, loading, reload }}
 */
export function useElements(bookRoot) {
    const [elements, setElements] = useState({ characters: [], items: [], settings: [], maps: [] })
    const [loading, setLoading] = useState(false)

    const reload = useCallback(async () => {
        if (!bookRoot) return
        setLoading(true)
        try {
            const res = await phAiState(bookRoot)
            if (res?.elements) {
                // 确保每个字段都是数组
                setElements({
                    characters: Array.isArray(res.elements.characters) ? res.elements.characters : [],
                    items: Array.isArray(res.elements.items) ? res.elements.items : [],
                    settings: Array.isArray(res.elements.settings) ? res.elements.settings : [],
                    maps: Array.isArray(res.elements.maps) ? res.elements.maps : [],
                })
            }
        } catch (e) {
            console.error('加载元素失败', e)
        } finally {
            setLoading(false)
        }
    }, [bookRoot])

    useEffect(() => {
        reload()
    }, [reload])

    return { elements, setElements, loading, reload }
}

/**
 * ElementsToggleButton - 元素面板切换按钮
 *
 * @param {Object} props
 * @param {number} props.count - 元素数量
 * @param {boolean} props.open - 是否展开
 * @param {Function} props.onToggle - 切换回调
 */
export function ElementsToggleButton({ count, open, onToggle }) {
    return (
        <button
            onClick={onToggle}
            style={{
                padding: '4px 10px', borderRadius: 6, border: '1px solid var(--line)',
                background: open ? 'var(--accent)' : 'var(--paper)',
                color: open ? '#fff' : 'var(--ink)', cursor: 'pointer', fontSize: 12,
                display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0, whiteSpace: 'nowrap',
            }}
        >
            元素
            {count > 0 && (
                <span style={{
                    fontSize: 10, background: open ? 'rgba(255,255,255,0.3)' : 'var(--accent)',
                    color: open ? '#fff' : '#fff', borderRadius: 8, padding: '1px 5px',
                }}>
                    {count}
                </span>
            )}
        </button>
    )
}

/**
 * ElementsPanelContent - 元素面板内容（左侧列表）
 *
 * @param {Object} props
 * @param {Object} props.elements - 元素数据
 * @param {Function} props.onElementClick - 点击元素回调
 */
export function ElementsPanelContent({ elements, onElementClick }) {
    const count = getElementCount(elements)

    return (
        <div style={{
            width: 240, borderRight: '1px solid var(--line)', background: 'var(--paper-raised)',
            display: 'flex', flexDirection: 'column', overflow: 'hidden', flexShrink: 0,
        }}>
            <div style={{
                padding: '10px 12px', borderBottom: '1px solid var(--line)',
                fontWeight: 700, fontSize: 13, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
                <span>已有元素</span>
                <span style={{ fontSize: 11, color: 'var(--ink-sub)' }}>
                    {count} 项
                </span>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
                {Object.entries(elements).map(([kind, items]) => {
                    if (!items || items.length === 0) return null
                    const kindConfig = ELEMENT_KINDS[kind] || { label: kind }
                    return (
                        <div key={kind} style={{ marginBottom: 12 }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-sub)', marginBottom: 4 }}>
                                {kindConfig.label}
                            </div>
                            {items.map(item => (
                                <div
                                    key={item.id}
                                    style={{
                                        padding: '6px 8px', marginBottom: 4, borderRadius: 4,
                                        background: 'var(--paper)', border: '1px solid var(--line)',
                                        cursor: 'pointer', fontSize: 12,
                                    }}
                                    onClick={() => onElementClick?.(item)}
                                >
                                    <div style={{ fontWeight: 600, marginBottom: 2 }}>{item.name || '（无名称）'}</div>
                                    {item.desc && (
                                        <div style={{ color: 'var(--ink-sub)', fontSize: 11, lineHeight: 1.4 }}>
                                            {item.desc.slice(0, 60)}{item.desc.length > 60 ? '…' : ''}
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    )
                })}
                {count === 0 && (
                    <div style={{ color: 'var(--ink-mute)', fontSize: 12, textAlign: 'center', padding: 20 }}>
                        暂无元素，请先通过对话创建
                    </div>
                )}
            </div>
        </div>
    )
}

export default ElementsPanelContent
