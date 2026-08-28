import { useEffect, useRef, useState } from 'react'
import { subscribeTaskStream } from '../api.js'

/**
 * 绑定一个 task_id 的 SSE 流，实时追加日志行。
 *
 * 渲染的 phase：
 *  - stdout / stderr : 子进程输出
 *  - argv            : 子进程命令行
 *  - step            : 工作流步骤状态（running/done/failed）
 *  - token           : LLM 流式 token（连续 token 累积成一行）
 *  - tool_use        : agent 工具调用
 *  - tool_result     : 工具返回
 *  - awaiting_input  : 工作流挂起等待用户输入
 *  - done / error    : 终止
 *
 * Props:
 *  - taskId: string | null
 *  - onDone: ({ payload }) => void
 *  - onError: (msg) => void
 *  - onAwaitingInput: (promptObj) => void   // 可选：交给上层弹 AskUserModal
 *
 * 注意：SSE 订阅只依赖 taskId。onDone/onError/onAwaitingInput 通过 ref 桥接，
 * 避免父组件重渲染时回调引用变化导致重订阅（否则会多开 EventSource、事件被
 * 重复处理、弹窗叠加闪烁）。awaiting_input 按 seq 去重，防止 SSE 断线重连
 * 重放历史事件时重复弹窗。
 */
export default function StreamLog({ taskId, onDone, onError, onAwaitingInput, onEvent }) {
    const [lines, setLines] = useState([])
    const bottomRef = useRef(null)

    // 回调用 ref 桥接，保证 SSE 订阅不因回调引用变化而重建
    const cbRef = useRef({ onDone, onError, onAwaitingInput, onEvent })
    cbRef.current = { onDone, onError, onAwaitingInput, onEvent }
    // 已处理过的 awaiting_input seq 集合，防止重连重放时重复弹窗
    const seenAskSeq = useRef(new Set())

    useEffect(() => {
        if (!taskId) return
        setLines([])
        seenAskSeq.current = new Set()

        const unsub = subscribeTaskStream(
            taskId,
            ev => {
                if (cbRef.current.onEvent) cbRef.current.onEvent(ev)
                if (ev.phase === 'stdout' || ev.phase === 'stderr') {
                    setLines(prev => [...prev, { kind: ev.phase, text: ev.line }])
                } else if (ev.phase === 'argv') {
                    setLines(prev => [...prev, { kind: 'info', text: `$ ${ev.argv.join(' ')}` }])
                } else if (ev.phase === 'step') {
                    const icon = ev.status === 'done' ? '✓' : ev.status === 'failed' ? '✗' : '▶'
                    setLines(prev => [...prev, { kind: 'step', text: `${icon} [${ev.step}] ${ev.status || ''}` }])
                } else if (ev.phase === 'token') {
                    // 连续 token 累积到上一行（若上一行也是 token）
                    setLines(prev => {
                        const last = prev[prev.length - 1]
                        if (last && last.kind === 'token') {
                            const next = prev.slice()
                            next[next.length - 1] = { ...last, text: last.text + ev.text }
                            return next
                        }
                        return [...prev, { kind: 'token', text: ev.text }]
                    })
                } else if (ev.phase === 'tool_use') {
                    const inp = ev.input || {}
                    const arg = inp.file_path || inp.command || inp.pattern || ''
                    setLines(prev => [...prev, { kind: 'tool', text: `${ev.name}(${arg})` }])
                } else if (ev.phase === 'tool_result') {
                    const preview = ev.output_preview || ''
                    setLines(prev => [...prev, { kind: 'tool', text: `↳ ${preview}` }])
                } else if (ev.phase === 'done') {
                    const payload = ev.payload || {}
                    if (payload.truncated) {
                        setLines(prev => [...prev, {
                            kind: 'stderr',
                            text: '⚠任务因达到最大轮次限制而截断，可能未完成所有步骤',
                        }])
                    }
                    if (cbRef.current.onDone) cbRef.current.onDone(payload)
                } else if (ev.phase === 'error') {
                    if (cbRef.current.onError) cbRef.current.onError(ev.msg)
                } else if (ev.phase === 'cancelled') {
                    setLines(prev => [...prev, { kind: 'info', text: `[⏹ ${ev.msg || '已取消'}]` }])
                    // 取消等同任务结束，通知上层关闭任务区
                    if (cbRef.current.onDone) cbRef.current.onDone({ cancelled: true })
                } else if (ev.phase === 'awaiting_input') {
                    // 按 seq 去重：SSE 断线重连会重放历史事件，同一 seq 只弹一次
                    const seq = ev.seq
                    if (seq !== undefined && seenAskSeq.current.has(seq)) return
                    if (seq !== undefined) seenAskSeq.current.add(seq)
                    setLines(prev => [...prev, { kind: 'info', text: `[⏸ 等待用户输入]` }])
                    if (cbRef.current.onAwaitingInput) cbRef.current.onAwaitingInput(ev)
                }
            },
            {
                onError: () => {
                    setLines(prev => [...prev, { kind: 'stderr', text: 'SSE 连接中断（将自动重连）' }])
                },
            },
        )
        return unsub
    }, [taskId])

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [lines])

    if (!taskId) return null

    return (
        <div className="stream-log">
            {lines.map((l, i) => (
                <div key={i} className={`log-line log-${l.kind}`}>
                    <span className="log-prefix">
                        {l.kind === 'stdout' ? '>' : l.kind === 'stderr' ? '!' :
                         l.kind === 'token' ? '…' : l.kind === 'tool' ? '' :
                         l.kind === 'step' ? '▸' : '•'}
                    </span>
                    <span className="log-text">{l.text}</span>
                </div>
            ))}
            <div ref={bottomRef} />
        </div>
    )
}
