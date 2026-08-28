import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
    createProject, registerProject, phAiArcChat, phAiInitStatus,
    fetchApiLibrary, applyApiPreset,
    phAiPendingGet, phAiPendingApprove, phAiPendingReject,
    phAiExtractCards,
    phAiMemoryList, phAiMemoryAdd, phAiMemoryDelete,
    phAiChatSessionsGet, phAiChatSessionsSave,
} from '../api.js'
import InitAssistantProgress from '../components/InitAssistantProgress.jsx'
import ChatWindow from '../components/ChatWindow.jsx'
import { useElements, ElementsPanelContent } from '../components/ElementsPanel.jsx'

const welcomeMessage = {
  role: 'assistant',
  content: `你好！我是你的初始化创作助手。（v2 卡片版）\n\n我可以帮你：\n1. 定一个吸引人的书名\n2. 选择故事类型和风格\n3. 设定世界观和主角\n4. 生成角色和元素\n5. 规划开篇剧情\n\n你可以直接告诉我你的想法，或者点击下方的快捷按钮开始。`,
  isWelcome: true,
}

const quickReplies = [
  { text: '帮我定一个玄幻书名', stage: 'name' },
  { text: '设定一个修仙世界观', stage: 'settings' },
  { text: '生成开篇剧情', stage: 'arc' },
]

/**
 * 新建书 / 续接初始化：**创作助手直接参与建书**（对话与建书同时进行）。
 *
 * 新建书：进入页面即建裸书 → 立即嵌完整创作助手（init 模式，裁剪 l2-l5 工具）——
 * 助手引导书名/类型/基本设定/开篇情节，决策内容直接落盘到这本书。
 * 对话确认后 registerProject 入库 → 进工作台无缝衔接。
 *
 * 续接初始化：URL 带 ?bookRoot=xxx → 跳过建书，直接加载已有书的初始化助手。
 */
// 生成唯一ID
const genId = () => Math.random().toString(36).slice(2, 10)

// 本地存储键名
const STORAGE_KEY = 'init_chat_sessions'

// 加载会话（过滤无效数据）
function loadSessions() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY)
        if (!raw) return null
        const parsed = JSON.parse(raw)
        if (!Array.isArray(parsed)) return null
        // 过滤掉无效会话（必须有 id 和 messages）
        return parsed.filter(s => s && s.id && Array.isArray(s.messages))
    } catch { return null }
}

// 保存会话（localStorage 即时 + 服务端异步备份）
function saveSessions(sessions, bookRoot) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions)) } catch {}
    if (bookRoot) {
        phAiChatSessionsSave(bookRoot, sessions).catch(() => {})
    }
}

// 创建默认会话
function createDefaultSession() {
    return { id: genId(), name: '新对话', messages: [welcomeMessage] }
}

export default function CreateBookPage() {
    const navigate = useNavigate()
    const [searchParams] = useSearchParams()
    const existingBookRoot = searchParams.get('bookRoot') || ''

    const [bookRoot, setBookRoot] = useState(existingBookRoot)
    const bookRootRef = useRef(bookRoot)
    bookRootRef.current = bookRoot
    const [bookName, setBookName] = useState('')
    const [chatInput, setChatInput] = useState('')
    const [chatBusy, setChatBusy] = useState(false)
    const [pendingConfirm, setPendingConfirm] = useState(null)
    const [submitting, setSubmitting] = useState(false)
    const [errorMsg, setErrorMsg] = useState('')
    const chatBoxRef = useRef(null)

    // 多会话管理（避免 TDZ：用 useMemo 计算初始值）
    const initialSessions = (() => {
        const saved = loadSessions()
        return saved?.length ? saved : [createDefaultSession()]
    })()
    const [sessions, setSessions] = useState(initialSessions)
    const [currentSessionId, setCurrentSessionId] = useState(() => {
        return initialSessions[0]?.id || ''
    })
    const [sessionListOpen, setSessionListOpen] = useState(false)

    // 当前会话的消息
    const messages = sessions.find(s => s.id === currentSessionId)?.messages || [welcomeMessage]

    // 更新当前会话消息
    const updateMessages = useCallback((msgs) => {
        setSessions(prev => {
            const updated = prev.map(s =>
                s.id === currentSessionId ? { ...s, messages: msgs } : s
            )
            saveSessions(updated, bookRootRef.current)
            return updated
        })
    }, [currentSessionId])

    // 全局卡片
    const [pendingCards, setPendingCards] = useState([])
    const [cardsPanelOpen, setCardsPanelOpen] = useState(true)
    const [selectedMsgs, setSelectedMsgs] = useState([])  // 选中的消息索引数组
    const [extracting, setExtracting] = useState(false)
    const [editingCard, setEditingCard] = useState(null)  // 正在编辑的卡片（用 id 字段匹配）

    // 已通过审核的元素（使用共享 Hook）
    const { elements, setElements, reload: reloadElements } = useElements(bookRoot)
    const [elementsPanelOpen, setElementsPanelOpen] = useState(true)

    // 记忆列表
    const [memList, setMemList] = useState([])
    const [memInput, setMemInput] = useState('')

    // API 预设
    const [apiLib, setApiLib] = useState(null)
    const [presetOpen, setPresetOpen] = useState(false)
    const [presetSwitching, setPresetSwitching] = useState(false)
    const currentPreset = (apiLib?.text_presets && Array.isArray(apiLib.text_presets))
        ? (apiLib.text_presets.find(p => p.is_current) || apiLib.text_presets[0] || null)
        : null

    const applyPreset = useCallback(async (id) => {
        if (presetSwitching) return
        setPresetSwitching(true)
        try {
            await applyApiPreset(id)
            const lib = await fetchApiLibrary()
            if (lib && lib.text_presets) {
                setApiLib(lib)
            }
        } catch (e) {
            console.error('切换预设失败', e)
        } finally {
            setPresetSwitching(false)
            setPresetOpen(false)
        }
    }, [presetSwitching])

    // 新建会话
    const createSession = useCallback(() => {
        const newSession = createDefaultSession()
        setSessions(prev => {
            const updated = [newSession, ...prev]
            saveSessions(updated, bookRootRef.current)
            return updated
        })
        setCurrentSessionId(newSession.id)
        setSessionListOpen(false)
    }, [])

    // 切换会话
    const switchSession = useCallback((id) => {
        setCurrentSessionId(id)
        setSessionListOpen(false)
    }, [])

    // 删除会话
    const deleteSession = useCallback((id) => {
        setSessions(prev => {
            const updated = prev.filter(s => s.id !== id)
            if (updated.length === 0) {
                const def = createDefaultSession()
                updated.push(def)
                setCurrentSessionId(def.id)
            } else if (id === currentSessionId) {
                setCurrentSessionId(updated[0].id)
            }
            saveSessions(updated, bookRootRef.current)
            return updated
        })
    }, [currentSessionId])

    // 清空当前会话
    const clearSession = useCallback(() => {
        setSessions(prev => {
            const updated = prev.map(s =>
                s.id === currentSessionId ? { ...s, messages: [welcomeMessage] } : s
            )
            saveSessions(updated, bookRootRef.current)
            return updated
        })
    }, [currentSessionId])

    // 重命名会话
    const renameSession = useCallback((id, name) => {
        setSessions(prev => {
            const updated = prev.map(s => s.id === id ? { ...s, name } : s)
            saveSessions(updated, bookRootRef.current)
            return updated
        })
    }, [])

    // 加载待审核卡片（必须在 extractFromSelected 之前声明）
    const loadPendingCards = useCallback(async () => {
        if (!bookRoot) { console.log('[DEBUG] loadPendingCards: no bookRoot'); return }
        try {
            console.log('[DEBUG] loadPendingCards: calling API with bookRoot=', bookRoot)
            const res = await phAiPendingGet(bookRoot)
            console.log('[DEBUG] loadPendingCards: res=', JSON.stringify(res)?.slice(0, 200))
            setPendingCards(res?.items || [])
        } catch (e) {
            console.error('[DEBUG] 加载待审核卡片失败', e)
        }
    }, [bookRoot])

    // 加载记忆列表
    const loadMemory = useCallback(async () => {
        if (!bookRoot) return
        try {
            const res = await phAiMemoryList(bookRoot)
            setMemList(res?.items || [])
        } catch (e) {
            console.error('加载记忆失败', e)
        }
    }, [bookRoot])

    // 从选中消息提取卡片
    const extractFromSelected = useCallback(async () => {
        if (!bookRoot || selectedMsgs.length === 0) return
        setExtracting(true)
        try {
            const texts = selectedMsgs.map(idx => {
                const msg = messages[idx]
                return msg ? `[${msg.role === 'user' ? '用户' : 'AI'}]: ${msg.content}` : ''
            }).filter(Boolean)
            const combinedText = texts.join('\n\n')
            await phAiExtractCards(bookRoot, combinedText)
            await loadPendingCards()
            setSelectedMsgs([])
        } catch (e) {
            console.error('提取卡片失败', e)
        } finally {
            setExtracting(false)
        }
    }, [bookRoot, selectedMsgs, messages, loadPendingCards])

    // 切换消息选中状态
    const toggleMsgSelect = useCallback((idx) => {
        setSelectedMsgs(prev =>
            prev.includes(idx) ? prev.filter(i => i !== idx) : [...prev, idx]
        )
    }, [])

    // 全选/取消全选
    const toggleSelectAll = useCallback(() => {
        setSelectedMsgs(prev =>
            prev.length === messages.length ? [] : messages.map((_, i) => i)
        )
    }, [messages.length])

    // 审核卡片（支持编辑后提交）
    const approveCard = useCallback(async (pid, edits = null) => {
        if (!bookRoot) return
        try {
            await phAiPendingApprove(bookRoot, pid, edits)
            await loadPendingCards()
        } catch (e) {
            console.error('审核失败', e)
        }
    }, [bookRoot, loadPendingCards])

    const rejectCard = useCallback(async (pid) => {
        if (!bookRoot) return
        try {
            await phAiPendingReject(bookRoot, pid)
            await loadPendingCards()
        } catch (e) {
            console.error('拒绝失败', e)
        }
    }, [bookRoot, loadPendingCards])

    useEffect(() => {
        const loadApiLib = async (retryCount = 0) => {
            try {
                const lib = await fetchApiLibrary()
                if (lib && lib.text_presets && Array.isArray(lib.text_presets)) {
                    setApiLib(lib)
                } else if (retryCount < 3) {
                    setTimeout(() => loadApiLib(retryCount + 1), 1000)
                }
            } catch (e) {
                if (retryCount < 3) {
                    setTimeout(() => loadApiLib(retryCount + 1), 1000)
                }
            }
        }
        loadApiLib()
    }, [])

    useEffect(() => {
        if (bookRoot) {
            loadPendingCards()
            loadMemory()
            reloadElements()
            // 从服务端加载对话记录（优先级高于 localStorage）
            phAiChatSessionsGet(bookRoot).then(res => {
                const serverSessions = res?.sessions
                if (Array.isArray(serverSessions) && serverSessions.length > 0) {
                    const valid = serverSessions.filter(s => s && s.id && Array.isArray(s.messages))
                    if (valid.length > 0) {
                        setSessions(valid)
                        setCurrentSessionId(prev => valid.find(s => s.id === prev)?.id || valid[0].id)
                        // 同步写回 localStorage
                        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(valid)) } catch {}
                    }
                }
            }).catch(() => {})
        }
    }, [bookRoot, loadPendingCards, loadMemory, reloadElements])

    // 续接初始化：加载已有书的信息
    const loadExistingBook = useCallback(async (root) => {
        if (!root) return
        setSubmitting(true)
        try {
            const initStatus = await phAiInitStatus(root).catch(() => null)
            if (initStatus?.initialized) {
                // 已初始化完成，直接进工作台
                navigate('/ai-creation', { replace: true })
                return
            }
            // 从URL传入的书名
            const titleFromUrl = root.split(/[/\\]/).pop() || '未命名书'
            setBookName(titleFromUrl)
        } catch (e) {
            setErrorMsg(e.message || '加载书失败')
        } finally {
            setSubmitting(false)
        }
    }, [navigate])  // eslint-disable-line react-hooks/exhaustive-deps

    // 进入页面：立即建裸书（对话与建书同时进行）
    const createBook = useCallback(async (name) => {
        setSubmitting(true)
        setErrorMsg('')
        try {
            const res = await createProject(name || '未命名书', {
                project: { title: name || '未命名书', genre: '', target_words: 0, target_chapters: 0, one_liner: '', core_conflict: '', target_reader: '', platform: '' },
                protagonist: {}, relationship: {}, golden_finger: {}, world: {},
                constraints: { hard_constraints: [] },
            }, null, null, true)
            if (!res?.project_root) throw new Error('建书失败：未返回书目录')
            setBookRoot(res.project_root)
            setBookName(res.name || name)
            // 更新URL添加bookRoot参数，刷新后可恢复
            const url = new URL(window.location)
            url.searchParams.set('bookRoot', res.project_root)
            window.history.replaceState({}, '', url)
            // 建书后创作助手立即开场引导
            handleChat('我新开了一本书，请你作为初始化创作助手，帮我一起把这本书的名字、类型、故事设定和开篇剧情定下来。', res.project_root)
        } catch (e) {
            setErrorMsg(e.message || '建书失败')
        } finally {
            setSubmitting(false)
        }
    }, [])  // eslint-disable-line react-hooks/exhaustive-deps

    // handleChat 需要 messages，用 ref 避免闭包问题
    const messagesRef = useRef(messages)
    messagesRef.current = messages

    const handleChat = useCallback(async (text, explicitRoot) => {
        const root = explicitRoot || bookRootRef.current
        if (!text.trim() || chatBusy || !root) return
        const history = [...messagesRef.current, { role: 'user', content: text }]
        updateMessages(history)
        setChatBusy(true)
        try {
            const r = await phAiArcChat(root, '', history, false, null, null, 'init')
            if (r?.ok === false) {
                updateMessages([...history, { role: 'assistant', content: `（错误：${r.error}）` }])
                return
            }
            const evs = r.tool_events || []
            updateMessages([...history, { role: 'assistant', content: r.reply || '', tool_events: evs }])
            if (r.changed) setErrorMsg('')
            if (r.pending?.length) setPendingConfirm(r.pending[0])
            // 自动从 AI 回复中提取待审核卡片（arc_chat 不会自动生成，需手动触发）
            if (r.reply) {
                phAiExtractCards(root, `[AI]: ${r.reply}`).catch(() => {})
            }
            setTimeout(() => loadPendingCards(), 500)
            setTimeout(() => chatBoxRef.current?.scrollTo?.({ top: 99999 }), 50)
        } catch (e) {
            updateMessages([...history, { role: 'assistant', content: `（对话失败：${e.message || e}）` }])
        } finally {
            setChatBusy(false)
        }
    }, [chatBusy, loadPendingCards, updateMessages])

    const handleConfirm = useCallback(async () => {
        if (!pendingConfirm) return
        const root = bookRootRef.current
        setChatBusy(true)
        try {
            const r = await fetch('/api/prompt-harness/ai-creation/arc/chat/apply', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ book_root: root, arc_id: '', tool: pendingConfirm.tool, args: pendingConfirm.args || {}, mode: 'init' }),
            }).then(res => res.json())
            if (r?.ok) {
                const evs = [pendingConfirm]
                updateMessages([...messagesRef.current, { role: 'assistant', content: '（已执行）', tool_events: evs }])
            }
        } catch (e) {
            updateMessages([...messagesRef.current, { role: 'assistant', content: `（执行失败：${e.message || e}）` }])
        } finally {
            setPendingConfirm(null)
            setChatBusy(false)
            setTimeout(() => loadPendingCards(), 100)
        }
    }, [pendingConfirm, updateMessages, loadPendingCards])

    // 完成初始化：入库 → 进工作台
    const finishInit = useCallback(async () => {
        const root = bookRootRef.current
        if (!root) return
        setSubmitting(true)
        try {
            await registerProject(root)
            navigate('/ai-creation', { replace: true })
        } catch (e) {
            setErrorMsg(e.message || '入库失败')
            setSubmitting(false)
        }
    }, [navigate])

    // 自动开场：有 bookRoot 参数 → 续接初始化；无 → 新建书
    useEffect(() => {
        if (existingBookRoot) {
            loadExistingBook(existingBookRoot)
        } else if (!bookRoot && !submitting && !errorMsg) {
            createBook('')
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    return (
        <div className="wizard-page" style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
            {/* 顶部工具栏 */}
            <header style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px',
                paddingRight: 276, /* 预留全局「应用新代码」按钮空间：按钮宽120px + right:16px + 140px安全边距 */
                borderBottom: '1px solid var(--line)', background: 'var(--paper-raised)',
                flexShrink: 0, maxWidth: 'none',
            }}>
                <button className="btn btn-small" onClick={() => navigate('/', { replace: true })}>
                    ⇦ 返回
                </button>
                <button
                    className="btn btn-small"
                    onClick={async () => {
                        const root = bookRootRef.current
                        if (!root) return
                        try {
                            await registerProject(root)
                            navigate('/ai-creation', { replace: true })
                        } catch (e) {
                            setErrorMsg(e.message || '切换失败')
                        }
                    }}
                    disabled={!bookRoot}
                >
                    进入工作台
                </button>
                <h1 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>
                    {existingBookRoot ? '续接初始化' : '新建书'}
                </h1>
                {/* API 预设切换器 - 放在标题后面 */}
                <div className="wb-presetwrap" style={{ flexShrink: 0 }} onClick={e => e.stopPropagation()}>
                    <button type="button" className="wb-presetbtn"
                        onClick={() => setPresetOpen(!presetOpen)}
                        title="切换文字模型预设">
<span className="pname">{presetSwitching ? '切换中…' : (currentPreset?.name || '模型预设')}</span>
                        <span className="parrow">▾</span>
                    </button>
                    {presetOpen && (
                        <div className="wb-presetmenu">
                            <div className="wb-pmtitle">文字模型预设</div>
                            {(apiLib?.text_presets || []).filter(Boolean).map(p => (
                                <div key={p.id}
                                    className={`wb-pmitem ${p.is_current ? 'current' : ''} ${presetSwitching ? 'disabled' : ''}`}
                                    onClick={() => !presetSwitching && !p.is_current && applyPreset(p.id)}>
                                    <span className="pmname">
                                        <span>{p.name || '未命名预设'}</span>
                                        {p.is_current && <span className="pmmark">✓ 当前</span>}
                                    </span>
                                    <span className="pmmodel">{p.fields?.ARK_MODEL_PRO || ''}</span>
                                </div>
                            ))}
                            <div className="wb-pmfooter">
                                <a href="#/api-presets" onClick={() => setPresetOpen(false)}>管理全部预设 →</a>
                            </div>
                        </div>
                    )}
                </div>
                {/* 聊天会话管理 */}
                <div style={{ position: 'relative' }}>
                    <button
                        className="btn btn-small"
                        onClick={() => setSessionListOpen(!sessionListOpen)}
                    >
                        {sessions.find(s => s.id === currentSessionId)?.name || '对话'}
                        <span style={{ marginLeft: 4, fontSize: 11, color: 'var(--ink-sub)' }}>▾</span>
                    </button>
                    {sessionListOpen && (
                        <div style={{
                            position: 'absolute', top: '100%', left: 0, marginTop: 4,
                            background: 'var(--paper-raised)', border: '1px solid var(--line)',
                            borderRadius: 8, boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
                            minWidth: 200, maxHeight: 300, overflowY: 'auto', zIndex: 100,
                        }}>
                            <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--line)', fontWeight: 600, fontSize: 13 }}>
                                对话列表
                            </div>
                            {sessions.filter(Boolean).map(s => (
                                <div
                                    key={s.id}
                                    style={{
                                        padding: '8px 12px', cursor: 'pointer',
                                        background: s.id === currentSessionId ? 'var(--dai-wash)' : 'transparent',
                                        borderBottom: '1px solid var(--line)',
                                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                    }}
                                    onClick={() => switchSession(s.id)}
                                >
                                    <span style={{ fontSize: 13, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {s.name || '未命名对话'}
                                        <span style={{ fontSize: 11, color: 'var(--ink-sub)', marginLeft: 6 }}>
                                            ({(s.messages?.length || 1) - 1} 条)
                                        </span>
                                    </span>
                                    <button
                                        style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 12, color: 'var(--ink-sub)', padding: '2px 4px' }}
                                        title="清空对话"
                                        onClick={(e) => { e.stopPropagation(); clearSession() }}
                                    >
                                        
                                    </button>
                                </div>
                            ))}
                            <div
                                style={{
                                    padding: '8px 12px', cursor: 'pointer', color: 'var(--dai)',
                                    fontWeight: 600, fontSize: 13,
                                }}
                                onClick={createSession}
                            >
                                + 新建对话
                            </div>
                        </div>
                    )}
                </div>
                {bookRoot && (
                    <InitAssistantProgress
                        currentStage="name"
                        completedItems={messages.length > 1 ? 1 : 0}
                        totalItems={5}
                        onQuickAction={(stage) => {
                            const prompts = {
                                name: '帮我定一个书名',
                                genre: '选择故事类型',
                                settings: '设定世界观',
                                elements: '生成角色元素',
                                arc: '规划开篇剧情',
                            }
                            setChatInput(prompts[stage])
                        }}
                    />
                )}
                <div style={{ flex: 1 }} />
            </header>

            {/* 主体区域：元素面板 + 聊天 + 卡片面板 */}
            <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
                {/* 最左侧：已通过审核的元素面板 */}
                {elementsPanelOpen && (
                    <ElementsPanelContent
                        elements={elements}
                        onElementClick={(item) => {
                            const label = item.name || ''
                            setChatInput(prev => prev ? `${prev}\n参考「${label}」` : `关于「${label}」`)
                        }}
                    />
                )}

                {/* 中间：聊天区域 */}
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                    {/* 提取工具栏 */}
                    {messages.length > 1 && (
                        <div style={{
                            padding: '6px 16px', borderBottom: '1px solid var(--line)',
                            background: 'var(--paper-raised)', display: 'flex', alignItems: 'center', gap: 8,
                            fontSize: 12, flexShrink: 0,
                        }}>
                            <label style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
                                <input
                                    type="checkbox"
                                    checked={selectedMsgs.length === messages.length && messages.length > 0}
                                    onChange={toggleSelectAll}
                                />
                                全选
                            </label>
                            <span style={{ color: 'var(--ink-sub)' }}>已选 {selectedMsgs.length} 条</span>
                            <div style={{ flex: 1 }} />
                            <button
                                className="btn btn-small"
                                onClick={extractFromSelected}
                                disabled={selectedMsgs.length === 0 || extracting || !bookRoot}
                                style={{ fontSize: 12 }}
                            >
                                {extracting ? '提取中…' : '从选中消息提取卡片'}
                            </button>
                        </div>
                    )}

                    {/* 使用共享ChatWindow组件 */}
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                        <ChatWindow
                            messages={messages}
                            onSend={handleChat}
                            busy={chatBusy}
                            disabled={!bookRoot && !submitting}
                            pendingConfirm={pendingConfirm}
                            onConfirm={handleConfirm}
                            onCancel={() => setPendingConfirm(null)}
                            placeholder="告诉 AI 你想写什么…"
                            inputValue={chatInput}
                            onInputChange={setChatInput}
                        />
                    </div>

                    {/* 底部按钮区 */}
                    <div style={{
                        padding: '8px 16px', borderTop: '1px solid var(--line)',
                        background: 'var(--paper-raised)', flexShrink: 0,
                        display: 'flex', justifyContent: 'flex-end', alignItems: 'center',
                    }}>
                        <button className="btn" onClick={finishInit} disabled={submitting || !bookRoot}>
                            {submitting ? '处理中…' : '✓ 完成初始化，进入工作台'}
                        </button>
                    </div>

                    {/* 错误提示 */}
                    {errorMsg && (
                        <div style={{ padding: '10px 16px', background: 'var(--cinnabar-wash)', color: 'var(--cinnabar-d)', fontSize: 13 }}>
                            {errorMsg}
                        </div>
                    )}
                </div>

                {/* 右侧：全局卡片面板 */}
                {cardsPanelOpen && (
                    <div style={{
                        width: 320, borderLeft: '1px solid var(--line)', background: 'var(--paper-raised)',
                        display: 'flex', flexDirection: 'column', overflow: 'hidden', flexShrink: 0,
                    }}>
                        <div style={{
                            padding: '12px 16px', borderBottom: '1px solid var(--line)',
                            fontWeight: 700, fontSize: 14, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                        }}>
                            <span>产出卡片（待审核）</span>
                            <span style={{ fontSize: 12, color: 'var(--ink-sub)' }}>{pendingCards.length} 项</span>
                            <span style={{ fontSize: 9, color: 'red', marginLeft: 4 }}>[root:{bookRoot ? '✓' : '✗'}]</span>
                        </div>
                        <div style={{ flex: 1, overflowY: 'auto', padding: 12 }}>
                            {pendingCards.length === 0 ? (
                                <div style={{ color: 'var(--ink-mute)', fontSize: 13, textAlign: 'center', paddingTop: 20 }}>
                                    暂无待审核卡片
                                </div>
                            ) : (
                                pendingCards.filter(Boolean).map(p => (
                                    <div key={p.id} style={{
                                        border: '1px solid var(--line)', borderRadius: 6, padding: 10,
                                        marginBottom: 8, background: 'var(--paper)', cursor: 'pointer',
                                    }}
                                        onClick={() => {
                                            const label = p.name || ''
                                            const desc = p.desc || p.text || p.detail || ''
                                            const ref = desc ? `${label}：${desc}` : label
                                            setChatInput(prev => prev ? `${prev}\n关于「${ref}」，` : `关于「${ref}」，`)
                                        }}
                                    >
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                                            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--dai)' }}>
                                                {p.type === 'character' ? '角色' : p.type === 'item' ? '物品' : p.type === 'setting' ? '设定' : p.type === 'mem' ? '记忆' : p.type || '卡片'}
                                            </span>
                                            <span style={{ fontSize: 10, color: 'var(--ink-mute)' }}>{p.scope || 'global'}</span>
                                        </div>
                                        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>{p.name || '（无名称）'}</div>
                                        <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 6, whiteSpace: 'pre-wrap' }}>{p.desc || p.text || p.detail || ''}</div>
                                        <div style={{ display: 'flex', gap: 6 }} onClick={e => e.stopPropagation()}>
                                            <button className="btn btn-green" style={{ flex: 1, padding: '6px 12px', fontSize: 12 }} onClick={() => approveCard(p.id)}>✓ 通过</button>
                                            <button className="btn" style={{ flex: 1, padding: '6px 12px', fontSize: 12 }} onClick={() => rejectCard(p.id)}>✕ 驳回</button>
                                        </div>
                                    </div>
                                ))
                            )}

                            {/* 记忆卡片 */}
                            <div style={{ marginTop: 16, borderTop: '1px solid var(--line)', paddingTop: 12 }}>
                                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                    <span>记忆（{memList.length}）</span>
                                    <span style={{ fontSize: 11, color: 'var(--ink-sub)' }}>AI 自动记住</span>
                                </div>
                                {memList.length === 0 ? (
                                    <div style={{ color: 'var(--ink-mute)', fontSize: 12, textAlign: 'center', padding: 12 }}>
                                        AI 会自动记住设定/偏好
                                    </div>
                                ) : (
                                    memList.map(m => (
                                        <div key={m.id} style={{
                                            border: '1px solid var(--line)', borderRadius: 6, padding: 8, marginBottom: 6,
                                            background: 'var(--paper)', fontSize: 12, lineHeight: 1.5,
                                            display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer',
                                        }}
                                            onClick={() => {
                                                setChatInput(prev => prev ? `${prev}\n关于「${m.text || ''}」，` : `关于「${m.text || ''}」，`)
                                            }}
                                        >
                                            <span style={{ flex: 1, color: 'var(--ink)' }}>{m.text || ''}</span>
                                            <button
                                                style={{ background: 'none', border: 'none', color: 'var(--cinnabar)', cursor: 'pointer', fontSize: 12, padding: 2 }}
                                                onClick={async (e) => {
                                                    e.stopPropagation()
                                                    if (window.confirm('删除这条记忆？')) {
                                                        await phAiMemoryDelete(bookRoot, m.id)
                                                        loadMemory()
                                                    }
                                                }}
                                            >
                                                ✕
                                            </button>
                                        </div>
                                    ))
                                )}
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}
