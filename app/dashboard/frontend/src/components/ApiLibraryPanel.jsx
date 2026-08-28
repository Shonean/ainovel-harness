/**
 * API 预设库面板（主页）。
 * 两部分：
 *   文字模型预设 — 多组，可增删改、可切换
 *   向量模型预设 — 多组，可增删改、可切换（v7.6.4 起向量也支持多预设）
 * 敏感字段掩码显示。
 */
import { useCallback, useEffect, useState } from 'react'
import {
    fetchApiLibrary, saveApiPreset, deleteApiPreset, applyApiPreset,
    saveApiEmbedPreset, deleteApiEmbedPreset, applyApiEmbedPreset,
} from '../api.js'
import Badge from './Badge.jsx'

function maskDisplay(val, isSecret) {
    if (!val) return '(未设)'
    if (!isSecret) return val
    if (val.length <= 8) return '*'.repeat(val.length)
    return val.slice(0, 4) + '****' + val.slice(-4)
}

export default function ApiLibraryPanel() {
    // ── 共享状态 ──
    const [loading, setLoading] = useState(true)
    const [msg, setMsg] = useState('')
    const [err, setErr] = useState('')

    // ── 文字模型预设 ──
    const [textPresets, setTextPresets] = useState([])
    const [currentTextId, setCurrentTextId] = useState(null)
    const [textFieldsMeta, setTextFieldsMeta] = useState([])
    const [editingText, setEditingText] = useState(null)  // {id, name, fields, secretsConfigured}
    const [savingText, setSavingText] = useState(false)

    // ── 向量模型预设 ──
    const [embedPresets, setEmbedPresets] = useState([])
    const [currentEmbedId, setCurrentEmbedId] = useState(null)
    const [embedFieldsMeta, setEmbedFieldsMeta] = useState([])
    const [editingEmbed, setEditingEmbed] = useState(null)  // {id, name, fields, secretsConfigured}
    const [savingEmbed, setSavingEmbed] = useState(false)

    const reload = useCallback(() => {
        setLoading(true); setErr(''); setMsg('')
        fetchApiLibrary()
            .then(r => {
                setTextPresets(r.text_presets || [])
                setCurrentTextId(r.current_text_id || null)
                setTextFieldsMeta(r.text_fields || [])
                setEmbedPresets(r.embed_presets || [])
                setCurrentEmbedId(r.current_embed_id || null)
                setEmbedFieldsMeta(r.embed_fields || [])
            })
            .catch(e => setErr(e.message || '加载失败'))
            .finally(() => setLoading(false))
    }, [])

    useEffect(() => { reload() }, [reload])

    // ============================================================
    // 文字模型预设
    // ============================================================

    const startNewText = () => setEditingText({ id: null, name: '', fields: {}, secretsConfigured: {} })
    const startEditText = (p) => {
        const f = {}
        for (const m of textFieldsMeta) {
            f[m.key] = m.secret ? '' : (p.fields[m.key] || '')
        }
        setEditingText({ id: p.id, name: p.name, fields: f, secretsConfigured: p.fields })
    }

    const onTextFieldChange = (key, val) =>
        setEditingText(e => ({ ...e, fields: { ...e.fields, [key]: val } }))

    const handleSaveText = () => {
        if (!editingText.name.trim()) { setErr('请填预设名称'); return }
        setSavingText(true); setErr(''); setMsg('')
        saveApiPreset(editingText.id, editingText.name, editingText.fields)
            .then(() => {
                setEditingText(null)
                setMsg(editingText.id ? '已更新文字模型预设' : '已新建文字模型预设')
                reload()
            })
            .catch(e => setErr(e.message || '保存失败'))
            .finally(() => setSavingText(false))
    }

    const handleDeleteText = (p) => {
        if (!window.confirm(`删除文字模型预设「${p.name}」？`)) return
        deleteApiPreset(p.id).then(reload).catch(e => setErr(e.message || '删除失败'))
    }

    const handleApplyText = (p) => {
        setMsg(''); setErr('')
        applyApiPreset(p.id)
            .then(() => { setMsg(`已应用文字模型预设「${p.name}」`); reload() })
            .catch(e => setErr(e.message || '应用失败'))
    }

    // ============================================================
    // 向量模型预设
    // ============================================================

    const startNewEmbed = () => setEditingEmbed({ id: null, name: '', fields: {}, secretsConfigured: {} })
    const startEditEmbed = (p) => {
        const f = {}
        for (const m of embedFieldsMeta) {
            f[m.key] = m.secret ? '' : (p.fields[m.key] || '')
        }
        setEditingEmbed({ id: p.id, name: p.name, fields: f, secretsConfigured: p.fields })
    }

    const onEmbedFieldChange = (key, val) =>
        setEditingEmbed(e => ({ ...e, fields: { ...e.fields, [key]: val } }))

    const handleSaveEmbed = () => {
        if (!editingEmbed.name.trim()) { setErr('请填预设名称'); return }
        setSavingEmbed(true); setErr(''); setMsg('')
        saveApiEmbedPreset(editingEmbed.id, editingEmbed.name, editingEmbed.fields)
            .then(() => {
                setEditingEmbed(null)
                setMsg(editingEmbed.id ? '已更新向量模型预设' : '已新建向量模型预设')
                reload()
            })
            .catch(e => setErr(e.message || '保存失败'))
            .finally(() => setSavingEmbed(false))
    }

    const handleDeleteEmbed = (p) => {
        if (!window.confirm(`删除向量模型预设「${p.name}」？`)) return
        deleteApiEmbedPreset(p.id).then(reload).catch(e => setErr(e.message || '删除失败'))
    }

    const handleApplyEmbed = (p) => {
        setMsg(''); setErr('')
        applyApiEmbedPreset(p.id)
            .then(() => { setMsg(`已应用向量模型预设「${p.name}」`); reload() })
            .catch(e => setErr(e.message || '应用失败'))
    }

    // ============================================================
    // 渲染
    // ============================================================

    if (loading) return <div className="card" style={{ marginTop: 16 }}>加载中…</div>

    const renderPresetCards = (presets, isCurrent, onEdit, onDelete, onApply) => (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
            {presets.map(p => (
                <div key={p.id} style={{
                    border: '1px solid var(--border-soft, var(--line))', borderRadius: 6,
                    padding: '10px 12px',
                    background: isCurrent(p.id) ? 'var(--amber-wash)' : 'var(--bg-card-2, var(--paper))',
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <strong>{p.name}</strong>
                        {isCurrent(p.id) && <Badge tone="amber">当前</Badge>}
                        <span style={{ flex: 1 }} />
                        <button className="btn btn-small btn-green" onClick={() => onApply(p)}>应用</button>
                        <button className="btn btn-small" onClick={() => onEdit(p)}>编辑</button>
                        <button className="btn btn-small btn-red" onClick={() => onDelete(p)}>删除</button>
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--ink-mute)', marginTop: 4 }}>
                        模型：{p.fields.EMBED_MODEL || p.fields.ARK_MODEL_PRO || '(未设)'}
                    </div>
                </div>
            ))}
        </div>
    )

    const renderEditForm = (editing, meta, onFieldChange, onSave, saving, setEditing) => (
        <div style={{
            marginTop: 12, padding: 14, border: '2px solid var(--border-main)', borderRadius: 6,
            background: 'var(--bg-card, var(--paper-raised))',
        }}>
            <div style={{ fontWeight: 'bold', marginBottom: 8 }}>
                {editing.id ? '编辑预设' : '新建预设'}
            </div>
            <div className="env-config-row" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <label className="field-label env-config-label" style={{ minWidth: 120 }}>预设名称</label>
                <input className="field-input env-config-input" value={editing.name}
                    placeholder="如 BGE-M3 / 豆包embedding"
                    onChange={e => setEditing(ed => ({ ...ed, name: e.target.value }))} />
            </div>

            {meta.map(m => {
                const isSecret = m.secret
                const configured = editing.secretsConfigured && editing.secretsConfigured[m.key]
                const placeholder = m.placeholder || ''
                return (
                    <div key={m.key} className="env-config-row" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                        <label className="field-label env-config-label" style={{ minWidth: 120 }}>{m.label}</label>
                        <input
                            type={isSecret ? 'password' : 'text'}
                            className="field-input env-config-input"
                            value={editing.fields[m.key] || ''}
                            placeholder={isSecret ? (configured ? `已配置(${configured})·输入新值覆盖，留空保留` : placeholder) : placeholder}
                            onChange={e => onFieldChange(m.key, e.target.value)}
                        />
                    </div>
                )
            })}

            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                <button className="btn btn-green btn-small" disabled={saving} onClick={onSave}>
                    {saving ? '保存中…' : '保存'}
                </button>
                <button className="btn btn-small" disabled={saving} onClick={() => setEditing(null)}>取消</button>
            </div>
        </div>
    )

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 16 }}>

            {/* === 文字模型预设 === */}
            <div className="card">
                <div className="env-config-panel-header">
                    <div>
                        <div className="section-label">文字模型</div>
                        <div className="card-title">文字模型预设</div>
                    </div>
                    <button className="btn btn-amber btn-small" onClick={startNewText}>＋ 新建预设</button>
                </div>

                <div className="env-config-panel-hint">
                    多组文字模型 API 配置，可在工作台顶栏一键切换。
                </div>

                {textPresets.length === 0 ? (
                    <p style={{ color: 'var(--ink-mute)' }}>尚无文字模型预设，点「新建预设」添加。</p>
                ) : (
                    renderPresetCards(
                        textPresets,
                        id => id === currentTextId,
                        startEditText,
                        handleDeleteText,
                        handleApplyText,
                    )
                )}

                {editingText && renderEditForm(
                    editingText, textFieldsMeta, onTextFieldChange, handleSaveText, savingText, setEditingText,
                )}
            </div>

            {/* === 向量模型预设 === */}
            <div className="card">
                <div className="env-config-panel-header">
                    <div>
                        <div className="section-label">向量模型</div>
                        <div className="card-title">向量模型预设</div>
                    </div>
                    <button className="btn btn-amber btn-small" onClick={startNewEmbed}>＋ 新建预设</button>
                </div>

                <div className="env-config-panel-hint">
                    多组 Embedding 向量模型配置，可在工作台顶栏一键切换（v7.6.4 起支持多预设）。
                </div>

                {embedPresets.length === 0 ? (
                    <p style={{ color: 'var(--ink-mute)' }}>尚无向量模型预设，点「新建预设」添加。</p>
                ) : (
                    renderPresetCards(
                        embedPresets,
                        id => id === currentEmbedId,
                        startEditEmbed,
                        handleDeleteEmbed,
                        handleApplyEmbed,
                    )
                )}

                {editingEmbed && renderEditForm(
                    editingEmbed, embedFieldsMeta, onEmbedFieldChange, handleSaveEmbed, savingEmbed, setEditingEmbed,
                )}
            </div>

            {/* 消息提示 */}
            {msg && <div className="env-config-msg env-config-success">{msg}</div>}
            {err && <div className="env-config-msg env-config-error">{err}</div>}
        </div>
    )
}
