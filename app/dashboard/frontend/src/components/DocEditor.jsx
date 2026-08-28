import { useState, useEffect } from 'react'
import { fetchFilesTree, fetchFileContent, postFileWrite } from '../api.js'

export default function DocEditor({ bookRoot, bookTitle, onNotice, onError }) {
    const [docTree, setDocTree] = useState(null)
    const [docSelPath, setDocSelPath] = useState('')
    const [docContent, setDocContent] = useState('')
    const [docDirty, setDocDirty] = useState(false)
    const [docSaveState, setDocSaveState] = useState('idle')
    const [docErr, setDocErr] = useState('')

    // 加载目录树
    useEffect(() => {
        if (!bookRoot) return
        const loadTree = async () => {
            try {
                const r = await fetchFilesTree(bookRoot)
                if (r?.ok) setDocTree(r.tree || {})
                else setDocErr(r?.error || '加载目录失败')
            } catch (e) {
                setDocErr(e.message || '加载目录失败')
            }
        }
        loadTree()
    }, [bookRoot])

    // 选择文件时加载内容
    useEffect(() => {
        if (!docSelPath || !bookRoot) return
        const loadContent = async () => {
            try {
                const r = await fetchFileContent(docSelPath)
                if (r?.ok) {
                    setDocContent(r.content || '')
                    setDocDirty(false)
                    setDocSaveState('idle')
                } else {
                    setDocErr(r?.error || '加载文件失败')
                }
            } catch (e) {
                setDocErr(e.message || '加载文件失败')
            }
        }
        loadContent()
    }, [docSelPath, bookRoot])

    // 保存文件
    const handleSave = async () => {
        if (!docDirty || !docSelPath) return
        setDocSaveState('saving')
        try {
            const r = await postFileWrite(docSelPath, docContent)
            if (r?.ok) {
                setDocDirty(false)
                setDocSaveState('saved')
                onNotice?.(`已保存 ${docSelPath}`)
            } else {
                setDocSaveState('error')
                onError?.(r?.error || '保存失败')
            }
        } catch (e) {
            setDocSaveState('error')
            onError?.(e.message || '保存失败')
        }
    }

    // 渲染目录树
    const renderNodes = (items, depth = 0) => {
        if (!items || !items.length) return null
        return items.map(item => {
            const key = item.path || item.name
            if (item.type === 'dir') {
                return (
                    <div key={key} style={{ paddingLeft: depth * 14 }}>
                        <div style={{ fontWeight: 600, fontSize: 12.5, margin: '6px 0 2px', color: 'var(--ink-sub)' }}>
                            {item.name}
                        </div>
                        {renderNodes(item.children, depth + 1)}
                    </div>
                )
            }
            return (
                <div key={key} style={{ paddingLeft: depth * 14 + 14 }}>
                    <button
                        onClick={() => setDocSelPath(item.path)}
                        style={{
                            background: 'none',
                            border: 'none',
                            cursor: 'pointer',
                            fontSize: 12.5,
                            color: docSelPath === item.path ? 'var(--cinnabar)' : 'var(--ink)',
                            padding: '2px 4px',
                            textAlign: 'left',
                            width: '100%',
                            fontFamily: 'inherit'
                        }}
                    >
                        {item.name}
                    </button>
                </div>
            )
        })
    }

    const btnStyle = (disabled, primary) => ({
        padding: '6px 12px',
        borderRadius: 4,
        border: '1px solid var(--line)',
        background: primary ? 'var(--dai)' : 'var(--paper)',
        color: primary ? 'var(--paper-raised)' : 'var(--ink)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        fontSize: 13,
    })

    return (
        <div className="content-grid files-layout">
            <article className="card files-tree-card">
                <div className="card-header">
                    <div className="card-title">目录树</div>
                    <span style={{ fontSize: 12, color: 'var(--ink-sub)' }}>正文 / AI生成 / 大纲 / 设定集</span>
                </div>
                <div style={{ maxHeight: 520, overflow: 'auto', padding: 8 }}>
                    {docErr && (
                        <div style={{ fontSize: 12, color: 'var(--cinnabar-d)', marginBottom: 8 }}>
                            ⚠ {docErr}
                        </div>
                    )}
                    {!docTree ? (
                        <div style={{ fontSize: 13, color: 'var(--ink-mute)' }}>加载目录…</div>
                    ) : (
                        Object.entries(docTree).map(([folder, items]) => (
                            <div key={folder}>
                                <div style={{ fontSize: 13, fontWeight: 700, margin: '8px 0 2px' }}>
                                    {folder}
                                </div>
                                {renderNodes(items)}
                            </div>
                        ))
                    )}
                </div>
            </article>
            <article className="card files-preview-card">
                <div className="card-header">
                    <div className="card-title">内容编辑</div>
                    {docSelPath && (
                        <span style={{ fontSize: 12, color: 'var(--ink-sub)' }}>
                            {docSelPath} · {docContent.length} 字 {docDirty ? '· 未保存' : ''}
                        </span>
                    )}
                </div>
                {docSelPath ? (
                    <>
                        <textarea
                            value={docContent}
                            onChange={e => { setDocContent(e.target.value); setDocDirty(true) }}
                            spellCheck={false}
                            style={{
                                width: '100%',
                                minHeight: 480,
                                boxSizing: 'border-box',
                                fontFamily: 'inherit',
                                fontSize: 13.5,
                                lineHeight: 1.8,
                                padding: 10,
                                border: '1px solid var(--line-soft)',
                                borderRadius: 6
                            }}
                        />
                        <div style={{ marginTop: 8 }}>
                            <button
                                style={btnStyle(!docDirty, false)}
                                disabled={!docDirty}
                                onClick={handleSave}
                            >
                                {docSaveState === 'saving' ? '保存中…' : '保存'}
                            </button>
                            {docSaveState === 'saved' && (
                                <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--green)' }}>✓ 已保存</span>
                            )}
                            {docSaveState === 'error' && (
                                <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--cinnabar-d)' }}>保存失败</span>
                            )}
                        </div>
                    </>
                ) : (
                    <div className="empty-state">
                        <p>选择左侧文件以编辑内容</p>
                    </div>
                )}
            </article>
        </div>
    )
}
