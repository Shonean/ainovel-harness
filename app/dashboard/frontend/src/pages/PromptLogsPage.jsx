// 日志中心 —— 任务日志 / LLM 调用 全量查看（训练模块已移除，仅保留榨干/模板库相关日志）
import { useEffect, useState } from 'react'
import {
    phListLlmLogs, phListLlmLogDates,
    phListLogs, phGetLog,
} from '../api.js'

const EVENT_CATEGORIES = [
    { key: 'tasks', label: '任务日志' },
    { key: 'llm', label: 'LLM 调用' },
]

function formatTime(isoStr) {
    if (!isoStr) return ''
    try {
        const d = new Date(isoStr)
        return d.toLocaleString('zh-CN', { hour12: false })
    } catch {
        return isoStr
    }
}

function formatBytes(bytes) {
    if (!bytes) return '0 B'
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

// ============================================================
// 主组件
// ============================================================

export default function PromptLogsPage() {
    const [activeCategory, setActiveCategory] = useState('tasks')
    const [sourceFilter, setSourceFilter] = useState('')

    return (
        <div className="prompt-logs-page">
            <div className="plp-header">
                <h2>日志中心</h2>
                <p className="plp-subtitle">
                    榨干提取 / 情节模板库 / LLM 调用 全量日志 —— 每次任务、每次 LLM 调用都可追溯
                </p>
            </div>

            {/* 分类 Tab */}
            <div className="plp-categories">
                {EVENT_CATEGORIES.map(c => (
                    <button
                        key={c.key}
                        className={`plp-cat-btn ${activeCategory === c.key ? 'active' : ''}`}
                        onClick={() => setActiveCategory(c.key)}
                    >
                        {c.label}
                    </button>
                ))}
            </div>

            {/* 过滤栏 */}
            {activeCategory !== 'llm' && (
                <div className="plp-filter-bar">
                    <input
                        type="text"
                        placeholder="按来源文档名过滤..."
                        value={sourceFilter}
                        onChange={e => setSourceFilter(e.target.value)}
                        className="plp-filter-input"
                    />
                </div>
            )}

            {/* 内容区 */}
            <div className="plp-content">
                {activeCategory === 'tasks' && (
                    <GenericLogsView category="tasks" sourceFilter={sourceFilter} />
                )}
                {activeCategory === 'llm' && <LlmLogsView />}
            </div>
        </div>
    )
}

// ============================================================
// 任务日志视图（tasks/*.jsonl，含榨干任务日志）
// ============================================================

function GenericLogsView({ category, sourceFilter }) {
    const [logs, setLogs] = useState([])
    const [loading, setLoading] = useState(true)
    const [selectedPath, setSelectedPath] = useState(null)
    const [events, setEvents] = useState([])
    const [detailLoading, setDetailLoading] = useState(false)

    const load = () => {
        setLoading(true)
        phListLogs(category, 50, sourceFilter)
            .then(r => { setLogs(r || []) })
            .catch(() => { setLogs([]) })
            .finally(() => setLoading(false))
    }

    useEffect(() => { load() }, [category, sourceFilter])

    const selectLog = (filepath) => {
        setSelectedPath(filepath)
        setDetailLoading(true)
        setEvents([])
        phGetLog(filepath, '', 500)
            .then(r => { setEvents(r || []) })
            .catch(() => {})
            .finally(() => setDetailLoading(false))
    }

    return (
        <div className="plp-two-col">
            <div className="plp-list-panel">
                <div className="plp-list-header">
                    <span>日志文件 ({logs.length})</span>
                    <button className="plp-refresh-btn" onClick={load}>刷新</button>
                </div>
                {loading ? (
                    <div className="plp-empty">加载中...</div>
                ) : logs.length === 0 ? (
                    <div className="plp-empty">暂无日志</div>
                ) : (
                    <div className="plp-list">
                        {logs.map(l => (
                            <div
                                key={l.filepath}
                                className={`plp-list-item ${selectedPath === l.filepath ? 'selected' : ''}`}
                                onClick={() => selectLog(l.filepath)}
                            >
                                <div className="plp-item-title">
                                    <span className={`plp-status-badge status-${l.status || 'unknown'}`}>
                                        {l.status || 'unknown'}
                                    </span>
                                    <span className="plp-item-name">{l.filename}</span>
                                </div>
                                <div className="plp-item-meta">
                                    <span>{l.category}</span>
                                    <span>{formatBytes(l.size)}</span>
                                    <span>⏱ {formatTime(l.modified_at)}</span>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
            <div className="plp-detail-panel">
                {!selectedPath ? (
                    <div className="plp-empty">← 选择一个日志文件查看</div>
                ) : detailLoading ? (
                    <div className="plp-empty">加载中...</div>
                ) : (
                    <div className="plp-raw-view">
                        <div className="plp-raw-header">
                            共 {events.length} 条事件
                        </div>
                        <pre className="plp-json-preview">
                            {JSON.stringify(events, null, 2)}
                        </pre>
                    </div>
                )}
            </div>
        </div>
    )
}

// ============================================================
// LLM 调用日志视图
// ============================================================

function LlmLogsView() {
    const [dates, setDates] = useState([])
    const [selectedDate, setSelectedDate] = useState('')
    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(false)
    const [statusFilter, setStatusFilter] = useState('')
    const [callType, setCallType] = useState('')

    useEffect(() => {
        phListLlmLogDates().then(r => {
            const list = r || []
            setDates(list)
            if (list.length > 0 && !selectedDate) {
                setSelectedDate(list[0])
            }
        }).catch(() => {})
    }, [])

    const load = () => {
        if (!selectedDate) return
        setLoading(true)
        phListLlmLogs(selectedDate, 200, statusFilter, callType)
            .then(r => { setData(r) })
            .catch(() => { setData(null) })
            .finally(() => setLoading(false))
    }

    useEffect(() => {
        if (selectedDate) load()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedDate, statusFilter, callType])

    const stats = data?.stats || {}
    const events = data?.events || []

    return (
        <div className="plp-llm-view">
            {/* 顶部控制栏 */}
            <div className="plp-llm-controls">
                <div className="plp-control-group">
                    <label>日期</label>
                    <select
                        value={selectedDate}
                        onChange={e => setSelectedDate(e.target.value)}
                        className="plp-filter-select"
                    >
                        {dates.map(d => (
                            <option key={d} value={d}>{d}</option>
                        ))}
                        {dates.length === 0 && <option value="">暂无数据</option>}
                    </select>
                </div>
                <div className="plp-control-group">
                    <label>状态</label>
                    <select
                        value={statusFilter}
                        onChange={e => setStatusFilter(e.target.value)}
                        className="plp-filter-select"
                    >
                        <option value="">全部</option>
                        <option value="success">成功</option>
                        <option value="error">失败</option>
                    </select>
                </div>
                <div className="plp-control-group">
                    <label>类型</label>
                    <select
                        value={callType}
                        onChange={e => setCallType(e.target.value)}
                        className="plp-filter-select"
                    >
                        <option value="">全部</option>
                        {stats.by_call_type && Object.keys(stats.by_call_type).map(ct => (
                            <option key={ct} value={ct}>{ct}</option>
                        ))}
                    </select>
                </div>
                <button className="plp-refresh-btn" onClick={load}>刷新</button>
            </div>

            {/* 统计卡片 */}
            {data && (
                <div className="plp-summary-cards">
                    <div className="plp-stat-card">
                        <div className="plp-stat-label">总调用数</div>
                        <div className="plp-stat-value">{stats.total_calls?.toLocaleString() || 0}</div>
                    </div>
                    <div className="plp-stat-card">
                        <div className="plp-stat-label">Token 总量</div>
                        <div className="plp-stat-value">{stats.total_tokens?.toLocaleString() || 0}</div>
                    </div>
                    <div className="plp-stat-card">
                        <div className="plp-stat-label">平均延迟</div>
                        <div className="plp-stat-value">{stats.avg_latency_ms || 0} ms</div>
                    </div>
                    <div className="plp-stat-card">
                        <div className="plp-stat-label">错误率</div>
                        <div className={`plp-stat-value ${stats.error_rate > 0.01 ? 'error' : ''}`}>
                            {((stats.error_rate || 0) * 100).toFixed(2)}%
                        </div>
                    </div>
                    <div className="plp-stat-card">
                        <div className="plp-stat-label">错误数</div>
                        <div className={`plp-stat-value ${stats.error_count ? 'error' : ''}`}>
                            {stats.error_count || 0}
                        </div>
                    </div>
                </div>
            )}

            {/* 按调用类型分布 */}
            {stats.by_call_type && Object.keys(stats.by_call_type).length > 0 && (
                <div className="plp-llm-type-breakdown">
                    <h4>按调用类型分布</h4>
                    <div className="plp-type-grid">
                        {Object.entries(stats.by_call_type).map(([type, info]) => (
                            <div key={type} className="plp-type-card">
                                <div className="plp-type-name">{type || 'unknown'}</div>
                                <div className="plp-type-stats">
                                    <span>调用 {info.count}</span>
                                    <span>Token {(info.total_tokens || 0).toLocaleString()}</span>
                                    {info.errors > 0 && (
                                        <span className="error">错误 {info.errors}</span>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* 调用列表 */}
            <div className="plp-llm-list">
                <h4>调用记录（{events.length} / {data?.total || 0}）</h4>
                {loading ? (
                    <div className="plp-empty">加载中...</div>
                ) : events.length === 0 ? (
                    <div className="plp-empty">暂无 LLM 调用记录</div>
                ) : (
                    <div className="plp-llm-table">
                        <div className="plp-llm-row header">
                            <span>时间</span>
                            <span>模型</span>
                            <span>类型</span>
                            <span>状态</span>
                            <span>Token</span>
                            <span>延迟</span>
                            <span>提示词</span>
                        </div>
                        {events.map((evt, i) => (
                            <LlmCallRow key={i} evt={evt} />
                        ))}
                    </div>
                )}
            </div>
        </div>
    )
}

function LlmCallRow({ evt }) {
    const [expanded, setExpanded] = useState(false)
    const isError = evt.status === 'error'

    return (
        <>
            <div
                className={`plp-llm-row ${isError ? 'error-row' : ''}`}
                onClick={() => setExpanded(!expanded)}
            >
                <span className="plp-llm-time">{formatTime(evt.ts).split(' ')[1] || ''}</span>
                <span className="plp-llm-model">{evt.model || '-'}</span>
                <span className="plp-llm-type">{evt.call_type || '-'}</span>
                <span>
                    <span className={`plp-status-badge status-${evt.status}`}>
                        {evt.status}
                    </span>
                </span>
                <span className="plp-llm-tokens">{evt.total_tokens?.toLocaleString() || 0}</span>
                <span className="plp-llm-latency">{evt.latency_ms} ms</span>
                <span className="plp-llm-prompt-preview">
                    {evt.error
                        ? `${evt.error.substring(0, 50)}`
                        : `${evt.prompt_len || 0} 字 → ${evt.completion_len || 0} 字`}
                </span>
            </div>
            {expanded && (
                <div className="plp-llm-expanded">
                    <pre className="plp-json-preview">
                        {JSON.stringify(evt, null, 2)}
                    </pre>
                </div>
            )}
        </>
    )
}
