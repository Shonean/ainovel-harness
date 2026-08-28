import { useEffect, useRef, useState } from 'react'
import { updateSqliteCell } from '../api.js'

// 单元格编辑器：单击进入编辑态，1.5s 防抖自动保存
// 状态：idle（未编辑）/ editing（编辑中）/ saving / saved / error
export default function EditableCell({ cacheId, table, rowPk, column, value, onSaved, onError, multiline = true }) {
    const [state, setState] = useState('idle')          // idle | editing | saving | saved | error
    const [draft, setDraft] = useState(value ?? '')
    const [displayValue, setDisplayValue] = useState(value ?? '')  // 解决保存后显示回退
    const [errMsg, setErrMsg] = useState('')
    const timerRef = useRef(null)
    const savedTimerRef = useRef(null)   // doSave 成功后 1.5s 回 idle 的定时器
    const lastSavedRef = useRef(value)
    const isEditing = state === 'editing'

    // 外部 value 变化时同步（仅在 idle 且值真正变化时）
    useEffect(() => {
        if (state === 'idle' && value !== lastSavedRef.current && value !== displayValue) {
            setDraft(value ?? '')
            setDisplayValue(value ?? '')
            lastSavedRef.current = value
        }
    }, [value, state])

    // 清理防抖 timer + saved 回 idle timer
    useEffect(() => () => {
        if (timerRef.current) clearTimeout(timerRef.current)
        if (savedTimerRef.current) clearTimeout(savedTimerRef.current)
    }, [])

    const startEdit = () => {
        setDraft(displayValue ?? '')
        setState('editing')
    }

    const cancelEdit = () => {
        setDraft(displayValue ?? '')
        setState('idle')
    }

    const scheduleSave = (newVal) => {
        if (timerRef.current) clearTimeout(timerRef.current)
        timerRef.current = setTimeout(() => doSave(newVal), 1500)
    }

    const doSave = async (newVal) => {
        if (String(newVal) === String(displayValue)) {
            setState('idle')
            return
        }
        setState('saving')
        setErrMsg('')
        try {
            await updateSqliteCell(cacheId, table, rowPk, column, newVal)
            lastSavedRef.current = newVal
            setDisplayValue(newVal)
            setState('saved')
            if (onSaved) onSaved(newVal)
            // 1.5s 后回 idle（可被卸载清理）
            savedTimerRef.current = setTimeout(() => setState((s) => (s === 'saved' ? 'idle' : s)), 1500)
        } catch (e) {
            setState('error')
            setErrMsg(e.message || '保存失败')
            if (onError) onError(e)
        }
    }

    const onChange = (e) => {
        const v = e.target.value
        setDraft(v)
        setState('editing')
        scheduleSave(v)
    }

    const onBlur = () => {
        if (timerRef.current) clearTimeout(timerRef.current)
        if (state === 'editing') doSave(draft)
    }

    const indicator = () => {
        if (state === 'saving') return <span className="text-gray text-xs" title="保存中…">⟳</span>
        if (state === 'saved') return <span className="text-green text-xs" title="已保存">✓</span>
        if (state === 'error') return <span className="text-red text-xs" title={errMsg}>⚠ {errMsg}</span>
        return null
    }

    if (state === 'editing' || state === 'saving') {
        return (
            <div className="editable-cell editing">
                <textarea
                    autoFocus
                    value={draft}
                    onChange={onChange}
                    onBlur={onBlur}
                    rows={multiline ? Math.min(8, Math.max(2, draft.split('\n').length)) : 1}
                    className="editable-cell-textarea"
                />
                {indicator()}
            </div>
        )
    }

    return (
        <div className="editable-cell" onClick={startEdit} title="点击编辑">
            <div className="editable-cell-display">
                <span className="editable-cell-text">{displayValue || <em className="text-gray">（空）</em>}</span>
                {indicator()}
            </div>
        </div>
    )
}
