/**
 * ark 控制台（T36 · 自建前端九域）——嵌在漫剧工作台。
 *
 * 域：概览 / 生成 / 理解 / 对话 / 模型 / 精调部署 / 用量账单 / 账号 / 高级。
 * 生成任务走 /ai-creation/ark（任务队列 → 驱动执行 → 回填资产库 + 账本）。
 * 诚实降级：未开通方舟视觉资源时任务显示「降级」+ 原因（T34 边界），不伪造产物。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { phArk, phArkChat } from '../api.js'
import { toast } from '../lib/toast.js'

const DOMAINS = [
    { key: 'overview', label: '概览', cmd: '' },
    { key: 'gen', label: '生成', cmd: '+gen' },
    { key: 'understanding', label: '理解', cmd: '+understand' },
    { key: 'chat', label: '对话', cmd: '+chat' },
    { key: 'models', label: '模型', cmd: 'models' },
    { key: 'train', label: '精调 / 部署', cmd: 'train' },
    { key: 'usage', label: '用量 / 账单', cmd: 'usage' },
    { key: 'account', label: '账号', cmd: 'auth' },
    { key: 'advanced', label: '高级', cmd: 'api' },
]

const KIND_OPTIONS = [
    { key: 'image', label: '图片 · Seedream' },
    { key: 'video', label: '视频 · Seedance' },
    { key: 'voice', label: '语音 · Kokoro / 云 TTS' },
]

function StatusBadge({ t }) {
    if (t.status === 'queued') return <span className="badge badge-neutral">排队</span>
    if (t.status === 'running') return <span className="badge badge-amber">生成中…</span>
    if (t.status === 'succeeded') return <span className="badge badge-green">成功</span>
    if (t.status === 'skipped' || t.status === 'not_installed') return <span className="badge badge-amber">降级</span>
    if (t.status === 'cancelled') return <span className="badge badge-neutral">已取消</span>
    return <span className="badge badge-red">失败</span>
}

function TaskCard({ t, onFill, onCancel, onRetry }) {
    return (
        <div className="ak-task">
            <div className="h">{t.label} <StatusBadge t={t} />
                {t.filled && <span className="badge badge-green">已回填</span>}
            </div>
            <div className="cmd">{t.cmd}</div>
            {t.reason && <div className="cmd" style={{ color: 'var(--ink-mute)' }}>原因：{t.reason}</div>}
            <div className="btns">
                {(t.status === 'queued' || t.status === 'running') && (
                    <button className="mini-btn" onClick={() => onCancel(t.id)}>取消</button>
                )}
                {(t.status === 'skipped' || t.status === 'not_installed' || t.status === 'failed' || t.status === 'cancelled') && (
                    <button className="mini-btn" onClick={() => onRetry(t.id)}>重试</button>
                )}
                <button className="mini-btn primary" disabled={t.status !== 'succeeded' || t.filled}
                    onClick={() => onFill(t.id)}>回填资产库</button>
            </div>
            {(t.outputs || []).length > 0 && (
                <div className="cmd">产物：{(t.outputs || []).map(o => o.content_id || o.path || '—').join('、').slice(0, 120)}</div>
            )}
        </div>
    )
}

export default function ArkConsole({ open, onClose, bookRoot, packId, ctx = 'drama', initialDomain = 'overview', prefill = null }) {
    const [domain, setDomain] = useState(initialDomain)
    const [tasks, setTasks] = useState([])
    const [usage, setUsage] = useState(null)
    const [models, setModels] = useState(null)
    const [auth, setAuth] = useState(null)
    const [busy, setBusy] = useState(false)
    const [form, setForm] = useState({
        kind: 'image', size: '1080x1920', prompt: '', draft: true, count: 1, voice: '',
        entry_asset_id: '', label: '',
    })
    const [understandOut, setUnderstandOut] = useState('')
    const [chatMsgs, setChatMsgs] = useState([{ who: 'ai', text: 'ark 控制台已就绪。可以问模型与参数、让助手起草提示词，或直接提交生成任务。' }])
    const [chatIn, setChatIn] = useState('')
    const [apiAction, setApiAction] = useState('model.list_foundation_models')
    const [apiParams, setApiParams] = useState('{"PageSize": 3}')
    const [apiOut, setApiOut] = useState('')
    const prefillApplied = useRef(null)

    const loadTasks = useCallback(async () => {
        try { const r = await phArk('list', { book_root: bookRoot || '', limit: 60 }); setTasks(r.tasks || []) } catch { /* 静默 */ }
    }, [bookRoot])

    const loadUsage = useCallback(async () => {
        try { setUsage(await phArk('usage')) } catch { /* 静默 */ }
    }, [])

    useEffect(() => {
        if (!open) { prefillApplied.current = null; return }
        setDomain(initialDomain)
        if (prefill && prefill !== prefillApplied.current) {
            prefillApplied.current = prefill
            setForm(f => ({ ...f, ...prefill }))
        }
        loadTasks(); loadUsage()
    }, [open, initialDomain, prefill, loadTasks, loadUsage])

    useEffect(() => {
        if (!open) return
        const pending = tasks.some(t => t.status === 'queued' || t.status === 'running')
        if (!pending) return
        const iv = setInterval(() => { loadTasks(); loadUsage() }, 1500)
        return () => clearInterval(iv)
    }, [open, tasks, loadTasks, loadUsage])

    useEffect(() => {
        if (!open) return
        if (domain === 'models' && !models) phArk('models').then(setModels).catch(() => {})
        if (domain === 'account' && !auth) phArk('auth').then(setAuth).catch(() => {})
        if (domain === 'usage') loadUsage()
    }, [open, domain, models, auth, loadUsage])

    if (!open) return null

    const submit = async () => {
        if (busy) return
        if (!form.prompt.trim()) { toast('先填提示词 / 文本'); return }
        setBusy(true)
        try {
            const payload = {
                kind: form.kind, prompt: form.prompt, size: form.size, draft: form.draft,
                count: Number(form.count) || 1, voice: form.voice, model: '',
                label: form.label || (form.kind === 'video' ? '生成镜头视频' : form.kind === 'voice' ? '生成语音' : '生成图片'),
                book_root: bookRoot || '', pack_id: packId || '',
                entry_asset_id: form.entry_asset_id || '', ctx,
            }
            const r = await phArk('submit', payload)
            if (!r.ok) { toast(`提交失败：${r.error}`); return }
            toast(`已提交任务：${r.task.label}`)
            await loadTasks(); loadUsage()
        } finally { setBusy(false) }
    }

    const fill = async (id) => {
        const r = await phArk('fill', {}, id)
        if (!r.ok) { toast(r.error); return }
        toast(r.assets_updated ? '已回填资产库' : '任务产物已入库（账本已记）')
        loadTasks(); loadUsage()
    }
    const cancel = async (id) => { const r = await phArk('cancel', {}, id); if (!r.ok) toast(r.error); loadTasks() }
    const retry = async (id) => { const r = await phArk('retry', {}, id); if (!r.ok) toast(r.error); else toast('已重新入队'); loadTasks() }

    const domainNav = (
        <nav className="ak-nav">
            {DOMAINS.map(d => (
                <button key={d.key} className={domain === d.key ? 'on' : ''} onClick={() => setDomain(d.key)}>
                    {d.label}{d.cmd && <span className="cmd">{d.cmd}</span>}
                </button>
            ))}
        </nav>
    )

    const budget = usage?.budget || {}
    const remaining = usage?.remaining || {}
    const overview = (
        <>
            <div className="ak-h">概览</div>
            <div className="ak-sub">ark-cli 自建前端 · 生成 / 理解 / 对话 / 模型 / 精调部署 / 用量账单 / 账号 / 高级</div>
            <div className="ak-grid2">
                <div>
                    <div className="ak-card">
                        <div className="ak-card-t">账号与资源</div>
                        <div className="ak-kv">
                            <span>ark-cli</span><b>{auth?.runner_ok ? `可用 ${auth?.version || ''}` : (auth?.runner_reason || '检测中…')}</b>
                            <span>凭据</span><span>{auth?.credentials_env ? '环境变量已配置' : '未检测到 ARK_API_KEY（驱动将降级 skipped）'}</span>
                            <span>视觉资源</span><span>Seedream / Seedance 需方舟平台开通（T34 边界，当前诚实降级）</span>
                            <span>预算</span><span>生图 ¥{budget.image_month_cny ?? '—'} · 视频 ¥{budget.video_month_cny ?? '—'} · 导演 ${budget.director_month_usd ?? '—'}</span>
                        </div>
                    </div>
                    <div className="ak-card">
                        <div className="ak-card-t">最近任务</div>
                        {tasks.length === 0 ? <div className="ak-sub" style={{ margin: 0 }}>还没有任务——到「生成」域提交，或在工作台步骤里点生成入口。</div>
                            : tasks.slice(0, 5).map(t => <TaskCard key={t.id} t={t} onFill={fill} onCancel={cancel} onRetry={retry} />)}
                    </div>
                </div>
                <div>
                    <div className="ak-card">
                        <div className="ak-card-t">用量与预算</div>
                        <div className="ak-kv">
                            <span>生图余额</span><b>¥{remaining.image_month_cny ?? '—'}</b>
                            <span>视频余额</span><b>¥{remaining.video_month_cny ?? '—'}</b>
                            <span>账本</span><span>{usage?.summary?.total_calls ?? 0} 条（usage_ledger.jsonl 口径）</span>
                        </div>
                        <div className="ak-actions">
                            <button className="mini-btn" onClick={() => setDomain('usage')}>查看账本</button>
                            <button className="mini-btn" onClick={() => setDomain('models')}>模型与参数</button>
                        </div>
                    </div>
                    <div className="ak-card">
                        <div className="ak-card-t">结果预览（回填后）</div>
                        <div className="ak-results">
                            {(tasks.flatMap(t => t.outputs || []).slice(0, 6).map((o, i) => (
                                <div key={i} className="ak-thumb done">{o.kind || '产物'}<br />{(o.content_id || '').slice(0, 10)}</div>
                            )))}
                            {!tasks.some(t => (t.outputs || []).length) && (
                                <><div className="ak-thumb">暂无产物<br />提交生成后在此预览</div><div className="ak-thumb">图片 / 视频<br />结果网格</div></>
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </>
    )

    const gen = (
        <>
            <div className="ak-h">生成（+gen）</div>
            <div className="ak-sub">参数、提交、任务与结果都在这里；产物回填资产库并记账。视频走 draft 两段式控本。</div>
            <div className="ak-grid2">
                <div>
                    <div className="ak-card">
                        <div className="ak-card-t">生成参数</div>
                        <div className="ak-form">
                            <label className="row"><span className="k">类型</span>
                                <select value={form.kind} onChange={e => setForm(f => ({ ...f, kind: e.target.value }))}>
                                    {KIND_OPTIONS.map(k => <option key={k.key} value={k.key}>{k.label}</option>)}
                                </select>
                            </label>
                            {form.kind === 'image' && (
                                <label className="row"><span className="k">尺寸</span>
                                    <input type="text" className="inline" value={form.size} onChange={e => setForm(f => ({ ...f, size: e.target.value }))} />
                                </label>
                            )}
                            {form.kind === 'image' && (
                                <label className="row"><span className="k">张数</span>
                                    <input type="number" min="1" max="4" style={{ width: 70 }} value={form.count}
                                        onChange={e => setForm(f => ({ ...f, count: e.target.value }))} />
                                </label>
                            )}
                            {form.kind === 'voice' && (
                                <label className="row"><span className="k">音色</span>
                                    <select value={form.voice} onChange={e => setForm(f => ({ ...f, voice: e.target.value }))}>
                                        <option value="">默认（中性 · 压抑）</option>
                                        <option value="narrator">旁白</option>
                                        <option value="protagonist">主角</option>
                                    </select>
                                </label>
                            )}
                            <label className="row" style={{ alignItems: 'flex-start' }}>
                                <span className="k">提示词</span>
                                <textarea value={form.prompt} onChange={e => setForm(f => ({ ...f, prompt: e.target.value }))}
                                    placeholder="例如：深夜的旧教室，路灯的光斜切进来，空气里有浮尘……" />
                            </label>
                            {form.kind === 'video' && (
                                <label className="row"><span className="k">选项</span>
                                    <span className="sw"><input type="checkbox" checked={form.draft}
                                        onChange={e => setForm(f => ({ ...f, draft: e.target.checked }))} /> draft 控本（先出 5s 低成本）</span>
                                </label>
                            )}
                            {form.entry_asset_id && (
                                <div className="row"><span className="k">回填目标</span><span className="mono" style={{ fontSize: 11 }}>{form.entry_asset_id}</span></div>
                            )}
                        </div>
                        <div className="ak-actions">
                            <button className="mini-btn primary" disabled={busy} onClick={submit}>提交任务（arkcli +gen）</button>
                            <button className="mini-btn" onClick={() => setDomain('models')}>模型与参数校验</button>
                        </div>
                    </div>
                    <div className="ak-card">
                        <div className="ak-card-t">任务队列</div>
                        {tasks.length === 0 ? <div className="ak-sub" style={{ margin: 0 }}>还没有任务——在上方表单提交。</div>
                            : tasks.map(t => <TaskCard key={t.id} t={t} onFill={fill} onCancel={cancel} onRetry={retry} />)}
                    </div>
                </div>
                <div>
                    <div className="ak-card">
                        <div className="ak-card-t">结果预览（回填后）</div>
                        <div className="ak-results">
                            {(tasks.flatMap(t => t.outputs || []).slice(0, 8).map((o, i) => (
                                <div key={i} className="ak-thumb done">{o.kind || '产物'}<br />{(o.content_id || '').slice(0, 10)}</div>
                            )))}
                            {!tasks.some(t => (t.outputs || []).length) && <div className="ak-thumb">暂无产物<br />提交生成后在此预览</div>}
                        </div>
                    </div>
                    <div className="ak-card">
                        <div className="ak-card-t">预算</div>
                        <div className="ak-kv">
                            <span>生图余额</span><b>¥{remaining.image_month_cny ?? '—'}</b>
                            <span>视频余额</span><b>¥{remaining.video_month_cny ?? '—'}</b>
                        </div>
                        <div className="ak-sub" style={{ margin: '8px 0 0' }}>未开通视觉资源时驱动返回 skipped（不计费），任务留痕可审计。</div>
                    </div>
                </div>
            </div>
        </>
    )

    const understanding = (
        <>
            <div className="ak-h">理解（+understand）</div>
            <div className="ak-sub">字幕打轴 / 语音转写等；本地未装 faster-whisper 时如实降级。</div>
            <div className="ak-card">
                <div className="ak-form">
                    <label className="row"><span className="k">子能力</span>
                        <select><option>字幕打轴（字幕 → 时间戳 srt）</option></select>
                    </label>
                    <label className="row"><span className="k">输入</span>
                        <input type="text" className="inline" placeholder="pack 内 srt 与音频路径（当前无音频资产）" disabled />
                    </label>
                </div>
                <div className="ak-actions">
                    <button className="mini-btn primary" onClick={async () => {
                        const r = await phArk('understand', { action: 'subtitle_align' })
                        setUnderstandOut(JSON.stringify(r, null, 2))
                        toast(r.ok ? '理解完成' : `降级：${r.reason || r.status}`)
                    }}>执行（arkcli +understand）</button>
                </div>
                <textarea className="ak-io" value={understandOut} readOnly placeholder="输出：字幕时间戳 / 转写文本将显示在这里" />
                <div className="ak-sub" style={{ marginTop: 8 }}>字幕优先用 TTS 返回的时间信息；缺失时才走强制对齐（faster-whisper 兜底）。</div>
            </div>
        </>
    )

    const chat = (
        <>
            <div className="ak-h">对话（+chat）</div>
            <div className="ak-sub">用自然语言问模型 / 参数 / 提示词；走本机已配置 LLM。</div>
            <div className="ak-card">
                <div>
                    {chatMsgs.map((m, i) => (
                        <div key={i} className={`ak-msg ${m.who === 'me' ? 'me' : ''}`}>
                            <span className="who">{m.who === 'me' ? '你' : 'ark'}</span>{m.text}
                        </div>
                    ))}
                </div>
                <div className="ak-form" style={{ marginTop: 8 }}>
                    <textarea value={chatIn} onChange={e => setChatIn(e.target.value)} placeholder="例如：这个镜头想更压抑，提示词怎么改？" />
                </div>
                <div className="ak-actions">
                    <button className="mini-btn primary" onClick={async () => {
                        const txt = chatIn.trim()
                        if (!txt) return
                        setChatIn('')
                        const history = chatMsgs.slice(-6).map(m => ({ role: m.who === 'me' ? 'user' : 'assistant', content: m.text }))
                        setChatMsgs(m => [...m, { who: 'me', text: txt }])
                        const r = await phArkChat(txt, history)
                        setChatMsgs(m => [...m, { who: 'ai', text: r.ok ? r.reply : `（失败：${r.error}）` }])
                    }}>发送</button>
                </div>
            </div>
        </>
    )

    const modelsPanel = (
        <>
            <div className="ak-h">模型（models / resources / pricing）</div>
            <div className="ak-sub">静态清单 + 驱动可用性（真实可用以平台开通与 arkcli 输出为准）。</div>
            <div className="ak-card">
                <table className="ak-table">
                    <thead><tr><th>模型</th><th>能力</th><th>价格</th><th>状态</th></tr></thead>
                    <tbody>
                        {(models?.models || []).map(m => (
                            <tr key={m.id}><td className="mono" style={{ fontSize: 11 }}>{m.id}</td><td>{m.cap}</td><td>{m.cost}</td><td>{m.plan}</td></tr>
                        ))}
                    </tbody>
                </table>
                <div className="ak-card-t" style={{ marginTop: 12 }}>驱动可用性</div>
                <div className="ak-kv">
                    {(models?.drivers || []).map(d => (
                        <span key={d.name} style={{ display: 'contents' }}>
                            <span>{d.name}</span>
                            <span>{d.available ? '可用' : `降级：${d.reason}`}</span>
                        </span>
                    ))}
                </div>
                <div className="ak-actions">
                    <button className="mini-btn" onClick={() => phArk('models').then(setModels).catch(() => {})}>刷新</button>
                    <button className="mini-btn" onClick={() => toast('真实核价：arkcli pricing models --modality ComputerVision（需凭据）')}>按模态核价</button>
                </div>
            </div>
        </>
    )

    const trainPanel = (
        <>
            <div className="ak-h">精调 / 部署（train / +deploy / infer）</div>
            <div className="ak-sub">精调任务、自定义模型、endpoint 上线——需官方资源开通，本机当前只读。</div>
            <div className="ak-card">
                <div className="ak-kv">
                    <span>精调任务</span><span>0 个（未创建）· 需训练数据与计费开通</span>
                    <span>自定义模型</span><span>0 个</span>
                    <span>Endpoints</span><span>无（infer endpoint list 空）</span>
                    <span>开通路径</span><span>arkcli train finetune create / +deploy --dry-run → 控制台开通后执行</span>
                </div>
                <div className="ak-actions">
                    <button className="mini-btn" onClick={() => setDomain('account')}>查看账号与套餐</button>
                    <button className="mini-btn" disabled>创建精调（需开通）</button>
                </div>
            </div>
        </>
    )

    const usagePanel = (
        <>
            <div className="ak-h">用量 / 账单（usage / billing）</div>
            <div className="ak-sub">与 llm_calls 同款口径 · 媒体账本按币种分桶。</div>
            <div className="ak-card">
                <div className="ak-kv">
                    <span>生图余额</span><b>¥{remaining.image_month_cny ?? '—'}</b>
                    <span>视频余额</span><b>¥{remaining.video_month_cny ?? '—'}</b>
                    <span>账本行数</span><span>{usage?.summary?.total_calls ?? 0}</span>
                </div>
                <table className="ak-table" style={{ marginTop: 10 }}>
                    <thead><tr><th>时间</th><th>驱动</th><th>能力</th><th>状态</th><th>金额</th></tr></thead>
                    <tbody>
                        {(usage?.rows || []).slice(0, 20).map((r, i) => (
                            <tr key={i}><td>{(r.ts || '').slice(5, 16)}</td><td>{r.driver}</td><td>{r.capability}</td>
                                <td>{r.status}</td><td>{r.est_cost ? `¥${r.est_cost}` : '—'}</td></tr>
                        ))}
                        {(!usage?.rows || !usage.rows.length) && <tr><td colSpan="5" style={{ color: 'var(--ink-mute)' }}>暂无记账（生成回填后累计）</td></tr>}
                    </tbody>
                </table>
                <div className="ak-actions"><button className="mini-btn" onClick={loadUsage}>刷新（usage stats）</button></div>
            </div>
        </>
    )

    const accountPanel = (
        <>
            <div className="ak-h">账号（auth / profile / doctor）</div>
            <div className="ak-sub">登录态与健康检查；密钥由 arkcli 管理，本项目不代管。</div>
            <div className="ak-card">
                <div className="ak-kv">
                    <span>ark-cli</span><span>{auth ? (auth.runner_ok ? `可用 · ${auth.version || ''}` : auth.runner_reason) : '检测中…'}</span>
                    <span>可执行文件</span><span className="mono" style={{ fontSize: 11, wordBreak: 'break-all' }}>{auth?.executable || '—'}</span>
                    <span>环境凭据</span><span>{auth?.credentials_env ? 'ARK_API_KEY 已配置' : '未配置（生成将降级）'}</span>
                    <span>doctor</span><span>{auth?.doctor || '—'}</span>
                </div>
                <div className="ak-actions">
                    <button className="mini-btn" onClick={() => phArk('auth').then(setAuth).catch(() => {})}>运行 doctor（刷新）</button>
                    <button className="mini-btn" onClick={() => toast('真机：arkcli auth login volc-sso（打开浏览器授权）')}>重新登录</button>
                </div>
            </div>
        </>
    )

    const advancedPanel = (
        <>
            <div className="ak-h">高级（api explorer）</div>
            <div className="ak-sub">原样透传 arkcli 的 api 动作（高级用法，出错风险自负）。</div>
            <div className="ak-card">
                <div className="ak-form">
                    <label className="row"><span className="k">action</span>
                        <input type="text" className="inline" value={apiAction} onChange={e => setApiAction(e.target.value)} />
                    </label>
                    <textarea className="ak-io" value={apiParams} onChange={e => setApiParams(e.target.value)} />
                </div>
                <div className="ak-actions">
                    <button className="mini-btn primary" onClick={async () => {
                        let params = null
                        try { params = apiParams.trim() ? JSON.parse(apiParams) : null } catch { toast('params 不是合法 JSON'); return }
                        const r = await phArk('api', { action: apiAction, params })
                        setApiOut(r.stdout || r.error || JSON.stringify(r, null, 2))
                        toast(r.ok ? '已执行' : `失败：${r.error || r.status}`)
                    }}>执行</button>
                </div>
                <textarea className="ak-io" value={apiOut} readOnly placeholder="输出（stdout JSON）" />
            </div>
        </>
    )

    const panels = { overview, gen, understanding, chat, models: modelsPanel, train: trainPanel, usage: usagePanel, account: accountPanel, advanced: advancedPanel }

    return (
        <div className="ak-mask on">
            <div className="ak">
                <div className="ak-head">
                    <span className="t">ark 控制台</span>
                    <span className="sub">ark-cli 自建前端 · 生成 / 理解 / 对话 / 模型 / 精调部署 / 用量账单 / 账号 / 高级</span>
                    <span className="ak-auth">{auth?.credentials_env ? 'auth · 环境凭据可用' : 'auth · 未配置（诚实降级）'}</span>
                    <span className="grow" />
                    <button className="btn btn-small" onClick={onClose}>关闭</button>
                </div>
                <div className="ak-body">
                    {domainNav}
                    <div className="ak-main">{panels[domain] || overview}</div>
                </div>
            </div>
        </div>
    )
}
