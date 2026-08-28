// 常驻状态条：当日 LLM 用量 / 缓存命中 / 花费 / 错误。
// 借鉴 codex TUI 的 status line：底部一行永远显示运行健康度，点击展开详情。
import { useEffect, useRef, useState } from 'react'
import { phListLlmLogs } from '../api.js'

function fmtTok(n) {
    n = Number(n) || 0
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k'
    return String(n)
}

export default function StatusLine({ intervalMs = 15000 }) {
    const [stats, setStats] = useState(null)
    const [open, setOpen] = useState(false)
    const [err, setErr] = useState('')
    const errRef = useRef(0)

    useEffect(() => {
        let alive = true
        const poll = async () => {
            try {
                const r = await phListLlmLogs('', 1)
                if (!alive) return
                const s = r?.stats
                if (s) { setStats(s); setErr('') }
            } catch (e) {
                // 连续失败才显示，避免后端重启瞬间闪红
                errRef.current += 1
                if (errRef.current >= 2) setErr(String(e?.message || e))
            }
        }
        poll()
        const iv = setInterval(poll, intervalMs)
        return () => { alive = false; clearInterval(iv) }
    }, [intervalMs])

    if (!stats && !err) {
        return <div style={barStyle}>加载用量…</div>
    }

    const s = stats || {}
    const cachePct = s.prompt_tokens
        ? Math.round(100 * (s.cache_hit_tokens || 0) / s.prompt_tokens)
        : 0
    const errPct = s.total_calls ? Math.round(100 * (s.error_count || 0) / s.total_calls) : 0
    const healthy = !err && (!s.error_count || errPct < 10)

    return (
        <div
            style={{ ...barStyle, cursor: 'pointer', justifyContent: 'space-between' }}
            onClick={() => setOpen(v => !v)}
            title="点击展开/收起用量详情"
        >
            <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ color: healthy ? 'var(--green-dark, #2a7)' : 'var(--red, #c33)' }}>
                    {healthy ? '● 正常' : '● 异常'}
                </span>
                <span>今日 <b>{s.total_calls || 0}</b> 次调用</span>
                <span title="输入 token 总数">入 {fmtTok(s.prompt_tokens)}</span>
                <span title="输出 token 总数">出 {fmtTok(s.completion_tokens)}</span>
                <span title="前缀缓存命中率" style={{ color: cachePct >= 30 ? 'var(--green-dark, #2a7)' : undefined }}>
                    缓存 {cachePct}%
                </span>
                {s.total_cost_usd ? <span>${s.total_cost_usd.toFixed(3)}</span> : null}
                <span style={{ color: 'var(--ink-sub, #999)' }}>
                    均延迟 {s.avg_latency_ms || 0}ms
                </span>
                {s.error_count ? (
                    <span style={{ color: 'var(--red, #c33)' }}>错误 {s.error_count}（{errPct}%）</span>
                ) : null}
                {err ? <span style={{ color: 'var(--red, #c33)' }}>统计拉取失败</span> : null}
            </div>
            <span style={{ color: 'var(--ink-sub, #999)', fontSize: 10.5 }}>{open ? '▲' : '▼'}</span>

            {open && (
                <div style={{
                    position: 'absolute', bottom: '100%', right: 0, left: 0,
                    background: 'var(--paper-raised, #fff)', border: '1px solid var(--line-soft, #ddd)',
                    borderBottom: 'none', borderTopLeftRadius: 6, borderTopRightRadius: 6,
                    padding: '8px 12px', fontSize: 11, maxHeight: 220, overflowY: 'auto',
                    boxShadow: '0 -2px 8px rgba(0,0,0,0.06)',
                }} onClick={e => e.stopPropagation()}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>今日按调用类型</div>
                    {s.by_call_type && Object.keys(s.by_call_type).length ? (
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                            <thead>
                                <tr style={{ color: 'var(--ink-sub, #999)', textAlign: 'left' }}>
                                    <th style={th}>类型</th><th style={thr}>次数</th>
                                    <th style={thr}>tokens</th><th style={thr}>错误</th>
                                </tr>
                            </thead>
                            <tbody>
                                {Object.entries(s.by_call_type)
                                    .sort((a, b) => (b[1].total_tokens || 0) - (a[1].total_tokens || 0))
                                    .map(([ct, v]) => (
                                        <tr key={ct}>
                                            <td style={td}>{ct}</td>
                                            <td style={tdr}>{v.count}</td>
                                            <td style={tdr}>{fmtTok(v.total_tokens)}</td>
                                            <td style={{ ...tdr, color: v.errors ? 'var(--red,#c33)' : undefined }}>{v.errors || 0}</td>
                                        </tr>
                                    ))}
                            </tbody>
                        </table>
                    ) : <div style={{ color: 'var(--ink-sub, #999)' }}>暂无调用</div>}
                </div>
            )}
        </div>
    )
}

const barStyle = {
    position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 9000,
    height: 22, padding: '0 12px',
    display: 'flex', alignItems: 'center',
    background: 'var(--paper, #faf8f3)', borderTop: '1px solid var(--line-soft, #e2ddd2)',
    fontSize: 11, color: 'var(--ink, #333)', fontFamily: 'var(--font-sans, inherit)',
    userSelect: 'none',
}
const th = { padding: '2px 8px 2px 0', fontWeight: 600 }
const thr = { ...th, textAlign: 'right' }
const td = { padding: '2px 8px 2px 0', borderTop: '1px solid var(--line-soft, #eee)' }
const tdr = { ...td, textAlign: 'right' }
