import { useCallback, useEffect, useRef, useState, Fragment } from 'react'
import PromptModal, { askText } from '../components/PromptModal.jsx'
import {
    fetchCurrentProject, fetchApiLibrary, applyApiPreset, applyApiEmbedPreset,
    fetchFilesTree, fetchFileContent, postFileWrite,
    phAiState, phAiElementsPut, phAiSettingsPut, phAiSettingsGenerate, phAiSettingFiles,
    phAiArcNew, phAiArcSelect, phAiArcStep, phAiArcConfirm, phAiArcSetActiveChapter,
    phAiArcModify, phAiArcChat, phAiArcChatStream, phAiArcFinish, phAiArcRegenerateL5, phAiArcSetLevel, phAiArcUpdate,
    phAiArcDelete, phAiArcDeleteChapter, phAiArcChatApply,
    phAiArcSetTemplate, phAiInitStatus,
    phAiElementPut, phAiElementDelete, phAiElementAdd,
    phAiChapterScore, phAiChapterPollution, phAiChapterFinalize,
    phFragmentParse, phFragmentUnderstand, phFragmentExpand, phFragmentFinalize,
    phAiBatchGenerate, phAiOptimizeStatus, phAiCancelTask,
    phTaskStatus,
    phAiMemoryList,
    phAiChatSessionsGet, phAiChatSessionsSave, phAiChatSessionsDelete,
    phAiSelAccessOptions,
    phAiShortDramas, phAiShortDramaAdd, phAiShortDramaFetch,
    phFragmentsPut, phNotesPut,
    phFragmentsGet, phNotesGet,
    phAiPendingGet, phAiPendingApprove, phAiPendingReject,
} from '../api.js'
import BasicSettingsForm from '../components/BasicSettingsForm.jsx'
import AdaptPanel from '../components/AdaptPanel.jsx'
import ChatWindow from '../components/ChatWindow.jsx'
import MemoryPanel from '../components/MemoryPanel.jsx'
import L4BeatView from '../components/L4BeatView.jsx'
import InsertToolbar from '../components/InsertToolbar.jsx'
import SearchPanel from '../components/SearchPanel.jsx'
import SearchHealthPanel from '../components/SearchHealthPanel.jsx'
import StoryMindMap from '../components/StoryMindMap.jsx'
import InspireWorkspace from '../components/InspireWorkspace.jsx'
import ChartWrapper from '../components/ChartWrapper.jsx'
import LevelConfirmOverlay from '../components/LevelConfirmOverlay.jsx'

const LEVEL_META = {
    l1: { label: 'l1 · 一句话极简', hint: '本情节一句话剧情（阶梯起点）' },
    l2: { label: 'l2 · 情节概要', hint: '起因 / 核心冲突 / 转折 / 结局' },
    l3: { label: 'l3 · 章核心', hint: '标题 / 核心 / 拍（元素白名单从这一级开始生效）' },
    l4: { label: 'l4 · 场景分解', hint: '按时序拆场景 × 6 类叶子' },
    l5: { label: 'l5 · 正文', hint: '场景级生成 → 拼接 → AI 味审阅 → 双评分' },
}
const LEVEL_ORDER = ['l1', 'l2', 'l3', 'l4', 'l5']

function levelText(state, level) {
    const lv = (state?.levels || {})[level] || {}
    if (level === 'l3') return lv.data ? renderL3(lv.data) : (lv.text || '')
    if (level === 'l4') return renderL4(lv.scenes || [])
    return lv.text || ''
}

function renderL3(d) {
    if (!d) return ''
    const parts = []
    if (d.title) parts.push(`标题：${d.title}`)
    if (d.core) parts.push(`核心：${d.core}`)
    if (Array.isArray(d.beats) && d.beats.length) parts.push(`拍：${d.beats.join('；')}`)
    return parts.join('\n')
}

// 对白解析（统一入口）：拆 "（神态）角色：台词" 为 {expr, speaker, line}
function parseDialogue(d) {
    const s = (d || '').trim()
    let expr = '', rest = s
    const em = s.match(/^（([^）]*)）\s*(.*)$/)
    if (em) { expr = em[1].trim(); rest = em[2].trim() }
    const cm = rest.match(/^([^：:]*)[：:](.*)$/)
    if (cm) return { expr, speaker: cm[1].trim(), line: cm[2].trim() }
    return { expr, speaker: '', line: rest }
}
// 重构对白字符串：从 {expr, speaker, line} 拼回 "（神态）角色：台词"
function joinDialogue(expr, speaker, line) {
    const sp = String(speaker || '').trim()
    const ln = String(line || '').trim()
    const body = sp ? `${sp}：${ln}` : ln
    return expr ? `（${expr}）${body}` : body
}

// 紧凑对白行：默认「1. （神态）角色：台词」一句一行（不换行不留白）；点击行展开成 3 个 input 就地编辑，失焦/✓ 合并回字符串
function CompactDialogueRow({ value, index, onChange, onRemove }) {
    const { expr, speaker, line } = parseDialogue(value)
    const [editing, setEditing] = useState(false)
    const inpBase = { fontSize: 12, padding: '1px 4px', border: '1px solid var(--line-soft)', borderRadius: 3 }
    if (!editing) {
        return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '1px 2px', cursor: 'text', minWidth: 0 }}
                onClick={() => setEditing(true)} title="点击编辑对白">
                <span style={{ fontSize: 10, color: 'var(--ink-mute)', flexShrink: 0, width: 18, textAlign: 'right' }}>{index + 1}.</span>
                {expr && <span style={{ fontSize: 11, color: 'var(--ink-sub)', flexShrink: 0 }}>（{expr}）</span>}
                {speaker && <span style={{ fontSize: 12, fontWeight: 600, flexShrink: 0, color: 'var(--ink)' }}>{speaker}</span>}
                {speaker && <span style={{ fontSize: 12, flexShrink: 0, color: 'var(--ink)' }}>：</span>}
                <span style={{ fontSize: 12, color: 'var(--ink-sub)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{line || '（空台词）'}</span>
            </div>
        )
    }
    const spW = Math.max(4, speaker.length + 2)
    const exW = Math.max(4, expr.length + 2)
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '1px 2px' }}>
            <span style={{ fontSize: 10, color: 'var(--ink-mute)', flexShrink: 0, width: 18, textAlign: 'right' }}>{index + 1}.</span>
            <input autoFocus value={speaker}
                onChange={e => onChange(joinDialogue(expr, e.target.value, line))}
                onBlur={() => setEditing(false)}
                onKeyDown={e => { if (e.key === 'Enter') setEditing(false) }}
                placeholder="角色" style={{ ...inpBase, width: `${spW}ch`, flexShrink: 0 }} />
            <input value={expr}
                onChange={e => onChange(joinDialogue(e.target.value, speaker, line))}
                placeholder="神态" style={{ ...inpBase, width: `${exW}ch`, flexShrink: 0, color: 'var(--ink-sub)' }} />
            <input value={line}
                onChange={e => onChange(joinDialogue(expr, speaker, e.target.value))}
                placeholder="台词" style={{ ...inpBase, flex: 1, minWidth: 0 }} />
            {onRemove && (
                <button style={{ fontSize: 10, color: 'var(--red)', cursor: 'pointer', border: 'none', background: 'none', flexShrink: 0 }}
                    onMouseDown={e => e.stopPropagation()} onClick={onRemove}>✕</button>
            )}
        </div>
    )
}

function renderL4(scenes) {
    if (!scenes || !scenes.length) return '（无场景）'
    return scenes.map((sc, i) => {
        const n = k => (Array.isArray(sc[k]) ? sc[k].filter(x => String(x).trim()).length : 0)
        const extras = []
        if (sc.elements?.length) extras.push(`${sc.elements.length}元素`)
        if (sc.scene_note) extras.push('有备注')
        const extraStr = extras.length ? `｜${extras.join('/')}` : ''
        return `${i + 1}. ${sc.name || `场景${i + 1}`}｜动作${n('actions')}/对白${n('dialogues')}/冲突${n('conflicts')}/细节${n('details')}${extraStr}`
    }).join('\n')
}

const badgeStyle = confirmed => ({
    padding: '2px 8px', borderRadius: 10, fontSize: 12,
    background: confirmed ? 'var(--green-wash)' : 'var(--amber-wash)',
    color: confirmed ? 'var(--green)' : 'var(--amber)',
})

const btnStyle = (disabled, primary) => ({
    padding: '6px 12px', borderRadius: 4, border: '1px solid var(--line)',
    background: primary ? 'var(--dai)' : 'var(--paper)', color: primary ? 'var(--paper-raised)' : 'var(--ink)',
    cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1,
    fontSize: 13,
})

// 情节视图切换（思维导图 / 线性列表）
const viewBtn = { padding: '4px 10px', borderRadius: 4, border: '1px solid var(--line)', background: 'var(--paper)', cursor: 'pointer', fontSize: 12, color: 'var(--ink-sub)' }
const viewBtnActive = { ...viewBtn, background: 'var(--dai)', borderColor: 'var(--dai)', color: 'var(--paper-raised)' }

const cardStyle = { border: '1px solid var(--line)', borderRadius: 4, padding: 12, background: 'var(--paper)', marginBottom: 10 }
const titleStyle = { fontSize: 14, fontWeight: 600, margin: '0 0 6px', display: 'flex', alignItems: 'center', gap: 8 }
const preStyle = { whiteSpace: 'pre-wrap', fontSize: 13.5, lineHeight: 1.9, margin: 0, fontFamily: 'var(--font-serif)', color: 'var(--ink)' }

const KIND_LABEL = { characters: '角色', items: '物品', settings: '设定' }

export default function AICreationPage() {
    const [bookRoot, setBookRoot] = useState('')
    const [bookTitle, setBookTitle] = useState('')
    const [testBook, setTestBook] = useState(false)
    const [bookLoading, setBookLoading] = useState(true)

    // 元素清单（elements.json）
    const [elements, setElements] = useState({ characters: [], items: [], settings: [], maps: [] })
    const [elementsDraft, setElementsDraft] = useState(null)

    // 素材 / 留空 / 备注（fragments.json / blanks.json / notes.json）
    const [fragments, setFragments] = useState([])
    const [notes, setNotes] = useState([])

    // 左栏标签页（写作 hub）
    const [leftTab, setLeftTab] = useState('ladder') // 'ladder' | 'fragments' | 'blanks' | 'pending' | 'notes'
    const [pendingCards, setPendingCards] = useState([])
    const [newNoteInput, setNewNoteInput] = useState('')

    // 【2026-08-18】阶梯展开/收起状态
    const [ladderExpanded, setLadderExpanded] = useState({ l1: true, l2: true, l3: true, l4: true, l5: true })

    // 【2026-08-21】层级确认浮层（step_ladder 生成后从底部升起，不打断转录视图）
    const [confirmLevel, setConfirmLevel] = useState(null)  // 'l2'..'l5' | null

    // 扁平元素列表（顶层，供灵感面板等使用）
    const flatElements = [
        ...(elements.characters || []).map(e => ({ ...e, kind: 'characters', icon: '' })),
        ...(elements.items || []).map(e => ({ ...e, kind: 'items', icon: '' })),
        ...(elements.settings || []).map(e => ({ ...e, kind: 'settings', icon: '' })),
        ...(elements.maps || []).map(e => ({ ...e, kind: 'maps', icon: '' })),
    ]

    // 情节注册表
    const [arcs, setArcs] = useState({ arcs: [], next_chapter_num: 1 })
    const [galleryExpanded, setGalleryExpanded] = useState(null)   // 角色图鉴展开的元素 id
    const [activeArcId, setActiveArcId] = useState('')
    // 情节视图切换：思维导图（默认）/ 线性列表
    const [arcView, setArcView] = useState('map')

    // 新建情节表单
    const [newL1, setNewL1] = useState('')
    const [newN, setNewN] = useState(1)
    const [newCarryPrev, setNewCarryPrev] = useState(true)

    // 基本设定（可编辑字段源）
    const [settings, setSettings] = useState(null)
    const [settingsDraft, setSettingsDraft] = useState(null)
    const [genTaskId, setGenTaskId] = useState('')
    const [genBusy, setGenBusy] = useState(false)

    // 本情节元素选择草稿
    const [selDraft, setSelDraft] = useState({ characters: [], items: [], settings: [] })

    // 阶梯
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState('')
    const [notice, setNotice] = useState('')

    // 修改 + 对话
    const [modifyInput, setModifyInput] = useState('')
    const [modifyLevel, setModifyLevel] = useState(null)
    const [messages, setMessages] = useState([])
    const [chatInput, setChatInput] = useState('')
    const [chatBusy, setChatBusy] = useState(false)
    const [pendingConfirm, setPendingConfirm] = useState(null)   // 工具循环里待确认动作（如落盘）
    const chatBoxRef = useRef(null)
    // 【2026-08-15 初始化助手】书未初始化（无设定集/无情节）→ 对话走 init/assistant
    const [initStatus, setInitStatus] = useState(null)   // {initialized, stage}
    const [initMsg, setInitMsg] = useState('')
    // 【2026-08-18 书级讨论模式】讨论全书设定/人物/世界观（arc_id 传空）
    const [bookLevelMode, setBookLevelMode] = useState(false)

    // 当前情节派生（提升到顶部：useEffect 依赖数组会引用它们，避免 TDZ「Cannot access before initialization」）
    const activeArc = (arcs.arcs || []).find(a => a.id === activeArcId) || null
    const arcState = activeArc?.state || null
    const l5Text = (arcState?.levels?.l5?.text) || ''

    // 直接编辑（三栏平行：都可编辑）
    const [editLevel, setEditLevel] = useState(null)   // 大纲栏 l1/l2 就地编辑
    const [editText, setEditText] = useState('')
    const [l3Edit, setL3Edit] = useState(null)         // l3 单章编辑草稿 {title,core,beats,idx}
    const [l3ChaptersDraft, setL3ChaptersDraft] = useState(null) // l3 多章整组草稿（数组）
    const [l4Edit, setL4Edit] = useState(null)         // l4 场景编辑草稿（数组）
    const [l4SceneExpanded, setL4SceneExpanded] = useState(null)  // l4 展开的场景索引
    const [l4FragDraft, setL4FragDraft] = useState({})             // {sceneIdx: {type, content}}
    const [l4BeatSelected, setL4BeatSelected] = useState({})       // {sceneIdx: Set(beatIdx)}
    const [wbL4Draft, setWbL4Draft] = useState(null)   // 【常驻编辑卡】中栏 l4 就地草稿（null=回退读 l4scenes）
    const [l4Cursor, setL4Cursor] = useState(null)     // l4 节拍叙事流光标位置 { sceneIdx, beatIdx, rowIdx, kind }
    const [sceneTab, setSceneTab] = useState('note')   // 右栏场景面板 Tab：note / beat / frag
    const insertFromToolbar = useRef(null)             // 右栏插入工具栏的实际执行函数（renderWbL4Card内赋值）
    const fragRefs = useRef({})                        // {sceneIdx: textarea} 素材光标插入
    const fragCursor = useRef({})                      // {sceneIdx: {start,end}} 素材光标位置
    const [l5Draft, setL5Draft] = useState(null)       // 正文栏 l5 可编辑草稿
    const [viewFin, setViewFin] = useState(null)       // 树里点已落盘章节 → {arcId, idx}
    const [expandFin, setExpandFin] = useState(null)   // 正文页签已落盘章节就地展开 → {arcId, idx}

    // 评分
    const [lastScore, setLastScore] = useState(null)
    const [scoreBusy, setScoreBusy] = useState(false)
    const [pollutionResult, setPollutionResult] = useState(null)

    // 顶部模块切换：create（创作） | adapt（改编） | system（系统）
    const [tab, setTab] = useState('create')
    // 深链：/ai-creation?tab=adapt（改编中心书卡/书级改编入口直达）
    useEffect(() => {
        const h = window.location.hash || ''
        const qi = h.indexOf('?')
        if (qi < 0) return
        const t = new URLSearchParams(h.slice(qi + 1)).get('tab')
        if (t === 'adapt' || t === 'create' || t === 'system') setTab(t)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])
    // 创作内部子页（全屏浮层）：stats（概览浮层，备用） / inspire / fragment / pacing
    const [ovTab, setOvTab] = useState('stats')
    // 写作 hub 内部子页（保留备用）：大纲与章纲 / 章节生成
    const [wrTab, setWrTab] = useState('outline')
    // 系统 hub 内部子页：文档 / 检索体检 / 通道配置 / 导出 / 批量生成
    const [syTab, setSyTab] = useState('docedit')
    // 创作助手 tab：chat（对话） | inspire（灵感） | expand（扩写） | mem（记忆）
    const [aiTab, setAiTab] = useState('targeted') // 创作助手三态：targeted 定向 / free 自由对话 / expand 扩写
    const [memOpen, setMemOpen] = useState(false)  // 记忆轻量面板（快捷动作切换，非独立模式）
    const [selAccess, setSelAccess] = useState({ settings: [], arcs: [], elements: [], memory: [], corpus: [], templates: [] }) // 条目级选择性注入（id 列表，空=不限制）
    const [selOpts, setSelOpts] = useState(null) // 条目级注入候选项（settings/arcs/memory/corpus/templates chips）
    // 红果短剧参考库（自动剧名 + 用户录入剧情）
    const [sdList, setSdList] = useState(null)
    const [sdOpen, setSdOpen] = useState(false)
    const [sdDraft, setSdDraft] = useState({ name: '', tags: '', intro: '' })
    const [sdShowForm, setSdShowForm] = useState(false)
    const [sdBusy, setSdBusy] = useState(false)
    const loadShortDramas = useCallback(async () => {
        try { setSdList(await phAiShortDramas()) } catch { setSdList(null) }
    }, [])
    useEffect(() => { loadShortDramas() }, [loadShortDramas])
    const fetchShortDramas = async () => {
        setSdBusy(true)
        try { await phAiShortDramaFetch(); await loadShortDramas() }
        catch (e) { setError(`抓取红果热播失败：${e.message || e}`) }
        finally { setSdBusy(false) }
    }
    const addShortDrama = async () => {
        const name = sdDraft.name.trim()
        if (!name) { setError('请填写剧名'); return }
        setSdBusy(true)
        try {
            const tags = sdDraft.tags.split(/[,，、]/).map(t => t.trim()).filter(Boolean)
            await phAiShortDramaAdd(name, tags, sdDraft.intro.trim())
            setSdDraft({ name: '', tags: '', intro: '' })
            await loadShortDramas()
        } catch (e) { setError(`录入失败：${e.message || e}`) }
        finally { setSdBusy(false) }
    }
    // 点击红果剧名 → 设讨论对象（横幅聚焦 + 对话注入该剧信息）
    const discussShortDrama = (d) => {
        const tagTxt = (d.tags || []).join('、')
        const meta = d.intro ? `${d.intro}` : `（暂无剧情简介——可在「红果热播·录剧情」补录）`
        setDiscuss({ name: `${d.name}`, icon: '', content: `红果热播短剧《${d.name}》${tagTxt ? `（套路：${tagTxt}）` : ''}。${meta}` })
    }
    // 灵感工坊当前机制
    const [inspireMech, setInspireMech] = useState('seed')
    // 左栏节奏雷达是否展开
    const [paceExpand, setPaceExpand] = useState(false)
    // 左栏元素段各类型是否展开
    const [elemKindOpen, setElemKindOpen] = useState({}) // 默认全部收敛（角色/物品/设定初始不展开）
    const [elemPopId, setElemPopId] = useState(null)   // 当前弹出详情的元素 id
    const [newArcOpen, setNewArcOpen] = useState(false) // 左栏「＋ 新建情节」弹层是否展开
    const [access, setAccess] = useState({ web: false, book: true, memory: true, corpus: true }) // 上下文访问控制（联网/本书/记忆/语料）
    const setAcc = (k, v) => setAccess(a => ({ ...a, [k]: v }))
    // 条目级注入候选项随当前书加载（settings/arcs/memory/corpus/templates）
    useEffect(() => {
        if (bookRoot) {
            setSelOpts(null)
            phAiSelAccessOptions(bookRoot).then(setSelOpts).catch(() => setSelOpts(null))
            phAiInitStatus(bookRoot).then(setInitStatus).catch(() => setInitStatus(null))
        }
    }, [bookRoot])
    const toggleAccessSel = (kind, val) => setSelAccess(s => ({
        ...s,
        [kind]: s[kind].includes(val) ? s[kind].filter(x => x !== val) : [...s[kind], val],
    }))
    const [discuss, setDiscuss] = useState(null)           // 讨论对象 {kind:'level'|'arc'|'element', name, content, icon, lv?/arcId?/elemId?}
    const [ladderEdit, setLadderEdit] = useState(null)     // 阶梯层就地编辑 {lv, draft}
    const [elemEdit, setElemEdit] = useState(null)         // 元素浮层编辑草稿 {kind, id, name, alias, desc}
    const [mapViewId, setMapViewId] = useState(null)   // 当前展开大预览的地图 id
    // 顶部 meta 浮层：'ladder' | 'participants' | 'chapter' | null
    const [metaPop, setMetaPop] = useState(null)
    // 快捷操作面板是否展开
    const [quickOpen, setQuickOpen] = useState(false)
    // 就地编辑状态：{ key, status: 'saving'|'saved'|'error'|null }
    const [editStatus, setEditStatus] = useState({})
    // 防抖保存定时器
    const editTimers = useRef({})
    // 记忆列表
    const [memList, setMemList] = useState([])
    const [memLoading, setMemLoading] = useState(false)
    const loadMemory = useCallback(async () => {
        if (!bookRoot) return
        setMemLoading(true)
        try {
            const r = await phAiMemoryList(bookRoot).catch(() => ({ items: [] }))
            setMemList(r.items || [])
        } catch { /* 静默 */ }
        setMemLoading(false)
    }, [bookRoot])
    // bookRoot 就绪就加载记忆（不用等切 tab）
    useEffect(() => {
        if (bookRoot) loadMemory()
    }, [bookRoot, loadMemory])

    // ── 【Phase 3】会话管理：工作台对话接入 chat-sessions 持久化 ─────────
    // 与初始化助手（CreateBookPage）共用一份 chat_sessions.json，用 kind 隔离：
    // 本页会话 kind='workbench'；回存时其它 kind（初始化助手等）原样带回去，互不覆盖。
    const CHAT_SESSION_KIND = 'workbench'
    const [chatSessions, setChatSessions] = useState([])    // [{id,title,arc_id,updated_at,kind,messages}]
    const [chatSessionId, setChatSessionId] = useState('')  // '' = 新对话（首条消息时才建档）
    const otherSessionsRef = useRef([])                     // 非 workbench 会话（保存时合并回存）
    const chatSessionsLoadedRef = useRef(false)

    const _chatTitle = (msgs) => {
        const firstUser = (msgs || []).find(m => m.role === 'user')
        const t = String(firstUser?.content || '').split('\n')[0].trim()
        return (t || '新对话').slice(0, 20)
    }

    // 进书加载：workbench 会话进 state，其它 kind 留在 ref 备回存；默认恢复最近一段
    useEffect(() => {
        chatSessionsLoadedRef.current = false
        if (!bookRoot) { setChatSessions([]); setChatSessionId(''); setMessages([]); return }
        phAiChatSessionsGet(bookRoot).then(res => {
            const all = Array.isArray(res?.sessions)
                ? res.sessions.filter(s => s && s.id && Array.isArray(s.messages)) : []
            otherSessionsRef.current = all.filter(s => (s.kind || 'init') !== CHAT_SESSION_KIND)
            const wb = all.filter(s => (s.kind || 'init') === CHAT_SESSION_KIND)
            setChatSessions(wb)
            const latest = [...wb].sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))[0]
            if (latest) { setChatSessionId(latest.id); setMessages(latest.messages) }
            chatSessionsLoadedRef.current = true
        }).catch(() => { chatSessionsLoadedRef.current = true })
    }, [bookRoot])

    // 会话列表变化即全量回存（服务端备份；与其它 kind 会话合并）
    useEffect(() => {
        if (!bookRoot || !chatSessionsLoadedRef.current) return
        phAiChatSessionsSave(bookRoot, [...otherSessionsRef.current, ...chatSessions]).catch(() => {})
    }, [bookRoot, chatSessions])

    // 把一段消息写进当前会话（title=首句截断，arc_id 随当前情节，updated_at 刷新）
    const upsertChatSession = (msgs, sidOverride = '') => {
        if (!msgs.length || !bookRoot) return
        const sid = sidOverride || chatSessionId
        if (!sid) return
        const now = new Date().toISOString()
        setChatSessions(prev => {
            const item = { id: sid, title: _chatTitle(msgs), arc_id: activeArcId || '', updated_at: now, kind: CHAT_SESSION_KIND, messages: msgs }
            return prev.some(s => s.id === sid)
                ? prev.map(s => (s.id === sid ? { ...s, ...item } : s))
                : [...prev, item]
        })
    }

    const newChatSession = () => {
        if (chatBusy) return
        setChatSessionId(''); setMessages([]); setPendingConfirm(null)
    }
    const switchChatSession = (sid) => {
        if (chatBusy || sid === chatSessionId) return
        const target = chatSessions.find(s => s.id === sid)
        if (!target) return
        setChatSessionId(sid)
        setMessages(Array.isArray(target.messages) ? target.messages : [])
        setPendingConfirm(null)
    }
    const deleteChatSession = (sid) => {
        if (chatBusy) return
        setChatSessions(prev => prev.filter(s => s.id !== sid))
        phAiChatSessionsDelete(bookRoot, sid).catch(() => {})
        if (sid === chatSessionId) { setChatSessionId(''); setMessages([]); setPendingConfirm(null) }
    }
    // 消息级「删除这段对话」：删该消息及其配对消息（user+assistant 成对），全量回存
    const deleteMessagePair = (idx) => {
        if (chatBusy) return
        const m = messages[idx]
        if (!m) return
        let start = idx, end = idx
        if (m.role === 'user' && messages[idx + 1]?.role === 'assistant') end = idx + 1
        else if (m.role === 'assistant' && messages[idx - 1]?.role === 'user') start = idx - 1
        const next = [...messages.slice(0, start), ...messages.slice(end + 1)]
        if (!next.length) {
            // 删空了 → 本会话一并移除（服务端同步删）
            if (chatSessionId) {
                setChatSessions(prev => prev.filter(s => s.id !== chatSessionId))
                phAiChatSessionsDelete(bookRoot, chatSessionId).catch(() => {})
                setChatSessionId('')
            }
            setMessages([]); setPendingConfirm(null)
            return
        }
        setMessages(next)
        upsertChatSession(next)
    }

    // ── 文档编辑器 state ──────────────────────────────────────
    const [docTree, setDocTree] = useState(null)
    const [docSelPath, setDocSelPath] = useState('')
    const [docContent, setDocContent] = useState('')
    const [docDirty, setDocDirty] = useState(false)
    const [docSaveState, setDocSaveState] = useState('')
    const [docErr, setDocErr] = useState('')
    useEffect(() => {
        if (tab !== 'system' || syTab !== 'docedit' || !bookRoot) return
        setDocErr('')
        fetchFilesTree().then(t => setDocTree(t)).catch(e => setDocErr(e.message || '加载目录失败'))
    }, [tab, syTab, bookRoot])
    useEffect(() => {
        if (tab !== 'system' || syTab !== 'docedit' || !docSelPath) { setDocContent(''); return }
        setDocErr('')
        fetchFileContent(docSelPath).then(r => { setDocContent(r.content || ''); setDocDirty(false) })
            .catch(e => setDocErr(e.message || '读取失败'))
    }, [tab, syTab, docSelPath])

    // ── 导出 state ────────────────────────────────────────────
    const [exporting, setExporting] = useState(false)

    // ── 批量生成 state ────────────────────────────────────────
    const [batchTarget, setBatchTarget] = useState(10)
    const [batchPerArc, setBatchPerArc] = useState(3)
    const [batchBrief, setBatchBrief] = useState('')
    const [batchTaskId, setBatchTaskId] = useState('')
    const [batchStatus, setBatchStatus] = useState(null)
    const [batchPoll, setBatchPoll] = useState(null)
    useEffect(() => () => { if (batchPoll) clearInterval(batchPoll) }, [batchPoll])
    // 设定集文档（随当前书加载，供左栏「设定与文档」段展示）
    const [settingFiles, setSettingFiles] = useState(null)
    const [settingFilesErr, setSettingFilesErr] = useState('')
    useEffect(() => {
        if (bookRoot) {
            setSettingFiles(null)
            phAiSettingFiles(bookRoot)
                .then(r => { setSettingFiles(r); setSettingFilesErr('') })
                .catch(e => setSettingFilesErr(e.message || '加载设定集失败'))
        }
    }, [bookRoot])
    // ── v6.5 片段锚定扩写：只扩写【】内的内容 ───────────────────────
    const [fragText, setFragText] = useState('')
    const [fragParse, setFragParse] = useState(null)          // parse 结果（锚点+槽位）
    const [fragDirectives, setFragDirectives] = useState(null) // understand 结果（扩写指令）
    const [fragResult, setFragResult] = useState(null)        // expand 结果（output/fills/verify）
    const [fragBusy, setFragBusy] = useState(false)
    const [fragTitle, setFragTitle] = useState('')
    // API 预设（当前文字/向量模型展示，测试书接入）
    const [apiLib, setApiLib] = useState(null)
    const [presetOpen, setPresetOpen] = useState(false)
    const [presetSwitching, setPresetSwitching] = useState(false)
    const currentPreset = apiLib?.text_presets?.find?.(p => p.is_current) || apiLib?.text_presets?.[0] || null
    // 向量模型独立切换（多预设）
    const [embedPresetOpen, setEmbedPresetOpen] = useState(false)
    const [embedSwitching, setEmbedSwitching] = useState(false)
    const currentEmbedPreset = apiLib?.embed_presets?.find?.(p => p.is_current) || apiLib?.embed_presets?.[0] || null

    const applyEmbedPreset = async (id) => {
        if (embedSwitching) return
        setEmbedSwitching(true)
        try {
            const r = await applyApiEmbedPreset(id)
            if (r && r.current_embed_id) {
                const lib = await fetchApiLibrary()
                setApiLib(lib)
                const cur = lib?.embed_presets?.find?.(p => p.is_current)
                const m = cur?.fields?.EMBED_MODEL ? ` · ${cur.fields.EMBED_MODEL}` : ''
                setNotice(cur ? `已切换到「${cur.name}」${m}` : '向量模型已切换')
            } else {
                setError(r?.error || r?.detail || '切换失败')
            }
        } catch (e) {
            setError('切换向量模型失败：' + (e.message || e))
        } finally {
            setEmbedSwitching(false)
            setEmbedPresetOpen(false)
        }
    }

    const applyPreset = async (id) => {
        if (presetSwitching) return
        setPresetSwitching(true)
        try {
            const r = await applyApiPreset(id)
            // apply 返回 { applied_keys, current_text_id }，成功后重新拉取完整列表
            if (r && r.current_text_id) {
                const lib = await fetchApiLibrary()
                setApiLib(lib)
                const cur = lib?.text_presets?.find?.(p => p.is_current)
                setNotice(cur ? `已切换到「${cur.name}」` : '预设已切换')
            } else {
                setError(r?.error || r?.detail || '切换失败')
            }
        } catch (e) {
            setError('切换预设失败：' + (e.message || e))
        } finally {
            setPresetSwitching(false)
            setPresetOpen(false)
        }
    }

    const run = useCallback(async (label, fn) => {
        setBusy(true); setError(''); setNotice('')
        try {
            const r = await fn()
            if (r && r.ok === false) { setError(r.error || '操作失败'); return null }
            return r
        } catch (e) {
            setError(`${label}失败：${e.message || e}`); return null
        } finally {
            setBusy(false)
        }
    }, [])

    // ── 加载当前书 + 创作状态 ─────────────────────────────────────
    const refreshWorkbench = useCallback(async (root) => {
        const r = await phAiState(root).catch(e => ({ error: e.message }))
        if (r?.error) { setError(`加载创作状态失败：${r.error}`); return }
        setElements(r.elements)
        setArcs(r.arcs)
        setSettings(r.settings)
        setSettingsDraft(r.settings ? JSON.parse(JSON.stringify(r.settings)) : null)
        const list = r.arcs?.arcs || []
        if (list.length) {
            // 情节卡默认折叠：保留当前选中，否则不自动展开第一个
            setActiveArcId(prev => (list.some(a => a.id === prev) ? prev : ''))
        } else {
            setActiveArcId('')
        }
        setElementsDraft(JSON.parse(JSON.stringify(r.elements)))
        setFragments(r.fragments || [])
        setNotes(r.notes || [])
        setL5Draft(null)
        setViewFin(null)
        setEditLevel(null)
        setL3Edit(null)
        setL4Edit(null)
    }, [])

    const refreshBook = useCallback(async () => {
        setBookLoading(true)
        try {
            const cur = await fetchCurrentProject()
            if (cur?.project_root) {
                setBookRoot(cur.project_root)
                setBookTitle(cur.title || '')
                setTestBook(!!cur.test_book)
                await refreshWorkbench(cur.project_root)
            } else {
                setBookRoot('')
                setArcs({ arcs: [], next_chapter_num: 1 })
            }
        } catch (e) {
            setError(`读取当前书失败：${e.message || e}`)
        } finally {
            setBookLoading(false)
        }
    }, [refreshWorkbench])

    useEffect(() => { refreshBook() }, [refreshBook])

    // 点击预设菜单外部关闭下拉（文字 + 向量）
    useEffect(() => {
        if (!presetOpen && !embedPresetOpen) return
        const handler = (e) => {
            if (!e.target.closest('.wb-presetwrap')) {
                setPresetOpen(false)
                setEmbedPresetOpen(false)
            }
        }
        document.addEventListener('click', handler)
        return () => document.removeEventListener('click', handler)
    }, [presetOpen, embedPresetOpen])

    useEffect(() => {
        if (chatBoxRef.current) chatBoxRef.current.scrollTop = chatBoxRef.current.scrollHeight
    }, [messages])

    // 思维导图点情节节点 → 详情面板滚动到可见（初始无选中不触发）
    const panelRef = useRef(null)
    useEffect(() => {
        if (activeArcId && panelRef.current) panelRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }, [activeArcId])

    // API 预设：当前文字/向量模型展示（测试书接入 api 预设模块）
    useEffect(() => {
        fetchApiLibrary().then(d => setApiLib(d)).catch(() => {})
    }, [])

    // 切换当前情节 → 同步该情节选择草稿 + 清空对话/评分
    // 注意：只依赖 activeArcId——数据刷新（保存/生成/切章等）不重置对话/评分，
    // 避免用户刚讨论的剧情或刚出的评分被一次 refresh 清掉
    useEffect(() => {
        // eslint-disable-next-line react-hooks/exhaustive-deps
        const arc = (arcs.arcs || []).find(a => a.id === activeArcId)
        if (arc) {
            const sel = arc.selected || { characters: [], items: [], settings: [] }
            setSelDraft({
                characters: [...(sel.characters || [])],
                items: [...(sel.items || [])],
                settings: [...(sel.settings || [])],
            })
            setMessages([])
            setChatInput('')
            setLastScore(null)
            setPollutionResult(null)
        }
        // 【常驻编辑卡】切情节 → 重置中栏 l4 就地草稿（回退读该情节最新 l4）
        setWbL4Draft(null)
        setL4FragDraft({})
        setL4BeatSelected({})
        setL4SceneExpanded(null)
    }, [activeArcId])

    // 大纲栏 l3 直接可编辑：服务端数据一变 → 同步草稿（l4 用摘要模式场景卡展示，点「编辑」才进编辑）
    useEffect(() => {
        if (!arcState) return
        const l3d = arcState.levels?.l3?.data
        if (l3d) {
            if (Array.isArray(l3d.chapters)) {
                setL3ChaptersDraft(JSON.parse(JSON.stringify(l3d.chapters)))
                setL3Edit(null)
            } else if (l3d && l3d.title) {
                setL3Edit({ title: l3d.title || '', core: l3d.core || '', beats: Array.isArray(l3d.beats) ? l3d.beats : [], idx: null })
                setL3ChaptersDraft(null)
            }
        }
    }, [arcState])

    // 基本设定生成轮询
    useEffect(() => {
        if (!genTaskId) return
        const iv = setInterval(async () => {
            try {
                const s = await phTaskStatus(genTaskId)
                if (s?.status === 'done') {
                    clearInterval(iv); setGenTaskId(''); setGenBusy(false)
                    setNotice('基本设定已生成：设定集与元素清单已更新')
                    await refreshWorkbench(bookRoot)
                } else if (s?.status === 'failed' || s?.status === 'error') {
                    clearInterval(iv); setGenTaskId(''); setGenBusy(false)
                    setError('基本设定生成失败：' + (s?.error || '未知错误'))
                }
            } catch (e) { /* 继续轮询 */ }
        }, 2000)
        return () => clearInterval(iv)
    }, [genTaskId, bookRoot, refreshWorkbench])

    // ── 基本设定：保存 / 重新生成 ─────────────────────────────────
    const saveSettings = async () => {
        if (!settingsDraft) return
        const r = await run('保存基本设定', () => phAiSettingsPut(bookRoot, settingsDraft))
        if (r?.ok) {
            setNotice('基本设定已保存')
            await refreshWorkbench(bookRoot)
        }
    }
    const regenerateSettings = async () => {
        if (!settingsDraft) { setError('基本设定为空，请先填写'); return }
        setGenBusy(true); setError(''); setNotice('')
        try {
            const gen = await phAiSettingsGenerate({
                book_root: bookRoot, settings: settingsDraft,
                title: settingsDraft?.name || '', genre: settingsDraft?.genre || '',
            })
            if (gen?.task_id) setGenTaskId(gen.task_id)
            else setError('生成未返回任务 id')
        } catch (e) {
            setGenBusy(false)
            setError(`生成失败：${e.message || e}`)
        }
    }

    // ── v6.5 片段锚定扩写：只扩写【】内的内容 ──────────────────────
    const fragReset = () => { setFragParse(null); setFragDirectives(null); setFragResult(null) }
    const doFragParse = async () => {
        if (!fragText.trim()) { setError('请先粘贴片段'); return }
        const r = await run('解析片段', () => phFragmentParse(fragText))
        if (r?.ok) { setFragParse(r); setFragDirectives(null); setFragResult(null) }
    }
    const doFragUnderstand = async () => {
        if (!fragText.trim()) { setError('请先粘贴片段'); return }
        setFragBusy(true); setError(''); setNotice('')
        try {
            const r = await phFragmentUnderstand(fragText)
            if (r?.ok) setFragDirectives(r.slots)
            else setError(r?.error || '理解失败')
        } catch (e) { setError(`理解失败：${e.message || e}`) }
        finally { setFragBusy(false) }
    }
    const doFragExpand = async () => {
        if (!fragText.trim()) { setError('请先粘贴片段'); return }
        setFragBusy(true); setError(''); setNotice('')
        try {
            const r = await phFragmentExpand(fragText, fragDirectives)
            if (r?.ok) { setFragResult(r); setFragDirectives(r.directives) }
            else setError(r?.error || '扩写失败')
        } catch (e) { setError(`扩写失败：${e.message || e}`) }
        finally { setFragBusy(false) }
    }
    const doFragFinalize = async () => {
        if (!fragResult?.output || !fragText) { setError('请先扩写'); return }
        setFragBusy(true); setError(''); setNotice('')
        try {
            const r = await phFragmentFinalize({
                book_root: bookRoot, title: fragTitle, text: fragText, output: fragResult.output,
            })
            if (r?.ok) {
                setNotice(`已落盘：第 ${r.chapter} 章「${r.title}」（评分 ${r.scores?.overall}）`)
                setFragResult(null); setFragParse(null); setFragDirectives(null)
                await refreshWorkbench(bookRoot)
            } else setError(r?.error || '落盘失败')
        } catch (e) { setError(`落盘失败：${e.message || e}`) }
        finally { setFragBusy(false) }
    }
    const fragRender = () => {
        const fills = fragResult?.fills || {}
        const out = fragResult?.output || ''
        // 用不同颜色高亮填充部分：把 fills 按出现顺序替换回去染色
        if (!fragResult) return <pre style={preStyle}>{fragText || '（粘贴片段后点「解析/理解」）'}</pre>
        const markers = [...out.matchAll(/【待补】/g)]
        void markers
        // 直接展示输出文本，填充由 verify 保证；高亮方式：对 output 里首个出现的 fills 染色
        let html = out
        for (const [sid, fill] of Object.entries(fills)) {
            if (!fill || fill === '【待补】') continue
            const esc = fill.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
            html = html.replace(new RegExp(esc, 'g'), `<span style="color:var(--dai);background:var(--dai-wash)">${fill}</span>`)
        }
        return <div style={{ fontSize: 13, lineHeight: 1.7 }}>
            <div dangerouslySetInnerHTML={{ __html: html.replace(/\n/g, '<br/>') }} />
        </div>
    }

    // ── 元素清单 ───────────────────────────────────────────────────
    const updateDraftElem = (kind, idx, field, value) => {
        setElementsDraft(d => {
            const arr = (d?.[kind] || []).map(x => ({ ...x }))
            if (!arr[idx]) return d
            arr[idx][field] = value
            return { ...d, [kind]: arr }
        })
    }
    const addDraftElem = (kind) => {
        setElementsDraft(d => ({
            ...d,
            [kind]: [...(d?.[kind] || []), { name: '', alias: [], terms: [], desc: '', scope: 'global', arc_name: '' }],
        }))
    }
    const delDraftElem = (kind, idx) => {
        setElementsDraft(d => ({ ...d, [kind]: (d?.[kind] || []).filter((_, i) => i !== idx) }))
    }
    const saveElements = async () => {
        const splitList = (arr) => (arr || '').split(/[,，、\s]+/).map(s => s.trim()).filter(Boolean)
        const clean = (list) => list.map((e, i) => ({
            id: e.id || `${kindPrefix(e)}${Date.now().toString().slice(-4)}${i}`,
            name: (e.name || '').trim(),
            alias: Array.isArray(e.alias) ? e.alias : splitList(e.alias),
            terms: Array.isArray(e.terms) ? e.terms : splitList(e.terms),
            desc: (e.desc || '').trim(),
            setting_file: (e.setting_file || '').trim(),
            scope: e.scope || 'global',
            arc_name: e.arc_name || '',
            // v6.9：保留 fields/relations（灵感工坊类图卡片系统建的字段/关系，不能在行级列表保存时抹掉）
            fields: Array.isArray(e.fields) ? e.fields : [],
            relations: Array.isArray(e.relations) ? e.relations : [],
        })).filter(e => e.name)
        const kindPrefix = (e) => e.id ? e.id[0] : 'e'
        const payload = {
            characters: clean(elementsDraft?.characters || []),
            items: clean(elementsDraft?.items || []),
            settings: clean(elementsDraft?.settings || []),
        }
        const r = await run('保存元素清单', () => phAiElementsPut(bookRoot, payload))
        if (r?.ok) {
            setNotice('元素清单已保存')
            await refreshWorkbench(bookRoot)
        }
    }

    // ── 情节操作 ─────────────────────────────────────────────────────
    const createArc = async () => {
        const lines = newL1.split('\n').map(s => s.trim()).filter(Boolean)
        if (lines.length === 0) { setError('请填写本情节一句话极简剧情（l1），每行一条可批量建多个情节'); return }
        let lastArcId = ''
        let lastTpl = null
        for (const l1 of lines) {
            const r = await run(`新建情节「${l1.slice(0, 14)}…」`, () => phAiArcNew({
                book_root: bookRoot, l1, n_chapters: newN,
                style: '', role_setting: '',
                selected: null, carry_prev: newCarryPrev,
            }))
            if (!r?.ok) break
            lastArcId = r.arc?.id || lastArcId
            if (r.arc?.template?.id) lastTpl = r.arc.template
        }
        setNewL1('')
        setNewArcOpen(false) // 创建成功/失败都收起弹层：backdrop 会挡住整个舞台，不能留着
        if (lastArcId) {
            setActiveArcId(lastArcId)
            await refreshWorkbench(bookRoot)
            if (lines.length > 1) {
                setNotice(`已批量创建 ${lines.length} 个情节，选中最后一个——逐情节生成 l2 概要 → 选参与元素 → l3/l4/l5`)
            } else if (lastTpl?.id) {
                const sim = lastTpl.similarity ? `（相似度 ${(+lastTpl.similarity).toFixed(2)}）` : ''
                setNotice(`情节已创建，已自动套用情节模板「${lastTpl.name}」${sim}——l2-l5 将按模板格式+策略生成，可清除或更换`)
            } else {
                setNotice('情节已创建：写 l1 → 生成 l2 情节概要 → 选择本情节参与元素 → 生成 l3 起')
            }
        } else if (lines.length > 0) {
            setError('新建情节失败，请检查后重试')
        }
    }

    const selectArc = (id) => setActiveArcId(id)

    // 情节改名 / 删除（左栏情节项 hover 操作）
    const renameArc = async (a) => {
        // Electron 渲染进程不支持 window.prompt()，改用页面内弹窗（PromptModal）
        const name = await askText('情节新名字：', a.name)
        if (!name || name.trim() === a.name) return
        const r = await run('改名情节', () => phAiArcUpdate(bookRoot, a.id, { name: name.trim() }))
        if (r?.ok) { setNotice('情节已改名'); await refreshWorkbench(bookRoot) }
    }
    const deleteArc = async (a) => {
        if (!window.confirm(`确定删除情节「${a.name}」？该情节全部内容（阶梯/正文/评分）不可恢复。`)) return
        const r = await run('删除情节', () => phAiArcDelete(bookRoot, a.id))
        if (r?.ok) {
            if (activeArcId === a.id) setActiveArcId('')
            if (discuss?.kind === 'arc' && discuss.arcId === a.id) setDiscuss(null)
            setNotice(`已删除情节「${a.name}」`)
            await refreshWorkbench(bookRoot)
        }
    }
    // 删除本章（l3 章行 hover）
    const deleteChapter = async (arcId, idx) => {
        if (!window.confirm(`确定删除本章（第 ${idx + 1} 章）？下游 l4/l5 会被清空。`)) return
        const r = await run('删除本章', () => phAiArcDeleteChapter(bookRoot, arcId, idx))
        if (r?.ok) {
            setNotice(`已删除第 ${idx + 1} 章`)
            setL5Draft(null)
            await refreshWorkbench(bookRoot)
        }
    }

    // 阶梯层就地编辑保存 / 清空（l3 用「标题：核心」行格式）
    const saveLevelEdit = async () => {
        if (!ladderEdit || !activeArcId) return
        const { lv, draft } = ladderEdit
        setLadderEdit(null)
        if (lv === 'l3') {
            const chapters = (draft || '').split('\n').map(line => {
                const i = line.indexOf('：')
                return { title: i > 0 ? line.slice(0, i).trim() : '', core: i > 0 ? line.slice(i + 1).trim() : line.trim() }
            }).filter(c => c.title || c.core)
            const r = await run(`保存 ${lv}`, () => phAiArcSetLevel(bookRoot, activeArcId, 'l3', '', { chapters }, null))
            if (r?.ok) { setNotice('l3 已保存'); await refreshWorkbench(bookRoot) }
            return
        }
        const r = await run(`保存 ${lv}`, () => phAiArcSetLevel(bookRoot, activeArcId, lv, draft || ''))
        if (r?.ok) { setNotice(`${lv} 已保存`); await refreshWorkbench(bookRoot) }
    }
    const clearLevel = async (lv) => {
        if (!activeArcId) return
        if (!window.confirm(`确定清空 ${lv}？下游级会被清空。`)) return
        let r
        if (lv === 'l3') {
            r = await run(`清空 ${lv}`, () => phAiArcSetLevel(bookRoot, activeArcId, 'l3', '', { chapters: [] }, null))
        } else if (lv === 'l4') {
            r = await run(`清空 ${lv}`, () => phAiArcSetLevel(bookRoot, activeArcId, 'l4', '', [], null))
        } else {
            r = await run(`清空 ${lv}`, () => phAiArcSetLevel(bookRoot, activeArcId, lv, ''))
        }
        if (r?.ok) { setNotice(`${lv} 已清空`); await refreshWorkbench(bookRoot) }
    }

    // 元素浮层保存 / 删除
    const saveElemEdit = async () => {
        if (!elemEdit) return
        const { kind, id, name, alias, desc, isNew } = elemEdit
        const aliasArr = (alias || '').split(/[,，、\s]+/).filter(Boolean)
        if (isNew) {
            // 新建：POST 由后端生成 id；填了别名/术语再补一发更新
            const r = await run('新建元素', () => phAiElementAdd(bookRoot, kind, { name: name || '', desc: desc || '' }))
            if (r?.ok) {
                const card = r.card
                if (card?.id && aliasArr.length) {
                    await run('保存别名', () => phAiElementPut(bookRoot, kind, card.id,
                        kind === 'settings' ? { terms: aliasArr } : { alias: aliasArr }))
                }
                setNotice(`元素「${name || '未命名'}」已创建`)
                setElemEdit(null)
                await refreshWorkbench(bookRoot)
            }
            return
        }
        const r = await run('保存元素', () => phAiElementPut(bookRoot, kind, id, {
            name: name || '', desc: desc || '',
            ...(kind === 'settings' ? { terms: aliasArr } : { alias: aliasArr }),
        }))
        if (r?.ok) {
            setNotice('元素已保存')
            setElemEdit(null)
            await refreshWorkbench(bookRoot)
        }
    }
    const deleteElem = async () => {
        if (!elemEdit) return
        if (!window.confirm(`确定删除元素「${elemEdit.name}」？删除后本情节引用一并失效。`)) return
        const r = await run('删除元素', () => phAiElementDelete(bookRoot, elemEdit.kind, elemEdit.id))
        if (r?.ok) {
            setNotice(`已删除元素「${elemEdit.name}」`)
            setElemEdit(null); setElemPopId(null)
            if (discuss?.kind === 'element' && discuss.elemId === elemEdit.id) setDiscuss(null)
            await refreshWorkbench(bookRoot)
        }
    }

    // 引导卡「让 AI 规划第一个情节」：书级对话，AI 用 new_arc 建情节并一路推进
    const startAiFirstArc = () => {
        setAiTab('targeted')
        handleChat('请根据本书的基本设定、文风与元素，规划第一个情节：用 new_arc 创建（l1 一句话极简 + 章数），确认 l1 后逐步生成 l2 情节概要 → l3 章纲 → l4 场景 → l5 正文，一路推进到正文。')
    }

    // 讨论对象（VSCode 式）：点击模块设为「讨论对象」→ 对话区横幅 + 自由对话聚焦
    const setDiscussTarget = (t) => { setDiscuss(t); setAiTab('targeted') }

    const toggleSel = (kind, id) => {
        setSelDraft(d => {
            const cur = d[kind] || []
            return { ...d, [kind]: cur.includes(id) ? cur.filter(x => x !== id) : [...cur, id] }
        })
    }
    const saveSel = async () => {
        const r = await run('保存元素选择', () => phAiArcSelect(bookRoot, activeArcId, {
            characters: selDraft.characters, items: selDraft.items, settings: selDraft.settings,
        }))
        if (r?.ok) {
            setNotice('本情节参与元素已保存；选择从 l3 章核心起生效，l3 起下游已清空需重新生成')
            await refreshWorkbench(bookRoot)
        }
    }

    const handleStep = async () => {
        if (!activeArcId) return
        const r = await run('生成下一级', () => phAiArcStep(bookRoot, activeArcId))
        if (r?.ok) {
            await refreshWorkbench(bookRoot)
            setConfirmLevel(r.to || null)   // 弹出审阅浮层
        }
    }
    const handleConfirm = async (level) => {
        if (!activeArcId) return
        const r = await run('确认', () => phAiArcConfirm(bookRoot, activeArcId, level))
        if (r?.ok) {
            setConfirmLevel(null)
            await refreshWorkbench(bookRoot)
        }
    }
    // 浮层：重新生成本级（先清空当前级再 step）
    const handleOverlayRegenerate = async () => {
        if (!activeArcId || !confirmLevel) return
        const lv = confirmLevel
        setConfirmLevel(null)
        await clearLevel(lv)
        const r = await run('重新生成', () => phAiArcStep(bookRoot, activeArcId))
        if (r?.ok) {
            await refreshWorkbench(bookRoot)
            setConfirmLevel(r.to || lv)
        }
    }
    // 浮层：对话修改 → 聚焦定向对话并预填
    const handleOverlayDiscuss = () => {
        const lv = confirmLevel
        setConfirmLevel(null)
        if (!lv) return
        const l3data = arcState?.levels?.l3?.data || {}
        const l3chapters = Array.isArray(l3data.chapters) ? l3data.chapters : []
        const l4scenes = arcState?.levels?.l4?.scenes || []
        const l5v = l5Draft !== null ? l5Draft : l5Text
        const names = { l1: 'l1 一句话极简', l2: 'l2 情节概要', l3: 'l3 章核心', l4: 'l4 场景分解', l5: 'l5 正文' }
        let content = ''
        if (lv === 'l1') content = activeArc?.l1 || arcState?.levels?.l1?.text || ''
        else if (lv === 'l2') content = activeArc?.l2 || arcState?.levels?.l2?.text || ''
        else if (lv === 'l3') content = l3chapters.map((ch, i) => `第${i + 1}章${ch.title ? ' ' + ch.title : ''}：${ch.core || ch.beats?.[0] || ''}`).join('\n')
        else if (lv === 'l4') content = l4scenes.map(sc => sc.name || '').join('\n')
        else if (lv === 'l5') content = l5v || ''
        setDiscussTarget({ kind: 'level', lv, name: names[lv] || lv, content, icon: lv.toUpperCase() })
        setAiTab('targeted')
    }
    const clearArcTemplate = async (arcId) => {
        const r = await run('清除情节模板', () => phAiArcSetTemplate(bookRoot, arcId, ''))
        if (r?.ok) {
            setNotice('已清除情节模板，l2-l5 将按默认方式重新生成')
            await refreshWorkbench(bookRoot)
        }
    }
    const handleSetActiveChapter = async (idx) => {
        if (!activeArcId) return
        // 【v7.8.3 自动保存】所有文字修改自动保存——切章前若 l5 有编辑未保存，先自动保存；
        // 后端按章暂存 l4/l5，切回不丢失，不再弹「切换将丢失」确认。
        const curIdx = arcState?.active_chapter ?? 0
        if (idx !== curIdx && l5Draft !== null && l5Draft !== l5Text) {
            await handleSaveL5()
        }
        const r = await run('切章', () => phAiArcSetActiveChapter(bookRoot, activeArcId, idx))
        if (r?.ok) {
            setNotice(`已切到第 ${idx + 1} 章：${r.chapter?.title || ''}`)
            setLastScore(null); setPollutionResult(null)
            await refreshWorkbench(bookRoot)
        }
    }
    const handleModify = async () => {
        if (!modifyInput.trim() || !modifyLevel || !activeArcId) return
        const r = await run('修改', () => phAiArcModify(bookRoot, activeArcId, modifyLevel, modifyInput))
        if (r?.ok) {
            setModifyInput(''); setModifyLevel(null)
            setNotice(`${modifyLevel} 已按意见改写，下游级已清空，请重新生成`)
            await refreshWorkbench(bookRoot)
        }
    }
    const handleFinishArc = async () => {
        if (!activeArcId) return
        const r = await run('完结本情节', () => phAiArcFinish(bookRoot, activeArcId))
        if (r?.ok) {
            setNotice('本情节已标记完成（下一情节将带入本情节结局作前文锚点）')
            await refreshWorkbench(bookRoot)
        }
    }

    // 直接编辑 l1/l2：就地保存
    const handleSaveLevel = async () => {
        if (!activeArcId || !editLevel) return
        const r = await run(`保存 ${editLevel}`, () => phAiArcSetLevel(bookRoot, activeArcId, editLevel, editText))
        if (r?.ok) {
            setNotice(`${editLevel} 已保存（下游已清空，需重新生成）`)
            setEditLevel(null); setEditText('')
            await refreshWorkbench(bookRoot)
        }
    }
    // 直接编辑 l5：正文栏保存
    const handleSaveL5 = async () => {
        if (!activeArcId || l5Draft === null) return
        const r = await run('保存正文', () => phAiArcSetLevel(bookRoot, activeArcId, 'l5', l5Draft))
        if (r?.ok) {
            setNotice('正文已保存')
            await refreshWorkbench(bookRoot)
        }
    }
    // 直接编辑 l3（结构化：title/core/beats）
    const handleSaveL3 = async () => {
        if (!activeArcId || !l3Edit) return
        const data = { title: l3Edit.title || '', core: l3Edit.core || '', beats: l3Edit.beats || [] }
        const r = await run('保存 l3', () => phAiArcSetLevel(bookRoot, activeArcId, 'l3', '', data, l3Edit.idx ?? null))
        if (r?.ok) {
            setL3Edit(null)
            setNotice('l3 已保存（l4/l5 已清空，需重新生成）')
            await refreshWorkbench(bookRoot)
        }
    }
    // 直接编辑 l4（场景列表）
    const handleSaveL4 = async () => {
        if (!activeArcId || !l4Edit) return
        const r = await run('保存 l4', () => phAiArcSetLevel(bookRoot, activeArcId, 'l4', '', l4Edit, null))
        if (r?.ok) {
            setL4Edit(null)
            setNotice('l4 已保存（l5 已清空，需重新生成）')
            await refreshWorkbench(bookRoot)
        }
    }
    // 直接编辑 l3 多章整组
    const handleSaveL3Multi = async () => {
        if (!activeArcId || !l3ChaptersDraft) return
        const clean = l3ChaptersDraft.map(ch => ({
            title: (ch.title || '').trim(),
            core: (ch.core || '').trim(),
            beats: Array.isArray(ch.beats) ? ch.beats.map(s => s.trim()).filter(Boolean) : [],
        })).filter(ch => ch.title || ch.core)
        const r = await run('保存整组章纲', () => phAiArcSetLevel(bookRoot, activeArcId, 'l3', '', { chapters: clean }, null))
        if (r?.ok) {
            setL3ChaptersDraft(null)
            setNotice('整组章纲已保存（l4/l5 已清空，需重新生成）')
            await refreshWorkbench(bookRoot)
        }
    }
    // 树：查看已落盘章节（正文栏显示该章文本 + 评分）
    const viewFinalizedChapter = (arcId, idx) => {
        setActiveArcId(arcId)
        setViewFin({ arcId, idx })
    }

    // ── 就地编辑（防抖自动保存） ──────────────────────────────────
    const startEdit = (key) => setEditStatus(s => ({ ...s, [key]: 'editing' }))
    const finishEdit = (key) => {
        // 只清状态，不触发保存（由 onBlur 或 Enter 触发）
        setEditStatus(s => {
            const ns = { ...s }
            delete ns[key]
            return ns
        })
    }
    const debouncedSave = (key, saveFn, wait = 800) => {
        if (editTimers.current[key]) clearTimeout(editTimers.current[key])
        setEditStatus(s => ({ ...s, [key]: 'saving' }))
        editTimers.current[key] = setTimeout(async () => {
            try {
                const ok = await saveFn()
                if (ok) {
                    setEditStatus(s => ({ ...s, [key]: 'saved' }))
                    setTimeout(() => {
                        setEditStatus(s => {
                            const ns = { ...s }
                            delete ns[key]
                            return ns
                        })
                    }, 1200)
                } else {
                    setEditStatus(s => ({ ...s, [key]: 'error' }))
                }
            } catch (e) {
                setEditStatus(s => ({ ...s, [key]: 'error' }))
            }
        }, wait)
    }

    // 情节名编辑
    const handleArcNameEdit = (arcId, newName) => {
        debouncedSave(`arcname_${arcId}`, async () => {
            const r = await phAiArcUpdate(bookRoot, arcId, { name: newName })
            if (r?.ok) {
                setNotice('情节名已保存')
                await refreshWorkbench(bookRoot)
                return true
            }
            return false
        })
    }

    // l1 一句话编辑
    const handleL1Edit = (text) => {
        debouncedSave('l1_text', async () => {
            const r = await run('保存 l1', () => phAiArcSetLevel(bookRoot, activeArcId, 'l1', text, null, null))
            if (r?.ok) {
                await refreshWorkbench(bookRoot)
                return true
            }
            return false
        })
    }

    // l2 概要编辑
    const handleL2Edit = (text) => {
        debouncedSave('l2_text', async () => {
            const r = await run('保存 l2', () => phAiArcSetLevel(bookRoot, activeArcId, 'l2', text, null, null))
            if (r?.ok) {
                await refreshWorkbench(bookRoot)
                return true
            }
            return false
        }, 1200)
    }

    // 元素名编辑
    const handleElemNameEdit = (kind, eid, newName) => {
        debouncedSave(`elem_${kind}_${eid}`, async () => {
            const r = await phAiElementPut(bookRoot, kind, eid, { name: newName })
            if (r?.ok) {
                setNotice('元素名已保存')
                await refreshWorkbench(bookRoot)
                return true
            }
            return false
        })
    }

    // ── 对话（工具循环：预检索 + 可执行动作） ──────────────────────
    const handleChat = async (text) => {
        if (!text || chatBusy || !bookRoot) return
        // 【2026-09-07 讨论对象真注入】清零后端改动：讨论对象拼进消息文本，清除前每次发送都带上
        let fullText = text
        if (discuss) {
            const excerpt = String(discuss.content || '').trim().slice(0, 200)
            fullText = `【讨论对象：${discuss.name}】${excerpt ? '\n' + excerpt : ''}\n\n${text}`
        }
        const history = [...messages, { role: 'user', content: fullText }]
        setMessages(history)
        setChatBusy(true); setPendingConfirm(null)

        // 【2026-08-18】书级模式 或 书未初始化 → 走 init 模式（讨论全书设定/人物/世界观）
        const useInitMode = bookLevelMode || !(initStatus && initStatus.initialized)
        const arcId = useInitMode ? '' : (activeArcId || '')
        // 【Phase 3】会话 id：首条消息时建档，之后稳定传给后端（单对话层工作记忆按它隔离注入）
        const sid = chatSessionId || `cs_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`
        if (!chatSessionId) setChatSessionId(sid)
        upsertChatSession(history, sid)   // 先落用户消息（中途失败也留痕）

        // 【Phase 4】流式：先插空气泡，token 增量拼接、工具/预检索行实时上屏；
        // SSE 异常自动落回 REST（phAiArcChat），done 结果与 REST 同构 → 收尾逻辑共用。
        const aiIdx = history.length
        setMessages([...history, { role: 'assistant', content: '', tool_events: [], streaming: true }])
        const patchAI = (upd) => setMessages(prev => {
            const next = [...prev]
            if (next[aiIdx]) {
                const p = typeof upd === 'function' ? upd(next[aiIdx]) : upd
                next[aiIdx] = { ...next[aiIdx], ...p }
            }
            return next
        })

        try {
            let r
            const sres = await phAiArcChatStream(bookRoot, arcId, history, {
                onToken: (t) => patchAI(m => ({ content: (m.content || '') + t })),
                onToolCall: (ev) => patchAI(m => ({ tool_events: [...(m.tool_events || []), ev] })),
            }, { mode: useInitMode ? 'init' : 'normal', session_id: sid })
            if (sres.ok) {
                r = sres.result
            } else {
                console.warn('[chat] 流式失败，落回 REST：', sres.error)
                r = await phAiArcChat(bookRoot, arcId, history, false, null, null, useInitMode ? 'init' : 'normal', sid)
            }
            if (r?.ok === false) {
                const errs = [...history, { role: 'assistant', content: `（错误：${r.error}）` }]
                setMessages(errs)
                upsertChatSession(errs, sid)
                return
            }
            const evs = r.tool_events || []
            const aiMsg = { role: 'assistant', content: r.reply || '', tool_events: evs }
            const finalMsgs = [...history, aiMsg]
            setMessages(finalMsgs)
            upsertChatSession(finalMsgs, sid)
            if (r.changed) {
                await refreshWorkbench(bookRoot)
                phAiInitStatus(bookRoot).then(setInitStatus).catch(() => setInitStatus(null))
            }
            if (r.new_arc_id) setActiveArcId(r.new_arc_id)
            // 检查初始化状态更新
            const settingsNow = await phAiInitStatus(bookRoot).catch(() => null)
            if (settingsNow?.initialized) setInitStatus(settingsNow)
            if (r.pending?.length) setPendingConfirm(r.pending[0])
        } catch (e) {
            const errs = [...history, { role: 'assistant', content: `（对话失败：${e.message || e}）` }]
            setMessages(errs)
            upsertChatSession(errs, sid)
        } finally {
            setChatBusy(false)
        }
    }

    // 检索面板「发给 AI 讨论」：无情节时提示，有情节直接发
    const sendSearchToChat = (text) => {
        if (!activeArcId) { setError('请先到大纲与章纲创建/选择情节，再发 AI 讨论'); return }
        handleChat(text)
    }

    // ── 评分 / 落盘 ────────────────────────────────────────────────
    const handleScore = async () => {
        const currentL5 = l5Draft !== null ? l5Draft : l5Text
        if (!activeArcId || !currentL5.trim()) { setError('请先生成正文（l5）再评分'); return }
        setScoreBusy(true); setError('')
        try {
            const r = await phAiChapterScore(bookRoot, activeArcId, currentL5)
            if (r?.ok === false) setError(r.error || '评分失败')
            else {
                setLastScore(r)
                setNotice(`评分完成：意图 ${r.intent_score} / 质量 ${r.quality_score} / 综合 ${r.overall}`)
            }
        } catch (e) { setError(`评分失败：${e.message || e}`) }
        finally { setScoreBusy(false) }
    }
    const handlePollution = async () => {
        const currentL5 = l5Draft !== null ? l5Draft : l5Text
        if (!activeArcId || !currentL5.trim()) return
        setScoreBusy(true); setError('')
        try {
            const r = await phAiChapterPollution(bookRoot, activeArcId, currentL5)
            if (r?.ok === false) setError(r.error || '检测失败')
            else {
                setPollutionResult(r)
                setNotice(r.polluted ? `⚠ 检出 ${r.hits?.length || 0} 处未参与元素污染` : '✓ 未发现未参与元素污染')
            }
        } catch (e) { setError(`检测失败：${e.message || e}`) }
        finally { setScoreBusy(false) }
    }
    const handleFinalize = async () => {
        if (!activeArcId) return
        // 未保存的正文草稿先落库——否则落盘用的是旧正文（l5 草稿编辑丢失）
        if (l5Draft !== null && l5Draft !== l5Text) {
            const saved = await run('保存正文', () => phAiArcSetLevel(bookRoot, activeArcId, 'l5', l5Draft))
            if (!saved) return
        }
        const r = await run('保存本章到书', () => phAiChapterFinalize(bookRoot, activeArcId, null))
        if (r?.ok) {
            setNotice(`已落盘：${r.files?.outline} + ${r.files?.prose} + ${r.files?.report}（双评分：意图 ${r.scores.intent_score} / 质量 ${r.scores.quality_score}）`)
            setLastScore(null)
            await refreshWorkbench(bookRoot)
        }
    }

    // 创作助手待确认工具分发：根据 pending.tool 调用不同动作
    const handleConfirmPending = async (edits) => {
        if (!pendingConfirm) return
        const tool = pendingConfirm.tool
        const baseArgs = pendingConfirm.args || {}
        // 用户可在同意前修改提案内容 → 用改过的 args 应用
        const args = edits && Object.keys(edits).length ? { ...baseArgs, ...edits } : baseArgs
        setPendingConfirm(null)
        if (tool === 'finalize') {
            await handleFinalize()
            return
        }
        if (tool === 'finish_arc') {
            if (!activeArcId) return
            const r = await run('标记情节完成', () => phAiArcFinish(bookRoot, activeArcId))
            if (r?.ok) {
                setNotice('本情节已标记完成')
                await refreshWorkbench(bookRoot)
            }
            return
        }
        // 其它内容工具：用户已同意 → 经 apply 端点真执行（args 为用户确认/编辑后的版本）
        // 【Phase 3】带 session_id：remember scope=session 的确认应用落到本会话工作记忆
        const r = await run('执行助手操作', () => phAiArcChatApply(bookRoot, activeArcId || '', tool, args, chatSessionId))
        if (r?.ok) {
            if (r.event?.new_arc_id) setActiveArcId(r.event.new_arc_id)
            if (r.event?.summary) setNotice(r.event.summary)
            await refreshWorkbench(bookRoot)
            loadMemory()
        }
    }
    const handleRegenerateL5 = async () => {
        if (!activeArcId) return
        const r = await run('重生成正文', () => phAiArcRegenerateL5(bookRoot, activeArcId))
        if (r?.ok) {
            if (r.regenerated) setNotice(`已重生成正文（修正 ${r.pollution_was?.length || 0} 处未参与元素污染），请重新评分`)
            else setNotice(r.message || '未检出污染，无需重生成')
            setLastScore(null); setPollutionResult(null)
            await refreshWorkbench(bookRoot)
        }
    }

    // ══════════════════════════════════════════════════════════════
    // 渲染
    // ══════════════════════════════════════════════════════════════

    // 初始化卡
    // 基本设定卡（可编辑字段源 → 保存 / 重新生成）——始终显示，无数据时展示空表单
    const renderBasicSettings = () => {
        const draft = settingsDraft || {}
        const generated = settings?.generated_at
        return (
            <div className="section-block" style={{ borderLeft: generated ? '3px solid var(--green)' : '3px solid var(--amber)' }}>
                <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>
                    基本设定{generated ? '' : '（未生成）'}
                    {generated && <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--ink-sub)', marginLeft: 8 }}>上次生成：{generated}</span>}
                </div>
                <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 8 }}>
                    可编辑字段源（非一次性 init）：改字段 → 保存；点「重新生成」→ LLM 更新 设定集/*.md 与 元素清单
                    （保留你手改的元素条目）。硬约束由 l1-l5 不变prompt 承担（fixed_prompts.json 运行时可编辑）。
                </div>
                <BasicSettingsForm settings={draft} onChange={setSettingsDraft} />
                <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                    <button style={btnStyle(busy || genBusy, true)} disabled={busy || genBusy} onClick={saveSettings}>保存基本设定</button>
                    <button style={{ ...btnStyle(busy || genBusy, false), color: 'var(--cinnabar)' }} disabled={busy || genBusy} onClick={regenerateSettings}>
                        {genBusy || genTaskId ? '生成中…' : '重新生成设定集'}
                    </button>
                    {generated && (
                        <span style={{ fontSize: 12, color: 'var(--ink-sub)', alignSelf: 'center' }}>
                            元素清单 {elements.characters?.length} 角色 / {elements.items?.length} 物品 / {elements.settings?.length} 设定
                        </span>
                    )}
                </div>
            </div>
        )
    }

    // 顶部系统状态条：检查清单——哪些正常 ✓ / 哪些空或未配置 ⚠ / 哪些不支持 ✗（一行 chips）
    const renderStatusBar = () => {
        const presets = apiLib?.text_presets || []
        const cur = presets.find(p => p.is_current) || presets.find(p => p.id === apiLib?.current_text_id) || null
        const curModel = cur?.fields?.ARK_MODEL_PRO || ''
        const embedModel = apiLib?.embed_config?.fields?.EMBED_MODEL || ''
        const hasSettings = !!(settings?.generated_at)
        const elemCount = Object.values(elements || {}).flat().length
        const arcCount = (arcs.arcs || []).length
        const finCount = (arcs.arcs || []).reduce((n, a) => n + (a.chapters || []).length, 0)
        const item = (label, ok, hint, warn) => (
            <span title={hint} style={{
                display: 'inline-flex', gap: 4, alignItems: 'center', padding: '1px 8px', borderRadius: 10, fontSize: 12,
                background: ok ? 'var(--green-wash)' : (warn ? 'var(--amber-wash)' : 'var(--cinnabar-wash)'),
                color: ok ? 'var(--green)' : (warn ? 'var(--amber)' : 'var(--cinnabar-d)'),
            }}>
                {ok ? '✓' : (warn ? '⚠' : '✗')} {label}{hint ? ` · ${hint}` : ''}
            </span>
        )
        return (
            <div className="section-block" style={{ borderLeft: '3px solid var(--dai)', padding: '6px 12px' }}>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    <SearchHealthPanel compact bookRoot={bookRoot} />
                    {item('文字模型', !!cur, cur ? `${cur.name}${curModel ? `·${curModel}` : ''}` : '未选预设（用 .env 默认）', !cur)}
                    {item('向量模型', !!embedModel, embedModel || '', !embedModel)}
                    {item('基本设定', hasSettings, hasSettings ? '' : '未生成', !hasSettings)}
                    {item('元素', elemCount > 0, elemCount ? String(elemCount) : '', !(elemCount > 0))}
                    {item('情节', arcCount > 0, arcCount ? `${arcCount} 个` : '', !(arcCount > 0))}
                    {item('已落盘', finCount > 0, finCount ? `${finCount} 章` : '', !(finCount > 0))}
                    {item('初始化', !!(initStatus && initStatus.initialized),
                        initStatus && !initStatus.initialized ? `进行中（${initStatus.stage === 'settings' ? '填基本设定' : '建开篇情节'}）` : '完成',
                        !(initStatus && initStatus.initialized))}
                    <a href="#/api-presets" style={{ color: 'var(--cinnabar)', textDecoration: 'none', fontSize: 12 }}>管理预设</a>
                </div>
            </div>
        )
    }

    // ── 待审核卡片 / 备注 操作 ───────────────────────────────────────────
    const loadPendingCards = useCallback(async () => {
        if (!bookRoot) return
        try {
            const r = await phAiPendingGet(bookRoot)
            setPendingCards(r?.items || r?.pending || [])
        } catch { /* ignore */ }
    }, [bookRoot])

    const approveCard = async (pid) => {
        try {
            await phAiPendingApprove(bookRoot, pid)
            setNotice('已通过')
            await loadPendingCards()
            await refreshWorkbench(bookRoot)
        } catch (e) { setError(`审核失败：${e.message || e}`) }
    }

    const rejectCard = async (pid) => {
        try {
            await phAiPendingReject(bookRoot, pid)
            setNotice('已驳回')
            await loadPendingCards()
        } catch (e) { setError(`驳回失败：${e.message || e}`) }
    }

    const addNote = async (scope) => {
        const content = newNoteInput.trim()
        if (!content) return
        try {
            await phNotesPut({ book_root: bookRoot, scope, content })
            setNewNoteInput('')
            setNotice('备注已添加')
            await refreshWorkbench(bookRoot)
        } catch (e) { setError(`添加备注失败：${e.message || e}`) }
    }

    const deleteNote = async (nid) => {
        try {
            await phNotesPut({ book_root: bookRoot, nid })
            setNotice('备注已删除')
            await refreshWorkbench(bookRoot)
        } catch (e) { setError(`删除备注失败：${e.message || e}`) }
    }

    // 元素清单管理卡
    const renderElements = () => {
        const draft = elementsDraft || elements
        // 元素 ↔ 情节 双向连接：每个元素 → 使用它的情节（纯前端从各情节 selected 计算）
        const usageByElem = {}
        ;(arcs.arcs || []).forEach(a => {
            const asel = a.selected || {}
            ;[...(asel.characters || []), ...(asel.items || []), ...(asel.settings || [])]
                .filter(Boolean)
                .forEach(id => { (usageByElem[id] = usageByElem[id] || []).push({ arcId: a.id, arcName: a.name }) })
        })
        const renderRow = (kind, e, i) => {
            const isSettings = kind === 'settings'
            const aliasField = isSettings ? 'terms' : 'alias'
            const aliasVal = Array.isArray(e[aliasField]) ? (e[aliasField] || []).join('，') : (e[aliasField] || '')
            return (
                <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 4, flexWrap: 'wrap' }}>
                    <input value={e.name || ''} onChange={ev => updateDraftElem(kind, i, 'name', ev.target.value)}
                        placeholder={`${KIND_LABEL[kind]}名`} style={{ width: 110, padding: 6, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13 }} />
                    <input value={aliasVal} onChange={ev => updateDraftElem(kind, i, aliasField, ev.target.value.split(/[,，、\s]+/).filter(Boolean))}
                        placeholder={isSettings ? '固定叫法/术语，逗号分隔' : '别名/简称，逗号分隔'}
                        style={{ width: 160, padding: 6, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13 }} />
                    <input value={e.desc || ''} onChange={ev => updateDraftElem(kind, i, 'desc', ev.target.value)}
                        placeholder="身份与关键特质 / 用途 / 与剧情相关的关键设定"
                        style={{ flex: 1, minWidth: 160, padding: 6, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13 }} />
                    {/* scope 选择器 */}
                    <select value={e.scope || 'global'} onChange={ev => updateDraftElem(kind, i, 'scope', ev.target.value)}
                        style={{ fontSize: 11, padding: '2px 4px', border: '1px solid var(--line)', borderRadius: 4, background: 'var(--paper)', cursor: 'pointer' }}>
                        <option value="global">全局</option>
                        {activeArcId && <option value={`arc:${activeArcId}`}>局部（本情节）</option>}
                    </select>
                    {(usageByElem[e.id] || []).length > 0 && (
                        <span style={{ fontSize: 11, color: 'var(--dai)', display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
                            用:
                            {usageByElem[e.id].map((u, j) => (
                                <span key={j} onClick={() => { setTab('write'); setWrTab('outline'); selectArc(u.arcId) }} title={`跳到大纲与章纲情节「${u.arcName}」`}
                                    style={{ cursor: 'pointer', padding: '1px 7px', borderRadius: 10, background: 'var(--dai-wash)', border: '1px solid var(--line)' }}>
                                    {u.arcName}
                                </span>
                            ))}
                        </span>
                    )}
                    <button style={btnStyle(false, false)} onClick={() => delDraftElem(kind, i)}>✕</button>
                </div>
            )
        }
        // 当前情节参与元素（连接情节：设定 ↔ 大纲）
        const sel = activeArc?.selected || {}
        const nameById = {}
        Object.values(elements).flat().forEach(e => { if (e?.id) nameById[e.id] = e.name })
        const usedNames = [
            ...(sel.characters || []).map(id => nameById[id] || id),
            ...(sel.items || []).map(id => nameById[id] || id),
            ...(sel.settings || []).map(id => nameById[id] || id),
        ].filter(Boolean)
        const usedIds = new Set([
            ...(sel.characters || []), ...(sel.items || []), ...(sel.settings || []),
        ])
        // scope 分组辅助：将元素按 scope 分类，返回 {indices, elems}
        const groupByScope = (arr) => {
            const globalG = [], localG = [], archivedG = []
            ;(arr || []).forEach((e, i) => {
                const sc = e.scope || 'global'
                if (sc === 'global') globalG.push({ e, i })
                else if (sc.startsWith('archived:')) archivedG.push({ e, i })
                else localG.push({ e, i })  // arc:<id>
            })
            return { globalG, localG, archivedG }
        }
        const scopeTagStyle = (type) => ({
            fontSize: 10, padding: '1px 6px', borderRadius: 8, marginRight: 4, flexShrink: 0,
            background: type === 'global' ? 'var(--dai-wash)' : type === 'local' ? 'var(--green-wash)' : 'var(--amber-wash)',
            color: type === 'global' ? 'var(--dai)' : type === 'local' ? 'var(--green)' : 'var(--amber)',
        })
        return (
            <div className="section-block">
                <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>元素清单（本书全部角色/物品/设定，参与与否在情节内选择）</div>
                <div style={{ padding: 8, borderRadius: 6, border: '1px solid var(--line)', background: 'var(--dai-wash)', marginBottom: 8, fontSize: 12 }}>
                    {activeArc ? (
                        <>当前情节「{activeArc.name}」参与元素：
                            {usedNames.length
                                ? <b style={{ color: 'var(--dai)' }}>{usedNames.join('、')}</b>
                                : <span style={{ color: 'var(--cinnabar-d)' }}>未选择（到大纲与章纲选）</span>}
                        </>
                    ) : <span style={{ color: 'var(--ink-mute)' }}>选一个情节后显示其参与元素</span>}
                </div>
                {['characters', 'items', 'settings'].map(kind => {
                    const { globalG, localG, archivedG } = groupByScope(draft[kind])
                    const renderScopedRows = (group, scopeType) => group.map(({ e, i }) => {
                        const participating = e.id && usedIds.has(e.id)
                        return (
                            <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 4, flexWrap: 'wrap' }}>
                                <span title={participating ? '本情节参与' : '本情节未参与'} style={{
                                    width: 8, height: 8, borderRadius: 4, flexShrink: 0,
                                    background: participating ? 'var(--green)' : 'var(--line-soft)',
                                }} />
                                <span style={scopeTagStyle(scopeType)}>
                                    {scopeType === 'global' ? '全局' : scopeType === 'local' ? '局部' : '归档'}
                                </span>
                                {renderRow(kind, e, i)}
                            </div>
                        )
                    })
                    return (
                        <div key={kind} style={{ marginBottom: 8 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
                                {KIND_LABEL[kind]}
                                <button style={{ ...btnStyle(false, false), marginLeft: 8, padding: '2px 8px' }} onClick={() => addDraftElem(kind)}>+ 添加</button>
                            </div>
                            {globalG.length > 0 && (
                                <div style={{ marginBottom: 4 }}>
                                    <div style={{ fontSize: 11, color: 'var(--dai)', marginBottom: 2, fontWeight: 600 }}>全局元素</div>
                                    {renderScopedRows(globalG, 'global')}
                                </div>
                            )}
                            {localG.length > 0 && (
                                <div style={{ marginBottom: 4 }}>
                                    <div style={{ fontSize: 11, color: 'var(--green)', marginBottom: 2, fontWeight: 600 }}>局部元素</div>
                                    {renderScopedRows(localG, 'local')}
                                </div>
                            )}
                            {archivedG.length > 0 && (
                                <div style={{ marginBottom: 4 }}>
                                    <div style={{ fontSize: 11, color: 'var(--amber)', marginBottom: 2, fontWeight: 600 }}>归档元素</div>
                                    {renderScopedRows(archivedG, 'archived')}
                                </div>
                            )}
                            {(draft[kind] || []).length === 0 && <div style={{ fontSize: 12, color: 'var(--ink-mute)' }}>（暂无{ KIND_LABEL[kind] }）</div>}
                        </div>
                    )
                })}
                <button style={btnStyle(busy, true)} disabled={busy} onClick={saveElements}>保存元素清单</button>
                <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--ink-sub)' }}>绿点=当前情节参与；「用」=使用该元素的情节（点击跳转）；别名/术语参与「未选元素零出现」检测</span>
            </div>
        )
    }

    // 情节线性卡片流：设定在顶部作共享字典 → 情节按故事顺序一列卡片；点卡展开编辑（线性列表视图）
    const renderArcCards = () => {
        const list = arcs.arcs || []
        if (list.length === 0) {
            return (
                <div style={{ fontSize: 13, color: 'var(--ink-mute)', padding: 14, textAlign: 'center', border: '1px dashed var(--line)', borderRadius: 8 }}>
                    （还没有情节——在上方填 l1 一句话极简剧情 + 章数，点「新建情节」）
                </div>
            )
        }
        const nameById = {}
        Object.values(elements).flat().forEach(e => { if (e?.id) nameById[e.id] = e.name })
        // v6.4.2 章标题去前缀归一化（与 finalize 剥「第X章」前缀一致）
        const stripCh = (t) => (t || '').replace(/^第\s*\d+\s*章[\s：:、．.，,]?/, '')
        return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {list.map((arc, ai) => {
                    const isActive = arc.id === activeArcId
                    const sel = arc.selected || {}
                    const partNames = [
                        ...(sel.characters || []).map(id => nameById[id] || id),
                        ...(sel.items || []).map(id => nameById[id] || id),
                        ...(sel.settings || []).map(id => nameById[id] || id),
                    ].filter(Boolean)
                    const st = arc.state || {}
                    // 阶梯进度：✓绿=已确认 / ●黄=已生成待确认 / ○灰=未生成
                    const levelChips = LEVEL_ORDER.map(lv => {
                        const l = (st.levels || {})[lv] || {}
                        const filled = lv === 'l3' ? (l.data || l.text) : lv === 'l4' ? (l.scenes?.length) : l.text
                        const conf = !!l.confirmed
                        return <span key={lv} title={`${lv}：${conf ? '已确认' : filled ? '已生成待确认' : '未生成'}`}
                            style={{ color: conf ? 'var(--green)' : filled ? 'var(--amber)' : 'var(--line-soft)', fontWeight: 700 }}>
                            {conf ? '✓' : filled ? '●' : '○'}{lv.replace('l', '')}
                        </span>
                    })
                    // 章列表：已落盘 + 草稿（去重 v6.4.2）
                    const fin = arc.chapters || []
                    const finTitles = new Set((fin || []).map(c => c && stripCh(c.title)))
                    let drafts = []
                    const l3d = st.levels?.l3?.data || {}
                    if (Array.isArray(l3d.chapters)) drafts = l3d.chapters
                    else if (l3d && l3d.title) drafts = [l3d]
                    const pending = (drafts || []).map((ch, i) => ({ ch, i }))
                        .filter(({ ch }) => ch && ch.title && !finTitles.has(stripCh(ch.title)))
                    return (
                        <div key={arc.id} style={{ border: `1px solid ${isActive ? 'var(--dai)' : 'var(--line)'}`, borderRadius: 8, background: isActive ? 'var(--paper-raised)' : 'var(--paper)' }}>
                            {/* 卡头（点击切换 active 展开编辑） */}
                            <div onClick={() => selectArc(arc.id)}
                                style={{ cursor: 'pointer', padding: '10px 12px', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                                <b style={{ color: isActive ? 'var(--dai)' : 'var(--ink-sub)', fontSize: 14 }}>{ai + 1}. {arc.name}</b>
                                <span style={badgeStyle(arc.status === 'done')}>{arc.status === 'done' ? '已完成' : '进行中'}</span>
                                <span style={{ display: 'flex', gap: 5 }}>{levelChips}</span>
                                {partNames.length > 0 && (
                                    <span style={{ fontSize: 11, color: 'var(--ink-sub)', display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
                                        参与:
                                        {partNames.map(n => (
                                            <span key={n} style={{ padding: '1px 7px', borderRadius: 10, background: 'var(--dai-wash)', border: '1px solid var(--line)', color: 'var(--dai)' }}>{n}</span>
                                        ))}
                                    </span>
                                )}
                                {partNames.length === 0 && <span style={{ fontSize: 11, color: 'var(--ink-mute)' }}>（未选参与元素）</span>}
                                <span style={{ marginLeft: 'auto', fontSize: 12, color: isActive ? 'var(--ink-mute)' : 'var(--dai)' }}>{isActive ? '▾ 收起编辑' : '▸ 展开编辑'}</span>
                            </div>
                            {/* 折叠态也显示章列表 */}
                            {!isActive && (fin.length > 0 || pending.length > 0) && (
                                <div style={{ padding: '0 12px 8px', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                    {fin.map((c, i) => (
                                        <span key={`f${i}`} onClick={() => { setTab('write'); setWrTab('prose'); viewFinalizedChapter(arc.id, i) }}
                                            title={`查看第${c.num}章正文/评分`}
                                            style={{ cursor: 'pointer', fontSize: 11, color: 'var(--ink-sub)', padding: '2px 8px', borderRadius: 10, border: '1px solid var(--line)', background: 'var(--paper-raised)' }}>
                                            第{String(c.num).padStart(4, '0')}章 {c.title}
                                        </span>
                                    ))}
                                    {pending.map(({ ch, i }) => (
                                        <span key={`d${i}`} onClick={() => { selectArc(arc.id); handleSetActiveChapter(i) }} title="草稿章（点击切到该章编辑）"
                                            style={{ cursor: 'pointer', fontSize: 11, color: 'var(--amber)', padding: '2px 8px', borderRadius: 10, border: '1px solid var(--amber-wash)', background: 'var(--amber-wash)' }}>
                                            {stripCh(ch.title) || arcChapterLabel(arc, i)}（草稿）
                                        </span>
                                    ))}
                                </div>
                            )}
                            {/* 展开态 = 完整情节面板（l1/l2 + 元素选择 + 阶梯编辑） */}
                            {isActive && <div style={{ borderTop: '1px solid var(--line)', padding: 12 }}>{renderArcPanel(arc)}</div>}
                        </div>
                    )
                })}
            </div>
        )
    }

    // 大纲与章纲页（写作组第二项：新建情节 → 全书思维导图 + 当前情节详情面板 + 对话/检索）
    const renderArcs = () => {
        return (
            <div className="section-block">
                <div className="sec-h" style={{ margin: '24px 0 14px' }}>
                    <span className="no">§</span>
                    <span className="t">剧情情节</span>
                    <span className="hint">逐情节创作 · 每情节选一次参与元素</span>
                    <span style={{ display: 'flex', gap: 2, marginLeft: 'auto' }}>
                        <button type="button" onClick={() => setArcView('map')} style={arcView === 'map' ? viewBtnActive : viewBtn}>思维导图</button>
                        <button type="button" onClick={() => setArcView('list')} style={arcView === 'list' ? viewBtnActive : viewBtn}>线性列表</button>
                    </span>
                </div>

                {/* 新建情节（editorial 行） */}
                <div style={{ padding: '14px 0', borderTop: '1px solid var(--line)', borderBottom: '1px solid var(--line-soft)', marginBottom: 12 }}>
                    <div style={{ fontFamily: 'var(--font-serif)', fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--ink)' }}>＋ 新建剧情情节</div>
                    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
                        <textarea value={newL1} onChange={e => setNewL1(e.target.value)} rows={2} placeholder="本情节一句话极简剧情（l1），例：林昭查出灭门案幕后指向太师，决定设局引蛇出洞"
                            style={{ flex: 1, minWidth: 260, padding: '8px 0', border: 'none', borderBottom: '1px solid var(--line)', fontSize: 13, background: 'transparent', color: 'var(--ink)', fontFamily: 'var(--font-serif)', lineHeight: 1.8 }} />
                        <label style={{ fontSize: 12, color: 'var(--ink-mute)', fontFamily: 'var(--font-serif)', letterSpacing: '.05em' }}>
                            章数
                            <input type="number" min={1} max={20} value={newN}
                                onChange={e => setNewN(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
                                style={{ width: 56, padding: '6px 0', border: 'none', borderBottom: '1px solid var(--line)', fontSize: 13, marginLeft: 6, background: 'transparent', color: 'var(--ink)' }} />
                        </label>
                        <label style={{ fontSize: 12, color: 'var(--ink-mute)', fontFamily: 'var(--font-serif)', display: 'flex', alignItems: 'center', gap: 5 }}>
                            <input type="checkbox" checked={newCarryPrev} onChange={e => setNewCarryPrev(e.target.checked)} />
                            带入上一情节结局
                        </label>
                        <button style={btnStyle(busy || !newL1.trim(), true)} disabled={busy || !newL1.trim()} onClick={createArc}>＋ 新建情节</button>
                    </div>
                </div>
                {(arcs.arcs || []).length > 0 && (
                    <div style={{ fontSize: 11, color: 'var(--ink-mute)', marginBottom: 10, letterSpacing: '.04em', fontFamily: 'var(--font-serif)' }}>
                        下一章号：第 {String(arcs.next_chapter_num).padStart(4, '0')} 章
                        {arcView === 'map' ? ' · 思维导图：点情节节点选中编辑，点章节点查看/切换' : ' · 列表：点卡头展开编辑'}
                    </div>
                )}

                {/* 同一情节树，两种可视化：思维导图（点节点→下方详情面板）/ 线性列表（卡内展开编辑） */}
                {(arcs.arcs || []).length > 0 ? (
                    arcView === 'map' ? (
                        <>
                            <StoryMindMap
                                bookTitle={bookTitle}
                                arcs={arcs.arcs || []}
                                elements={elements}
                                activeArcId={activeArcId}
                                onSelectArc={(id) => selectArc(id)}
                                onViewChapter={(arcId, idx) => { setTab('write'); setWrTab('prose'); viewFinalizedChapter(arcId, idx) }}
                                onSelectDraft={(arcId, idx) => { selectArc(arcId); handleSetActiveChapter(idx) }}
                            />
                            {/* 当前情节编辑面板（思维导图点情节节点后在此编辑：l1/l2 + 参与元素 + 阶梯） */}
                            {activeArc && (
                                <div className="section-block" style={{ marginTop: 0 }} ref={panelRef}>
                                    {renderArcPanel(activeArc)}
                                </div>
                            )}
                        </>
                    ) : (
                        renderArcCards()
                    )
                ) : (
                    <div style={{ fontSize: 13, color: 'var(--ink-mute)', padding: 14, textAlign: 'center', border: '1px dashed var(--line)', borderRadius: 8 }}>
                        （还没有情节——在上方填 l1 一句话极简剧情 + 章数，点「新建情节」）
                    </div>
                )}

                {/* 对话窗口 + 检索面板（对当前情节）；未初始化时不禁用（走初始化助手） */}
                <ChatWindow messages={messages} onSend={handleChat} busy={chatBusy}
                    disabled={!activeArcId && !(initStatus && !initStatus.initialized)}
                    pendingConfirm={pendingConfirm}
                    onConfirm={handleConfirmPending}
                    onCancel={() => setPendingConfirm(null)}
                    sessions={chatSessions} currentSessionId={chatSessionId}
                    onNewSession={newChatSession} onSelectSession={switchChatSession} onDeleteSession={deleteChatSession}
                    onDeleteMessage={deleteMessagePair} />
                <SearchPanel bookRoot={bookRoot} onSendToChat={sendSearchToChat} title="检索" />
            </div>
        )
    }

    // 「大纲与章纲」页的设定部分（与 renderArcs 合并为同一入口）：
    // 状态条 + 基本设定 + 元素清单（书级配置），宽屏（≥1300px）下基本设定与元素清单并排两列
    const renderOverview = () => (
        <div>
            {renderStatusBar()}
            <div className="overview-grid">
                {renderBasicSettings()}
                {renderElements()}
            </div>
        </div>
    )

    // 单情节面板（始终显示；无情节时占位）
    const renderArcPanel = (arc) => {
        if (!arc) {
            return (
                <div style={{ border: '1px solid var(--line)', borderRadius: 8, padding: 12, background: 'var(--paper-raised)' }}>
                    <div style={titleStyle}><span>当前情节</span></div>
                    <div style={{ fontSize: 13, color: 'var(--ink-mute)', padding: 16, textAlign: 'center', border: '1px dashed var(--line)', borderRadius: 8 }}>
                        尚未创建情节——在上方填 l1 一句话极简剧情 + 章数，点「新建情节」。<br />
                        创建后：生成 l2 情节概要 → 选择本情节参与元素 → 逐级生成 l3/l4/l5（每级确认后才继续）。
                    </div>
                </div>
            )
        }
        const selC = selDraft.characters || []
        const selI = selDraft.items || []
        const selS = selDraft.settings || []
        const hasSel = selC.length || selI.length || selS.length

        const renderSelGroup = (kind, label) => {
            const list = elements[kind] || []
            const sel = selDraft[kind] || []
            return (
                <div style={{ marginBottom: 6 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-sub)', marginBottom: 3 }}>{label}</div>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {list.length === 0 && <span style={{ fontSize: 12, color: 'var(--ink-mute)' }}>（清单为空，先在上方维护元素）</span>}
                        {list.map(e => (
                            <label key={e.id} style={{
                                display: 'flex', alignItems: 'center', gap: 4, fontSize: 12,
                                padding: '3px 8px', borderRadius: 10, cursor: 'pointer',
                                background: sel.includes(e.id) ? 'var(--dai-wash)' : 'var(--bg-main)',
                                border: `1px solid ${sel.includes(e.id) ? 'var(--dai)' : 'var(--line)'}`,
                            }}>
                                <input type="checkbox" checked={sel.includes(e.id)} onChange={() => toggleSel(kind, e.id)} />
                                {e.name}
                                {e.id && sel.includes(e.id) && <span style={{ color: 'var(--dai)' }}>✓参与</span>}
                            </label>
                        ))}
                    </div>
                </div>
            )
        }

        // l4/l5 默认折叠（先只显示 l1-l3），点开才展开完整卡片
        const renderLevelCollapsed = (level) => {
            const lv = (arcState?.levels || {})[level] || {}
            const filled = level === 'l4' ? (lv.scenes?.length) : lv.text
            const confirmed = !!lv.confirmed
            const meta = LEVEL_META[level]
            const badge = confirmed ? '已确认' : filled ? '待确认' : '未生成'
            const summary = level === 'l4'
                ? (filled ? `l4 · 场景（${filled} 个）` : 'l4 · 场景（未生成）')
                : level === 'l5'
                    ? (filled ? `l5 · 正文（约 ${String(filled).length} 字）` : 'l5 · 正文（未生成）')
                    : meta.label
            return (
                <details key={level} style={{ marginBottom: 6, border: '1px solid var(--line)', borderRadius: 8, background: 'var(--paper-raised)' }}>
                    <summary style={{ cursor: 'pointer', padding: '8px 10px', fontSize: 13, fontWeight: 600, display: 'flex', gap: 8, alignItems: 'center', borderRadius: 8 }}>
                        <span>{summary}</span>
                        <span style={badgeStyle(confirmed)}>{badge}</span>
                        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--dai)' }}>▸ 展开</span>
                    </summary>
                    <div style={{ padding: '0 10px 10px' }}>{renderLevel(arcState, level)}</div>
                </details>
            )
        }

        return (
            <div style={{ border: '1px solid var(--line)', borderRadius: 8, padding: 12, background: 'var(--paper-raised)' }}>
                {/* 情节信息 */}
                <div style={titleStyle}>
                    <span
                        className={`wb-editable wb-arcname-edit ${editStatus[`arcname_${arc.id}`] === 'editing' ? 'wb-editing' : ''}`}
                        contentEditable
                        suppressContentEditableWarning
                        onFocus={(e) => {
                            startEdit(`arcname_${arc.id}`)
                            const range = document.createRange()
                            range.selectNodeContents(e.currentTarget)
                            const sel = window.getSelection()
                            sel.removeAllRanges()
                            sel.addRange(range)
                        }}
                        onBlur={(e) => {
                            const text = e.currentTarget.innerText.trim()
                            if (text && text !== arc.name) {
                                handleArcNameEdit(arc.id, text)
                            } else {
                                finishEdit(`arcname_${arc.id}`)
                            }
                        }}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault()
                                e.currentTarget.blur()
                            }
                        }}
                        style={{ fontWeight: 600, fontSize: 16, minWidth: 80, display: 'inline-block' }}
                    >{arc.name}</span>
                    {editStatus[`arcname_${arc.id}`] === 'saving' && <span className="wb-edit-indicator saving">保存中…</span>}
                    {editStatus[`arcname_${arc.id}`] === 'saved' && <span className="wb-edit-indicator saved">✓</span>}
                    {editStatus[`arcname_${arc.id}`] === 'error' && <span className="wb-edit-indicator error">✗</span>}
                    <span style={badgeStyle(arc.status === 'done')}>{arc.status === 'done' ? '已完成' : '进行中'}</span>
                    <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                        {arc.status !== 'done' && (
                            <button style={btnStyle(busy, false)} disabled={busy} onClick={handleFinishArc}>完结本情节</button>
                        )}
                    </span>
                </div>
                {arc.prev_anchor?.length > 0 && (
                    <details style={{ marginBottom: 6 }}>
                        <summary style={{ fontSize: 12, color: 'var(--ink-sub)', cursor: 'pointer' }}>前文锚点（上一情节结局，已注入本情节 l2/l3）</summary>
                        <pre style={{ ...preStyle, fontSize: 12, color: 'var(--ink-sub)', background: 'var(--bg-card-2)', padding: 8, borderRadius: 6 }}>
                            {Array.isArray(arc.prev_anchor) ? arc.prev_anchor.join('\n') : arc.prev_anchor}
                        </pre>
                    </details>
                )}
                {arc.l1 && <div style={{ fontSize: 13, marginBottom: 4 }}><b>l1：</b>{arc.l1}</div>}
                {arc.l2 && <div style={{ fontSize: 13, marginBottom: 6 }}><b>l2：</b>{arc.l2}</div>}
                {arc.state?.template && (
                    <div style={{ fontSize: 12, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        <span style={{
                            padding: '2px 8px', borderRadius: 10, background: 'var(--dai-wash)',
                            border: '1px solid var(--dai)', color: 'var(--dai)', fontSize: 12,
                        }}>
                            情节模板「{arc.state.template.name || arc.state.template.id}」
                            {arc.state.template.archetype ? `（${arc.state.template.archetype}）` : ''}
                            {arc.state.template_sim ? ` · 相似 ${(+arc.state.template_sim).toFixed(2)}` : ''}
                        </span>
                        <button style={{ ...btnStyle(busy, false), padding: '2px 8px', fontSize: 12 }}
                            disabled={busy} onClick={() => clearArcTemplate(arc.id)}>✕ 清除模板</button>
                    </div>
                )}

                {/* 本情节元素选择 */}
                <div style={{ padding: 8, borderRadius: 6, border: '1px solid var(--line)', background: 'var(--dai-wash)', marginBottom: 10 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
                        本情节参与元素（选择后从 l3 章核心起只出现这些；未选元素禁止出现）
                        {!hasSel && <span style={{ color: 'var(--cinnabar-d)', marginLeft: 6 }}>⚠ 未选择——生成 l3 起会要求先选</span>}
                    </div>
                    {renderSelGroup('characters', '参与角色')}
                    {renderSelGroup('items', '参与物品')}
                    {renderSelGroup('settings', '参与设定')}
                    <button style={{ ...btnStyle(busy, true), marginTop: 4 }} disabled={busy} onClick={saveSel}>保存本情节元素选择</button>
                </div>

                {/* 阶梯（l1-l3 全显；l4/l5 默认折叠，点开展开） */}
                <div style={{ marginTop: 4 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
                        <div style={{ fontSize: 14, fontWeight: 700 }}>阶梯（1 → 12 → 123 → 1234 → 12345）</div>
                        <div style={{ display: 'flex', gap: 4 }}>
                            {LEVEL_ORDER.map(lv => {
                                const f = arcState ? levelText(arcState, lv) : ''
                                const c = arcState ? (arcState.levels?.[lv] || {}).confirmed : false
                                return (
                                    <span key={lv} style={{
                                        padding: '2px 8px', borderRadius: 12, fontSize: 12,
                                        background: c ? 'var(--green)' : (f ? 'var(--amber)' : 'var(--line)'),
                                        color: (c || f) ? 'var(--paper-raised)' : 'var(--ink-sub)',
                                    }}>{lv}</span>
                                )
                            })}
                        </div>
                        <span style={{ fontSize: 12, color: 'var(--ink-sub)' }}>绿=已确认 黄=待确认 灰=未生成</span>
                    </div>
                    {arcState ? (
                        <>
                            {['l1', 'l2', 'l3'].map(level => (level === 'l3' && isMultiL3(arcState) ? renderL3Multi(arcState) : renderLevel(arcState, level)))}
                            {['l4', 'l5'].map(level => renderLevelCollapsed(level))}
                            <div style={{ marginTop: 10 }}>
                                <button style={btnStyle(busy, true)} disabled={busy} onClick={handleStep}>生成下一级</button>
                                <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--ink-sub)' }}>
                                    每级生成后停在「待确认」，审阅/修改后确认才继续；l3 起注入元素白名单
                                </span>
                            </div>
                        </>
                    ) : (
                        <div style={{ fontSize: 13, color: 'var(--ink-mute)', padding: 14, textAlign: 'center', border: '1px dashed var(--line)', borderRadius: 8 }}>
                            阶梯将从 l1 开始逐级生成；每级生成后停在「待确认」，审阅/确认后才继续。
                        </div>
                    )}
                </div>

            </div>
        )
    }

    // 正文栏：当前章 l5（可编辑）+ 评分卡 + 对话 + 已落盘章节（树连接）
    const renderProse = () => {
        // 树里点了已落盘章节 → 查看该章文本 + 评分
        const fin = viewFin
            ? ((arcs.arcs || []).find(a => a.id === viewFin.arcId)?.chapters?.[viewFin.idx] || null)
            : null
        if (fin) {
            // 全书已落盘章平铺，支持连续浏览（跨情节）
            const allFin = (arcs.arcs || []).flatMap(a => (a.chapters || []).map((c, i) => ({ ...c, arcId: a.id, idx: i })))
            const pos = allFin.findIndex(f => f.arcId === viewFin.arcId && f.idx === viewFin.idx)
            const prevF = pos > 0 ? allFin[pos - 1] : null
            const nextF = pos >= 0 && pos < allFin.length - 1 ? allFin[pos + 1] : null
            const goFin = f => { setViewFin({ arcId: f.arcId, idx: f.idx }); setActiveArcId(f.arcId) }
            return (
                <div className="reading-paper" style={{ maxWidth: 720, margin: '0 auto', padding: '44px 52px' }}>
                    {/* 书页顶部的返回/导航（细小、克制） */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 28, fontSize: 12, color: 'var(--ink-mute)' }}>
                        <button style={btnStyle(!prevF, false)} disabled={!prevF} onClick={() => prevF && goFin(prevF)}>◀ 上一章</button>
                        <button style={btnStyle(!nextF, false)} disabled={!nextF} onClick={() => nextF && goFin(nextF)}>下一章 ▶</button>
                        <select value={pos} onChange={e => { const n = Number(e.target.value); const f = allFin[n]; if (f) goFin(f) }}
                            style={{ fontSize: 12, padding: '3px 6px', border: 'none', borderBottom: '1px solid var(--line)', background: 'transparent', maxWidth: 240, color: 'var(--ink-sub)' }}>
                            {allFin.map((f, i) => (
                                <option key={i} value={i}>第{String(f.num).padStart(4, '0')}章 {f.title}</option>
                            ))}
                        </select>
                        <span style={{ marginLeft: 'auto' }}>{allFin.length} 章已落盘 · <button style={btnStyle(false, false)} onClick={() => { setViewFin(null); setActiveArcId(viewFin.arcId) }}>返回草稿</button></span>
                    </div>
                    {/* 书页正文：章题书宋大字居中，正文两倍行距 */}
                    <div className="rbody" style={{ textIndent: 0 }}>
                        <div style={{ fontFamily: 'var(--font-serif)', fontSize: 24, fontWeight: 700, textAlign: 'center', letterSpacing: '0.1em', marginBottom: 8, color: 'var(--ink)', lineHeight: 1.4 }}>
                            {fin.title}
                        </div>
                        <div style={{ textAlign: 'center', fontSize: 11, color: 'var(--ink-mute)', letterSpacing: '0.2em', marginBottom: 32 }}>
                            第 {String(fin.num).padStart(4, '0')} 章
                        </div>
                        {fin.text.split(/\n{2,}/).map((para, i) => (
                            <p key={i} style={{ fontFamily: 'var(--font-serif)', fontSize: 15, lineHeight: 2.1, textAlign: 'justify', textIndent: '2em', marginBottom: '0.6em', color: '#3a352c' }}>{para}</p>
                        ))}
                    </div>
                    {/* 书页底部评分，像书后附记 */}
                    <div style={{ marginTop: 32, paddingTop: 16, borderTop: '1px solid var(--line)', fontSize: 12, color: 'var(--ink-sub)', display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                        <span>意图 <b style={{ color: 'var(--ink)' }}>{fin.intent_score}</b></span>
                        <span>质量 <b style={{ color: 'var(--ink)' }}>{fin.quality_score}</b></span>
                        <span>综合 <b style={{ color: 'var(--ink)' }}>{fin.overall}</b></span>
                        {fin.polluted
                            ? <span style={{ color: 'var(--cinnabar-d)' }}>⚠ 检测到污染</span>
                            : <span style={{ color: 'var(--green)' }}>✓ 元素隔离通过</span>}
                        <span style={{ marginLeft: 'auto', color: 'var(--ink-mute)' }}>AI生成/第{String(fin.num).padStart(4, '0')}章.md</span>
                    </div>
                </div>
            )
        }
        let title = ''
        if (arcState) {
            const l3d = arcState.levels?.l3?.data || {}
            const chs = Array.isArray(l3d.chapters) ? l3d.chapters : null
            const idx = arcState.active_chapter || 0
            const ch = chs ? (chs[idx] || {}) : l3d
            title = ch.title || ''
        }
        const l5val = l5Draft !== null ? l5Draft : l5Text
        const hasL5 = l5Text.trim() !== ''
        return (
            <div className="section-block" style={{ borderLeft: '3px solid var(--cinnabar)' }}>
                <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>
                    正文{title ? ` · ${title}` : ''}
                    {!activeArc && <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--ink-mute)', marginLeft: 8 }}>（未选情节——到大纲与章纲创建/选择情节后生成正文）</span>}
                </div>

                {/* l5 正文编辑器（始终显示，空则占位） */}
                <div className="reading-paper" style={{ padding: 0, overflow: 'hidden' }}>
                    <div className="ruler" style={{ display: l5val.trim() ? 'block' : 'none' }} />
                    <textarea value={l5val} onChange={e => setL5Draft(e.target.value)} rows={16}
                        onBlur={() => { if (l5Draft !== null && l5Draft !== l5Text) handleSaveL5() }}
                        placeholder={activeArc ? '正文草稿（可编辑，失焦自动保存）——在大纲与章纲把阶梯生成到 l5 后自动填充，也可直接手写' : '先在大纲与章纲创建/选择情节，再回来生成正文'}
                        style={{ width: '100%', boxSizing: 'border-box', padding: 14, borderRadius: 4, border: 'none', fontSize: 15, lineHeight: 2, fontFamily: 'var(--font-serif)', color: 'var(--ink)', background: 'transparent', position: 'relative', zIndex: 1, resize: 'vertical' }} />
                </div>
                <div style={{ marginTop: 6 }}>
                    <button style={btnStyle(busy, true)} disabled={busy || l5Draft === null || !activeArcId} onClick={handleSaveL5}>保存正文</button>
                    <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--ink-sub)' }}>失焦自动保存（编辑后点别处即保存）；再评分/落盘</span>
                </div>
                {!hasL5 && (
                    <div style={{ color: 'var(--ink-mute)', fontSize: 12, marginTop: 4 }}>
                        正文将显示在这里——在大纲与章纲把阶梯生成到 l5（场景级生成 → AI 味审阅）后自动出现，也可直接手写。
                    </div>
                )}

                        {/* 评分卡 */}
                        <div style={{ padding: 10, borderRadius: 6, border: '1px solid var(--cinnabar-border)', background: 'var(--cinnabar-wash)', marginTop: 10 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>本章双评分（无参考原文）</div>
                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                                <button style={btnStyle(scoreBusy, true)} disabled={scoreBusy || !hasL5} onClick={handleScore}>评分</button>
                                <button style={btnStyle(scoreBusy, false)} disabled={scoreBusy || !hasL5} onClick={handlePollution}>元素污染检测</button>
                                {(lastScore?.polluted || pollutionResult?.polluted) && (
                                    <button style={{ ...btnStyle(busy, true), background: 'var(--cinnabar-d)', borderColor: 'var(--cinnabar-d)' }} disabled={busy} onClick={handleRegenerateL5}>
                                        重生成正文（规避未选元素）
                                    </button>
                                )}
                                <button style={btnStyle(busy, false)} disabled={busy || !lastScore || lastScore.polluted} onClick={handleFinalize}>保存本章到书</button>
                            </div>
                            {(lastScore?.polluted || pollutionResult?.polluted) && (
                                <div style={{ marginTop: 6, fontSize: 12, color: 'var(--cinnabar-d)' }}>
                                    ⚠ 检出未参与元素污染，请先「重生成正文（规避未选元素）」再评分；仍不满意可对话修改 l3/l4 后重生成
                                </div>
                            )}
                            {lastScore && (
                                <div style={{ marginTop: 8 }}>
                                    <div style={{ display: 'flex', gap: 14, fontSize: 13, marginBottom: 4 }}>
                                        <b style={{ color: 'var(--dai)' }}>意图兑现 {lastScore.intent_score}</b>
                                        <b style={{ color: 'var(--green)' }}>纯质量 {lastScore.quality_score}</b>
                                        <b style={{ color: 'var(--cinnabar)' }}>综合 {lastScore.overall}</b>
                                        {lastScore.polluted && <span style={{ color: 'var(--cinnabar-d)' }}>⚠ 检出未参与元素污染</span>}
                                    </div>
                                    <details>
                                        <summary style={{ fontSize: 12, color: 'var(--dai)', cursor: 'pointer' }}>评分明细</summary>
                                        <pre style={{ ...preStyle, fontSize: 12, color: 'var(--ink-sub)', background: 'var(--bg-card-2)', padding: 8, borderRadius: 6 }}>
{JSON.stringify(lastScore.details, null, 2)}
                                        </pre>
                                    </details>
                                </div>
                            )}
                            {pollutionResult && (
                                <div style={{ marginTop: 6, fontSize: 12 }}>
                                    {pollutionResult.polluted ? (
                                        <div style={{ color: 'var(--cinnabar-d)' }}>⚠ 未参与元素命中：
                                            {pollutionResult.hits?.map((h, i) => (
                                                <span key={i} style={{ marginRight: 8 }}>{h.name}（{h.src === 'llm' ? 'LLM复核' : '确定性'}）</span>
                                            ))}
                                        </div>
                                    ) : <div style={{ color: 'var(--green)' }}>✓ 未发现未参与元素污染</div>}
                                </div>
                            )}
                            {lastScore && !busy && (
                                <div style={{ marginTop: 4, fontSize: 12, color: 'var(--ink-sub)' }}>
                                    保存后写入：大纲/章纲 + AI生成/正文 + 审查报告/评分.json（主系统书结构）
                                </div>
                            )}
                        </div>

                        {/* 对话窗口（共享组件，正文页签也放）+ 检索面板 */}
                        <ChatWindow messages={messages} onSend={handleChat} busy={chatBusy}
                            disabled={!activeArcId && !(initStatus && !initStatus.initialized)}
                            pendingConfirm={pendingConfirm}
                            onConfirm={handleConfirmPending}
                            onCancel={() => setPendingConfirm(null)}
                            sessions={chatSessions} currentSessionId={chatSessionId}
                            onNewSession={newChatSession} onSelectSession={switchChatSession} onDeleteSession={deleteChatSession}
                            onDeleteMessage={deleteMessagePair} />
                        <SearchPanel bookRoot={bookRoot} onSendToChat={sendSearchToChat} title="检索" />
            </div>
        )
    }

    // 已落盘章节：全书树形（情节分组 → 展开章节 → 点章就地展开正文，不跳页）
    const renderChapters = () => {
        const arcsList = arcs.arcs || []
        const totalFin = arcsList.reduce((n, a) => n + (a.chapters || []).length, 0)
        const openKey = (arcId, idx) => (expandFin && expandFin.arcId === arcId && expandFin.idx === idx)
        return (
            <div className="section-block">
                <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>已落盘章节（全书 · {totalFin} 章）</div>
                {totalFin === 0 && <div style={{ fontSize: 12, color: 'var(--ink-mute)', marginBottom: 6 }}>（还没有已落盘的章节——生成正文并评分后点「保存本章到书」）</div>}
                {arcsList.map(a => {
                    const chs = a.chapters || []
                    if (chs.length === 0) return null
                    const isActive = a.id === activeArcId
                    return (
                        <details key={a.id} open={isActive} style={{ marginBottom: 6, border: '1px solid var(--line)', borderRadius: 6, background: 'var(--paper-raised)' }}>
                            <summary style={{ cursor: 'pointer', fontSize: 14, fontWeight: isActive ? 700 : 600, padding: '8px 10px', background: isActive ? 'var(--dai-wash)' : 'var(--paper)', borderRadius: 6, color: isActive ? 'var(--dai)' : 'var(--ink-sub)' }}>
                                {a.name}（{chs.length} 章）
                                {a.status === 'done' && <span style={{ marginLeft: 6 }}></span>}
                                {!isActive && <span style={{ fontWeight: 400, color: 'var(--ink-sub)', marginLeft: 6 }}>点击展开</span>}
                            </summary>
                            <div style={{ padding: '6px 10px' }}>
                                {chs.map((c, i) => {
                                    const expanded = openKey(a.id, i)
                                    return (
                                        <div key={i}>
                                            <div onClick={() => { setExpandFin(expanded ? null : { arcId: a.id, idx: i }) }}
                                                style={{ cursor: 'pointer', fontSize: 13, padding: '6px 8px', border: '1px solid var(--bg-main)', borderRadius: 6, marginBottom: 4, display: 'flex', gap: 10, alignItems: 'center', background: expanded ? 'var(--cinnabar-wash)' : 'var(--paper)' }}
                                                title="点击展开/收起该章正文">
                                                <b>第{String(c.num).padStart(4, '0')}章</b>
                                                <span style={{ flex: 1 }}>{c.title}</span>
                                                <span>意图 {c.intent_score}</span>
                                                <span>质量 {c.quality_score}</span>
                                                {c.polluted ? <span style={{ color: 'var(--cinnabar-d)' }}>⚠污染</span> : <span style={{ color: 'var(--green)' }}>✓</span>}
                                                <span style={{ fontSize: 11, color: 'var(--dai)' }}>{expanded ? '▾ 收起正文' : '展开正文 ▸'}</span>
                                            </div>
                                            {expanded && (
                                                <div style={{ marginBottom: 6, padding: '10px 12px', border: '1px solid var(--cinnabar-border)', borderRadius: 6, background: 'var(--paper-raised)df5' }}>
                                                    <pre style={{ ...preStyle, fontSize: 14, lineHeight: 1.9, margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'inherit' }}>{c.text || '（该章无正文文本）'}</pre>
                                                </div>
                                            )}
                                        </div>
                                    )
                                })}
                            </div>
                        </details>
                    )
                })}
            </div>
        )
    }

    // 阶梯各级（改造成 arc 用）——始终显示，未生成显示占位
    const renderLevel = (state, level) => {
        const lv = (state?.levels || {})[level] || {}
        const filled = level === 'l3' ? (lv.data || lv.text) : level === 'l4' ? (lv.scenes?.length) : lv.text
        const confirmed = !!lv.confirmed
        const text = levelText(state, level)
        const meta = LEVEL_META[level]
        return (
            <div key={level} style={{ ...cardStyle, borderLeft: confirmed ? '3px solid var(--green)' : '3px solid var(--amber)' }}>
                <div style={titleStyle}>
                    <span>{meta.label}</span>
                    <span style={badgeStyle(confirmed)}>{confirmed ? '已确认' : '待确认'}</span>
                    <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                        {filled && level !== 'l1' && !confirmed && (
                            <button style={btnStyle(busy, true)} disabled={busy} onClick={() => handleConfirm(level)}>确认</button>
                        )}
                        {['l1', 'l2'].includes(level) && (
                            <button style={btnStyle(busy, false)} disabled={busy} onClick={() => { setEditLevel(level); setEditText(text) }}>编辑</button>
                        )}
                        {['l1', 'l2', 'l3', 'l4'].includes(level) && (
                            <button style={btnStyle(busy, false)} disabled={busy} onClick={() => setModifyLevel(level)}>对话修改</button>
                        )}
                    </span>
                </div>
                <div style={meta.hint ? { fontSize: 12, color: 'var(--ink-sub)', marginBottom: 4 } : {}}>{meta.hint}</div>
                {!filled ? (
                    <div style={{ fontSize: 13, color: 'var(--ink-mute)', padding: '10px 0' }}>（未生成——点「生成下一级」逐级生成；生成后停在「待确认」）</div>
                ) : (
                    level === 'l3' ? (l3Edit ? renderL3Editor() : <pre style={preStyle}>{text}</pre>)
                        : level === 'l4' ? (l4Edit ? renderL4Editor() : <pre style={preStyle}>{text}</pre>)
                            : <pre style={preStyle}>{text}</pre>
                )}
                {lv.prompt && (
                    <details style={{ marginTop: 6 }}>
                        <summary style={{ fontSize: 12, color: 'var(--dai)', cursor: 'pointer' }}>生成指令</summary>
                        <pre style={{ ...preStyle, fontSize: 12, color: 'var(--ink-sub)', background: 'var(--bg-card-2)', padding: 8, borderRadius: 6 }}>{lv.prompt}</pre>
                    </details>
                )}
                {editLevel === level && (
                    <div style={{ marginTop: 8, borderTop: '1px dashed var(--line-soft)', paddingTop: 8 }}>
                        <textarea value={editText} onChange={e => setEditText(e.target.value)} rows={3}
                            placeholder={`直接编辑 ${level} 内容…`}
                            style={{ width: '100%', boxSizing: 'border-box', padding: 8, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13 }} />
                        <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                            <button style={btnStyle(busy, true)} disabled={busy} onClick={handleSaveLevel}>保存</button>
                            <button style={btnStyle(false, false)} onClick={() => { setEditLevel(null); setEditText('') }}>取消</button>
                        </div>
                    </div>
                )}
                {modifyLevel === level && (
                    <div style={{ marginTop: 8 }}>
                        <textarea value={modifyInput} onChange={e => setModifyInput(e.target.value)} rows={2}
                            placeholder={`对 ${level} 说你的修改意见…`}
                            style={{ width: '100%', boxSizing: 'border-box', padding: 8, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13 }} />
                        <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                            <button style={btnStyle(busy || !modifyInput.trim(), true)} disabled={busy || !modifyInput.trim()} onClick={handleModify}>提交修改</button>
                            <button style={btnStyle(false, false)} onClick={() => { setModifyLevel(null); setModifyInput('') }}>取消</button>
                        </div>
                    </div>
                )}
            </div>
        )
    }

    // l3 结构化编辑器（title/core/beats）
    const renderL3Editor = () => {
        const inp = { width: '100%', boxSizing: 'border-box', padding: 6, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13, marginTop: 4 }
        return (
            <div style={{ marginTop: 8, borderTop: '1px dashed var(--line-soft)', paddingTop: 8 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>编辑 l3 章核心{l3Edit.idx !== null ? `（第${l3Edit.idx + 1}章）` : ''}</div>
                <input value={l3Edit.title || ''} onChange={e => setL3Edit(d => ({ ...d, title: e.target.value }))} placeholder="标题" style={inp} />
                <textarea value={l3Edit.core || ''} onChange={e => setL3Edit(d => ({ ...d, core: e.target.value }))} rows={2} placeholder="一句话核心" style={inp} />
                <textarea value={(l3Edit.beats || []).join('\n')}
                    onChange={e => setL3Edit(d => ({ ...d, beats: e.target.value.split('\n').map(s => s.trim()).filter(Boolean) }))}
                    rows={4} placeholder="拍（每行一条）" style={inp} />
                <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                    <button style={btnStyle(busy, true)} disabled={busy} onClick={handleSaveL3}>保存 l3</button>
                    <button style={btnStyle(false, false)} onClick={() => setL3Edit(null)}>取消</button>
                </div>
            </div>
        )
    }

    // l4 场景编辑器（名称/环境/动作/对白/冲突/细节 + 元素绑定/素材/留空/备注）
    const renderL4Editor = () => {
        const setSc = (i, field, val) => setL4Edit(arr => {
            const c = JSON.parse(JSON.stringify(arr))
            c[i][field] = val
            return c
        })
        const listText = (v) => Array.isArray(v) ? v.join('\n') : ''
        const lines = (s) => s.split('\n').map(x => x.trim()).filter(Boolean)
        const inp = { width: '100%', boxSizing: 'border-box', padding: 6, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13, marginTop: 4 }

        // 素材工具栏按钮
        const fragToolbar = [
            { type: 'blank', label: '留空', desc: '选中文本 → 包成 [意图]（l5 时按意图填充）' },
            { type: 'dialogue', label: '对白', desc: '添加对白素材' },
            { type: 'detail', label: '细节', desc: '添加细节素材' },
            { type: 'action', label: '动作', desc: '添加动作素材' },
            { type: 'item_card', label: '物品卡片', desc: '插入物品卡片引用' },
            { type: 'char_card', label: '角色卡片', desc: '插入角色卡片引用' },
            { type: 'setting_card', label: '设定卡片', desc: '插入设定卡片引用' },
        ]

        // 留空插入：选中文本 → 包成 [意图]（纯 [] 自由插入，不建独立记录）
        const insertBlank = (sceneIdx) => {
            const sel = window.getSelection()
            const selectedText = sel?.toString()?.trim()
            if (!selectedText) { setError('请先在素材文本中选中要留空的内容'); return }
            // 纯 [] 自由插入：把选中文本包成 [意图]，不建独立 blanks 记录
            const draft = l4FragDraft[sceneIdx] || { type: 'detail', content: '' }
            const newContent = (draft.content || '').replace(selectedText, `[${selectedText}]`)
            setL4FragDraft(d => ({ ...d, [sceneIdx]: { ...draft, content: newContent } }))
            setNotice(`已标记留空：[${selectedText}]（l5 时 LLM 按此意图填充）`)
        }

        // 素材类型快捷添加
        const addFragFromToolbar = (sceneIdx, ftype) => {
            if (ftype === 'blank') { insertBlank(sceneIdx); return }
            const draft = l4FragDraft[sceneIdx] || { type: 'detail', content: '' }
            const prefix = { dialogue: '【对白】', detail: '【细节】', action: '【动作】', item_card: '【物品】', char_card: '【角色】', setting_card: '【设定】' }[ftype] || ''
            const newType = ftype.replace('_card', '')
            setL4FragDraft(d => ({ ...d, [sceneIdx]: { type: newType, content: (draft.content || '') + (draft.content ? '\n' : '') + prefix } }))
        }

        // 保存素材到场景
        const saveFrag = async (sceneIdx) => {
            const draft = l4FragDraft[sceneIdx]
            if (!draft?.content?.trim()) { setError('素材内容不能为空'); return }
            try {
                const r = await phFragmentsPut({
                    book_root: bookRoot, arc_id: activeArcId,
                    ftype: draft.type || 'detail', content: draft.content.trim(),
                    scene_idx: sceneIdx,
                })
                if (r?.ok) {
                    setNotice('素材已保存')
                    setL4FragDraft(d => { const n = { ...d }; delete n[sceneIdx]; return n })
                    await refreshWorkbench(bookRoot)
                }
            } catch (e) { setError(`素材保存失败：${e.message || e}`) }
        }

        // 段级备注：勾选节拍 → 弹窗输入 → 创建 note
        const addBeatNote = async (sceneIdx) => {
            const sel = l4BeatSelected[sceneIdx]
            if (!sel || sel.size === 0) { setError('请先勾选要备注的节拍'); return }
            const content = prompt('请输入段级备注内容：')
            if (!content?.trim()) return
            const beatStr = Array.from(sel).sort().join(',')
            try {
                const r = await phNotesPut({
                    book_root: bookRoot,
                    scope: `arc:${activeArcId}:scene${sceneIdx}:beats:${beatStr}`,
                    content: content.trim(),
                })
                if (r?.ok) {
                    setNotice('段级备注已保存')
                    setL4BeatSelected(d => ({ ...d, [sceneIdx]: new Set() }))
                    await refreshWorkbench(bookRoot)
                }
            } catch (e) { setError(`备注保存失败：${e.message || e}`) }
        }

        // 切换节拍勾选
        const toggleBeat = (sceneIdx, beatIdx) => {
            setL4BeatSelected(d => {
                const prev = d[sceneIdx] || new Set()
                const next = new Set(prev)
                if (next.has(beatIdx)) next.delete(beatIdx)
                else next.add(beatIdx)
                return { ...d, [sceneIdx]: next }
            })
        }

        // 获取当前场景的素材
        const sceneFrags = (sceneIdx) => (fragments || []).filter(f => f.scene_idx === sceneIdx)
        // 获取当前场景的备注
        const sceneNotes = (sceneIdx) => (notes || []).filter(n => (n.scope || '').includes(`scene${sceneIdx}`))

        return (
            <div style={{ marginTop: 8, borderTop: '1px dashed var(--line-soft)', paddingTop: 8 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>编辑 l4 场景（每行一条）</div>
                {l4Edit.map((sc, i) => {
                    const expanded = l4SceneExpanded === i
                    const beats = sc.beats || []
                    const selSet = l4BeatSelected[i] || new Set()
                    return (
                        <div key={i} style={{ border: '1px solid var(--line)', borderRadius: 6, padding: 8, marginBottom: 6, background: 'var(--paper)' }}>
                            {/* 场景头部：名称/环境/展开按钮 */}
                            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                                <b style={{ fontSize: 12, flexShrink: 0, cursor: 'pointer' }}
                                    onClick={() => setL4SceneExpanded(expanded ? null : i)}>
                                    {expanded ? '▼' : '▶'} 场景{i + 1}
                                </b>
                                <input style={{ ...inp, marginTop: 0, flex: 1 }} value={sc.name || ''} onChange={e => setSc(i, 'name', e.target.value)} placeholder="场景名" />
                                <input style={{ ...inp, marginTop: 0, flex: 1 }} value={sc.environment || ''} onChange={e => setSc(i, 'environment', e.target.value)} placeholder="环境" />
                                <button style={btnStyle(false, false)} onClick={() => setL4Edit(a => a.filter((_, j) => j !== i))}>✕</button>
                            </div>

                            {/* 基础字段（始终显示）- 对白逐轮编辑 */}
                            <div style={{ marginTop: 4 }}>
                                <label style={{ fontSize: 11, color: 'var(--ink-sub)', display: 'flex', alignItems: 'center', gap: 4 }}>
                                    动作
                                    {(sc.actions || []).length > 0 && (
                                        <span style={{ color: 'var(--ink-mute)', fontWeight: 400 }}>
                                            ({(sc.actions || []).length} 条)
                                        </span>
                                    )}
                                </label>
                                <textarea value={listText(sc.actions)} onChange={e => setSc(i, 'actions', lines(e.target.value))} rows={2} placeholder="动作（每行一条）"
                                    style={inp} />
                            </div>

                            {/* 对白 - 逐轮编辑（角色 + 台词 + 神态） */}
                            <div style={{ marginTop: 4 }}>
                                <label style={{ fontSize: 11, color: 'var(--ink-sub)', display: 'flex', alignItems: 'center', gap: 4 }}>
                                    对白
                                    {(sc.dialogues || []).length > 0 && (
                                        <span style={{ color: 'var(--ink-mute)', fontWeight: 400 }}>
                                            ({(sc.dialogues || []).length} 轮)
                                        </span>
                                    )}
                                </label>
                                <div style={{ marginTop: 4, border: '1px solid var(--line-soft)', borderRadius: 4, overflow: 'hidden' }}>
                                    {(sc.dialogues || []).length === 0 ? (
                                        <div style={{ padding: '8px 12px', fontSize: 12, color: 'var(--ink-mute)', textAlign: 'center' }}>
                                            暂无对白，点击下方添加
                                        </div>
                                    ) : (
                                        (sc.dialogues || []).map((d, di) => (
                                            <CompactDialogueRow key={di} value={d} index={di}
                                                onChange={newD => {
                                                    const nd = [...(sc.dialogues || [])]
                                                    nd[di] = newD
                                                    setSc(i, 'dialogues', nd)
                                                }}
                                                onRemove={() => setSc(i, 'dialogues', (sc.dialogues || []).filter((_, j) => j !== di))} />
                                        ))
                                    )}
                                </div>
                                <button style={{ ...btnStyle(false, false), marginTop: 4, fontSize: 11 }}
                                    onClick={() => {
                                        const nd = [...(sc.dialogues || []), '角色：台词']
                                        setSc(i, 'dialogues', nd)
                                    }}>
                                    + 添加对白
                                </button>
                            </div>

                            <div style={{ marginTop: 4 }}>
                                <label style={{ fontSize: 11, color: 'var(--ink-sub)', display: 'flex', alignItems: 'center', gap: 4 }}>
                                    冲突
                                    {(sc.conflicts || []).length > 0 && (
                                        <span style={{ color: 'var(--ink-mute)', fontWeight: 400 }}>
                                            ({(sc.conflicts || []).length} 条)
                                        </span>
                                    )}
                                </label>
                                <textarea value={listText(sc.conflicts)} onChange={e => setSc(i, 'conflicts', lines(e.target.value))} rows={2} placeholder="冲突（每行一条）"
                                    style={inp} />
                            </div>

                            <div style={{ marginTop: 4 }}>
                                <label style={{ fontSize: 11, color: 'var(--ink-sub)', display: 'flex', alignItems: 'center', gap: 4 }}>
                                    细节
                                    {(sc.details || []).length > 0 && (
                                        <span style={{ color: 'var(--ink-mute)', fontWeight: 400 }}>
                                            ({(sc.details || []).length} 条)
                                        </span>
                                    )}
                                </label>
                                <textarea value={listText(sc.details)} onChange={e => setSc(i, 'details', lines(e.target.value))} rows={2} placeholder="细节（每行一条）"
                                    style={inp} />
                            </div>

                            {/* 展开区域：元素绑定/节拍/素材/备注 */}
                            {expanded && (
                                <div style={{ marginTop: 8, borderTop: '1px dashed var(--line-soft)', paddingTop: 8 }}>
                                    {/* 出场元素绑定 - 按类型分组，已选在前 */}
                                    <div style={{ marginBottom: 8 }}>
                                        <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-sub)' }}>
                                            出场元素
                                            {(sc.elements || []).length > 0 && (
                                                <span style={{ fontWeight: 400, color: 'var(--dai)', marginLeft: 4 }}>
                                                    ({(sc.elements || []).length} 已选)
                                                </span>
                                            )}
                                        </label>
                                        <div style={{ marginTop: 4 }}>
                                            {(() => {
                                                const elemGroups = [
                                                    { key: 'characters', label: '角色', icon: '' },
                                                    { key: 'items', label: '物品', icon: '' },
                                                    { key: 'settings', label: '设定', icon: '' },
                                                    { key: 'maps', label: '地图', icon: '' },
                                                ]
                                                const selectedIds = new Set(sc.elements || [])
                                                return elemGroups.map(g => {
                                                    const elems = flatElements.filter(e => e.kind === g.key)
                                                    if (elems.length === 0) return null
                                                    // 已选排前
                                                    const sorted = [...elems].sort((a, b) => {
                                                        const sa = selectedIds.has(a.id) ? 0 : 1
                                                        const sb = selectedIds.has(b.id) ? 0 : 1
                                                        return sa - sb
                                                    })
                                                    return (
                                                        <div key={g.key} style={{ marginBottom: 4 }}>
                                                            <div style={{ fontSize: 10, color: 'var(--ink-mute)', marginBottom: 2 }}>{g.label}</div>
                                                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                                                                {sorted.map(e => {
                                                                    const bound = selectedIds.has(e.id)
                                                                    return (
                                                                        <button key={e.id}
                                                                            style={{
                                                                                padding: '2px 8px', borderRadius: 12, fontSize: 11, cursor: 'pointer',
                                                                                border: `1px solid ${bound ? 'var(--dai)' : 'var(--line-soft)'}`,
                                                                                background: bound ? 'var(--dai-wash)' : 'var(--paper-raised)',
                                                                                color: bound ? 'var(--dai)' : 'var(--ink-sub)',
                                                                                fontWeight: bound ? 600 : 400,
                                                                                order: bound ? -1 : 0,
                                                                            }}
                                                                            onClick={() => {
                                                                                const prev = sc.elements || []
                                                                                const next = bound ? prev.filter(x => x !== e.id) : [...prev, e.id]
                                                                                setSc(i, 'elements', next)
                                                                            }}>
                                                                            {e.name || e.id}
                                                                        </button>
                                                                    )
                                                                })}
                                                            </div>
                                                        </div>
                                                    )
                                                })
                                            })()}
                                            {flatElements.length === 0 && <span style={{ fontSize: 11, color: 'var(--ink-mute)' }}>（无元素）</span>}
                                        </div>
                                    </div>

                                    {/* 节拍列表（可勾选 + 内联编辑） */}
                                    {beats.length > 0 && (
                                        <div style={{ marginBottom: 8 }}>
                                            <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-sub)' }}>
                                                节拍
                                                {selSet.size > 0 && (
                                                    <span style={{ fontWeight: 400, color: 'var(--dai)', marginLeft: 4 }}>
                                                        (已选 {selSet.size} 条)
                                                    </span>
                                                )}
                                            </label>
                                            <div style={{ marginTop: 4, border: '1px solid var(--line-soft)', borderRadius: 4, overflow: 'hidden' }}>
                                                {beats.map((beat, bi) => (
                                                    <div key={bi} style={{
                                                        display: 'flex', alignItems: 'center', gap: 6,
                                                        padding: '4px 8px', fontSize: 12,
                                                        background: selSet.has(bi) ? 'var(--dai-wash)' : 'var(--paper)',
                                                        borderBottom: bi < beats.length - 1 ? '1px solid var(--line-soft)' : 'none',
                                                    }}>
                                                        <input type="checkbox" checked={selSet.has(bi)}
                                                            onChange={() => toggleBeat(i, bi)}
                                                            style={{ flexShrink: 0 }} />
                                                        <span style={{ flex: 1, color: selSet.has(bi) ? 'var(--dai)' : 'var(--ink)' }}>
                                                            {beat}
                                                        </span>
                                                        <span style={{ fontSize: 10, color: 'var(--ink-mute)' }}>#{bi + 1}</span>
                                                    </div>
                                                ))}
                                            </div>
                                            {selSet.size > 0 && (
                                                <button style={{ ...btnStyle(false, false), marginTop: 4, fontSize: 11 }}
                                                    onClick={() => addBeatNote(i)}>
                                                    段级备注
                                                </button>
                                            )}
                                        </div>
                                    )}

                                    {/* 素材编辑区 - 分类工具栏 + 输入 + 已有列表 */}
                                    <div style={{ marginBottom: 8 }}>
                                        <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-sub)' }}>
                                            素材编辑
                                            {sceneFrags(i).length > 0 && (
                                                <span style={{ fontWeight: 400, color: 'var(--ink-mute)', marginLeft: 4 }}>
                                                    ({sceneFrags(i).length} 条)
                                                </span>
                                            )}
                                        </label>
                                        {/* 分类工具栏 */}
                                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, marginTop: 4, marginBottom: 6 }}>
                                            {fragToolbar.map(tb => (
                                                <button key={tb.type}
                                                    style={{
                                                        padding: '3px 8px', borderRadius: 4, fontSize: 11, cursor: 'pointer',
                                                        border: '1px solid var(--line-soft)',
                                                        background: (l4FragDraft[i] || {}).type === tb.type.replace('_card', '') ? 'var(--dai-wash)' : 'var(--paper-raised)',
                                                        color: (l4FragDraft[i] || {}).type === tb.type.replace('_card', '') ? 'var(--dai)' : 'var(--ink-sub)',
                                                    }}
                                                    title={tb.desc}
                                                    onClick={() => addFragFromToolbar(i, tb.type)}>
                                                    {tb.label}
                                                </button>
                                            ))}
                                        </div>
                                        {/* 素材输入区 */}
                                        <textarea
                                            value={(l4FragDraft[i] || {}).content || ''}
                                            onChange={e => setL4FragDraft(d => ({ ...d, [i]: { ...(d[i] || { type: 'detail' }), content: e.target.value } }))}
                                            rows={3} placeholder="输入素材内容；选中文本点「留空」包成 [意图]，或直接写 [意图] 标记留空"
                                            style={{ ...inp, fontFamily: 'var(--font-mono)' }} />
                                        <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                                            <select value={(l4FragDraft[i] || {}).type || 'detail'}
                                                onChange={e => setL4FragDraft(d => ({ ...d, [i]: { ...(d[i] || { content: '' }), type: e.target.value } }))}
                                                style={{ fontSize: 11, padding: '2px 6px', border: '1px solid var(--line)', borderRadius: 4 }}>
                                                <option value="detail">细节</option>
                                                <option value="dialogue">对白</option>
                                                <option value="action">动作</option>
                                                <option value="item">物品</option>
                                                <option value="character">角色</option>
                                                <option value="setting">设定</option>
                                            </select>
                                            <button style={btnStyle(false, true)} onClick={() => saveFrag(i)}>保存</button>
                                        </div>
                                        {/* 已有素材列表 - 分类折叠 */}
                                        {sceneFrags(i).length > 0 && (
                                            <div style={{ marginTop: 6, fontSize: 11 }}>
                                                {(() => {
                                                    const frags = sceneFrags(i)
                                                    const groups = {}
                                                    frags.forEach(f => {
                                                        const t = f.type || 'detail'
                                                        if (!groups[t]) groups[t] = []
                                                        groups[t].push(f)
                                                    })
                                                    const typeIcons = { dialogue: '', detail: '', action: '', item: '', character: '', setting: '' }
                                                    return Object.entries(groups).map(([type, list]) => (
                                                        <div key={type} style={{ marginBottom: 4 }}>
                                                            <div style={{ color: 'var(--ink-sub)', marginBottom: 2, fontWeight: 500 }}>
                                                                {typeIcons[type] || ''} {type} ({list.length})
                                                            </div>
                                                            {list.map(f => (
                                                                <div key={f.id} style={{
                                                                    padding: '3px 6px', background: 'var(--bg-card-2)',
                                                                    borderRadius: 4, marginBottom: 2,
                                                                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                                                }}>
                                                                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                                        {(f.content || '').slice(0, 80)}{(f.content || '').length > 80 ? '…' : ''}
                                                                    </span>
                                                                    <button style={{ fontSize: 10, color: 'var(--red)', cursor: 'pointer', border: 'none', background: 'none', flexShrink: 0, marginLeft: 4 }}
                                                                        onClick={async () => { await phFragmentsPut({ book_root: bookRoot, fid: f.id }); await refreshWorkbench(bookRoot) }}>
                                                                        ✕
                                                                    </button>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    ))
                                                })()}
                                            </div>
                                        )}
                                    </div>

                                    {/* 留空槽 - 从素材文本提取 [意图] */}
                                    {(() => {
                                        // 当前素材草稿 + 已保存素材文本里的 [xxx] 留空标记
                                        const srcTexts = [
                                            (l4FragDraft[i] || {}).content || '',
                                            ...sceneFrags(i).map(f => String(f.content || f.text || '')),
                                        ]
                                        const blanksFromText = []
                                        srcTexts.forEach(txt => {
                                            const re = /\[([^\[\]]+)\]/g
                                            let m
                                            while ((m = re.exec(txt)) !== null) blanksFromText.push(m[1].trim())
                                        })
                                        if (blanksFromText.length === 0) return null
                                        return (
                                            <div style={{ marginBottom: 8 }}>
                                                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-sub)' }}>
                                                    留空槽
                                                    <span style={{ fontWeight: 400, color: 'var(--ink-mute)', marginLeft: 4 }}>
                                                        ({blanksFromText.length} 个)
                                                    </span>
                                                </label>
                                                <div style={{ marginTop: 4, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                                                    {blanksFromText.map((intention, bi) => (
                                                        <span key={bi} style={{
                                                            display: 'inline-block', padding: '2px 8px', fontSize: 11,
                                                            background: 'var(--amber-wash)', border: '1px dashed var(--amber)',
                                                            color: 'var(--amber)', borderRadius: 2,
                                                        }}>
                                                            {intention || '空位'}
                                                        </span>
                                                    ))}
                                                </div>
                                            </div>
                                        )
                                    })()}

                                    {/* 场景备注 */}
                                    <div style={{ marginBottom: 4 }}>
                                        <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-sub)' }}>场景备注</label>
                                        <textarea
                                            value={sc.scene_note || ''}
                                            onChange={e => setSc(i, 'scene_note', e.target.value)}
                                            rows={2} placeholder="场景级备注（不影响正文，仅供 AI 参考）"
                                            style={inp} />
                                        {sceneNotes(i).length > 0 && (
                                            <div style={{ marginTop: 4, fontSize: 11, color: 'var(--ink-sub)' }}>
                                                已有备注：{sceneNotes(i).map(n => n.content).join('；')}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )}
                        </div>
                    )
                })}
                <button style={{ ...btnStyle(false, false), marginBottom: 6 }} onClick={() => setL4Edit(a => [...a, { name: '新场景', environment: '', actions: [], dialogues: [], conflicts: [], details: [], elements: [], fragments: [], scene_note: '' }])}>
                    + 添加场景
                </button>
                <div style={{ display: 'flex', gap: 6 }}>
                    <button style={btnStyle(busy, true)} disabled={busy} onClick={handleSaveL4}>保存 l4</button>
                    <button style={btnStyle(false, false)} onClick={() => { setL4Edit(null); setL4SceneExpanded(null); setL4FragDraft({}); setL4BeatSelected({}) }}>取消</button>
                </div>
            </div>
        )
    }

    const isMultiL3 = (state) => {
        const l3d = state?.levels?.l3?.data
        return !!(l3d && Array.isArray(l3d.chapters) && l3d.chapters.length)
    }

    // 多章章纲（改造成 arc 用）
    const renderL3Multi = (state) => {
        const l3d = state.levels.l3.data
        const chapters = l3d.chapters || []
        const active = state.active_chapter || 0
        const confirmed = !!state.levels.l3.confirmed
        return (
            <div key="l3" style={{ ...cardStyle, borderLeft: confirmed ? '3px solid var(--green)' : '3px solid var(--amber)' }}>
                <div style={titleStyle}>
                    <span>{LEVEL_META.l3.label}</span>
                    <span style={badgeStyle(confirmed)}>{confirmed ? '已确认' : '待确认'}</span>
                    <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                        {!confirmed && (
                            <button style={btnStyle(busy, true)} disabled={busy} onClick={() => handleConfirm('l3')}>确认整组章纲</button>
                        )}
                    </span>
                </div>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
                    {chapters.map((ch, i) => (
                        <button key={i}
                            onClick={() => handleSetActiveChapter(i)}
                            style={{
                                padding: '4px 10px', borderRadius: 12, fontSize: 12, cursor: 'pointer',
                                border: `1px solid ${i === active ? 'var(--dai)' : 'var(--line-soft)'}`,
                                background: i === active ? 'var(--dai-wash)' : 'var(--paper-raised)',
                                color: i === active ? 'var(--dai)' : 'var(--ink-sub)',
                                fontWeight: i === active ? 600 : 400,
                            }}>
                            {arcChapterNum(activeArc, i)}. {stripCh(ch.title) || arcChapterLabel(activeArc, i)}
                        </button>
                    ))}
                </div>
                <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 6 }}>
                    点章名切换当前章（用于下钻 l4/l5）；对「修改」操作的是当前选中的章
                </div>
                {(l3ChaptersDraft || chapters).map((ch, i) => {
                    const chActive = i === active
                    const draft = l3ChaptersDraft ? (l3ChaptersDraft[i] || ch) : ch
                    const setCh = (field, val) => setL3ChaptersDraft(d => {
                        const c = JSON.parse(JSON.stringify(d))
                        c[i][field] = val
                        return c
                    })
                    const inp = { width: '100%', boxSizing: 'border-box', padding: 6, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13, marginTop: 4 }
                    return (
                        <div key={i} style={{ padding: 8, borderRadius: 6, border: `1px solid ${chActive ? 'var(--line)' : 'var(--bg-main)'}`, background: chActive ? 'var(--paper-sb)' : 'var(--bg-card-2)', marginBottom: 6 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                <b style={{ fontSize: 13, flexShrink: 0 }}>{arcChapterLabel(activeArc, i)}</b>
                                <input value={stripCh(draft.title || '')} onChange={e => setCh('title', e.target.value)} placeholder="章名"
                                    style={{ ...inp, marginTop: 0, flex: 1 }} />
                                <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                                    <button style={btnStyle(busy, false)} disabled={busy} onClick={() => { handleSetActiveChapter(i); setModifyLevel('l3') }}>
                                        对话修改
                                    </button>
                                </span>
                            </div>
                            <textarea value={draft.core || ''} onChange={e => setCh('core', e.target.value)} rows={2} placeholder="一句话核心" style={inp} />
                            <textarea value={(draft.beats || []).join('\n')}
                                onChange={e => setCh('beats', e.target.value.split('\n').map(s => s.trim()).filter(Boolean))}
                                rows={3} placeholder="拍（每行一条）" style={inp} />
                            {modifyLevel === 'l3' && chActive && (
                                <div style={{ marginTop: 6 }}>
                                    <textarea value={modifyInput} onChange={e => setModifyInput(e.target.value)} rows={2}
                                        placeholder={`对第 ${i + 1} 章章纲说修改意见…`}
                                        style={{ width: '100%', boxSizing: 'border-box', padding: 8, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13 }} />
                                    <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                                        <button style={btnStyle(busy || !modifyInput.trim(), true)} disabled={busy || !modifyInput.trim()} onClick={handleModify}>提交修改</button>
                                        <button style={btnStyle(false, false)} onClick={() => { setModifyLevel(null); setModifyInput('') }}>取消</button>
                                    </div>
                                </div>
                            )}
                        </div>
                    )
                })}
                <div style={{ marginTop: 6 }}>
                    <button style={btnStyle(busy, true)} disabled={busy || !l3ChaptersDraft} onClick={handleSaveL3Multi}>保存整组章纲</button>
                    <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--ink-sub)' }}>保存后 l4/l5 清空，需重新生成</span>
                </div>
            </div>
        )
    }

    // ── 侧边栏导航（全书概览 / 写作 / 系统 三个 hub 置顶）────
    const renderSidebarNav = () => {
        const hubStyle = { textAlign: 'left', width: '100%', fontSize: 14, fontWeight: 600, letterSpacing: '.04em' }
        return (
            <nav className="sidebar-nav">
                <button type="button" onClick={() => setTab('overview')}
                    className={`nav-item ${tab === 'overview' ? 'active' : ''}`} style={hubStyle}>
                    <span>全书概览</span>
                </button>
                <button type="button" onClick={() => setTab('write')}
                    className={`nav-item ${tab === 'write' ? 'active' : ''}`} style={hubStyle}>
                    <span>写作</span>
                </button>
                <button type="button" onClick={() => setTab('system')}
                    className={`nav-item ${tab === 'system' ? 'active' : ''}`} style={hubStyle}>
                    <span>系统</span>
                </button>
            </nav>
        )
    }

    // ── 通道配置（API 预设）页 ─────────────────────────────────
    const renderApiConfig = () => {
        const presets = apiLib?.text_presets || []
        const cur = presets.find(p => p.is_current) || presets.find(p => p.id === apiLib?.current_text_id) || null
        const curModel = cur?.fields?.ARK_MODEL_PRO || ''
        const embedModel = apiLib?.embed_config?.fields?.EMBED_MODEL || ''
        const embedUrl = apiLib?.embed_config?.fields?.EMBED_BASE_URL || ''
        return (
            <div style={{ maxWidth: 920 }}>
                <div className="section-block" style={{ borderLeft: '3px solid var(--cinnabar)' }}>
                    <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>通道配置（API 预设）</div>
                    <div style={{ fontSize: 13, lineHeight: 1.9 }}>
                        <div>文字模型：{cur ? `${cur.name}${curModel ? ` · ${curModel}` : ''}` : '未选预设（用 .env 默认）'}</div>
                        <div>向量模型：{embedModel || '未配置'}{embedUrl ? ` · ${embedUrl}` : ''}</div>
                    </div>
                    <div style={{ marginTop: 6 }}>
                        <a href="#/api-presets" className="btn" style={{ background: 'var(--cinnabar)', color: 'var(--paper-raised)', border: 'none', padding: '6px 12px', borderRadius: 6, textDecoration: 'none', fontSize: 13 }}>管理 API 预设</a>
                        <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--ink-sub)' }}>切预设即时生效（无需重启），测试书生成/评分用当前预设</span>
                    </div>
                </div>
            </div>
        )
    }

    // ── 设定集文档页（浏览 basic_settings + 设定集/*.md，宽屏多列）────────
    const renderSettingFiles = () => (
        <div>
            <div className="section-block">
                <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>设定集文档</div>
                {settingFilesErr && <div style={{ fontSize: 12, color: 'var(--cinnabar-d)', marginBottom: 6 }}>⚠ {settingFilesErr}</div>}
                {!settingFiles ? (
                    <div style={{ color: 'var(--ink-mute)', fontSize: 12 }}>加载设定集…</div>
                ) : (
                    <>
                        <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 8 }}>共 {settingFiles.files?.length || 0} 个设定文档（设定集/*.md）· 基本设定字段见「总览」</div>
                        {(settingFiles.files || []).length === 0 && (
                            <div style={{ fontSize: 12, color: 'var(--ink-mute)', padding: 10, border: '1px dashed var(--line)', borderRadius: 6 }}>（还没有设定集文件——先在「总览」填基本设定并点「重新生成设定集」）</div>
                        )}
                        <div className="setting-files-grid">
                            {(settingFiles.files || []).map(f => (
                                <details key={f.name} style={{ marginBottom: 6, border: '1px solid var(--line)', borderRadius: 8, background: 'var(--paper-raised)' }}>
                                    <summary style={{ cursor: 'pointer', padding: '8px 10px', fontSize: 13, fontWeight: 600 }}>{f.name}.md</summary>
                                    <pre style={{ ...preStyle, fontSize: 13, lineHeight: 1.8, padding: '8px 12px' }}>{f.content}</pre>
                                </details>
                            ))}
                        </div>
                    </>
                )}
            </div>
        </div>
    )

    // ── 分析 · 全书概览（前端聚合 arcs/elements）──────────────
    const renderAnalysisOverview = () => {
        const list = arcs.arcs || []
        const chapters = list.flatMap(a => (a.chapters || []).map(c => ({ ...c, arcName: a.name })))
        const words = chapters.reduce((s, c) => s + (c.text ? c.text.length : 0), 0)
        const polluted = chapters.filter(c => c.polluted).length
        const avg = arr => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0)
        const intAvg = avg(chapters.filter(c => c.intent_score != null).map(c => c.intent_score))
        const quaAvg = avg(chapters.filter(c => c.quality_score != null).map(c => c.quality_score))

        const scoreOption = {
            tooltip: { trigger: 'axis' },
            legend: { bottom: 0, data: ['意图兑现', '纯质量', '综合'] },
            grid: { left: 40, right: 16, top: 30, bottom: 40 },
            xAxis: { type: 'category', data: chapters.map(c => `第${c.num}章`) },
            yAxis: { type: 'value', min: 0, max: 1 },
            series: [
                { name: '意图兑现', type: 'line', data: chapters.map(c => c.intent_score), symbolSize: 6 },
                { name: '纯质量', type: 'line', data: chapters.map(c => c.quality_score), symbolSize: 6 },
                { name: '综合', type: 'line', data: chapters.map(c => c.overall), symbolSize: 6 },
            ],
        }
        const elemUsage = {}
        list.forEach(a => {
            (a.selected?.characters || []).forEach(id => { elemUsage[id] = (elemUsage[id] || 0) + 1 })
            ;(a.selected?.items || []).forEach(id => { elemUsage[id] = (elemUsage[id] || 0) + 1 })
            ;(a.selected?.settings || []).forEach(id => { elemUsage[id] = (elemUsage[id] || 0) + 1 })
        })

        return (
            <div>
                <div className="stat-grid">
                    {[['情节', String(list.length)], ['已落盘章', String(chapters.length)],
                      ['总字数', String(words)], ['⚠ 污染章', String(polluted)],
                      ['意图均分', intAvg ? intAvg.toFixed(3) : '—'],
                      ['质量均分', quaAvg ? quaAvg.toFixed(3) : '—']].map(([label, value]) => (
                        <article key={label} className="card stat-card">
                            <span className="stat-label">{label}</span>
                            <span className="stat-value">{value}</span>
                        </article>
                    ))}
                </div>
                {chapters.length ? (
                    <article className="card" style={{ marginTop: 12 }}>
                        <div className="card-header">
                            <div className="card-title">每章双评分走势</div>
                        </div>
                        <ChartWrapper option={scoreOption} height={300} />
                    </article>
                ) : (
                    <div className="empty-state" style={{ margin: '20px 0' }}><p>还没有落盘章节——先在「章节生成」逐情节创作，或用「批量生成」。</p></div>
                )}
            </div>
        )
    }

    // ── 地图布局渲染（复用灵感工坊 .map-house CSS，画宅邸/城池平面示意）──
    const renderMapLayout = (layout) => {
        if (!layout || !layout.length) return null
        const find = (z) => layout.find(x => x.zone === z)
        if (find('palace') || find('imperial')) {
            return (
                <div className="map-nest">
                    {layout.map(z => <div key={z.zone} className={`map-zone nest-${z.zone}`} title={z.desc}>{z.name}</div>)}
                </div>
            )
        }
        const box = (z, cls) => {
            const it = find(z)
            return it ? <div className={`map-zone ${cls}`} title={it.desc}>{it.name}</div>
                : <div className={`map-zone ${cls} empty`} />
        }
        return (
            <div className="map-house">
                <div className="map-row">{box('rear', 'main')}</div>
                <div className="map-row">{box('west', 'side')}{box('third', 'main')}{box('east', 'side')}</div>
                <div className="map-row"><div className="map-side-spacer" />{box('second', 'main')}<div className="map-side-spacer" /></div>
                <div className="map-row">{box('first', 'main')}</div>
                <div className="map-row">{box('gate', 'gate')}</div>
            </div>
        )
    }

    // ── 分析 · 角色图鉴（四类元素 + 点开详情 + 地图布局）──────────────
    const renderGalleryView = () => {
        const list = arcs.arcs || []
        const kinds = [['characters', '角色', ''], ['items', '物品', ''], ['settings', '设定', ''], ['locations', '地点', ''], ['maps', '地图', '']]
        const rows = []
        for (const [coll, label, emoji] of kinds) {
            for (const e of (elements[coll] || [])) {
                const usedArcs = list.filter(a => (a.selected?.[coll] || []).includes(e.id))
                const chapNums = usedArcs.flatMap(a => (a.chapters || []).map(c => c.num))
                rows.push({ coll, label, emoji, elem: e, usedArcs, chapNums })
            }
        }
        const totalCh = list.reduce((s, a) => s + (a.chapters || []).length, 0)
        // 关联地图卡：自身是地图 / 关联了地图 / 被地图关联
        const relatedMaps = (e) => {
            const out = []
            for (const m of (elements.maps || [])) {
                if (m.id === e.id) { out.push(m); continue }
                const byRel = (m.relations || []).some(r => r.to_id === e.id)
                const toRel = (e.relations || []).some(r => r.to_kind === 'map' && r.to_id === m.id)
                if (byRel || toRel) out.push(m)
            }
            return out
        }
        const relName = (r) => {
            for (const [coll] of kinds) {
                for (const x of (elements[coll] || [])) {
                    if (x.id === r.to_id) return `${x.name}`
                }
            }
            return r.to_id
        }
        return (
            <div>
                <div className="stat-grid">
                    {[['角色', String((elements.characters || []).length)],
                      ['物品', String((elements.items || []).length)],
                      ['设定', String((elements.settings || []).length)],
                      ['地图', String((elements.maps || []).length)],
                      ['情节', String(list.length)],
                      ['章', String(totalCh)]].map(([label, value]) => (
                        <article key={label} className="card stat-card">
                            <span className="stat-label">{label}</span>
                            <span className="stat-value">{value}</span>
                        </article>
                    ))}
                </div>
                <article className="card" style={{ marginTop: 12 }}>
                    <div className="card-header"><div className="card-title">角色 / 物品 / 设定 / 地图 · 点开看详情与地图</div></div>
                    {rows.length ? (
                        <table className="data-table">
                            <thead><tr><th>名称</th><th>参与情节</th><th>出场章</th><th>描述</th></tr></thead>
                            <tbody>
                                {rows.map(r => (
                                    <Fragment key={r.elem.id}>
                                        <tr onClick={() => setGalleryExpanded(galleryExpanded === r.elem.id ? null : r.elem.id)} style={{ cursor: 'pointer' }}>
                                            <td style={{ fontWeight: 600 }}>{r.elem.name}</td>
                                            <td>{r.usedArcs.length ? r.usedArcs.map(a => a.name).join('、') : '—'}</td>
                                            <td>{r.chapNums.length ? r.chapNums.join('、') : <span style={{ color: 'var(--ink-mute)' }}>未出场</span>}</td>
                                            <td>{(r.elem.desc || '').slice(0, 40) || '—'}</td>
                                        </tr>
                                        {galleryExpanded === r.elem.id && (
                                            <tr className="gallery-detail-row">
                                                <td colSpan={4}>
                                                    <div className="gallery-detail">
                                                        <div className="gallery-desc">{r.elem.desc || '（无描述）'}</div>
                                                        {(r.elem.fields || []).length > 0 && (
                                                            <div className="gallery-fields">
                                                                {(r.elem.fields || []).map((f, i) => (
                                                                    <div className="gallery-frow" key={i}><span className="fn">{f.name}</span><span className="fv">{f.value}</span></div>
                                                                ))}
                                                            </div>
                                                        )}
                                                        {(r.elem.relations || []).length > 0 && (
                                                            <div className="gallery-rellist">
                                                                {(r.elem.relations || []).map((rel, i) => (
                                                                    <span key={i} className="gallery-rel">{rel.name} → {relName(rel)}</span>
                                                                ))}
                                                            </div>
                                                        )}
                                                        {r.coll === 'maps' && renderMapLayout(r.elem.layout)}
                                                        {r.coll !== 'maps' && relatedMaps(r.elem).length > 0 && (
                                                            <div className="gallery-maps">
                                                                <div className="gallery-maps-title">相关地图</div>
                                                                {relatedMaps(r.elem).map(m => (
                                                                    <div key={m.id} className="gallery-map-block">
                                                                        <div className="gallery-map-name">{m.name}</div>
                                                                        {renderMapLayout(m.layout)}
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        )}
                                                    </div>
                                                </td>
                                            </tr>
                                        )}
                                    </Fragment>
                                ))}
                            </tbody>
                        </table>
                    ) : (
                        <div className="empty-state"><p>还没有元素——先在「大纲与章纲」创建</p></div>
                    )}
                </article>
            </div>
        )
    }

    // ── 分析 · 节奏雷达 ───────────────────────────────────────
    const renderPacing = () => {
        const list = arcs.arcs || []
        const chapters = list.flatMap(a => (a.chapters || []).map(c => ({ ...c, arcName: a.name })))
        const words = chapters.map(c => c.text ? c.text.length : 0)
        const avgW = words.length ? words.reduce((a, b) => a + b, 0) / words.length : 0
        const wordOption = {
            tooltip: { trigger: 'axis' },
            grid: { left: 40, right: 16, top: 20, bottom: 40 },
            xAxis: { type: 'category', data: chapters.map(c => `第${c.num}章`) },
            yAxis: { type: 'value' },
            series: [{ type: 'bar', data: words, itemStyle: { color: 'var(--dai)' }, label: { show: true, position: 'top', fontSize: 10 } }],
        }
        return (
            <div>
                <div className="stat-grid">
                    {[['章数', String(chapters.length)], ['平均字数', Math.round(avgW) || '—'],
                      ['最长沙', String(Math.max(0, ...words))], ['最短章', String(Math.min(...words))]].map(([label, value]) => (
                        <article key={label} className="card stat-card">
                            <span className="stat-label">{label}</span>
                            <span className="stat-value">{value}</span>
                        </article>
                    ))}
                </div>
                {chapters.length ? (
                    <article className="card" style={{ marginTop: 12 }}>
                        <div className="card-header"><div className="card-title">每章字数分布</div></div>
                        <ChartWrapper option={wordOption} height={300} />
                    </article>
                ) : (
                    <div className="empty-state" style={{ margin: '20px 0' }}><p>暂无章节字数数据</p></div>
                )}
                <div className="section-label" style={{ marginTop: 16 }}>情节进度甘特（情节 → 章）</div>
                {list.length ? list.map(a => (
                    <div key={a.id} style={{ marginBottom: 10, border: '1px solid var(--line)', borderRadius: 8, padding: 10 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>{a.name} {a.status === 'done' ? '' : a.status === 'active' ? '' : '○'}</div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                            {(a.chapters || []).map(c => (
                                <span key={c.num} title={`第${c.num}章 ${c.title} · 综合${c.overall ?? '—'}`}
                                    style={{ padding: '3px 8px', borderRadius: 8, fontSize: 12, background: c.polluted ? 'var(--amber-wash)' : 'var(--green-wash)', color: 'var(--ink)', border: '1px solid var(--line)' }}>
                                    {c.num}{c.overall != null ? ` ${(c.overall).toFixed(2)}` : ''}
                                </span>
                            ))}
                        </div>
                    </div>
                )) : <div className="empty-state"><p>还没有情节</p></div>}
            </div>
        )
    }

    // ── 系统 · 文档编辑器（全量 md 编辑：正文/AI生成/大纲/设定集）───
    const renderDocEdit = () => {
        const renderNodes = (items, depth = 0) => {
            if (!items || !items.length) return null
            return items.map(item => {
                const key = item.path || item.name
                if (item.type === 'dir') {
                    return (
                        <div key={key} style={{ paddingLeft: depth * 14 }}>
                            <div style={{ fontWeight: 600, fontSize: 12.5, margin: '6px 0 2px', color: 'var(--ink-sub)' }}>{item.name}</div>
                            {renderNodes(item.children, depth + 1)}
                        </div>
                    )
                }
                return (
                    <div key={key} style={{ paddingLeft: depth * 14 + 14 }}>
                        <button onClick={() => setDocSelPath(item.path)}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 12.5, color: docSelPath === item.path ? 'var(--cinnabar)' : 'var(--ink)', padding: '2px 4px', textAlign: 'left', width: '100%', fontFamily: 'inherit' }}>
                            {item.name}
                        </button>
                    </div>
                )
            })
        }

        return (
            <div className="content-grid files-layout">
                <article className="card files-tree-card">
                    <div className="card-header">
                        <div className="card-title">目录树</div>
                        <span style={{ fontSize: 12, color: 'var(--ink-sub)' }}>正文 / AI生成 / 大纲 / 设定集</span>
                    </div>
                    <div style={{ maxHeight: 520, overflow: 'auto', padding: 8 }}>
                        {docErr && <div style={{ fontSize: 12, color: 'var(--cinnabar-d)', marginBottom: 8 }}>⚠ {docErr}</div>}
                        {!docTree ? <div style={{ fontSize: 13, color: 'var(--ink-mute)' }}>加载目录…</div> : Object.entries(docTree).map(([folder, items]) => (
                            <div key={folder}>
                                <div style={{ fontSize: 13, fontWeight: 700, margin: '8px 0 2px' }}>{folder}</div>
                                {renderNodes(items)}
                            </div>
                        ))}
                    </div>
                </article>
                <article className="card files-preview-card">
                    <div className="card-header">
                        <div className="card-title">内容编辑</div>
                        {docSelPath && <span style={{ fontSize: 12, color: 'var(--ink-sub)' }}>{docSelPath} · {docContent.length} 字 {docDirty ? '· 未保存' : ''}</span>}
                    </div>
                    {docSelPath ? (
                        <>
                            <textarea
                                value={docContent}
                                onChange={e => { setDocContent(e.target.value); setDocDirty(true) }}
                                spellCheck={false}
                                style={{ width: '100%', minHeight: 480, boxSizing: 'border-box', fontFamily: 'inherit', fontSize: 13.5, lineHeight: 1.8, padding: 10, border: '1px solid var(--line-soft)', borderRadius: 6 }}
                            />
                            <div style={{ marginTop: 8 }}>
                                <button style={btnStyle(docDirty, false)} disabled={!docDirty}
                                    onClick={async () => {
                                        setDocSaveState('saving')
                                        try {
                                            await postFileWrite(docSelPath, docContent)
                                            setDocDirty(false); setDocSaveState('saved')
                                            setNotice(`已保存 ${docSelPath}`)
                                        } catch (e) { setError(`保存失败: ${e.message || e}`); setDocSaveState('error') }
                                    }}>
                                    {docSaveState === 'saving' ? '保存中…' : '保存'}
                                </button>
                                {docSaveState === 'saved' && <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--green)' }}>✓ 已保存</span>}
                                {docSaveState === 'error' && <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--cinnabar-d)' }}>保存失败</span>}
                            </div>
                        </>
                    ) : (
                        <div className="empty-state"><p>选择左侧文件以编辑内容</p></div>
                    )}
                </article>
            </div>
        )
    }

    // ── 系统 · 导出（前端聚合 → JSON 下载）────────────────────
    const renderExport = () => {
        const doExport = () => {
            setExporting(true)
            try {
                const payload = {
                    export_metadata: { exported_at: new Date().toISOString(), book_name: bookTitle || bookRoot },
                    basic_settings: settings,
                    elements,
                    arcs: {
                        next_chapter_num: arcs.next_chapter_num,
                        arcs: (arcs.arcs || []).map(a => ({
                            id: a.id, name: a.name, l1: a.l1, l2: a.l2, n_chapters: a.n_chapters,
                            status: a.status, selected: a.selected, finalized: a.finalized,
                            chapters: (a.chapters || []).map(c => ({
                                num: c.num, title: c.title, core: c.core,
                                intent_score: c.intent_score, quality_score: c.quality_score,
                                overall: c.overall, polluted: c.polluted, text: c.text,
                            })),
                        })),
                    },
                }
                const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
                const url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url
                a.download = `${bookTitle || 'book'}_${new Date().toISOString().slice(0, 10)}.json`
                a.click()
                URL.revokeObjectURL(url)
                setNotice('导出成功')
            } catch (e) { setError(`导出失败: ${e.message || e}`) } finally { setExporting(false) }
        }
        return (
            <div className="section-block" style={{ maxWidth: 920 }}>
                <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>导出完整书数据</div>
                <div style={{ fontSize: 13, color: 'var(--ink-sub)', lineHeight: 1.9, marginBottom: 12 }}>
                    导出内容：基本设定 · 元素清单（角色/物品/设定）· 全部情节（含逐章双评分、污染标记、正文）·
                    已落盘章号。JSON 下载便于备份/迁移。
                </div>
                <button style={btnStyle(exporting, true)} disabled={exporting} onClick={doExport}>
                    {exporting ? '导出中…' : '导出 JSON'}
                </button>
            </div>
        )
    }

    // ── 系统 · 批量生成（逐情节流水线）─────────────────────────
    const startBatch = async () => {
        if (!bookRoot) return
        setError(''); setNotice('')
        try {
            const r = await phAiBatchGenerate({
                book_root: bookRoot,
                target_chapters: Number(batchTarget) || 10,
                n_chapters_per_arc: Number(batchPerArc) || 3,
                arc_briefs: batchBrief.trim() ? batchBrief.split('\n').map(s => s.trim()).filter(Boolean) : null,
                select_all_elements: true,
            })
            setBatchTaskId(r.task_id)
            pollBatch(r.task_id)
            setNotice('批量生成已启动（后台逐情节驱动，可在「全书概览」看进度）')
        } catch (e) { setError(`启动失败: ${e.message || e}`) }
    }

    const pollBatch = (tid) => {
        if (batchPoll) clearInterval(batchPoll)
        const iv = setInterval(async () => {
            try {
                const s = await phAiOptimizeStatus(tid)
                setBatchStatus(s)
                if (s.status !== 'running') {
                    clearInterval(iv); setBatchPoll(null)
                    refreshWorkbench(bookRoot)
                }
            } catch { /* 轮询失败忽略 */ }
        }, 3000)
        setBatchPoll(iv)
    }

    const stopBatch = async () => {
        if (batchTaskId) { try { await phAiCancelTask(batchTaskId) } catch {} }
        if (batchPoll) { clearInterval(batchPoll); setBatchPoll(null) }
        setNotice('批量生成已请求停止')
    }

    const renderBatch = () => {
        const prog = batchStatus?.progress || {}
        const st = batchStatus?.status || 'idle'
        return (
            <div style={{ maxWidth: 920 }}>
                <div className="section-block">
                    <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>批量生成全书</div>
                    <div style={{ fontSize: 13, color: 'var(--ink-sub)', lineHeight: 1.9, marginBottom: 12 }}>
                        自动驱动逐情节流水线：新建情节 → 全选参与元素 → l2 → l3 → 逐章 l4/l5 → 双评分 → 落盘 → 完成。
                        每情节自动衔接上一情节（前文锚点）。可随时停止。<br />
                        <b>下框每行一条 = 每情节的 l1（批量生产 l1）</b>；留空由系统按基本设定自动生成。生成后回创作 hub 逐情节精修。
                    </div>
                    <div className="field-row" style={{ flexWrap: 'wrap', gap: 10 }}>
                        <label className="field-label">目标总章数：
                            <input type="number" min={1} max={500} value={batchTarget} onChange={e => setBatchTarget(e.target.value)}
                                className="field-input" style={{ width: 90 }} />
                        </label>
                        <label className="field-label">每情节章数：
                            <input type="number" min={1} max={20} value={batchPerArc} onChange={e => setBatchPerArc(e.target.value)}
                                className="field-input" style={{ width: 80 }} />
                        </label>
                        <button style={btnStyle(!!batchTaskId, !batchTaskId)} disabled={!!batchTaskId || !bookRoot} onClick={startBatch}>
                            {batchTaskId ? '生成中…' : '▶ 开始批量生成'}
                        </button>
                        {batchTaskId && <button style={btnStyle(batchTaskId, true)} onClick={stopBatch}>⏹ 停止</button>}
                    </div>
                    {batchBrief !== null && (
                        <div style={{ marginTop: 10 }}>
                            <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 4 }}>逐情节一句话剧情（每行一条 = 每情节的 l1，批量生产 l1；留空自动生成）：</div>
                            <textarea value={batchBrief} onChange={e => setBatchBrief(e.target.value)}
                                placeholder={'沈云初入青云宗，在入门大比中崭露头角\n宗门遭袭，沈云用九转玄功力挽狂澜'}
                                style={{ width: '100%', minHeight: 90, boxSizing: 'border-box', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.7, padding: 8, border: '1px solid var(--line-soft)', borderRadius: 6 }} />
                        </div>
                    )}
                </div>
                {batchTaskId && (
                    <div className="section-block" style={{ marginTop: 12, borderLeft: '3px solid var(--dai)' }}>
                        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>
                            批量生成进度：{prog.done || 0} / {prog.target || '?'} 章
                            {st === 'running' ? ' ' : st === 'done' ? ' 完成' : st === 'failed' ? ' 失败' : ''}
                        </div>
                        {st === 'failed' && batchStatus?.error && (
                            <div style={{ fontSize: 12, color: 'var(--cinnabar-d)', marginBottom: 6 }}>⚠ {batchStatus.error}</div>
                        )}
                        <div style={{ fontSize: 12.5, color: 'var(--ink-sub)', lineHeight: 1.8 }}>
                            当前：{prog.arc_name || '—'} ｜ 步骤：{prog.step || '—'} ｜ 最新章节：第{(prog.last_chapter || 0) || '—'}章
                        </div>
                        {(prog.messages || []).slice(-6).map((m, i) => (
                            <div key={i} style={{ fontSize: 12, color: 'var(--ink-mute)', fontFamily: 'monospace', lineHeight: 1.6 }}>{m}</div>
                        ))}
                    </div>
                )}
            </div>
        )
    }

    // ── 素材 · 片段扩写（锚定扩写，只扩写【】内内容；并入「全书概览」子页）────
    const renderFragment = () => (
        <>
            <div style={cardStyle}>
                <div style={titleStyle}>片段锚定扩写 <span style={{ fontSize: 12, color: 'var(--ink-sub)', fontWeight: 400 }}>只扩写【】内的内容，锚点（对白/叙述）逐字保留 · LLM 先理解【】再扩写</span></div>
                <textarea value={fragText} onChange={e => { setFragText(e.target.value); fragReset() }}
                    placeholder={'粘贴半成品片段：对白 + 叙述 + 【要扩写的地方】\n\n例如：\n“老子是开成二年的状元你是什么？”\n陆知砚站在【描写一个恢宏大气的建筑前】\n……'}
                    style={{ width: '100%', minHeight: 220, boxSizing: 'border-box', fontFamily: 'inherit', fontSize: 14, lineHeight: 1.8, padding: 10, border: '1px solid var(--line-soft)', borderRadius: 6 }} />
                <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <input value={fragTitle} onChange={e => setFragTitle(e.target.value)} placeholder="章节标题（可选）"
                        style={{ padding: '6px 10px', border: '1px solid var(--line-soft)', borderRadius: 6, fontSize: 13, width: 180 }} />
                    <button style={btnStyle(fragBusy, false)} disabled={fragBusy} onClick={doFragParse}>① 解析【】</button>
                    <button style={btnStyle(fragBusy, false)} disabled={fragBusy} onClick={doFragUnderstand}>② 理解标记</button>
                    <button style={btnStyle(fragBusy, true)} disabled={fragBusy} onClick={doFragExpand}>{fragBusy ? '扩写中…' : '③ 扩写'}</button>
                    <button style={btnStyle(fragBusy, false)} disabled={fragBusy || !fragResult} onClick={doFragFinalize}>落盘当前书</button>
                    {fragBusy && <span style={{ fontSize: 12, color: 'var(--ink-sub)' }}>（理解+扩写走 LLM，每个【】都要读一遍，可能较慢）</span>}
                </div>
            </div>

            {fragParse?.ok && !fragResult && (
                <div style={cardStyle}>
                    <div style={titleStyle}>① 解析结果：{fragParse.n_slots} 个【】待扩写 <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--ink-sub)' }}>（prose=描写/动作/收束 · inline=对白内补全）</span></div>
                    {(fragParse.slots || []).map(s => (
                        <div key={s.id} style={{ display: 'flex', gap: 8, marginBottom: 4, fontSize: 13 }}>
                            <code style={{ color: 'var(--dai)', minWidth: 60 }}>{s.id}</code>
                            <span style={{ minWidth: 50 }}>{s.kind === 'inline' ? '对白内补全' : '描写/收束'}</span>
                            <span style={{ color: 'var(--ink-sub)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.line}</span>
                        </div>
                    ))}
                </div>
            )}

            {fragDirectives && (
                <div style={cardStyle}>
                    <div style={titleStyle}>② 系统对每个【】的理解（LLM 先理解再扩写）</div>
                    {fragDirectives.map(d => (
                        <div key={d.id} style={{ border: '1px solid var(--line)', borderRadius: 6, padding: 8, marginBottom: 6, fontSize: 13 }}>
                            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 2 }}>
                                <code style={{ color: 'var(--dai)' }}>{d.id}</code>
                                <span style={{ background: d.needs_review ? 'var(--amber-wash)' : 'var(--green-wash)', color: d.needs_review ? 'var(--amber)' : 'var(--green)', padding: '1px 6px', borderRadius: 8, fontSize: 11 }}>
                                    {d.needs_review ? '⚠ 待确认' : '安全'}
                                </span>
                                <span style={{ color: 'var(--ink-sub)' }}>{d.instruction ? `【${d.instruction}】` : '（空白待补）'}</span>
                            </div>
                            <div style={{ color: 'var(--ink-sub)' }}>要写：{d.intent || '—'}</div>
                            {!!d.requirements?.length && <div style={{ color: 'var(--ink-sub)' }}>必须包含：{d.requirements.join('；')}</div>}
                            {!!d.constraints?.length && <div style={{ color: 'var(--cinnabar-d)' }}>禁止：{d.constraints.join('；')}</div>}
                        </div>
                    ))}
                </div>
            )}

            {fragResult && (
                <div style={cardStyle}>
                    <div style={titleStyle}>③ 扩写结果 <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--ink-sub)' }}>蓝色=系统扩写；黑色=你的锚点（逐字保留）</span></div>
                    {fragRender()}
                    <div style={{ marginTop: 8, fontSize: 12, color: 'var(--ink-sub)', background: 'var(--bg-card-2)', border: '1px solid var(--line)', borderRadius: 6, padding: 8 }}>
                        <b>校验：</b>
                        锚点保真 {fragResult.verify?.anchor_fidelity?.pass ? '逐字保留' : '有改动'}（{fragResult.verify?.anchor_fidelity?.checked} 处）
                        ｜ 覆盖 {fragResult.verify?.coverage?.filled}/{fragResult.verify?.coverage?.total}
                        {fragResult.verify?.coverage?.missing?.length ? ` ｜ 未填：${fragResult.verify.coverage.missing.join(',')}` : ''}
                        {fragResult.verify?.style?.overdetail?.verdict && ` ｜ 过场/细节：${fragResult.verify.style.overdetail.verdict}(${fragResult.verify.style.overdetail.overdetail_score})`}
                        {fragResult.verify?.style?.open?.verdict && ` ｜ 段首：${fragResult.verify.style.open.verdict}(${fragResult.verify.style.open.open_score})`}
                    </div>
                </div>
            )}
        </>
    )

    // ── 写作 hub（大纲与章纲 / 章节生成 两个子页合并一个界面）────────
    // ── 写作 hub（五级阶梯纵向 + 右操作台）────
    const renderWriteHub = () => {
        const CN_NUMS = ['壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖', '拾']
        const ls = ladderState(activeArc)
        const l3data = arcState?.levels?.l3?.data || {}
        const l3chapters = Array.isArray(l3data.chapters) ? l3data.chapters : []
        const activeChIdx = arcState?.active_chapter || 0
        const activeCh = l3chapters[activeChIdx] || null
        const l4scenes = arcState?.levels?.l4?.scenes || []
        const l5val = l5Draft !== null ? l5Draft : l5Text
        const hasL5 = l5Text.trim() !== ''

        // 本章评分
        const chScore = lastScore || (activeArc?.chapters?.[activeChIdx]
            ? {
                intent_score: activeArc.chapters[activeChIdx].intent_score,
                quality_score: activeArc.chapters[activeChIdx].quality_score,
                overall: activeArc.chapters[activeChIdx].overall,
                polluted: activeArc.chapters[activeChIdx].polluted,
            }
            : null)

        const arcChapters = activeArc?.chapters || []
        const allChapters = arcList.flatMap(a => (a.chapters || []).length)

        return (
            <>
                {/* 书头 */}
                <div className="wb-bookhead">
                    <div className="wb-bhtitle">
                        <span className="vol" style={{ color: 'var(--cinnabar)' }}>卷二</span>
                        <span>{activeArc?.name || '未选情节'}</span>
                        <span className="sub">
                            · {activeArc ? arcRange(activeArc) : '先选情节'}
                            {activeCh && ` · 当前第${arcChapterNum(activeArc, activeChIdx)}章「${stripCh(activeCh.title) || ''}」`}
                        </span>
                    </div>
                    <div className="wb-bhmeta">
                        <span>阶梯 <b>l{ls.filter(s => s !== 'no').length || 1}</b></span><span className="sep">|</span>
                        <span>参与 <b>{(activeArc?.selected?.characters?.length || 0) + (activeArc?.selected?.items?.length || 0) + (activeArc?.selected?.settings?.length || 0)}</b></span><span className="sep">|</span>
                        {chScore ? (
                            <span>综合 <b>{typeof chScore.overall === 'number' ? chScore.overall.toFixed(3) : chScore.overall || '—'}</b></span>
                        ) : (
                            <span>本章 <b style={{ color: 'var(--amber)' }}>进行中</b></span>
                        )}
                    </div>
                </div>

                {/* 双栏书页 */}
                <div className="wb-page">
                    {/* 左栏：标签页切换 */}
                    <div className="wb-col wb-col-left" style={{ display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
                        {/* 标签页栏 */}
                        <div style={{ display: 'flex', gap: 0, borderBottom: '2px solid var(--line)', flexShrink: 0, background: 'var(--paper-raised)' }}>
                            {[
                                { key: 'ladder', label: '阶梯' },
                                { key: 'fragments', label: '素材', count: fragments.filter(f => !activeArcId || f.arc_id === activeArcId).length },
                                { key: 'pending', label: '待审核', count: pendingCards.length },
                                { key: 'notes', label: '备注', count: notes.filter(n => n.scope === 'global' || n.scope === `arc:${activeArcId}`).length },
                            ].map(t => (
                                <div key={t.key}
                                    onClick={() => { setLeftTab(t.key); if (t.key === 'pending') loadPendingCards() }}
                                    style={{
                                        padding: '8px 14px', fontSize: 13, cursor: 'pointer', fontWeight: leftTab === t.key ? 700 : 500,
                                        color: leftTab === t.key ? 'var(--dai)' : 'var(--ink)',
                                        borderBottom: leftTab === t.key ? '3px solid var(--dai)' : '3px solid transparent',
                                        background: leftTab === t.key ? 'var(--paper)' : 'transparent',
                                        display: 'flex', alignItems: 'center', gap: 6,
                                        transition: 'all 0.15s',
                                    }}>
                                    {t.label}
                                    {t.count > 0 && (
                                        <span style={{
                                            fontSize: 10, fontWeight: 600, borderRadius: 10, padding: '1px 6px',
                                            background: t.key === 'pending' ? 'var(--cinnabar)' : 'var(--dai)',
                                            color: '#fff', minWidth: 18, textAlign: 'center',
                                        }}>{t.count}</span>
                                    )}
                                </div>
                            ))}
                        </div>

                        {/* 阶梯标签页（原有内容） */}
                        {leftTab === 'ladder' && (<>
                        <div className="wb-rubric">五级阶梯 · 自顶向下</div>
                        <div className="wb-ladderstack">

                            {/* l1 */}
                            <div className="wb-lvblock">
                                <div className="wb-lvlabel">l1<span className="lv-sub">一句话极简</span></div>
                                <div
                                    className={`wb-lvcontent wb-editable ${editStatus.l1_text === 'editing' ? 'wb-editing' : ''}`}
                                    contentEditable
                                    suppressContentEditableWarning
                                    onFocus={(e) => {
                                        startEdit('l1_text')
                                        // 选中全部文字
                                        const range = document.createRange()
                                        range.selectNodeContents(e.currentTarget)
                                        const sel = window.getSelection()
                                        sel.removeAllRanges()
                                        sel.addRange(range)
                                    }}
                                    onBlur={(e) => {
                                        const text = e.currentTarget.innerText.trim()
                                        if (text && text !== (activeArc?.l1 || arcState?.levels?.l1?.text || '')) {
                                            handleL1Edit(text)
                                        } else {
                                            finishEdit('l1_text')
                                        }
                                    }}
                                    onKeyDown={(e) => {
                                        if (e.key === 'Enter' && !e.shiftKey) {
                                            e.preventDefault()
                                            e.currentTarget.blur()
                                        }
                                    }}
                                    dangerouslySetInnerHTML={{ __html: (activeArc?.l1 || arcState?.levels?.l1?.text || '（未生成）').replace(/\n/g, '<br>') }}
                                />
                                {editStatus.l1_text === 'saving' && <span className="wb-edit-indicator saving">保存中…</span>}
                                {editStatus.l1_text === 'saved' && <span className="wb-edit-indicator saved">✓ 已保存</span>}
                                {editStatus.l1_text === 'error' && <span className="wb-edit-indicator error">✗ 保存失败</span>}
                                {arcState?.levels?.l1?.confirmed && !editStatus.l1_text && <span className="wb-lvhint">✓ 已确认</span>}
                            </div>

                            {/* l2 */}
                            <div className="wb-lvblock">
                                <div className="wb-lvlabel">l2<span className="lv-sub">情节概要</span></div>
                                <div
                                    className={`wb-lvcontent wb-editable wb-multi ${editStatus.l2_text === 'editing' ? 'wb-editing' : ''}`}
                                    contentEditable
                                    suppressContentEditableWarning
                                    onFocus={() => startEdit('l2_text')}
                                    onBlur={(e) => {
                                        const text = e.currentTarget.innerText.trim()
                                        if (text && text !== (activeArc?.l2 || arcState?.levels?.l2?.text || '')) {
                                            handleL2Edit(text)
                                        } else {
                                            finishEdit('l2_text')
                                        }
                                    }}
                                    onKeyDown={(e) => {
                                        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                                            e.preventDefault()
                                            e.currentTarget.blur()
                                        }
                                    }}
                                    dangerouslySetInnerHTML={{ __html: (activeArc?.l2 || arcState?.levels?.l2?.text || '（未生成）').replace(/\n/g, '<br>') }}
                                />
                                {editStatus.l2_text === 'saving' && <span className="wb-edit-indicator saving">保存中…</span>}
                                {editStatus.l2_text === 'saved' && <span className="wb-edit-indicator saved">✓ 已保存</span>}
                                {editStatus.l2_text === 'error' && <span className="wb-edit-indicator error">✗ 保存失败</span>}
                                {arcState?.levels?.l2?.confirmed && !editStatus.l2_text && <span className="wb-lvhint">✓ 已确认</span>}
                            </div>

                            {/* l3 章纲 */}
                            <div className="wb-lvblock">
                                <div className="wb-lvlabel">l3<span className="lv-sub">章核心</span></div>
                                <div className="wb-lvcontent">
                                    {l3chapters.length > 0 ? (
                                        <div className="wb-l3list">
                                            {l3chapters.map((ch, i) => (
                                                <div key={i} className={`wb-l3row ${i === activeChIdx ? 'active' : ''}`}
                                                    onClick={() => handleSetActiveChapter(i)}>
                                                    <span className="ch-n">{arcChapterNum(activeArc, i)}</span>
                                                    <span className="ch-t">{stripCh(ch.title) || arcChapterLabel(activeArc, i)}</span>
                                                    <span className="ch-c">{ch.core || ch.beats?.[0] || ''}</span>
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <span style={{ color: 'var(--ink-mute)' }}>（未生成）</span>
                                    )}
                                </div>
                            </div>

                            {/* l4 场景分解 */}
                            <div className="wb-lvblock">
                                <div className="wb-lvlabel">l4<span className="lv-sub">场景分解</span></div>
                                <div className="wb-lvcontent">
                                    {l4scenes.length > 0 ? (
                                        <div className="wb-l4list">
                                            {l4scenes.map((sc, i) => {
                                                const n = k => (Array.isArray(sc[k]) ? sc[k].filter(x => String(x).trim()).length : 0)
                                                const extras = []
                                                if (sc.elements?.length) extras.push(`${sc.elements.length}元素`)
                                                if (sc.scene_note) extras.push('')
                                                return (
                                                    <div key={i} className="wb-scenechip">
                                                        <span className="sn">{sc.name || `场景${i + 1}`}</span>
                                                        <span className="st">{n('actions') + n('dialogues') + n('conflicts') + n('details')}叶{extras.length ? `｜${extras.join('/')}` : ''}</span>
                                                    </div>
                                                )
                                            })}
                                        </div>
                                    ) : (
                                        <span style={{ color: 'var(--ink-mute)' }}>（未生成）</span>
                                    )}
                                    {l4scenes.length > 0 && (
                                        <div style={{ marginTop: 4, fontSize: 10, color: 'var(--ink-mute)' }}>
                                            {l4scenes.length} 个场景
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* l5 正文（占剩余空间，可滚动） */}
                            <div className="wb-lvblock expand">
                                <div className="wb-lvlabel" style={{ paddingTop: 4 }}>l5<span className="lv-sub">正文</span></div>
                                <div className="wb-lvcontent prose">
                                    {l5val ? (
                                        l5val.split(/\n{2,}/).map((para, i) => (
                                            <p key={i} style={{ textIndent: '2em', marginBottom: 6 }}>{para}</p>
                                        ))
                                    ) : (
                                        <span style={{ color: 'var(--ink-sub)', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
                                            （正文未生成 — 在右侧点「生成下一级」）
                                        </span>
                                    )}
                                </div>
                            </div>

                        </div>

                        {/* 章导航条（底部） */}
                        {l3chapters.length > 0 && (
                            <div className="wb-chnav">
                                {l3chapters.map((ch, i) => {
                                    const isFin = activeArc?.chapters?.[i]
                                    const isDoing = i === activeChIdx && !isFin
                                    return (
                                        <div key={i}
                                            className={`wb-chdot ${isFin ? 'done' : isDoing ? 'doing' : ''} ${i === activeChIdx ? 'active' : ''}`}
                                            onClick={() => handleSetActiveChapter(i)}>
                                            <span className="num">{i + 1}</span>
                                            <span className="mk"></span>
                                        </div>
                                    )
                                })}
                            </div>
                        )}
                        </>)}

                        {/* 素材标签页 */}
                        {leftTab === 'fragments' && (
                        <div className="wb-scroll" style={{ flex: 1, padding: 8 }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                                <div style={{ fontSize: 13, fontWeight: 600 }}>素材列表</div>
                                <button style={{ fontSize: 11, padding: '3px 8px', border: '1px solid var(--dai)', borderRadius: 4, background: 'var(--dai)', color: '#fff', cursor: 'pointer' }}
                                    onClick={async () => {
                                        const content = prompt('输入素材内容（对白/细节/动作）：')
                                        if (!content?.trim()) return
                                        const r = await phFragmentsPut({
                                            book_root: bookRoot, arc_id: activeArcId,
                                            ftype: 'detail', content: content.trim(),
                                            scene_idx: 0,
                                        })
                                        if (r?.ok) { setNotice('素材已添加'); await refreshWorkbench(bookRoot) }
                                    }}>+ 添加素材</button>
                            </div>
                            {fragments.filter(f => !activeArcId || f.arc_id === activeArcId).length === 0 ? (
                                <div style={{ fontSize: 12, color: 'var(--ink-mute)', padding: 20, textAlign: 'center', border: '1px dashed var(--line)', borderRadius: 6 }}>
                                    暂无素材<br/>
                                    <span style={{ fontSize: 11 }}>点击上方「+ 添加素材」开始</span>
                                </div>
                            ) : (
                                fragments.filter(f => !activeArcId || f.arc_id === activeArcId).map(f => (
                                    <div key={f.id} style={{ border: '1px solid var(--line)', borderRadius: 6, padding: 8, marginBottom: 6, background: 'var(--paper)' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                                            <span style={{ fontSize: 11, color: 'var(--dai)', fontWeight: 600 }}>[{f.type || '素材'}]</span>
                                            <span style={{ fontSize: 10, color: 'var(--ink-mute)' }}>场景{(f.scene_idx || 0) + 1}</span>
                                        </div>
                                        <div style={{ fontSize: 12.5, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{f.content}</div>
                                    </div>
                                ))
                            )}
                        </div>
                        )}

                        {/* 待审核标签页 */}
                        {leftTab === 'pending' && (
                        <div className="wb-scroll" style={{ flex: 1, padding: 8 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>待审核卡片</div>
                            {pendingCards.length === 0 ? (
                                <div style={{ fontSize: 12, color: 'var(--ink-mute)', padding: 12, textAlign: 'center' }}>
                                    暂无待审核卡片
                                </div>
                            ) : (
                                pendingCards.map(p => (
                                    <div key={p.id} style={{ border: '1px solid var(--line)', borderRadius: 6, padding: 10, marginBottom: 8, background: 'var(--paper)' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                                            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--dai)' }}>
                                                {p.type === 'character' ? '角色' : p.type === 'item' ? '物品' : p.type === 'setting' ? '设定' : p.type || '卡片'}
                                            </span>
                                            <span style={{ fontSize: 10, color: 'var(--ink-mute)' }}>{p.scope || 'global'}</span>
                                        </div>
                                        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>{p.name || '（无名称）'}</div>
                                        <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 6, whiteSpace: 'pre-wrap' }}>{p.desc || ''}</div>
                                        <div style={{ display: 'flex', gap: 6 }}>
                                            <button style={btnStyle(false, true)} onClick={() => approveCard(p.id)}>✓ 通过</button>
                                            <button style={btnStyle(false, false)} onClick={() => rejectCard(p.id)}>✕ 驳回</button>
                                        </div>
                                    </div>
                                ))
                            )}
                        </div>
                        )}

                        {/* 备注标签页 */}
                        {leftTab === 'notes' && (
                        <div className="wb-scroll" style={{ flex: 1, padding: 8 }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                                <div style={{ fontSize: 13, fontWeight: 600 }}>备注列表</div>
                                <button style={{ fontSize: 11, padding: '3px 8px', border: '1px solid var(--green)', borderRadius: 4, background: 'var(--green)', color: '#fff', cursor: 'pointer' }}
                                    onClick={async () => {
                                        const scope = activeArcId ? `arc:${activeArcId}` : 'global'
                                        const content = prompt(`输入备注内容（${activeArcId ? '情节' : '全局'}备注）：`)
                                        if (!content?.trim()) return
                                        const r = await phNotesPut({
                                            book_root: bookRoot, scope, content: content.trim(),
                                        })
                                        if (r?.ok) { setNotice('备注已添加'); await refreshWorkbench(bookRoot) }
                                    }}>+ 添加备注</button>
                            </div>

                            {/* 全局备注区域 - 始终显示 */}
                            <div style={{ marginBottom: 12 }}>
                                <div style={{ fontSize: 11, color: 'var(--dai)', fontWeight: 600, marginBottom: 6, display: 'flex', alignItems: 'center', gap: 4 }}>
                                    全局备注
                                    <span style={{ fontSize: 10, color: 'var(--ink-mute)', fontWeight: 400 }}>({notes.filter(n => n.scope === 'global').length})</span>
                                </div>
                                {notes.filter(n => n.scope === 'global').length === 0 ? (
                                    <div style={{ fontSize: 11, color: 'var(--ink-mute)', padding: 12, textAlign: 'center', border: '1px dashed var(--line)', borderRadius: 6 }}>
                                        暂无全局备注
                                    </div>
                                ) : (
                                    notes.filter(n => n.scope === 'global').map(n => (
                                        <div key={n.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', border: '1px solid var(--line)', borderRadius: 6, padding: 8, marginBottom: 4, background: 'var(--paper)' }}>
                                            <div style={{ fontSize: 12.5, flex: 1, whiteSpace: 'pre-wrap' }}>{n.content}</div>
                                            <button style={{ ...btnStyle(false, false), padding: '2px 6px', fontSize: 10, marginLeft: 6, flexShrink: 0 }}
                                                onClick={() => deleteNote(n.id)}>✕</button>
                                        </div>
                                    ))
                                )}
                            </div>

                            {/* 情节备注区域 - 始终显示 */}
                            <div style={{ marginBottom: 12 }}>
                                <div style={{ fontSize: 11, color: 'var(--green)', fontWeight: 600, marginBottom: 6, display: 'flex', alignItems: 'center', gap: 4 }}>
                                    情节备注
                                    {activeArcId && <span style={{ fontSize: 10, color: 'var(--ink-mute)', fontWeight: 400 }}>({notes.filter(n => n.scope === `arc:${activeArcId}`).length})</span>}
                                </div>
                                {!activeArcId ? (
                                    <div style={{ fontSize: 11, color: 'var(--ink-mute)', padding: 12, textAlign: 'center', border: '1px dashed var(--line)', borderRadius: 6 }}>
                                        先选择一个情节
                                    </div>
                                ) : notes.filter(n => n.scope === `arc:${activeArcId}`).length === 0 ? (
                                    <div style={{ fontSize: 11, color: 'var(--ink-mute)', padding: 12, textAlign: 'center', border: '1px dashed var(--line)', borderRadius: 6 }}>
                                        暂无情节备注
                                    </div>
                                ) : (
                                    notes.filter(n => n.scope === `arc:${activeArcId}`).map(n => (
                                        <div key={n.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', border: '1px solid var(--line)', borderRadius: 6, padding: 8, marginBottom: 4, background: 'var(--paper)' }}>
                                            <div style={{ fontSize: 12.5, flex: 1, whiteSpace: 'pre-wrap' }}>{n.content}</div>
                                            <button style={{ ...btnStyle(false, false), padding: '2px 6px', fontSize: 10, marginLeft: 6, flexShrink: 0 }}
                                                onClick={() => deleteNote(n.id)}>✕</button>
                                        </div>
                                    ))
                                )}
                            </div>

                            {/* 段级备注区域 - 始终显示 */}
                            <div>
                                <div style={{ fontSize: 11, color: 'var(--amber)', fontWeight: 600, marginBottom: 6, display: 'flex', alignItems: 'center', gap: 4 }}>
                                    段级备注
                                    <span style={{ fontSize: 10, color: 'var(--ink-mute)', fontWeight: 400 }}>({notes.filter(n => n.scope.includes('beats')).length})</span>
                                </div>
                                {notes.filter(n => n.scope.includes('beats')).length === 0 ? (
                                    <div style={{ fontSize: 11, color: 'var(--ink-mute)', padding: 12, textAlign: 'center', border: '1px dashed var(--line)', borderRadius: 6 }}>
                                        暂无段级备注<br/>
                                        <span style={{ fontSize: 10 }}>在l4场景中勾选节拍后添加</span>
                                    </div>
                                ) : (
                                    notes.filter(n => n.scope.includes('beats')).map(n => (
                                        <div key={n.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', border: '1px solid var(--line)', borderRadius: 6, padding: 8, marginBottom: 4, background: 'var(--paper)' }}>
                                            <div style={{ flex: 1 }}>
                                                <div style={{ fontSize: 10, color: 'var(--amber)', marginBottom: 2 }}>{n.scope}</div>
                                                <div style={{ fontSize: 12.5, whiteSpace: 'pre-wrap' }}>{n.content}</div>
                                            </div>
                                            <button style={{ ...btnStyle(false, false), padding: '2px 6px', fontSize: 10, marginLeft: 6, flexShrink: 0 }}
                                                onClick={() => deleteNote(n.id)}>✕</button>
                                        </div>
                                    ))
                                )}
                            </div>
                        </div>
                        )}
                    </div>

                    {/* 右栏：操作台 */}
                    <div className="wb-col wb-col-right" style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                        <div className="wb-rubric">操作台</div>
                        <div className="wb-opspanel">

                            {/* 参与元素 */}
                            {activeArc && (
                                <div className="wb-opsec">
                                    <div className="wb-optitle">本情节参与元素</div>
                                    <div className="wb-eg">
                                        <div className="wb-eglabel">角色</div>
                                        <div className="wb-echips">
                                            {(activeArc.selected?.characters || []).map(id => {
                                                const e = (elements.characters || []).find(x => x.id === id)
                                                return e ? <span key={id} className="wb-echip">{e.name}</span> : null
                                            })}
                                            <span className="wb-echip outline" onClick={() => { /* 跳去选择元素 */ }}>+ 选</span>
                                        </div>
                                    </div>
                                    <div className="wb-eg">
                                        <div className="wb-eglabel">物品</div>
                                        <div className="wb-echips">
                                            {(activeArc.selected?.items || []).map(id => {
                                                const e = (elements.items || []).find(x => x.id === id)
                                                return e ? <span key={id} className="wb-echip">{e.name}</span> : null
                                            })}
                                            <span className="wb-echip outline">+ 选</span>
                                        </div>
                                    </div>
                                    <div className="wb-eg">
                                        <div className="wb-eglabel">设定</div>
                                        <div className="wb-echips">
                                            {(activeArc.selected?.settings || []).map(id => {
                                                const e = (elements.settings || []).find(x => x.id === id)
                                                return e ? <span key={id} className="wb-echip">{e.name}</span> : null
                                            })}
                                            <span className="wb-echip outline">+ 选</span>
                                        </div>
                                    </div>
                                </div>
                            )}

                            {/* 阶梯状态 */}
                            <div className="wb-opsec">
                                <div className="wb-optitle">阶梯进度</div>
                                <div className="wb-lstate">
                                    {ls.map((s, i) => (
                                        <div key={i} className={`lb ${s}`}>l{i + 1}</div>
                                    ))}
                                </div>
                                <div style={{ fontSize: 9.5, color: 'var(--ink-mute)' }}>
                                    当前：l{ls.filter(s => s !== 'no').length + 1 || 1} · 下一级
                                </div>
                            </div>

                            {/* 双评分 */}
                            {chScore && (
                                <div className="wb-opsec">
                                    <div className="wb-optitle">双评分 · 本章</div>
                                    <div className="wb-scorebig">
                                        <div className="sb"><div className="n">{typeof chScore.intent_score === 'number' ? chScore.intent_score.toFixed(3) : chScore.intent_score || '—'}</div><div className="l">意图兑现</div></div>
                                        <div className="sb"><div className="n">{typeof chScore.quality_score === 'number' ? chScore.quality_score.toFixed(3) : chScore.quality_score || '—'}</div><div className="l">纯质量</div></div>
                                        <div className="sb"><div className="n">{typeof chScore.overall === 'number' ? chScore.overall.toFixed(3) : chScore.overall || '—'}</div><div className="l">综合</div></div>
                                    </div>
                                    {chScore.polluted && (
                                        <div style={{ fontSize: 10, color: 'var(--cinnabar-d)', marginTop: 3 }}>
                                            ⚠ 检出未参与元素污染
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* 操作按钮 */}
                            <div className="wb-opsec">
                                <div className="wb-optitle">操作</div>
                                <div className="wb-btnrow">
                                    <button className="wb-btn primary" disabled={busy || !activeArcId} onClick={handleStep}>生成下一级</button>
                                    <button className="wb-btn" disabled={!hasL5} onClick={() => setL5Draft(l5Text)}>编辑正文</button>
                                </div>
                                <div className="wb-btnrow" style={{ marginTop: 4 }}>
                                    <button className="wb-btn warm" disabled={scoreBusy || !hasL5} onClick={handleScore}>评分</button>
                                    <button className="wb-btn" disabled={!activeArcId} onClick={() => { /* 打开对话 */ }}>讨论</button>
                                </div>
                                <div className="wb-btnrow" style={{ marginTop: 4 }}>
                                    <button className="wb-btn" disabled={busy || !lastScore || lastScore.polluted} onClick={handleFinalize}>✓ 落盘本章</button>
                                    <button className="wb-btn danger" disabled={scoreBusy || !hasL5} onClick={handlePollution}>⚠ 污染检查</button>
                                </div>
                            </div>

                            {/* 元素隔离状态 */}
                            {activeArc && (
                                <div className="wb-opsec">
                                    <div className="wb-optitle">元素隔离</div>
                                    <div style={{ fontSize: 10.5, color: chScore?.polluted ? 'var(--cinnabar-d)' : 'var(--green)' }}>
                                        {chScore?.polluted ? '⚠ 检出污染' : '✓ 白名单生效中'}
                                    </div>
                                    <div style={{ fontSize: 10, color: 'var(--ink-mute)', marginTop: 2 }}>
                                        l3 起注入白名单 · 双检（确定性+LLM）
                                    </div>
                                </div>
                            )}

                            {/* 情节备注 */}
                            <div className="wb-opsec">
                                <div className="wb-optitle">情节备注</div>
                                {/* 全局备注 */}
                                {notes.filter(n => n.scope === 'global').length > 0 && (
                                    <div style={{ marginBottom: 6 }}>
                                        <div style={{ fontSize: 10, color: 'var(--dai)', fontWeight: 600, marginBottom: 3 }}>全局</div>
                                        {notes.filter(n => n.scope === 'global').slice(0, 3).map(n => (
                                            <div key={n.id} style={{ fontSize: 11, color: 'var(--ink-sub)', padding: '2px 0', borderBottom: '1px solid var(--line-soft)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.content}</span>
                                                <span style={{ cursor: 'pointer', marginLeft: 4, fontSize: 10, color: 'var(--ink-mute)' }} onClick={() => deleteNote(n.id)}>✕</span>
                                            </div>
                                        ))}
                                        {notes.filter(n => n.scope === 'global').length > 3 && (
                                            <div style={{ fontSize: 10, color: 'var(--ink-mute)', cursor: 'pointer', marginTop: 2 }}
                                                onClick={() => setLeftTab('notes')}>
                                                还有 {notes.filter(n => n.scope === 'global').length - 3} 条…
                                            </div>
                                        )}
                                    </div>
                                )}
                                {/* 情节级备注 */}
                                {activeArcId && notes.filter(n => n.scope === `arc:${activeArcId}`).length > 0 && (
                                    <div style={{ marginBottom: 6 }}>
                                        <div style={{ fontSize: 10, color: 'var(--green)', fontWeight: 600, marginBottom: 3 }}>本情节</div>
                                        {notes.filter(n => n.scope === `arc:${activeArcId}`).slice(0, 3).map(n => (
                                            <div key={n.id} style={{ fontSize: 11, color: 'var(--ink-sub)', padding: '2px 0', borderBottom: '1px solid var(--line-soft)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.content}</span>
                                                <span style={{ cursor: 'pointer', marginLeft: 4, fontSize: 10, color: 'var(--ink-mute)' }} onClick={() => deleteNote(n.id)}>✕</span>
                                            </div>
                                        ))}
                                        {notes.filter(n => n.scope === `arc:${activeArcId}`).length > 3 && (
                                            <div style={{ fontSize: 10, color: 'var(--ink-mute)', cursor: 'pointer', marginTop: 2 }}
                                                onClick={() => setLeftTab('notes')}>
                                                还有 {notes.filter(n => n.scope === `arc:${activeArcId}`).length - 3} 条…
                                            </div>
                                        )}
                                    </div>
                                )}
                                {notes.filter(n => n.scope === 'global' || (activeArcId && n.scope === `arc:${activeArcId}`)).length === 0 && (
                                    <div style={{ fontSize: 11, color: 'var(--ink-mute)', marginBottom: 6 }}>暂无备注</div>
                                )}
                                <div style={{ display: 'flex', gap: 4 }}>
                                    <button style={{ ...btnStyle(false, false), padding: '3px 8px', fontSize: 11 }}
                                        onClick={() => setLeftTab('notes')}>+ 情节备注</button>
                                    <button style={{ ...btnStyle(false, false), padding: '3px 8px', fontSize: 11 }}
                                        onClick={() => setLeftTab('notes')}>从记忆库拉取</button>
                                </div>
                            </div>

                            {/* 无情节时占位 */}
                            {!activeArc && (
                                <div style={{ fontSize: 11, color: 'var(--ink-mute)', textAlign: 'center', padding: 20 }}>
                                    去「全书概览」选一个情节，或新建一个
                                </div>
                            )}

                        </div>
                    </div>
                </div>

                {/* 原来的写作页完整功能（大纲/章节列表）保留为隐藏备用，需要时展开 */}
                <div style={{ display: 'none' }}>
                    {renderOverview()}
                    {renderArcs()}
                    {renderChapters()}
                    {renderProse()}
                </div>
            </>
        )
    }

    // ── 新建情节表单（弹层版 compact / 引导卡内嵌版）——单行建一情节，多行批量建情节 ──
    const renderNewArcForm = (compact) => (
        <div className="wb-newarc">
            <textarea value={newL1} onChange={e => setNewL1(e.target.value)} rows={compact ? 2 : 4}
                placeholder={'本情节一句话极简剧情（l1），如：\n妖潮漫上黑水渡，方嶂被迫拔刀\n每行一条可批量建多个情节'}
                style={{ width: '100%', boxSizing: 'border-box', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.7, padding: 8, border: '1px solid var(--line-soft)', borderRadius: 6, resize: 'vertical' }} />
            <div className="na-row">
                <label className="na-field" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12 }}>章数
                    <input type="number" min={1} max={20} value={newN}
                        onChange={e => setNewN(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
                        style={{ width: 58, padding: '4px 6px', border: '1px solid var(--line-soft)', borderRadius: 6, fontSize: 13 }} />
                </label>
                <label className="na-field" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12 }}>
                    <input type="checkbox" checked={newCarryPrev} onChange={e => setNewCarryPrev(e.target.checked)} /> 承接上一情节
                </label>
                <button style={btnStyle(busy || !newL1.trim(), true)} disabled={busy || !newL1.trim()} onClick={createArc}>
                    {busy ? '创建中…' : '新建情节'}
                </button>
            </div>
            <div className="na-hint">多行 = 批量建多情节；单情节流程：l1 → 生成 l2 概要 → 选参与元素 → l3 章纲 → l4 场景 → l5 正文 → 评分落盘</div>
        </div>
    )

    // 「＋ 新建情节」弹层（fixed 居中）。create-stage 有两个 return 分支：
    // 书级讨论模式（bookLevelMode 提前 return 书级总览）与情节模式——两个分支都必须挂载，
    // 否则书级模式下总览页的「＋ 新建情节」按钮切换了状态但弹层永远不渲染（点了没反应）。
    const renderNewArcPopup = () => !newArcOpen ? null : (
        <div className="wb-newarc-backdrop" onClick={() => setNewArcOpen(false)}>
            <div className="wb-newarc-pop" onClick={e => e.stopPropagation()}>
                <div className="nap-head">
                    新建情节
                    <span className="nap-close" onClick={() => setNewArcOpen(false)}>✕</span>
                </div>
                {renderNewArcForm(true)}
            </div>
        </div>
    )

    // 阶梯层 hover 操作：就地编辑 / 清空（l4 只清空；编辑走助手同意）
    const renderLevelOps = (lv, editDraft) => (
        <>
            <div className="lv-ops" onClick={e => e.stopPropagation()}>
                {editDraft !== null && (
                    <span className="lv-op" title="编辑本层" onClick={() => setLadderEdit({ lv, draft: editDraft })}>编辑</span>
                )}
                <span className="lv-op" title="清空本层（下游清空）" onClick={() => clearLevel(lv)}>清空</span>
            </div>
            {ladderEdit?.lv === lv && (
                <div className="lv-edit" onClick={e => e.stopPropagation()}>
                    <textarea value={ladderEdit.draft} onChange={e => setLadderEdit({ ...ladderEdit, draft: e.target.value })}
                        rows={lv === 'l5' ? 6 : 3}
                        placeholder={lv === 'l3' ? '每行：第X章 标题：章核心' : `编辑 ${lv} 内容…`}
                        style={{ width: '100%', boxSizing: 'border-box', fontFamily: 'inherit', fontSize: 12.5, lineHeight: 1.7, padding: 8, border: '1px solid var(--line-soft)', borderRadius: 6 }} />
                    <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
                        <button style={btnStyle(busy, true)} disabled={busy} onClick={saveLevelEdit}>保存</button>
                        <button style={btnStyle(false, false)} onClick={() => setLadderEdit(null)}>取消</button>
                    </div>
                </div>
            )}
        </>
    )

    // ── 书级讨论模式左栏（2026-09-07 方案二：单页滚动 + 锚点 + 搜索过滤，窄条撤销）────
    const [bookSearch, setBookSearch] = useState('')

    const renderBookLeft = () => {
        const BK_KINDS = [
            { key: 'characters', label: '角色' },
            { key: 'items', label: '物品' },
            { key: 'settings', label: '设定' },
            { key: 'locations', label: '地点' },
            { key: 'maps', label: '地图' },
        ]

        // 获取某情节的元素（用于卡片云标签）
        const getArcElems = (arc) => {
            const result = []
            const sel = arc.selected || {}
            for (const kind of ['characters', 'items', 'settings']) {
                const ids = sel[kind] || []
                const elems = (elements[kind] || []).filter(e => ids.includes(e.id))
                elems.forEach(e => result.push({ ...e, kind }))
            }
            return result
        }

        // 搜索过滤：全页统一（情节/元素/备注），空区整区隐藏
        const q = bookSearch.trim().toLowerCase()
        const match = (s) => !q || String(s || '').toLowerCase().includes(q)
        const visArcs = arcList.filter(a => {
            if (match(a.name) || match(a.l1)) return true
            return getArcElems(a).some(e => match(e.name))
        })
        const visKinds = BK_KINDS
            .map(km => ({ ...km, list: (elements[km.key] || []).filter(e => match(e.name) || match(e.desc)) }))
            .filter(km => km.list.length > 0)
        const visNotes = notes.filter(n => n.scope === 'global' && match(n.text))
        const jumpSec = (key) => document.getElementById('bk-sec-' + key)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
        const anchors = [
            ...(visArcs.length > 0 ? [{ key: 'arcs', label: '情节' }] : []),
            ...visKinds.map(km => ({ key: km.key, label: km.label })),
            { key: 'notes', label: '备注' },
        ]
        const bookEmpty = arcList.length === 0
            && Object.values(elements || {}).flat().length === 0
            && notes.filter(n => n.scope === 'global').length === 0

        return (
            <div className="wb-bookleft">
                {/* 头部：模式分段开关 + 搜索 + 新建情节 */}
                <div className="bk-head">
                    <div className="bk-seg">
                        <span className="on">书级讨论</span>
                        <span onClick={() => setBookLevelMode(false)}>情节讨论</span>
                    </div>
                    <div className="bk-search">
                        <input placeholder="搜索情节、元素、备注..." value={bookSearch}
                            onChange={e => setBookSearch(e.target.value)} />
                        {bookSearch && <span className="bk-search-x" onClick={() => setBookSearch('')}>✕</span>}
                    </div>
                    <button className="bo-new-arc" onClick={() => setNewArcOpen(true)}>＋ 新建情节</button>
                </div>

                {/* 锚点行（只列当前可见的区） */}
                <div className="bk-anchors">
                    {anchors.map(a => (
                        <span key={a.key} className="bk-ank" onClick={() => jumpSec(a.key)}>{a.label}</span>
                    ))}
                </div>

                {/* 单页主体：情节 → 全书元素 → 备注 */}
                <div className="bk-body">
                    {bookEmpty ? (
                        <div className="bk-empty">
                            <div style={{ fontSize: 40, marginBottom: 12 }}></div>
                            <div style={{ fontSize: 13, color: 'var(--ink-sub)', marginBottom: 8 }}>
                                还没有内容——和右侧助手聊设定、人物、世界观，或直接新建情节
                            </div>
                            <button className="bo-new-arc" onClick={() => setNewArcOpen(true)}>＋ 创建第一个情节</button>
                        </div>
                    ) : (q && visArcs.length === 0 && visKinds.length === 0 && visNotes.length === 0) ? (
                        <div className="bk-empty">
                            <div style={{ fontSize: 13, color: 'var(--ink-sub)' }}>没有找到匹配的内容</div>
                        </div>
                    ) : (<>
                        {/* 情节时间轴 */}
                        {visArcs.length > 0 && (
                            <section className="bk-sec" id="bk-sec-arcs">
                                <div className="bk-sec-hd">
                                    <span className="bk-sec-t">情节（{visArcs.length}）</span>
                                </div>
                                <div className="bo-timeline">
                                    {visArcs.map((arc, idx) => {
                                        const statusCls = arc.status === 'done' ? 'done' : arc.status === 'writing' ? 'doing' : 'todo'
                                        const statusText = arc.status === 'done' ? '已完成' : arc.status === 'writing' ? '进行中' : '待写'
                                        const arcElems = getArcElems(arc)
                                        const chCount = (arc.chapters || []).length
                                        return (
                                            <div key={arc.id} className={`bo-arc-node ${statusCls}`}>
                                                <div className="bo-arc-left">
                                                    <div className="bo-arc-idx">第 {idx + 1} 章</div>
                                                    <div className={`bo-arc-badge ${statusCls}`}>{statusText}</div>
                                                </div>
                                                <div className="bo-arc-marker"></div>
                                                <div className="bo-arc-right">
                                                    <div className="bo-arc-card"
                                                        onClick={() => { selectArc(arc.id); setBookLevelMode(false) }}>
                                                        <div className="bo-arc-title">{arc.name || `情节${idx + 1}`}</div>
                                                        {arc.l1 && <div className="bo-arc-summary">{arc.l1}</div>}
                                                        {chCount > 0 && (
                                                            <div className="bo-arc-meta">{chCount} 章 · 约{(arc.word_count || 0).toLocaleString()}字</div>
                                                        )}

                                                        {/* 卡片工具栏（hover显示） */}
                                                        <div className="bo-arc-tools">
                                                            <button className="bo-arc-tool chat"
                                                                onClick={(ev) => {
                                                                    ev.stopPropagation()
                                                                    setDiscussTarget({
                                                                        kind: 'arc',
                                                                        arcId: arc.id,
                                                                        name: `情节「${arc.name}」`,
                                                                        content: `${arc.l1 || ''}`,
                                                                        icon: '',
                                                                    })
                                                                }}>聊这个</button>
                                                            <button className="bo-arc-tool enter"
                                                                onClick={(ev) => {
                                                                    ev.stopPropagation()
                                                                    selectArc(arc.id)
                                                                    setBookLevelMode(false)
                                                                }}>→ 进入</button>
                                                        </div>

                                                        {/* 元素云标签 */}
                                                        {arcElems.length > 0 && (
                                                            <div className="bo-element-cloud">
                                                                {arcElems.slice(0, 6).map(e => {
                                                                    const kindClass = { characters: 'char', items: 'item', settings: 'setting', locations: 'loc' }[e.kind] || ''
                                                                    return (
                                                                        <span key={e.id} className={`bo-cloud-tag ${kindClass}`}
                                                                            onClick={(ev) => { ev.stopPropagation(); setElemPopId(e.id) }}>
                                                                            {e.name}
                                                                        </span>
                                                                    )
                                                                })}
                                                                {arcElems.length > 6 && (
                                                                    <span className="bo-cloud-tag add">+{arcElems.length - 6}</span>
                                                                )}
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>
                                            </div>
                                        )
                                    })}
                                    <div className="bo-arc-node add-arc" onClick={() => setNewArcOpen(true)}>
                                        <div className="bo-arc-left"></div>
                                        <div className="bo-arc-marker add">＋</div>
                                        <div className="bo-arc-right">
                                            <div className="bo-arc-card add-hint">新建下一个情节...</div>
                                        </div>
                                    </div>
                                </div>
                            </section>
                        )}

                        {/* 全书元素分区 */}
                        {visKinds.map(km => (
                            <section className="bk-sec" key={km.key} id={'bk-sec-' + km.key}>
                                <div className="bk-sec-hd">
                                    <span className="bk-sec-t">{km.label}（{km.list.length}）</span>
                                    <button className="bk-add" onClick={() => {
                                        setElemPopId(null)
                                        setElemEdit({ kind: km.key, isNew: true, name: '', alias: '', desc: '' })
                                    }}>＋ 新建</button>
                                </div>
                                <div className="bo-gs-grid bo-elem-grid">
                                    {km.list.map(e => (
                                        <div key={e.id} className="bo-gs-card" onClick={() => setElemPopId(e.id)}>
                                            <div className="bo-gs-name">{e.name}</div>
                                            <div className="bo-gs-desc">{(e.desc || '').slice(0, 80)}{(e.desc || '').length > 80 ? '…' : ''}</div>
                                            {e.tags && e.tags.length > 0 && (
                                                <div style={{ marginTop: 4, display: 'flex', gap: 3, flexWrap: 'wrap' }}>
                                                    {e.tags.slice(0, 3).map((t, i) => (
                                                        <span key={i} style={{ fontSize: 9, padding: '0 5px', borderRadius: 8, background: 'var(--paper-deep)', color: 'var(--ink-mute)' }}>{t}</span>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            </section>
                        ))}

                        {/* 全局备注（可添加） */}
                        <section className="bk-sec" id="bk-sec-notes">
                            <div className="bk-sec-hd">
                                <span className="bk-sec-t">全局备注（{visNotes.length}）</span>
                                <span className="bk-sec-tip">注入全书所有情节的生成</span>
                            </div>
                            {visNotes.length === 0 ? (
                                <div style={{ fontSize: 12, color: 'var(--ink-mute)', padding: '4px 2px 8px' }}>
                                    {q ? '没有匹配的备注' : '还没有全局备注'}
                                </div>
                            ) : (
                                <div className="bk-notes">
                                    {visNotes.map(n => (
                                        <div key={n.id} className="bo-gs-card bk-note" style={{ cursor: 'default' }}>
                                            <div className="bo-gs-desc" style={{ fontSize: 12, color: 'var(--ink)', lineHeight: 1.7 }}>{n.text}</div>
                                            {n.timestamp && (
                                                <div style={{ fontSize: 10, color: 'var(--ink-mute)', marginTop: 4 }}>{new Date(n.timestamp).toLocaleString()}</div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            )}
                            <div className="bk-noteadd">
                                <input value={newNoteInput} onChange={e => setNewNoteInput(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter' && newNoteInput.trim()) addNote('global') }}
                                    placeholder="输入全局备注，回车添加..." />
                                <button onClick={() => { if (newNoteInput.trim()) addNote('global') }}>添加</button>
                            </div>
                        </section>
                    </>)}
                </div>
                {renderNewArcPopup()}
            </div>
        )
    }


    // ── 创作 hub（概写合并：左情节侧栏 + 中五级阶梯 + 右操作台+全书数据）────
    const renderCreateHub = () => {
        const CN_NUMS = ['壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖', '拾']
        const ls = ladderState(activeArc)
        const l3data = arcState?.levels?.l3?.data || {}
        const l3chapters = Array.isArray(l3data.chapters) ? l3data.chapters : []
        const activeChIdx = arcState?.active_chapter || 0
        const activeCh = l3chapters[activeChIdx] || null
        const l4scenes = arcState?.levels?.l4?.scenes || []
        const l5val = l5Draft !== null ? l5Draft : l5Text
        const hasL5 = l5Text.trim() !== ''

        // 讨论对象：点击阶梯层 → 组装「l1-l5」对象（VSCode 式，自由对话聚焦）
        const levelInfo = (lv) => {
            const names = { l1: 'l1 一句话极简', l2: 'l2 情节概要', l3: 'l3 章核心', l4: 'l4 场景分解', l5: 'l5 正文' }
            let content = ''
            if (lv === 'l1') content = activeArc?.l1 || arcState?.levels?.l1?.text || ''
            else if (lv === 'l2') content = activeArc?.l2 || arcState?.levels?.l2?.text || ''
            else if (lv === 'l3') content = l3chapters.map((ch, i) => `第${i + 1}章${ch.title ? ' ' + ch.title : ''}：${ch.core || ch.beats?.[0] || ''}`).join('\n')
            else if (lv === 'l4') content = l4scenes.map(sc => {
                const n = (sc.actions || []).length + (sc.dialogues || []).length + (sc.conflicts || []).length + (sc.details || []).length
                const extras = []
                if (sc.elements?.length) extras.push(`${sc.elements.length}元素`)
                if (sc.scene_note) extras.push('有备注')
                return `${sc.name || ''}（${n}叶${extras.length ? '｜' + extras.join('/') : ''}）`
            }).join('\n')
            else if (lv === 'l5') content = l5val || ''
            return { kind: 'level', lv, name: names[lv] || lv, content, icon: lv.toUpperCase() }
        }
        const lvDiscussing = (lv) => discuss?.kind === 'level' && discuss.lv === lv

        // 【常驻编辑卡】l4 场景卡 = 全并集（场景字段就地可编辑 + 节拍/素材/留空/备注/统计/提示）
        const renderWbL4Card = () => {
            const scenes = wbL4Draft !== null ? wbL4Draft : l4scenes
            if (!scenes.length) return <span style={{ color: 'var(--ink-mute)' }}>（未生成 — 在右侧点「生成下一级」）</span>
            const setSc = (i, field, val) => setWbL4Draft(arr => {
                const base = arr !== null ? JSON.parse(JSON.stringify(arr)) : JSON.parse(JSON.stringify(l4scenes))
                base[i][field] = val
                return base
            })
            const listText = (v) => Array.isArray(v) ? v.join('\n') : ''
            const lines = (s) => (s || '').split('\n').map(x => x.trim()).filter(Boolean)
            const inp = { width: '100%', boxSizing: 'border-box', padding: 6, borderRadius: 6, border: '1px solid var(--line-soft)', fontSize: 13, marginTop: 4 }
            const sceneFrags = (i) => (fragments || []).filter(f => f.scene_idx === i)
            const sceneNotes = (i) => (notes || []).filter(n => (n.scope || '').includes(`scene${i}`))

            // 素材光标位置（textarea onSelect/onClick/onKeyUp 更新）
            const markCursor = (i) => {
                const ta = fragRefs.current[i]
                if (ta) fragCursor.current[i] = { start: ta.selectionStart ?? ta.value.length, end: ta.selectionEnd ?? ta.value.length }
            }
            // 工具栏插入：按光标位置插入对应内容
            const insertAtCursor = (i, kind) => {
                const ta = fragRefs.current[i]
                const cur = (l4FragDraft[i] || {}).content || ''
                const pos = (fragCursor.current[i] && fragCursor.current[i].start != null) ? fragCursor.current[i] : { start: cur.length, end: cur.length }
                const start = Math.max(0, Math.min(pos.start, cur.length))
                const end = Math.max(start, Math.min(pos.end, cur.length))
                if (kind === 'blank') {
                    const sel = cur.slice(start, end)
                    const inserted = sel ? `[${sel}]` : '[]'
                    const next = cur.slice(0, start) + inserted + cur.slice(end)
                    setL4FragDraft(d => ({ ...d, [i]: { type: 'detail', content: next } }))
                } else {
                    const prefix = { dialogue: '对白', detail: '细节', action: '动作', item: '物品', character: '角色', setting: '设定' }[kind] || '素材'
                    const pad = start > 0 && cur[start - 1] !== '\n' ? '\n\n' : ''
                    const insert = `${pad}[${prefix}] `
                    const next = cur.slice(0, start) + insert + cur.slice(end)
                    const ftype = kind === 'item_card' ? 'item' : kind === 'char_card' ? 'character' : kind === 'setting_card' ? 'setting' : kind
                    setL4FragDraft(d => ({ ...d, [i]: { type: ftype, content: next } }))
                }
                setTimeout(() => { const t2 = fragRefs.current[i]; if (t2) t2.focus() }, 0)
            }
            // 保存素材草稿到 fragments.json
            const saveFrag = async (i) => {
                const draft = l4FragDraft[i]
                if (!draft?.content?.trim()) { setError('素材内容不能为空'); return }
                try {
                    await phFragmentsPut({ book_root: bookRoot, arc_id: activeArcId, ftype: draft.type || 'detail', content: draft.content.trim(), scene_idx: i })
                    setNotice('素材已保存')
                    setL4FragDraft(d => { const n = { ...d }; delete n[i]; return n })
                    await refreshWorkbench(bookRoot)
                } catch (e) { setError(`素材保存失败：${e.message || e}`) }
            }
            // 段级备注：勾选节拍 → prompt 输入 → 创建 note
            const addBeatNote = async (i) => {
                const sel = l4BeatSelected[i]
                if (!sel || sel.size === 0) { setError('请先勾选要备注的节拍'); return }
                const content = prompt('请输入段级备注内容：')
                if (!content?.trim()) return
                const beatStr = Array.from(sel).sort().join(',')
                try {
                    await phNotesPut({ book_root: bookRoot, scope: `arc:${activeArcId}:scene${i}:beats:${beatStr}`, content: content.trim() })
                    setNotice('段级备注已保存')
                    setL4BeatSelected(d => ({ ...d, [i]: new Set() }))
                    await refreshWorkbench(bookRoot)
                } catch (e) { setError(`备注保存失败：${e.message || e}`) }
            }
            const toggleBeat = (i, bi) => setL4BeatSelected(d => {
                const prev = d[i] || new Set()
                const next = new Set(prev)
                if (next.has(bi)) next.delete(bi); else next.add(bi)
                return { ...d, [i]: next }
            })
            // 保存整个 l4（scenes 数组 → 后端清下游）
            const saveAllL4 = async () => {
                const r = await run('保存 l4', () => phAiArcSetLevel(bookRoot, activeArcId, 'l4', '', scenes, null))
                if (r?.ok) {
                    setNotice('l4 已保存（l5 已清空，需重新生成）')
                    setWbL4Draft(null)
                    setL4FragDraft({}); setL4BeatSelected({})
                    await refreshWorkbench(bookRoot)
                }
            }
            const toolbar = [
                { type: 'blank', label: '留空', desc: '选中文本 → 包成 [意图]，或插 []' },
                { type: 'dialogue', label: '+ 对白', desc: '光标处插入 [对白]' },
                { type: 'detail', label: '+ 细节', desc: '光标处插入 [细节]' },
                { type: 'action', label: '+ 动作', desc: '光标处插入 [动作]' },
                { type: 'item_card', label: '+ 物品卡', desc: '光标处插入 [物品]' },
                { type: 'char_card', label: '+ 角色卡', desc: '光标处插入 [角色]' },
            ]
            const elemGroups = [{ key: 'characters', label: '角色', icon: '' }, { key: 'items', label: '物品', icon: '' }, { key: 'settings', label: '设定', icon: '' }, { key: 'maps', label: '地图', icon: '' }]

            // 在光标位置插入一条新内容（节拍叙事流）
            const handleInsertFromToolbar = (kind) => {
                if (!l4Cursor) return
                const { sceneIdx, beatIdx, rowIdx } = l4Cursor
                const fieldMap = {
                    action: 'actions', dialogue: 'dialogues', conflict: 'conflicts',
                    detail: 'details', blank: 'details',
                    item_card: 'details', char_card: 'details', setting_card: 'details',
                }
                const field = fieldMap[kind] || 'details'
                const prefixMap = {
                    action: '',
                    dialogue: '角色：台词',
                    conflict: '',
                    detail: '',
                    blank: '[留空意图]',
                    item_card: '【物品】物品名：描述',
                    char_card: '【角色】角色名：描述',
                    setting_card: '【设定】设定名：描述',
                }
                const newItem = prefixMap[kind] || '新内容'
                setWbL4Draft(arr => {
                    const base = arr !== null ? JSON.parse(JSON.stringify(arr)) : JSON.parse(JSON.stringify(l4scenes))
                    const arr2 = [...(base[sceneIdx][field] || [])]
                    // 插入到光标位置之后
                    const insertPos = Math.min(rowIdx + 1, arr2.length)
                    arr2.splice(insertPos, 0, newItem)
                    base[sceneIdx][field] = arr2
                    return base
                })
                // 更新光标到新插入的行
                setL4Cursor({
                    sceneIdx,
                    beatIdx,
                    rowIdx: rowIdx + 1,
                    kind: field === 'actions' ? 'action' : field === 'dialogues' ? 'dialogue'
                        : field === 'conflicts' ? 'conflict' : 'detail',
                })
            }

            // 添加节拍
            const handleAddBeat = (sceneIdx) => {
                setWbL4Draft(arr => {
                    const base = arr !== null ? JSON.parse(JSON.stringify(arr)) : JSON.parse(JSON.stringify(l4scenes))
                    const beats = base[sceneIdx].beats || []
                    base[sceneIdx].beats = [...beats, '新节拍']
                    return base
                })
            }

            // 添加场景
            const handleAddScene = () => {
                setWbL4Draft(arr => [...(arr !== null ? arr : JSON.parse(JSON.stringify(l4scenes))),
                    { name: '新场景', environment: '', beats: ['新节拍'], actions: [], dialogues: [], conflicts: [], details: [], elements: [], scene_note: '' }])
            }

            // 保存时暂存插入函数到ref（供右栏工具栏调用）
            insertFromToolbar.current = handleInsertFromToolbar

            return (
                <L4BeatView
                    scenes={scenes}
                    onScenesChange={setWbL4Draft}
                    cursor={l4Cursor}
                    onCursorChange={setL4Cursor}
                    fragments={fragments}
                    notes={notes}
                    flatElements={flatElements}
                    btnStyle={btnStyle}
                    busy={busy}
                    onSave={saveAllL4}
                    onAddScene={handleAddScene}
                    onAddBeat={handleAddBeat}
                />
            )
        }


        // 本章评分
        const chScore = lastScore || (activeArc?.chapters?.[activeChIdx]
            ? {
                intent_score: activeArc.chapters[activeChIdx].intent_score,
                quality_score: activeArc.chapters[activeChIdx].quality_score,
                overall: activeArc.chapters[activeChIdx].overall,
                polluted: activeArc.chapters[activeChIdx].polluted,
            }
            : null)

        const activeIdx = arcList.findIndex(a => a.id === activeArcId)

        // 书级模式：中栏舞台不渲染（全部书级内容在 wb-bookleft 左栏，两栏布局 2026-09-07）
        if (bookLevelMode) return null

        return (
            <div className="wb-createstage">

                {/* 中：五级阶梯主舞台 */}
                <main className="wb-createmain">
                    {/* 书头 */}
                    <div className="wb-bookhead">
                        <div className="wb-bhtitle">
                            <span>{activeArc?.name || '未选情节'}</span>
                            <span className="sub">
                                · {activeArc ? arcRange(activeArc) : '先选情节'}
                                {activeCh && ` · 当前第${arcChapterNum(activeArc, activeChIdx)}章「${stripCh(activeCh.title) || ''}」`}
                            </span>
                            {activeArc && <span className="sub" style={{ marginLeft: 'auto' }}>{activeIdx + 1} / {arcList.length} 情节</span>}
                        </div>
                        <div className="wb-bhmeta">
                            <span className={`m-item ${metaPop === 'ladder' ? 'active' : ''}`}
                                onClick={e => { e.stopPropagation(); setMetaPop(metaPop === 'ladder' ? null : 'ladder') }}>
                                阶梯 <b>l{ls.filter(s => s !== 'no').length || 1}</b>
                            </span><span className="sep">|</span>
                            <span className={`m-item ${metaPop === 'participants' ? 'active' : ''}`}
                                onClick={e => { e.stopPropagation(); setMetaPop(metaPop === 'participants' ? null : 'participants') }}>
                                参与 <b>{(activeArc?.selected?.characters?.length || 0) + (activeArc?.selected?.items?.length || 0) + (activeArc?.selected?.settings?.length || 0)}</b>
                            </span><span className="sep">|</span>
                            <span className={`m-item ${metaPop === 'chapter' ? 'active' : ''}`}
                                onClick={e => { e.stopPropagation(); setMetaPop(metaPop === 'chapter' ? null : 'chapter') }}>
                                {chScore ? (
                                    <>综合 <b>{typeof chScore.overall === 'number' ? chScore.overall.toFixed(3) : chScore.overall || '—'}</b></>
                                ) : (
                                    <>本章 <b style={{ color: 'var(--amber)' }}>进行中</b></>
                                )}
                            </span>
                        </div>
                    </div>

                    {/* 顶部信息浮层 */}
                    {metaPop && activeArc && (
                        <div className="wb-metapop" onClick={e => e.stopPropagation()}>
                            {metaPop === 'ladder' && (
                                <div className="wb-mp-ladder">
                                {['l1', 'l2', 'l3', 'l4', 'l5'].map((lv, i) => {
                                    const st = ls[i] || 'no'
                                    const labels = { l1: '一句话极简', l2: '情节概要', l3: '章核心', l4: '场景分解', l5: '正文' }
                                    const statusText = st === 'ok' ? '✓ 已确认' : st === 'half' ? '● 待确认' : '○ 未生成'
                                    return (
                                        <div key={lv} className={`mp-lv-row ${st}`}>
                                            <span className="mp-lv-tag">{lv}</span>
                                            <span className="mp-lv-name">{labels[lv]}</span>
                                            <span className="mp-lv-st">{statusText}</span>
                                        </div>
                                    )
                                })}
                            </div>
                        )}
                        {metaPop === 'participants' && (
                            <div className="wb-mp-participants">
                                {['characters', 'items', 'settings'].map(kind => {
                                    const meta = kindMeta.find(m => m.key === kind)
                                    const list = (elements[kind] || [])
                                    const selSet = new Set(activeArc?.selected?.[kind] || [])
                                    if (list.length === 0) return null
                                    return (
                                        <div key={kind} className="mp-p-group">
                                            <div className="mp-p-title">{meta?.label}（{selSet.size}/{list.length}）</div>
                                            <div className="mp-p-chips">
                                                {list.map(e => (
                                                    <span key={e.id} className={`mp-chip ${selSet.has(e.id) ? 'in' : ''}`}>
                                                        {e.name}
                                                    </span>
                                                ))}
                                            </div>
                                        </div>
                                    )
                                })}
                                {allElems.length === 0 && (
                                    <div style={{ color: 'var(--ink-mute)', fontSize: 12, textAlign: 'center', padding: 20 }}>
                                        还没有元素
                                    </div>
                                )}
                            </div>
                        )}
                        {metaPop === 'chapter' && (
                            <div className="wb-mp-chapter">
                                {activeCh ? (
                                <>
                                    <div className="mp-ch-title">{activeCh.title || `第${activeChIdx + 1}章`}</div>
                                    <div className="mp-ch-stats">
                                        <div className="mp-ch-stat">
                                        <div className="mp-ch-n">{activeCh?.text?.length || ((activeCh?.beats?.length || 0) * 800)}</div>
                                        <div className="mp-ch-l">字数</div>
                                    </div>
                                    {chScore?.intent !== undefined && (
                                        <div className="mp-ch-stat">
                                            <div className="mp-ch-n">{(chScore.intent || 0).toFixed(2)}</div>
                                            <div className="mp-ch-l">意图</div>
                                        </div>
                                    )}
                                    {chScore?.quality !== undefined && (
                                        <div className="mp-ch-stat">
                                            <div className="mp-ch-n">{(chScore.quality || 0).toFixed(2)}</div>
                                            <div className="mp-ch-l">质量</div>
                                        </div>
                                    )}
                                    <div className="mp-ch-stat">
                                        <div className={`mp-ch-n ${chScore?.polluted ? 'bad' : ''}`}>{chScore?.polluted ? '⚠ 有' : '✓ 洁净'}</div>
                                        <div className="mp-ch-l">污染</div>
                                    </div>
                                </div>
                                </>
                            ) : (
                                <div style={{ color: 'var(--ink-mute)', fontSize: 12, textAlign: 'center', padding: 20 }}>
                                    还没有章节
                                </div>
                            )}
                        </div>
                    )}
                    </div>
                )}

                    {arcList.length === 0 && !bookLevelMode && (
                        <div className="wb-emptyguide">
                            <div className="eg-title">本书还没有情节 — 第一步：新建情节</div>
                            {renderNewArcForm(false)}
                            <div className="eg-actions">
                                <button className="eg-ai" style={btnStyle(false, true)} onClick={startAiFirstArc}>
                                    让 AI 规划第一个情节
                                </button>
                                <button className="eg-batch" style={btnStyle(false, false)} onClick={() => { setTab('system'); setSyTab('batch') }}>
                                    批量生成
                                </button>
                            </div>
                        </div>
                    )}

                    {!bookLevelMode && (
                        <>
                    {/* 创作流程指示器 */}
                    <div className="wb-flow-indicator">
                        {['l1', 'l2', 'l3', 'l4', 'l5'].map((lv, i) => {
                            const st = ls[i] || 'no'
                            const labels = { l1: '一句话极简', l2: '情节概要', l3: '章核心', l4: '场景分解', l5: '正文' }
                            const statusText = st === 'ok' ? '✓' : st === 'half' ? '●' : '○'
                            const isCurrent = st !== 'no' && (i === ls.length - 1 || ls[i + 1] === 'no')
                            return (
                                <span key={lv} className={`flow-step ${st} ${isCurrent ? 'current' : ''}`}>
                                    <span className="flow-lv">{lv}</span>
                                    <span className="flow-label">{labels[lv]}</span>
                                    <span className="flow-status">{statusText}</span>
                                </span>
                            )
                        })}
                        <span className="flow-current">当前：l{ls.filter(s => s !== 'no').length || 1} {ls.filter(s => s !== 'no').length > 0 ? ls[ls.filter(s => s !== 'no').length - 1] === 'ok' ? '已确认' : '进行中' : '未开始'}</span>
                    </div>

                    <div className="wb-rubric" style={{ marginTop: 4 }}>五级阶梯 · 自顶向下</div>

                    <div className="wb-ladderstack">

                        {/* l1 */}
                        <div className={`wb-lvblock ${lvDiscussing('l1') ? 'discussing' : ''} ${ladderExpanded.l1 ? 'expanded' : ''}`}>
                            <div className="wb-lvheader" onClick={() => setLadderExpanded(prev => ({ ...prev, l1: !prev.l1 }))}>
                                {renderLevelOps('l1', activeArc?.l1 || arcState?.levels?.l1?.text || '')}
                                <span className={`wb-lvbadge ${ls[0] === 'ok' ? 'ok' : ls[0] === 'half' ? 'half' : 'empty'}`}>
                                    {ls[0] === 'ok' ? '✓ 已确认' : ls[0] === 'half' ? '● 待确认' : '○ 未生成'}
                                </span>
                                <span className="wb-lvlabel">l1<span className="lv-sub">一句话极简</span></span>
                                <span className="wb-lvarrow">{ladderExpanded.l1 ? '▾' : '▸'}</span>
                            </div>
                            {ladderExpanded.l1 && (
                                <div className="wb-lvcontent" onClick={() => setDiscussTarget(levelInfo('l1'))} title="点击设为讨论对象，对话区自由讨论">
                                    {activeArc?.l1 || arcState?.levels?.l1?.text || '（未生成）'}
                                </div>
                            )}
                        </div>

                        {/* l2 */}
                        <div className={`wb-lvblock ${lvDiscussing('l2') ? 'discussing' : ''} ${ladderExpanded.l2 ? 'expanded' : ''}`}>
                            <div className="wb-lvheader" onClick={() => setLadderExpanded(prev => ({ ...prev, l2: !prev.l2 }))}>
                                {renderLevelOps('l2', activeArc?.l2 || arcState?.levels?.l2?.text || '')}
                                <span className={`wb-lvbadge ${ls[1] === 'ok' ? 'ok' : ls[1] === 'half' ? 'half' : 'empty'}`}>
                                    {ls[1] === 'ok' ? '✓ 已确认' : ls[1] === 'half' ? '● 待确认' : '○ 未生成'}
                                </span>
                                <span className="wb-lvlabel">l2<span className="lv-sub">情节概要</span></span>
                                <span className="wb-lvarrow">{ladderExpanded.l2 ? '▾' : '▸'}</span>
                            </div>
                            {ladderExpanded.l2 && (
                                <div className="wb-lvcontent" onClick={() => setDiscussTarget(levelInfo('l2'))} title="点击设为讨论对象，对话区自由讨论">
                                    {activeArc?.l2 || arcState?.levels?.l2?.text || '（未生成）'}
                                </div>
                            )}
                        </div>

                        {/* l3 章纲 */}
                        <div className={`wb-lvblock ${lvDiscussing('l3') ? 'discussing' : ''} ${ladderExpanded.l3 ? 'expanded' : ''}`}>
                            <div className="wb-lvheader" onClick={() => setLadderExpanded(prev => ({ ...prev, l3: !prev.l3 }))}>
                                {renderLevelOps('l3', l3chapters.map((ch, i) => `${ch.title || `第${i + 1}章`}：${ch.core || ''}`).join('\n'))}
                                <span className={`wb-lvbadge ${ls[2] === 'ok' ? 'ok' : ls[2] === 'half' ? 'half' : 'empty'}`}>
                                    {ls[2] === 'ok' ? '✓ 已确认' : ls[2] === 'half' ? '● 待确认' : '○ 未生成'}
                                </span>
                                <span className="wb-lvlabel">l3<span className="lv-sub">章核心</span></span>
                                <span className="wb-lvarrow">{ladderExpanded.l3 ? '▾' : '▸'}</span>
                            </div>
                            {ladderExpanded.l3 && (
                                <div className="wb-lvcontent" onClick={() => setDiscussTarget(levelInfo('l3'))} title="点击设为讨论对象，对话区自由讨论">
                                    {l3chapters.length > 0 ? (
                                        <div className="wb-l3list">
                                            {l3chapters.map((ch, i) => (
                                                <div key={i} className={`wb-l3row ${i === activeChIdx ? 'active' : ''}`}
                                                    onClick={(e) => { e.stopPropagation(); handleSetActiveChapter(i) }}>
                                                    <span className="ch-n">{i + 1}</span>
                                                    <span className="ch-t">{ch.title || `第${i + 1}章`}</span>
                                                    <span className="ch-c">{ch.core || ch.beats?.[0] || ''}</span>
                                                    <span className="ch-del" title="删除本章（下游 l4/l5 清空）"
                                                        onClick={(e) => { e.stopPropagation(); deleteChapter(activeArc?.id, i) }}>✕</span>
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <span style={{ color: 'var(--ink-mute)' }}>（未生成）</span>
                                    )}
                                </div>
                            )}
                        </div>

                        {/* l4 场景分解 */}
                        <div className={`wb-lvblock ${lvDiscussing('l4') ? 'discussing' : ''} ${ladderExpanded.l4 ? 'expanded' : ''}`}>
                            <div className="wb-lvheader" onClick={() => setLadderExpanded(prev => ({ ...prev, l4: !prev.l4 }))}>
                                {renderLevelOps('l4', null)}
                                <span className={`wb-lvbadge ${ls[3] === 'ok' ? 'ok' : ls[3] === 'half' ? 'half' : 'empty'}`}>
                                    {ls[3] === 'ok' ? '✓ 已确认' : ls[3] === 'half' ? '● 待确认' : '○ 未生成'}
                                </span>
                                <span className="wb-lvlabel">l4<span className="lv-sub">场景分解</span></span>
                                <span className="wb-lvarrow">{ladderExpanded.l4 ? '▾' : '▸'}</span>
                            </div>
                            {ladderExpanded.l4 && (
                                <div className="wb-lvcontent" onClick={(e) => { if (e.target.closest('input, textarea, select, button')) return; setDiscussTarget(levelInfo('l4')) }} title="点击设为讨论对象，对话区自由讨论">
                                    {renderWbL4Card()}
                                </div>
                            )}
                        </div>

                        {/* l5 正文（可滚动） */}
                        <div className={`wb-lvblock expand ${lvDiscussing('l5') ? 'discussing' : ''} ${ladderExpanded.l5 ? 'expanded' : ''}`}>
                            <div className="wb-lvheader" onClick={() => setLadderExpanded(prev => ({ ...prev, l5: !prev.l5 }))}>
                                {renderLevelOps('l5', l5val)}
                                <span className={`wb-lvbadge ${ls[4] === 'ok' ? 'ok' : ls[4] === 'half' ? 'half' : 'empty'}`}>
                                    {ls[4] === 'ok' ? '✓ 已确认' : ls[4] === 'half' ? '● 待确认' : '○ 未生成'}
                                </span>
                                <span className="wb-lvlabel" style={{ paddingTop: 4 }}>l5<span className="lv-sub">正文</span></span>
                                <span className="wb-lvarrow">{ladderExpanded.l5 ? '▾' : '▸'}</span>
                            </div>
                            {ladderExpanded.l5 && (
                                <div className="wb-lvcontent prose" style={{ overflowY: 'auto' }} onClick={() => setDiscussTarget(levelInfo('l5'))} title="点击设为讨论对象，对话区自由讨论">
                                    {l5val ? (
                                        l5val.split(/\n{2,}/).map((para, i) => (
                                            <p key={i} style={{ textIndent: '2em', marginBottom: 6 }}>{para}</p>
                                        ))
                                    ) : (
                                        <span style={{ color: 'var(--ink-sub)', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
                                            （正文未生成 — 在右侧点「生成下一级」）
                                        </span>
                                    )}
                                </div>
                            )}
                        </div>

                    </div>
                        </>
                    )}

                    {/* 章导航条 */}
                    {l3chapters.length > 0 && (
                        <div className="wb-chnav">
                            {l3chapters.map((ch, i) => {
                                const isFin = activeArc?.chapters?.[i]
                                const isDoing = i === activeChIdx && !isFin
                                return (
                                    <div key={i}
                                        className={`wb-chdot ${isFin ? 'done' : isDoing ? 'doing' : ''} ${i === activeChIdx ? 'active' : ''}`}
                                        onClick={() => handleSetActiveChapter(i)}>
                                        <span className="num">{i + 1}</span>
                                        <span className="mk"></span>
                                    </div>
                                )
                            })}
                        </div>
                    )}
                </main>

                {/* 「＋ 新建情节」弹层（左栏按钮触发，fixed 居中；书级模式分支另挂载一份） */}
                {renderNewArcPopup()}

                {/* 层级确认浮层：step_ladder 生成后审阅/确认/重生成/对话修改（借鉴 codex overlay） */}
                <LevelConfirmOverlay
                    open={!!confirmLevel}
                    level={confirmLevel}
                    busy={busy}
                    onConfirm={() => handleConfirm(confirmLevel)}
                    onRegenerate={handleOverlayRegenerate}
                    onDiscuss={handleOverlayDiscuss}
                    onClose={() => setConfirmLevel(null)}
                >
                    {confirmLevel === 'l1' && <div className="lco-readonly">{activeArc?.l1 || arcState?.levels?.l1?.text}</div>}
                    {confirmLevel === 'l2' && <div className="lco-readonly">{activeArc?.l2 || arcState?.levels?.l2?.text}</div>}
                    {confirmLevel === 'l3' && (
                        <div>
                            {l3chapters.map((ch, i) => (
                                <div key={i} className="lco-scene">
                                    <div className="lco-scene-name">第{i + 1}章 {ch.title || ''}</div>
                                    <div>{ch.core || ch.beats?.[0] || ''}</div>
                                </div>
                            ))}
                        </div>
                    )}
                    {confirmLevel === 'l4' && (
                        <div>
                            {l4scenes.map((sc, i) => (
                                <div key={i} className="lco-scene">
                                    <div className="lco-scene-name">{sc.name || `场景${i + 1}`}</div>
                                    <div style={{ fontSize: 12, color: 'var(--ink-sub)', marginBottom: 4 }}>
                                        {(sc.actions || []).length + (sc.dialogues || []).length + (sc.details || []).length} 叶
                                        {sc.dialogues?.length ? ` · ${sc.dialogues.length} 轮对白` : ''}
                                    </div>
                                    {(sc.dialogues || []).slice(0, 4).map((d, j) => (
                                        <div key={j} style={{ marginLeft: 6 }}>{d}</div>
                                    ))}
                                </div>
                            ))}
                            {l4scenes.length > 0 && (
                                <div style={{ fontSize: 12, color: 'var(--ink-mute)', marginTop: 8 }}>
                                    完整场景编辑请展开中栏 l4 卡片
                                </div>
                            )}
                        </div>
                    )}
                    {confirmLevel === 'l5' && (
                        <div className="lco-readonly">
                            {(l5val || '').split(/\n{2,}/).map((para, i) => (
                                <p key={i} style={{ textIndent: '2em', marginBottom: 6 }}>{para}</p>
                            ))}
                        </div>
                    )}
                </LevelConfirmOverlay>

                {/* 原操作台已移除，功能合并入右侧创作助手快捷操作面板 */}
                {/* 右：操作台 + 全书数据 */}
                <aside className="wb-createops" style={{ display: 'none' }}>
                    <div className="wb-ophead">
                        操作台
                        <div className="optools">
                            <span title="元素图鉴" onClick={() => setOvTab('gallery')}>图鉴</span>
                            <span title="检索" onClick={() => setOvTab('search')}>检索</span>
                            <span title="讨论" onClick={() => setAiTab('targeted')}>讨论</span>
                        </div>
                    </div>

                    <div className="wb-opsbody">

                        {/* 参与元素 */}
                        {activeArc && (
                            <div className="wb-opsec">
                                <div className="wb-optitle">本情节参与元素</div>
                                <div className="wb-eg">
                                    <div className="wb-eglabel">角色</div>
                                    <div className="wb-echips">
                                        {(activeArc.selected?.characters || []).map(id => {
                                            const e = (elements.characters || []).find(x => x.id === id)
                                            return e ? <span key={id} className="wb-echip">{e.name}</span> : null
                                        })}
                                        <span className="wb-echip outline">+ 选</span>
                                    </div>
                                </div>
                                <div className="wb-eg">
                                    <div className="wb-eglabel">物品</div>
                                    <div className="wb-echips">
                                        {(activeArc.selected?.items || []).map(id => {
                                            const e = (elements.items || []).find(x => x.id === id)
                                            return e ? <span key={id} className="wb-echip">{e.name}</span> : null
                                        })}
                                        <span className="wb-echip outline">+ 选</span>
                                    </div>
                                </div>
                                <div className="wb-eg">
                                    <div className="wb-eglabel">设定</div>
                                    <div className="wb-echips">
                                        {(activeArc.selected?.settings || []).map(id => {
                                            const e = (elements.settings || []).find(x => x.id === id)
                                            return e ? <span key={id} className="wb-echip">{e.name}</span> : null
                                        })}
                                        <span className="wb-echip outline">+ 选</span>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* 阶梯状态 */}
                        <div className="wb-opsec">
                            <div className="wb-optitle">阶梯进度</div>
                            <div className="wb-lstate">
                                {ls.map((s, i) => (
                                    <div key={i} className={`lb ${s}`}>l{i + 1}</div>
                                ))}
                            </div>
                            <div style={{ fontSize: 9.5, color: 'var(--ink-mute)' }}>
                                当前：l{ls.filter(s => s !== 'no').length + 1 || 1} · 下一级
                            </div>
                        </div>

                        {/* 双评分 */}
                        {chScore && (
                            <div className="wb-opsec">
                                <div className="wb-optitle">双评分 · 本章</div>
                                <div className="wb-scorebig">
                                    <div className="sb"><div className="n">{typeof chScore.intent_score === 'number' ? chScore.intent_score.toFixed(3) : chScore.intent_score || '—'}</div><div className="l">意图兑现</div></div>
                                    <div className="sb"><div className="n">{typeof chScore.quality_score === 'number' ? chScore.quality_score.toFixed(3) : chScore.quality_score || '—'}</div><div className="l">纯质量</div></div>
                                    <div className="sb"><div className="n">{typeof chScore.overall === 'number' ? chScore.overall.toFixed(3) : chScore.overall || '—'}</div><div className="l">综合</div></div>
                                </div>
                                {chScore.polluted && (
                                    <div style={{ fontSize: 10, color: 'var(--cinnabar-d)', marginTop: 3 }}>
                                        ⚠ 检出未参与元素污染
                                    </div>
                                )}
                            </div>
                        )}

                        {/* 操作按钮 */}
                        <div className="wb-opsec">
                            <div className="wb-optitle">操作</div>
                            <div className="wb-btnrow">
                                <button className="wb-btn primary" disabled={busy || !activeArcId} onClick={handleStep}>生成下一级</button>
                                <button className="wb-btn" disabled={!hasL5} onClick={() => setL5Draft(l5Text)}>编辑正文</button>
                            </div>
                            <div className="wb-btnrow" style={{ marginTop: 4 }}>
                                <button className="wb-btn warm" disabled={scoreBusy || !hasL5} onClick={handleScore}>评分</button>
                                <button className="wb-btn" disabled={!activeArcId} onClick={() => setAiTab('targeted')}>讨论</button>
                            </div>
                            <div className="wb-btnrow" style={{ marginTop: 4 }}>
                                <button className="wb-btn" disabled={busy || !lastScore || lastScore.polluted} onClick={handleFinalize}>✓ 落盘本章</button>
                                <button className="wb-btn danger" disabled={scoreBusy || !hasL5} onClick={handlePollution}>⚠ 污染检查</button>
                            </div>
                        </div>

                        {/* 元素隔离 */}
                        {activeArc && (
                            <div className="wb-opsec">
                                <div className="wb-optitle">元素隔离</div>
                                <div style={{ fontSize: 10.5, color: chScore?.polluted ? 'var(--cinnabar-d)' : 'var(--green)' }}>
                                    {chScore?.polluted ? '⚠ 检出污染' : '✓ 白名单生效中'}
                                </div>
                                <div style={{ fontSize: 10, color: 'var(--ink-mute)', marginTop: 2 }}>
                                    l3 起注入白名单 · 双检（确定性+LLM）
                                </div>
                            </div>
                        )}

                        {/* 无情节时占位 */}
                        {!activeArc && (
                            <div style={{ fontSize: 11, color: 'var(--ink-mute)', textAlign: 'center', padding: 20 }}>
                                从左侧选一个情节，或新建一个
                            </div>
                        )}

                        {/* 全书数据区 */}
                        <div className="wb-opdivider"></div>

                        {/* 情节状态速览 */}
                        <div className="wb-opsec">
                            <div className="wb-optitle">全书概览</div>
                            <div className="wb-arcstats">
                                <div className="wb-asitem"><div className="n">{arcList.length}</div><div className="l">情节</div></div>
                                <div className="wb-asitem"><div className="n">{finChapters.length}</div><div className="l">章</div></div>
                                <div className="wb-asitem"><div className="n">{(totalWords/1000).toFixed(0)}k</div><div className="l">字</div></div>
                                <div className="wb-asitem"><div className="n">{overallAvg ? overallAvg.toFixed(3).replace(/^0/, '') : '—'}</div><div className="l">均分</div></div>
                                <div className={`wb-asitem ${pollutedCount === 0 ? 'ok' : ''}`}><div className="n">{pollutedCount}</div><div className="l">污染</div></div>
                                <div className="wb-asitem"><div className="n">{Object.values(elements).flat().length}</div><div className="l">元素</div></div>
                            </div>
                        </div>

                        {/* 评分迷你图 */}
                        {finChapters.length > 0 && (
                            <div className="wb-opsec">
                                <div className="wb-optitle">评分走势</div>
                                <div className="wb-scorewrap" style={{ padding: 0 }}>
                                    <ChartWrapper option={{
                                        tooltip: { trigger: 'axis' },
                                        legend: { show: false },
                                        grid: { left: 25, right: 5, top: 5, bottom: 16 },
                                        xAxis: { type: 'category', data: finChapters.map(c => `第${c.num}章`), axisLabel: { show: false } },
                                        yAxis: { type: 'value', min: 0, max: 1, axisLabel: { show: false }, splitLine: { lineStyle: { color: '#E4DCCB', type: 'dashed' } } },
                                        series: [
                                            { name: '综合', type: 'line', data: finChapters.map(c => c.overall), symbolSize: 2, lineStyle: { width: 1.5, color: '#4a4034' } },
                                            { name: '质量', type: 'line', data: finChapters.map(c => c.quality_score), symbolSize: 0, lineStyle: { width: 1, color: '#8a7a5a', type: 'dashed' } },
                                            { name: '意图', type: 'line', data: finChapters.map(c => c.intent_score), symbolSize: 0, lineStyle: { width: 1, color: '#a89f8c', type: 'dotted' } },
                                        ],
                                    }} height={55} />
                                    <div className="wb-scorecap">
                                        <span><span style={{ display: 'inline-block', width: 8, height: 2, background: '#4a4034', verticalAlign: 'middle', marginRight: 3 }}></span>综 {overallAvg ? overallAvg.toFixed(3) : '—'}</span>
                                        <span><span style={{ display: 'inline-block', width: 8, height: 2, background: '#8a7a5a', verticalAlign: 'middle', marginRight: 3 }}></span>质</span>
                                        <span><span style={{ display: 'inline-block', width: 8, height: 2, background: '#a89f8c', verticalAlign: 'middle', marginRight: 3 }}></span>意</span>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* 元素 */}
                        <div className="wb-opsec">
                            <div className="wb-optitle">元素</div>
                            <div className="wb-elemscan">
                                {allElems.map(e => (
                                    <div key={e.id} className={`wb-esrow ${e.inArc ? 'in' : ''}`}>
<span
                                            className={`esname wb-editable ${editStatus[`elem_${e.kind}_${e.id}`] === 'editing' ? 'wb-editing' : ''}`}
                                            contentEditable
                                            suppressContentEditableWarning
                                            onClick={(e) => e.stopPropagation()}
                                            onFocus={(evt) => {
                                                startEdit(`elem_${e.kind}_${e.id}`)
                                                const range = document.createRange()
                                                range.selectNodeContents(evt.currentTarget)
                                                const sel = window.getSelection()
                                                sel.removeAllRanges()
                                                sel.addRange(range)
                                            }}
                                            onBlur={(evt) => {
                                                const text = evt.currentTarget.innerText.trim()
                                                if (text && text !== e.name) {
                                                    handleElemNameEdit(e.kind, e.id, text)
                                                } else {
                                                    finishEdit(`elem_${e.kind}_${e.id}`)
                                                }
                                            }}
                                            onKeyDown={(evt) => {
                                                if (evt.key === 'Enter' && !evt.shiftKey) {
                                                    evt.preventDefault()
                                                    evt.currentTarget.blur()
                                                }
                                            }}
                                        >{e.name}</span>
                                        <span className="esusage">{e.inArc ? '● 本情节' : `${e.usage} 情节`}</span>
                                    </div>
                                ))}
                                {allElems.length > 12 && (
                                    <div style={{ fontSize: 10, color: 'var(--dai)', textAlign: 'center', padding: '4px 0', cursor: 'pointer' }}
                                        onClick={() => setOvTab('gallery')}>
                                        查看全部 {allElems.length} 个 →
                                    </div>
                                )}
                            </div>
                        </div>

                    </div>
                </aside>

                {/* 右侧抽屉：灵感工坊 / 片段扩写 / 节奏雷达 / 角色图鉴 / 检索
                    从右侧滑入，只占部分宽度，不覆盖中间主舞台（解决图层混乱） */}
                {(ovTab === 'inspire' || ovTab === 'fragment' || ovTab === 'pacing' || ovTab === 'gallery' || ovTab === 'search') && (
                    <div className="wb-drawer open" onClick={(e) => e.stopPropagation()}>
                        <div className="wb-drawer-head">
                            <span className="wb-drawer-title">
                                {ovTab === 'inspire' && '灵感工坊'}
                                {ovTab === 'fragment' && '片段扩写'}
                                {ovTab === 'pacing' && '节奏雷达'}
                                {ovTab === 'gallery' && '元素图鉴'}
                                {ovTab === 'search' && '检索'}
                            </span>
                            <button className="wb-drawer-close" onClick={() => setOvTab('stats')}>✕</button>
                        </div>
                        <div className="wb-drawer-body">
                            {ovTab === 'inspire' && (
                                <InspireWorkspace bookRoot={bookRoot} elements={elements} arcs={arcs}
                                    onRefresh={() => refreshWorkbench(bookRoot)} />
                            )}
                            {ovTab === 'fragment' && renderFragment()}
                            {ovTab === 'pacing' && renderPacing()}
                            {ovTab === 'gallery' && renderGalleryView()}
                            {ovTab === 'search' && (
                                <SearchPanel bookRoot={bookRoot} compact
                                    onSendToChat={sendSearchToChat} />
                            )}
                        </div>
                    </div>
                )}
                {/* 抽屉背景遮罩（点击关闭） */}
                {(ovTab === 'inspire' || ovTab === 'fragment' || ovTab === 'pacing' || ovTab === 'gallery' || ovTab === 'search') && (
                    <div className="wb-drawer-backdrop" onClick={() => setOvTab('stats')} />
                )}
            </div>
        )
    }

    // ── 全书概览 hub（双栏书页式：左情节 / 右元素+记忆+评分）────
    // 保留备用，已合并入 renderCreateHub
    const renderOverviewHub = () => {
        const finCount = finChapters.length
        const elemCount = Object.values(elements || {}).flat().length
        // 把所有元素打平成一行行列表
        const kindMeta = [
            { key: 'characters', label: '角色', emoji: '' },
            { key: 'items', label: '物品', emoji: '' },
            { key: 'settings', label: '设定', emoji: '' },
            { key: 'locations', label: '地点', emoji: '' },
            { key: 'maps', label: '地图', emoji: '' },
        ]
        const allElems = []
        for (const km of kindMeta) {
            for (const e of (elements[km.key] || [])) {
                allElems.push({ ...e, kind: km.key, emoji: km.emoji, usage: elemUsageCount[e.id] || 0 })
            }
        }
        const CN_NUMS = ['壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖', '拾']

        return (
            <>
                {/* 书头 */}
                <div className="wb-bookhead">
                    <div className="wb-bhtitle">
                        <span className="vol">卷一</span>
                        <span>{bookTitle}</span>
                        <span className="sub">· {settings?.genre || '小说'} · {settings?.era || ''}</span>
                    </div>
                    <div className="wb-bhmeta">
                        <span>情节 <b>{arcList.length}</b></span><span className="sep">|</span>
                        <span>章 <b>{finCount}</b></span><span className="sep">|</span>
                        <span>字 <b>{totalWords.toLocaleString()}</b></span><span className="sep">|</span>
                        <span>均分 <b>{overallAvg ? overallAvg.toFixed(3) : '—'}</b></span><span className="sep">|</span>
                        <span>污染 <b style={{ color: pollutedCount ? 'var(--cinnabar)' : 'var(--green)' }}>{pollutedCount}</b></span>
                    </div>
                </div>

                {/* 双栏书页 */}
                <div className="wb-page">
                    {/* 左栏：情节脉络 */}
                    <div className="wb-col wb-col-left">
                        <div className="wb-rubric">情节脉络</div>
                        <div className="wb-scroll">
                            {arcList.length === 0 ? (
                                <div style={{ fontSize: 12, color: 'var(--ink-mute)', padding: 20, textAlign: 'center' }}>
                                    还没有情节——去「写作」创建第一个情节。
                                </div>
                            ) : (
                                arcList.map((a, i) => {
                                    const sc = arcScores(a)
                                    const ls = ladderState(a)
                                    const isActive = a.id === activeArcId
                                    const statusCls = a.status === 'done' ? '' : a.status === 'writing' ? 'doing' : 'todo'
                                    const statusText = a.status === 'done' ? '已完成' : a.status === 'writing' ? '进行中' : '待创作'
                                    const chCount = (a.chapters || []).length
                                    const l1 = a.l1 || (a.state?.levels?.l1?.text) || '（未填写 l1）'
                                    return (
                                        <div key={a.id} className={`wb-arcitem ${isActive ? 'active' : ''}`}
                                            onClick={() => { setActiveArcId(a.id); setTab('write') }}>
                                            <div className="wb-arctop">
                                                <span className="wb-arcname">{a.name}</span>
                                                <span className="wb-arcrange">{arcRange(a)}</span>
                                                <span className={`wb-arcstatus ${statusCls}`}>{statusText}</span>
                                            </div>
                                            <div className="wb-arcl1">{l1}</div>
                                            <div className="wb-arcprog">
                                                <div className="wb-ladder">
                                                    {ls.map((s, j) => (
                                                        <span key={j} className={`l ${s}`}>l{j + 1}</span>
                                                    ))}
                                                </div>
                                                {sc && (
                                                    <div className="wb-arcscore">
                                                        <span>意图 <b>{sc.intent.toFixed(3)}</b></span>
                                                        <span>质量 <b>{sc.quality.toFixed(3)}</b></span>
                                                        <span>综合 <b>{sc.overall.toFixed(3)}</b></span>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    )
                                })
                            )}
                        </div>
                    </div>

                    {/* 右栏：元素 + 记忆 + 评分图 */}
                    <div className="wb-col wb-col-right" style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                        <div className="wb-rubric">人物 · 物事 · 方域</div>
                        <div className="wb-scroll" style={{ marginBottom: 4 }}>
                            {allElems.length === 0 ? (
                                <div style={{ fontSize: 11, color: 'var(--ink-mute)', padding: 12, textAlign: 'center' }}>
                                    还没有元素
                                </div>
                            ) : (
                                allElems.map(e => (
                                    <div key={e.id} className="wb-elemrow"
                                        onClick={() => setGalleryExpanded(galleryExpanded === e.id ? null : e.id)}>
<span className="en">{e.name}</span>
                                        <span className="ed">{e.desc || '（无描述）'}</span>
                                        <span className="eu">{e.usage} 情节</span>
                                    </div>
                                ))
                            )}
                        </div>

                        {/* 快速子页入口：灵感工坊 / 片段扩写 / 节奏雷达 */}
                        <div className="wb-rubric" style={{ marginTop: 4 }}>
                            子页 · <span style={{ letterSpacing: 0, fontSize: 10.5 }}>
                                <span style={{ cursor: 'pointer', color: ovTab === 'inspire' ? 'var(--cinnabar)' : 'var(--dai-dark)' }}
                                    onClick={() => setOvTab(ovTab === 'inspire' ? 'stats' : 'inspire')}>
                                    {ovTab === 'inspire' ? '← 返回概览' : '灵感工坊'}
                                </span>
                                {' · '}
                                <span style={{ cursor: 'pointer', color: ovTab === 'fragment' ? 'var(--cinnabar)' : 'var(--dai-dark)' }}
                                    onClick={() => setOvTab(ovTab === 'fragment' ? 'stats' : 'fragment')}>
                                    {ovTab === 'fragment' ? '← 返回概览' : '片段扩写'}
                                </span>
                                {' · '}
                                <span style={{ cursor: 'pointer', color: ovTab === 'pacing' ? 'var(--cinnabar)' : 'var(--dai-dark)' }}
                                    onClick={() => setOvTab(ovTab === 'pacing' ? 'stats' : 'pacing')}>
                                    {ovTab === 'pacing' ? '← 返回概览' : '节奏雷达'}
                                </span>
                            </span>
                        </div>
                        <div className="wb-rubric" style={{ display: 'none' }}></div>

                        {/* 子页内容（替代原来的 tab 切换，显示在整个右栏+左栏？简化：只在右栏显示摘要，点跳转到完整页） */}
                        {ovTab === 'pacing' && (
                            <div className="wb-scroll" style={{ flex: 1 }}>
                                {renderPacing()}
                            </div>
                        )}
                        {ovTab === 'stats' && (
                            <>
                                <div className="wb-rubric">记忆 · 批注</div>
                                <div className="wb-memlist">
                                    <div className="wb-memitem">记忆功能在灵感工坊中维护，此处为快速预览</div>
                                </div>

                                <div className="wb-rubric">双评分走势</div>
                                <div className="wb-scorewrap">
                                    {finChapters.length > 0 ? (
                                        <ChartWrapper option={{
                                            tooltip: { trigger: 'axis' },
                                            legend: { show: false },
                                            grid: { left: 30, right: 8, top: 10, bottom: 20 },
                                            xAxis: { type: 'category', data: finChapters.map(c => `第${c.num}章`), axisLabel: { fontSize: 9, color: '#A89F8C' } },
                                            yAxis: { type: 'value', min: 0, max: 1, axisLabel: { show: false }, splitLine: { lineStyle: { color: '#E4DCCB', type: 'dashed' } } },
                                            series: [
                                                { name: '综合', type: 'line', data: finChapters.map(c => c.overall), symbolSize: 3, lineStyle: { width: 1.5, color: '#4a4034' } },
                                                { name: '质量', type: 'line', data: finChapters.map(c => c.quality_score), symbolSize: 2, lineStyle: { width: 1, color: '#8a7a5a', type: 'dashed' } },
                                                { name: '意图', type: 'line', data: finChapters.map(c => c.intent_score), symbolSize: 2, lineStyle: { width: 1, color: '#a89f8c', type: 'dotted' } },
                                            ],
                                        }} height={80} />
                                    ) : (
                                        <div style={{ fontSize: 10.5, color: 'var(--ink-mute)', textAlign: 'center', padding: 10 }}>暂无评分数据</div>
                                    )}
                                    <div className="wb-scorecap">
                                        <span><span style={{ display: 'inline-block', width: 8, height: 2, background: '#4a4034', verticalAlign: 'middle', marginRight: 3 }}></span>综合 {overallAvg ? overallAvg.toFixed(3) : '—'}</span>
                                        <span><span style={{ display: 'inline-block', width: 8, height: 2, background: '#8a7a5a', verticalAlign: 'middle', marginRight: 3 }}></span>质量</span>
                                        <span><span style={{ display: 'inline-block', width: 8, height: 2, background: '#a89f8c', verticalAlign: 'middle', marginRight: 3 }}></span>意图</span>
                                    </div>
                                </div>
                            </>
                        )}
                    </div>
                </div>

                {/* 全屏子页：灵感工坊 / 片段扩写（占满舞台） */}
                {ovTab === 'inspire' && (
                    <div style={{ position: 'absolute', inset: 0, background: 'var(--paper-deep)', zIndex: 10, padding: 10 }}>
                        <InspireWorkspace bookRoot={bookRoot} elements={elements} arcs={arcs}
                            onRefresh={() => refreshWorkbench(bookRoot)} />
                    </div>
                )}
                {ovTab === 'fragment' && (
                    <div style={{ position: 'absolute', inset: 0, background: 'var(--paper-deep)', zIndex: 10, padding: 10, overflow: 'auto' }}>
                        {renderFragment()}
                    </div>
                )}
            </>
        )
    }

    // ── 系统 hub（文档/检索体检/通道配置/导出/批量生成 五工具合并一个界面）────
    // ── 系统 hub（五条系统项纵向 + 右状态栏）────
    const renderSystemHub = () => {
        const CN_NUMS = ['壹', '贰', '叁', '肆', '伍']
        const presets = apiLib?.text_presets || []
        const curPreset = presets.find(p => p.is_current) || presets.find(p => p.id === apiLib?.current_text_id) || null
        const curModel = curPreset?.fields?.ARK_MODEL_PRO || ''
        const embedModel = apiLib?.embed_config?.fields?.EMBED_MODEL || ''

        // 最近文件（docTree 打平取 4 个）
        const recentFiles = []
        if (docTree && typeof docTree === 'object') {
            for (const [folder, items] of Object.entries(docTree)) {
                if (!Array.isArray(items)) continue
                for (const item of items) {
                    if (item.type === 'file') {
                        recentFiles.push({ folder, name: item.name, path: item.path })
                    } else if (item.children) {
                        for (const sub of item.children) {
                            if (sub.type === 'file') recentFiles.push({ folder: item.name, name: sub.name, path: sub.path })
                        }
                    }
                }
            }
        }
        const recent4 = recentFiles.slice(0, 4)

        // 批量进度（从 batchStatus.progress 取，后端结构：{done, target, arc_name, step, messages}）
        const bp = batchStatus?.progress || {}
        const batchProg = batchStatus ? {
            done: bp.done || 0,
            total: bp.target || 0,
            current: bp.arc_name || '',
            step: bp.step || '',
            elapsed: '',
        } : null
        const batchPct = batchProg && batchProg.total ? Math.round(batchProg.done / batchProg.total * 100) : 0

        // 系统项列表
        const sysItems = [
            {
                key: 'docedit', no: '壹', name: '文档', desc: '设定集 · 大纲 · 正文 · 在线编辑',
                status: docDirty ? 'warn' : 'good',
                statusText: docDirty ? '未保存' : '已保存',
                body: (
                    <>
                        {recent4.length > 0 ? recent4.map(f => (
                            <div key={f.path} className="wb-filerow" onClick={() => { setDocSelPath(f.path); setSyTab('docedit') }}>
                                <span className="fn">{f.folder} / {f.name}</span>
                                <span className="ft">{f.folder}</span>
                            </div>
                        )) : (
                            <div style={{ fontSize: 10.5, color: 'var(--ink-mute)' }}>文档目录加载中…</div>
                        )}
                        {recentFiles.length > 4 && (
                            <div style={{ fontSize: 10, color: 'var(--ink-mute)' }}>…共 {recentFiles.length} 个文件</div>
                        )}
                        <div className="wb-siaction">
                            <button className="wb-btn" onClick={() => setSyTab('docedit')}>打开编辑器</button>
                            <button className="wb-btn">新建文件</button>
                        </div>
                    </>
                ),
            },
            {
                key: 'health', no: '贰', name: '检索体检', desc: '六源联邦检索 · 健康状态',
                status: 'warn', statusText: '3 / 6 就绪',
                body: (
                    <>
                        <div className="wb-sixsrc">
                            <div className="wb-srcrow"><span className="sdot ok"></span><span className="sn">本书索引</span><span className="sv">{finChapters.length} 章</span></div>
                            <div className="wb-srcrow"><span className="sdot ok"></span><span className="sn">语料持久</span><span className="sv">青山 500</span></div>
                            <div className="wb-srcrow"><span className="sdot ok"></span><span className="sn">模板库</span><span className="sv">已有</span></div>
                            <div className="wb-srcrow"><span className="sdot no"></span><span className="sn">CSV 数据</span><span className="sv">未配置</span></div>
                            <div className="wb-srcrow"><span className="sdot no"></span><span className="sn">参考文档</span><span className="sv">空</span></div>
                            <div className="wb-srcrow"><span className="sdot warn"></span><span className="sn">Web 搜索</span><span className="sv">key过期</span></div>
                        </div>
                        <div className="wb-siaction">
                            <button className="wb-btn" onClick={() => setSyTab('health')}>查看详情</button>
                            <button className="wb-btn">重建索引</button>
                        </div>
                    </>
                ),
            },
            {
                key: 'api', no: '叁', name: '通道配置', desc: 'API 预设库 · 当前生效',
                status: 'good', statusText: '连接正常',
                body: (
                    <>
                        <div className="wb-apipresets">
                            {curPreset && (
                                <div className="wb-apipreset current">
                                    <span className="pdot"></span>
                                    <span className="pn">{curPreset.name}</span>
                                    <span className="pu">{curModel || '文字生成'}</span>
                                </div>
                            )}
                            <div className="wb-apipreset">
                                <span className="pdot" style={{ background: 'var(--amber)' }}></span>
                                <span className="pn">BGE-M3 · 硅基</span>
                                <span className="pu">{embedModel || '向量嵌入'}</span>
                            </div>
                        </div>
                        <div style={{ fontSize: 10, color: 'var(--ink-mute)', marginTop: 4 }}>
                            共 {presets.length} 个预设
                        </div>
                        <div className="wb-siaction">
                            <button className="wb-btn" onClick={() => setSyTab('api')}>管理预设</button>
                            <button className="wb-btn">测试连接</button>
                        </div>
                    </>
                ),
            },
            {
                key: 'export', no: '肆', name: '导出', desc: '备份 · 迁移 · 一键下载',
                status: '', statusText: '就绪',
                body: (
                    <>
                        <div style={{ marginBottom: 4 }}>
                            导出：基本设定 · 元素清单 · 情节（阶梯+评分+正文）
                        </div>
                        <div className="wb-siaction">
                            <button className="wb-btn primary" disabled={exporting} onClick={() => setSyTab('export')}>
                                {exporting ? '导出中…' : '导出 JSON'}
                            </button>
                            <button className="wb-btn" onClick={() => setSyTab('export')}>更多格式</button>
                        </div>
                    </>
                ),
            },
            {
                key: 'batch', no: '伍', name: '批量生成', desc: '逐情节流水线 · 无人值守',
                status: batchStatus ? 'warn' : '',
                statusText: batchStatus ? (batchStatus.status === 'running' ? '进行中' : batchStatus.status) : '就绪',
                body: (
                    <>
                        {batchStatus ? (
                            <>
                                <div className="wb-batchprog">
                                    <div className="wb-batchbar"><i style={{ width: `${batchPct}%` }}></i></div>
                                    <span>{batchProg.done}/{batchProg.total} 章</span>
                                </div>
                                <div className="wb-batchinfo">
                                    <span>当前：<b>{batchProg.current}</b></span>
                                    <span>已用 <b>{batchProg.elapsed}</b></span>
                                </div>
                            </>
                        ) : (
                            <div style={{ fontSize: 10.5, color: 'var(--ink-mute)' }}>暂无进行中的批量任务</div>
                        )}
                        <div className="wb-siaction">
                            {batchStatus?.status === 'running' ? (
                                <button className="wb-btn danger" onClick={async () => { if (batchTaskId) { try { await phAiCancelTask(batchTaskId) } catch {} } else { setSyTab('batch') } }}>⏸ 暂停</button>
                            ) : (
                                <button className="wb-btn primary" onClick={() => setSyTab('batch')}>▶ 新建批量</button>
                            )}
                            <button className="wb-btn" onClick={() => setSyTab('batch')}>查看详情</button>
                        </div>
                    </>
                ),
            },
        ]

        return (
            <>
                {/* 书头 */}
                <div className="wb-bookhead">
                    <div className="wb-bhtitle">
                        <span className="vol" style={{ color: 'var(--xuan-dark)' }}>卷三</span>
                        <span>系统</span>
                        <span className="sub">· 卷末杂录 · 五项工具</span>
                    </div>
                    <div className="wb-bhmeta">
                        <span>检索 <b style={{ color: 'var(--amber)' }}>3/6</b></span><span className="sep">|</span>
                        <span>文件 <b>{recentFiles.length || '—'}</b></span><span className="sep">|</span>
                        <span>预设 <b>{presets.length}</b></span><span className="sep">|</span>
                        {batchStatus ? (
                            <span>批量 <b style={{ color: 'var(--amber)' }}>{batchPct}%</b></span>
                        ) : (
                            <span>批量 <b>空闲</b></span>
                        )}
                    </div>
                </div>

                {/* 双栏书页 */}
                <div className="wb-page">
                    {/* 左栏：五条系统项 */}
                    <div className="wb-col wb-col-left">
                        <div className="wb-rubric">五项工具</div>
                        <div className="wb-scroll">
                            {sysItems.map(item => (
                                <div key={item.key} className="wb-sysitem" onClick={() => setSyTab(item.key)}>
                                    <div className="wb-sihead">
                                        <span className="wb-sino">{item.no}</span>
                                        <span className="wb-siname">{item.name}</span>
                                        <span className="wb-sidesc">{item.desc}</span>
                                        <span className={`wb-sistatus ${item.status}`}>{item.statusText}</span>
                                    </div>
                                    <div className="wb-sibody">{item.body}</div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* 右栏：系统状态 + 日志 */}
                    <div className="wb-col wb-col-right">
                        <div className="wb-rubric">系统状态</div>
                        <div className="wb-sysside">
                            <div className="wb-syskv">
                                <span className="k">书</span><span className="v">{bookTitle || '—'}</span>
                                <span className="k">情节</span><span className="v">{arcList.length}</span>
                                <span className="k">已落盘</span><span className="v">{finChapters.length} 章</span>
                                <span className="k">总字数</span><span className="v">{totalWords.toLocaleString()}</span>
                                <span className="k">元素数</span><span className="v">{Object.values(elements || {}).flat().length}</span>
                                <span className="k">存储</span><span className="v">—</span>
                                <span className="k">污染章</span><span className={`v ${pollutedCount ? 'bad' : 'good'}`}>{pollutedCount}</span>
                                <span className="k">均分</span><span className="v">{overallAvg ? overallAvg.toFixed(3) : '—'}</span>
                            </div>

                            <div className="wb-rubric" style={{ marginTop: 2 }}>最近日志</div>
                            <div className="wb-logmini">
                                <div className="wb-logitem">
                                    <span className="lt">最近活动</span>
                                    工作台最近操作记录（待接入日志系统）
                                </div>
                                <div className="wb-logitem">
                                    <span className="lt">提示</span>
                                    详细日志见 prompt-harness/logs/ 目录
                                </div>
                            </div>

                            <div className="wb-rubric" style={{ marginTop: 2 }}>快捷操作</div>
                            <div className="wb-btnrow" style={{ flexWrap: 'wrap', gap: 4 }}>
                                <button className="wb-btn">重建索引</button>
                                <button className="wb-btn">清缓存</button>
                                <button className="wb-btn">导日志</button>
                                <button className="wb-btn">重连API</button>
                            </div>
                        </div>
                    </div>
                </div>

                {/* 子页全屏展开（点系统项时显示在上面） */}
                {syTab !== 'docedit' && syTab !== 'health' && syTab !== 'api' && syTab !== 'export' && syTab !== 'batch' ? null : (
                    <div style={{
                        position: 'absolute', inset: 0, background: 'var(--paper-deep)', zIndex: 20,
                        padding: 16, overflow: 'auto',
                    }}>
                        <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 12 }}>
                            <button className="wb-btn" onClick={() => setSyTab('overview')}>← 返回系统总览</button>
                            <span style={{ fontFamily: 'var(--font-serif)', fontSize: 16, fontWeight: 700 }}>
                                {syTab === 'docedit' && '文档'}
                                {syTab === 'health' && '检索体检'}
                                {syTab === 'api' && '通道配置'}
                                {syTab === 'export' && '导出'}
                                {syTab === 'batch' && '批量生成'}
                            </span>
                        </div>
                        {syTab === 'docedit' && renderDocEdit()}
                        {syTab === 'health' && <SearchHealthPanel bookRoot={bookRoot} />}
                        {syTab === 'api' && renderApiConfig()}
                        {syTab === 'export' && renderExport()}
                        {syTab === 'batch' && renderBatch()}
                    </div>
                )}
            </>
        )
    }

    // 计算全局汇总数据（供书头和概览用）
    // 【v7.8.3 显示修复】情节按「情节N」数字排序（原按 arcs.json 数组顺序 → 乱序）；
    // 章范围：有 finalized（真实落盘章号）显示 min–max，否则有 start+章数显示区间，否则「未开始」
    const arcNo = (name) => { const m = String(name || '').match(/(\d+)/); return m ? parseInt(m[1], 10) : 0 }
    const arcRange = (a) => {
        const fin = a.finalized && typeof a.finalized === 'object' ? Object.values(a.finalized).map(Number) : []
        const l3n = a.state?.levels?.l3?.data?.chapters?.length || 0
        if (fin.length) {
            // 完整范围 = 落盘章号区间 + 未落盘草稿章（按 l3 章数推算）
            const lo = Math.min(...fin)
            const hi = Math.max(...fin) + Math.max(0, l3n - fin.length)
            return `第 ${lo}–${hi} 章`
        }
        const start = a.start_chapter
        if (start && l3n > 0) return `第 ${start}–${start + l3n - 1} 章`
        if (l3n > 0) return `草稿 ${l3n} 章`  // 有草稿章纲但未落盘、无起始号
        return '未开始'
    }
    const arcList = [...(arcs.arcs || [])].sort((a, b) => arcNo(a.name) - arcNo(b.name))
    // 情节内第 idx 章的全局章号：已落盘用 finalized 章号；未落盘按最后一个落盘章号推算；
    // 有 start_chapter 则 start+idx；否则回退情节内序号。
    const stripCh = (t) => (t || '').replace(/^第\s*\d+\s*章[\s：:、．.，,]?/, '')
    const arcChapterNum = (arc, idx) => {
        const fin = arc?.finalized && typeof arc.finalized === 'object' ? arc.finalized : {}
        const finIdx = Object.keys(fin).map(Number).filter(k => fin[k] != null).sort((a, b) => a - b)
        if (finIdx.length) {
            if (fin[idx] != null) return fin[idx]
            const last = finIdx[finIdx.length - 1]
            return fin[last] + (idx - last)
        }
        if (arc?.start_chapter) return arc.start_chapter + idx
        return idx + 1
    }
    const arcChapterLabel = (arc, idx) => `第 ${arcChapterNum(arc, idx)} 章`
    const finChapters = arcList.flatMap(a => (a.chapters || []).map(c => ({ ...c, arcName: a.name, arcId: a.id })))
    const totalWords = finChapters.reduce((s, c) => s + (c.text ? c.text.length : 0), 0)
    const avg = arr => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0)
    const overallAvg = avg(finChapters.filter(c => c.overall != null).map(c => c.overall))
    const pollutedCount = finChapters.filter(c => c.polluted).length

    // 元素使用计数（供概览右栏用）
    const elemUsageCount = {}
    arcList.forEach(a => {
        ;['characters', 'items', 'settings', 'maps'].forEach(k => {
            ;(a.selected?.[k] || []).forEach(id => { elemUsageCount[id] = (elemUsageCount[id] || 0) + 1 })
        })
    })

    // 阶梯状态判断
    const ladderState = (arc) => {
        const lv = arc?.state?.levels || {}
        const has = (k, key) => {
            const v = lv[k] || {}
            if (k === 'l4') return v.scenes?.length > 0
            if (k === 'l3') return !!(v.data || v.text)
            return !!v.text
        }
        const conf = k => !!(lv[k]?.confirmed)
        return ['l1', 'l2', 'l3', 'l4', 'l5'].map(k => conf(k) ? 'ok' : has(k) ? 'half' : 'no')
    }

    // 情节评分（已落盘章的均分）
    const arcScores = (arc) => {
        const chs = arc.chapters || []
        if (!chs.length) return null
        return {
            intent: avg(chs.filter(c => c.intent_score != null).map(c => c.intent_score)),
            quality: avg(chs.filter(c => c.quality_score != null).map(c => c.quality_score)),
            overall: avg(chs.filter(c => c.overall != null).map(c => c.overall)),
        }
    }

    // 元素速览数据（原 renderCreateHub 局部；随情节侧栏外移提升到组件级，侧栏/中栏共用）
    const kindMeta = [
        { key: 'characters', label: '角色', emoji: '' },
        { key: 'items', label: '物品', emoji: '' },
        { key: 'settings', label: '设定', emoji: '' },
        { key: 'locations', label: '地点', emoji: '' },
        { key: 'maps', label: '地图', emoji: '' },
    ]
    const allElems = []
    {
        const selSet = {
            characters: new Set(activeArc?.selected?.characters || []),
            items: new Set(activeArc?.selected?.items || []),
            settings: new Set(activeArc?.selected?.settings || []),
        }
        for (const km of kindMeta) {
            for (const e of (elements[km.key] || [])) {
                const inArc = selSet[km.key]?.has(e.id)
                allElems.push({ ...e, kind: km.key, emoji: km.emoji, usage: elemUsageCount[e.id] || 0, inArc })
            }
        }
        // 排序：本情节参与的在前，然后按使用次数
        allElems.sort((a, b) => (b.inArc ? 1 : 0) - (a.inArc ? 1 : 0) || b.usage - a.usage)
    }

    // 情节侧栏（三段式：情节树 + 元素速览 + 节奏迷你）——布局调整：挂到 wb-body 层、创作助手左侧
    const renderArcBar = () => (
                <aside className="wb-arcbar">
                    {/* 情节模式左栏（原有内容） */}

                    {/* 第一段：情节列表 */}
                    <div className="wb-arbsec arcs">
                        <div className="wb-arbhead">
                            情节脉络
                            <span className="cnt">{arcList.length} 情节</span>
                        </div>
                        <div className="wb-arbbody">
                            <div className="wb-ablist">
                                {arcList.length === 0 ? (
                                    <div style={{ fontSize: 11, color: 'var(--ink-mute)', padding: 20, textAlign: 'center' }}>
                                        还没有情节
                                    </div>
                                ) : (
                                    arcList.map((a, i) => {
                                        const sc = arcScores(a)
                                        const als = ladderState(a)
                                        const isActive = a.id === activeArcId
                                        const statusCls = a.status === 'done' ? 'done' : a.status === 'writing' ? 'doing' : 'todo'
                                        const statusText = a.status === 'done' ? '完' : a.status === 'writing' ? '进' : '待'
                                        const chCount = (a.chapters || []).length
                                        return (
                                            <div key={a.id} className={`wb-abitem ${isActive ? 'active' : ''} ${discuss?.kind === 'arc' && discuss.arcId === a.id ? 'discussing' : ''}`}
                                                onClick={() => { selectArc(a.id); setDiscussTarget({ kind: 'arc', arcId: a.id, name: `情节「${a.name}」`, content: `${a.l1 || ''}\n${a.l2 || ''}`.trim(), icon: '' }) }}>
                                                <div className="abname">{a.name}</div>
                                                <div className="abrange">{arcRange(a)}</div>
                                                <div className="abmini">
                                                    {als.map((s, j) => (
                                                        <span key={j} className={s}></span>
                                                    ))}
                                                </div>
                                                {sc && <span className="abscore">{sc.overall.toFixed(3).replace(/^0/, '')}</span>}
                                                <span className={`abstat ${statusCls}`}>{statusText}</span>
                                                <div className="ab-actions" onClick={e => e.stopPropagation()}>
                                                    <span className="ab-act" title="改名" onClick={() => renameArc(a)}>改名</span>
                                                    <span className="ab-act" title="删除情节" onClick={() => deleteArc(a)}>删除</span>
                                                </div>
                                            </div>
                                        )
                                    })
                                )}
                            </div>
                        </div>
                        <div className="wb-abfoot">
                            <button className="newbtn" onClick={() => setNewArcOpen(true)}>＋ 新建情节</button>
                        </div>
                    </div>

                    {/* 第二段：元素与设定（卡片墙：元素富化卡 + 书级卡，深度融合去重） */}
                    <div className="wb-arbsec elems">
                        <div className="wb-arbhead">
                            元素与设定
                            <span className="cnt">{allElems.length}</span>
                        </div>
                        <div className="wb-arbbody">
                            <div className="wb-ekindlist">
                                {(() => {
                                    // 按类型分组
                                    const groups = [
                                        { key: 'characters', label: '角色', icon: '', list: allElems.filter(e => e.kind === 'characters') },
                                        { key: 'items', label: '物品', icon: '', list: allElems.filter(e => e.kind === 'items') },
                                        { key: 'settings', label: '设定', icon: '', list: allElems.filter(e => e.kind === 'settings') },
                                        { key: 'maps', label: '地图', icon: '', list: allElems.filter(e => e.kind === 'maps' || (e.kind === 'settings' && e.name.includes('图'))) },
                                    ].filter(g => g.list.length > 0)
                                    const sfiles = settingFiles?.files || []
                                    const docOf = (name) => (sfiles.find(f => f.name === name) || {}).content || ''

                                    if (groups.length === 0) {
                                        return <div style={{ fontSize: 10.5, color: 'var(--ink-mute)', textAlign: 'center', padding: '12px 8px' }}>暂无元素</div>
                                    }

                                    return (
                                        <>
                                            {groups.map(g => {
                                                const open = !!elemKindOpen[g.key]
                                                return (
                                                    <div key={g.key} className={`wb-ekind ${open ? 'open' : ''}`}>
                                                        <div className="wb-ekhead" onClick={e => {
                                                            e.stopPropagation()
                                                            setElemKindOpen(prev => ({ ...prev, [g.key]: !prev[g.key] }))
                                                        }}>
<span className="ek-label">{g.label}</span>
                                                            <span className="ek-cnt">{g.list.length}</span>
                                                            <span className="ek-arr">{open ? '▾' : '▸'}</span>
                                                        </div>
                                                        {open && (
                                                            <div className="wb-ekbody">
                                                                <div className="wb-wallgrid">
                                                                    {g.list.map(e => {
                                                                        const isMap = e.kind === 'maps' || (e.kind === 'settings' && (e.name.includes('图') || e.name.includes('布局') || e.name.includes('地图')))
                                                                        const isProto = g.key === 'characters' && !!settings?.protagonist?.name && settings.protagonist.name === e.name
                                                                        const tags = Array.isArray(e.kind === 'settings' ? e.terms : e.alias) ? (e.kind === 'settings' ? e.terms : e.alias) : []
                                                                        return (
                                                                            <div key={e.id}
                                                                                className={`wb-wcard ${e.inArc ? 'in' : ''} ${elemPopId === e.id ? 'active' : ''} ${isMap ? 'is-map' : ''} ${discuss?.kind === 'element' && discuss.elemId === e.id ? 'discussing' : ''}`}
                                                                                onClick={(ev) => {
                                                                                    ev.stopPropagation()
                                                                                    if (isMap) {
                                                                                        setMapViewId(mapViewId === e.id ? null : e.id)
                                                                                        setElemPopId(null)
                                                                                    } else {
                                                                                        setMapViewId(null)
                                                                                        setElemPopId(null)
                                                                                        const tagLine = (Array.isArray(e.kind === 'settings' ? e.terms : e.alias) ? (e.kind === 'settings' ? e.terms : e.alias) : []).join('、')
                                                                                        setDiscussTarget({ kind: 'element', elemId: e.id, name: `${g.label}·${e.name}`, content: `${e.desc || ''}${tagLine ? '\n别名/术语：' + tagLine : ''}`.trim(), icon: e.emoji })
                                                                                    }
                                                                                }}
                                                                                title={`${g.label} · ${e.name}${e.inArc ? ' · 本情节参与' : ''}`}>
                                                                                <div className="wc-head">
<span className="wc-name">{e.name.split('（')[0]}</span>
                                                                                    <span className="wc-kind">{g.label}</span>
                                                                                    {e.inArc && <span className="wc-in">本情节✓</span>}
                                                                                    <span className="wc-info" title="查看/编辑/删除"
                                                                                        onClick={(ev) => {
                                                                                            ev.stopPropagation(); setMapViewId(null)
                                                                                            const aliasField = e.kind === 'settings' ? 'terms' : 'alias'
                                                                                            setElemEdit({ kind: e.kind, id: e.id, name: e.name, alias: (Array.isArray(e[aliasField]) ? e[aliasField] : []).join('、'), desc: e.desc || '' })
                                                                                            setElemPopId(elemPopId === e.id ? null : e.id)
                                                                                        }}>ⓘ</span>
                                                                                </div>
                                                                                {e.desc && <div className="wc-desc">{e.desc}</div>}
                                                                                {tags.length > 0 && (
                                                                                    <div className="wc-tags">
                                                                                        {tags.slice(0, 5).map(t => <span key={t} className="wc-tag">{t}</span>)}
                                                                                    </div>
                                                                                )}
                                                                                {isProto && (
                                                                                    <div className="wc-merge">
                                                                                        主角卡 · 欲望：{settings?.protagonist?.desire || '—'} ／ 缺陷：{settings?.protagonist?.flaw || '—'}
                                                                                    </div>
                                                                                )}
                                                                            </div>
                                                                        )
                                                                    })}
                                                                </div>
                                                            </div>
                                                        )}
                                                    </div>
                                                )
                                            })}
                                            {/* 书级卡：世界观 / 核心剧情 / 文风基调（文档并入元素后的剩余书级信息） */}
                                            <div className="wb-bcards">
                                                <div className="wb-bhead">书级</div>
                                                <details className="wb-bcard">
                                                    <summary>世界观</summary>
                                                    {settings?.role_setting && <div className="wb-bcore">{settings.role_setting}</div>}
                                                    {docOf('世界观') ? <pre className="wb-bpre">{docOf('世界观')}</pre> : <div className="wb-docempty">（暂无世界观文档）</div>}
                                                </details>
                                                <details className="wb-bcard">
                                                    <summary>核心剧情</summary>
                                                    {docOf('核心剧情') ? <pre className="wb-bpre">{docOf('核心剧情')}</pre> : <div className="wb-docempty">（暂无核心剧情文档）</div>}
                                                </details>
                                                <details className="wb-bcard">
                                                    <summary>文风基调</summary>
                                                    {settings?.style ? <pre className="wb-bpre">{settings.style}</pre> : <div className="wb-docempty">（未生成）</div>}
                                                </details>
                                            </div>
                                        </>
                                    )
                                })()}
                            </div>
                        </div>
                    </div>

                    {/* 第三段：节奏概览（点击展开完整雷达） */}
                    <div className={`wb-arbsec pacing ${paceExpand ? 'expanded' : ''}`}>
                        <div className="wb-arbhead" style={{ cursor: 'pointer' }}
                            onClick={() => setPaceExpand(!paceExpand)}>
                            节奏概览
                            <span className="cnt">{finChapters.length} 章 · {paceExpand ? '收起' : '展开'}</span>
                        </div>
                        {!paceExpand ? (
                            <div className="wb-pacemini">
                                <div className="pm-bars">
                                    {Array.from({ length: Math.min(arcList.length, 20) }).map((_, i) => {
                                        const a = arcList[i]
                                        const chs = a?.chapters?.length || 0
                                        const h = Math.max(8, Math.min(32, chs * 6 + 8))
                                        const isCur = a?.id === activeArcId
                                        const isDone = a?.status === 'done'
                                        const cls = isDone ? 'done' : isCur ? 'cur' : ''
                                        return <div key={i} className={`pm-bar ${cls}`} style={{ height: h + 'px' }} title={a?.name || ''} />
                                    })}
                                    {arcList.length === 0 && (
                                        <div style={{ fontSize: 10, color: 'var(--ink-mute)', textAlign: 'center', width: '100%', padding: '8px 0' }}>暂无</div>
                                    )}
                                </div>
                                <div className="pm-stats">
                                    <span>字 <b>{(totalWords / 1000).toFixed(0)}k</b></span>
                                    <span>均 <b>{overallAvg ? overallAvg.toFixed(2).replace(/^0/, '') : '—'}</b></span>
                                    <span style={{ color: pollutedCount ? 'var(--cinnabar)' : 'var(--green)' }}>
                                        污 <b>{pollutedCount}</b>
                                    </span>
                                </div>
                            </div>
                        ) : (
                            <div className="wb-pacefull">
                                {/* 展开版：完整节奏图（从 renderPacing 简化） */}
                                <div className="pace-arc-list">
                                    {arcList.map((a, i) => {
                                        const chs = a.chapters || []
                                        const sc = arcScores(a)
                                        return (
                                            <div key={a.id} className={`pace-arc-row ${a.id === activeArcId ? 'cur' : ''}`}>
                                                <div className="pace-arc-name">{a.name}</div>
                                                <div className="pace-arc-bars">
                                                    {chs.length === 0 ? (
                                                        <span className="pace-empty">未开始</span>
                                                    ) : chs.map((c, ci) => (
                                                        <div key={ci} className={`pace-ch-dot ${c.status || ''}`}
                                                            title={`第${ci+1}章 ${c.title || ''}`} />
                                                    ))}
                                                </div>
                                                {sc && <div className="pace-score">{sc.overall.toFixed(2).replace(/^0/, '')}</div>}
                                            </div>
                                        )
                                    })}
                                </div>
                            </div>
                        )}
                    </div>

                    {/* 地图大预览面板（fixed 定位，浮在所有内容之上） */}
                    {mapViewId && (() => {
                        const m = allElems.find(x => x.id === mapViewId)
                        if (!m) return null
                        const places = (m.fields || [])
                            .filter(f => f.name && f.value)
                            .map(f => ({ name: f.name, desc: String(f.value || '').slice(0, 30) }))
                        const extraPlaces = places.length < 3 && m.desc
                            ? m.desc.split(/[。，；\n]/).filter(s => s.length > 2 && s.length < 12).slice(0, 6).map(s => ({ name: s, desc: '' }))
                            : []
                        const allPlaces = [...places, ...extraPlaces].slice(0, 12)
                        return (
                            <div className="wb-mapview wb-mapview-fixed" onClick={e => e.stopPropagation()}>
                                <div className="mv-head">
                                    <span className="mv-title">{m.name}</span>
                                    <span className="mv-close" onClick={() => setMapViewId(null)}>✕</span>
                                </div>
                                <div className="mv-canvas">
                                    <svg viewBox="0 0 400 300" className="mv-svg" preserveAspectRatio="xMidYMid meet">
                                        <rect width="400" height="300" fill="#f7f2e6" />
                                        <path d="M0,120 Q40,80 80,110 T160,95 T240,105 T320,90 T400,100 L400,140 L0,140 Z"
                                            fill="#d6d0c0" opacity="0.4" />
                                        <path d="M0,140 Q60,100 120,130 T240,115 T360,125 T400,115 L400,160 L0,160 Z"
                                            fill="#c8c0a8" opacity="0.3" />
                                        <path d="M50,30 Q80,80 60,140 T90,220 T70,280"
                                            stroke="#8ba4b0" strokeWidth="8" fill="none" opacity="0.5" strokeLinecap="round" />
                                        <path d="M350,20 Q320,70 340,130 T310,210 T330,280"
                                            stroke="#8ba4b0" strokeWidth="6" fill="none" opacity="0.4" strokeLinecap="round" />
                                        <rect x="140" y="130" width="120" height="80" fill="#f0e6d0" stroke="#8b7355" strokeWidth="1.5" rx="2" />
                                        <line x1="200" y1="130" x2="200" y2="210" stroke="#a89070" strokeWidth="0.5" strokeDasharray="3,3" />
                                        <line x1="140" y1="170" x2="260" y2="170" stroke="#a89070" strokeWidth="0.5" strokeDasharray="3,3" />
                                        <rect x="192" y="126" width="16" height="8" fill="#8b7355" rx="1" />
                                        <rect x="192" y="206" width="16" height="8" fill="#8b7355" rx="1" />
                                        {allPlaces.slice(0, 8).map((p, i) => {
                                            const positions = [
                                                [80, 60], [300, 50], [60, 200], [320, 180],
                                                [180, 160], [230, 160], [150, 240], [280, 250]
                                            ]
                                            const [cx, cy] = positions[i] || [100 + i * 40, 150]
                                            return (
                                                <g key={i}>
                                                    <circle cx={cx} cy={cy} r="4" fill="#b8432c" opacity="0.8" />
                                                    <circle cx={cx} cy={cy} r="8" fill="none" stroke="#b8432c" strokeWidth="0.8" opacity="0.4" />
                                                    <text x={cx + 8} y={cy + 3} fontSize="10" fill="#4a3c2a" fontFamily="serif" fontWeight="600">
                                                        {p.name.slice(0, 6)}
                                                    </text>
                                                </g>
                                            )
                                        })}
                                        <g transform="translate(365, 40)">
                                            <circle r="14" fill="none" stroke="#7a6a4f" strokeWidth="0.8" />
                                            <polygon points="0,-10 3,0 0,10 -3,0" fill="#b8432c" />
                                            <text y="-16" textAnchor="middle" fontSize="9" fill="#7a6a4f" fontFamily="serif" fontWeight="700">北</text>
                                        </g>
                                        <g transform="translate(20, 275)">
                                            <line x1="0" y1="0" x2="60" y2="0" stroke="#7a6a4f" strokeWidth="1" />
                                            <line x1="0" y1="-3" x2="0" y2="3" stroke="#7a6a4f" strokeWidth="1" />
                                            <line x1="30" y1="-2" x2="30" y2="2" stroke="#7a6a4f" strokeWidth="0.8" />
                                            <line x1="60" y1="-3" x2="60" y2="3" stroke="#7a6a4f" strokeWidth="1" />
                                            <text x="30" y="14" textAnchor="middle" fontSize="8" fill="#7a6a4f" fontFamily="serif">十里</text>
                                        </g>
                                    </svg>
                                </div>
                                {m.desc && <div className="mv-desc">{m.desc}</div>}
                                {allPlaces.length > 0 && (
                                    <div className="mv-places">
                                        <div className="mv-pk">地点索引</div>
                                        <div className="mv-plist">
                                            {allPlaces.map((p, i) => (
                                                <div key={i} className="mv-pitem">
                                                    <span className="mv-pdot"></span>
                                                    <span className="mv-pname">{p.name}</span>
                                                    {p.desc && <span className="mv-pdesc">{p.desc}</span>}
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        )
                    })()}

                </aside>
    )

    // 元素详情浮层（2026-09-07 提为两模式共用：fixed 定位挂 wb-body 层，书级画布也能弹；支持 isNew 新建表单）
    const renderElemPop = () => {
        const e = elemPopId ? allElems.find(x => x.id === elemPopId) : null
        const isNew = !e && elemEdit?.isNew
        if (!e && !isNew) return null
        const kindMap = { characters: '角色', items: '物品', settings: '设定', locations: '地点', maps: '地图' }
        const kind = e ? e.kind : elemEdit.kind
        const aliasField = kind === 'settings' ? 'terms' : 'alias'
        const editing = isNew || (elemEdit?.id === e.id)
        return (
            <div className="wb-epop" onClick={(ev) => ev.stopPropagation()}>
                <div className="epop-head">
                    <div className="epop-title">
                        {editing ? (
                            <input className="epop-inp" value={elemEdit.name || ''} onChange={ev => setElemEdit({ ...elemEdit, name: ev.target.value })}
                                placeholder="元素名" style={{ width: 150, fontSize: 13, fontWeight: 600 }} />
                        ) : (
                            <div className="epop-name">{e.name}</div>
                        )}
                        <div className="epop-kind">{kindMap[kind] || kind}{e?.inArc && <span className="epop-in">· 本情节参与</span>}{isNew && <span className="epop-in">· 新建</span>}</div>
                    </div>
                    <span className="epop-close" onClick={() => { setElemPopId(null); setElemEdit(null) }}>✕</span>
                </div>
                <div className="epop-row">
                    <span className="epop-k">{kind === 'settings' ? '术语' : '别名'}</span>
                    {editing ? (
                        <input className="epop-inp" value={elemEdit.alias || ''} onChange={ev => setElemEdit({ ...elemEdit, alias: ev.target.value })}
                            placeholder="逗号分隔" style={{ flex: 1 }} />
                    ) : (
                        <span className="epop-v">{(e.kind === 'settings' ? e.terms : e.alias)?.join(' / ') || '—'}</span>
                    )}
                </div>
                <div className="epop-row">
                    <span className="epop-k">简述</span>
                    {editing ? (
                        <textarea className="epop-inp" value={elemEdit.desc || ''} onChange={ev => setElemEdit({ ...elemEdit, desc: ev.target.value })}
                            rows={3} placeholder="身份与关键特质 / 用途 / 与剧情相关的关键设定"
                            style={{ flex: 1, fontSize: 12.5, lineHeight: 1.6, fontFamily: 'inherit', resize: 'vertical' }} />
                    ) : (
                        <span className="epop-v">{e.desc ? (e.desc.slice(0, 200) + (e.desc.length > 200 ? '…' : '')) : '—'}</span>
                    )}
                </div>
                {!isNew && e.fields?.length > 0 && (
                    <div className="epop-fields">
                        {e.fields.slice(0, 6).map((f, i) => (
                            <div key={i} className="epop-field">
                                <span className="ef-k">{f.name}</span>
                                <span className="ef-v">{String(f.value || '').slice(0, 30)}</span>
                            </div>
                        ))}
                    </div>
                )}
                {!isNew && e.relations?.length > 0 && (
                    <div className="epop-row">
                        <span className="epop-k">关系</span>
                        <span className="epop-v">
                            {e.relations.slice(0, 5).map((r, i) => {
                                const target = allElems.find(x => x.id === r.to_id)
                                const tname = target ? target.name.split('（')[0] : r.to_id
                                return (
                                    <span key={i} className="epop-rel">
                                        {r.name} → {tname}
                                    </span>
                                )
                            })}
                        </span>
                    </div>
                )}
                <div className="epop-actions" style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                    {!isNew && (
                        <button style={btnStyle(busy, false)} disabled={busy}
                            onClick={() => { const tagLine = (Array.isArray(e.alias) ? e.alias : []).join('、'); setElemPopId(null); setElemEdit(null); setDiscussTarget({ kind: 'element', elemId: e.id, name: `${kindMap[e.kind] || e.kind}·${e.name}`, content: `${e.desc || ''}${tagLine ? '\n别名：' + tagLine : ''}`.trim(), icon: e.emoji }) }}>
                            发 AI 讨论
                        </button>
                    )}
                    {editing ? (
                        <>
                            <button style={btnStyle(busy, true)} disabled={busy} onClick={saveElemEdit}>保存</button>
                            {isNew ? (
                                <button style={btnStyle(false, false)} onClick={() => setElemEdit(null)}>取消</button>
                            ) : (
                                <button style={{ ...btnStyle(busy, false), color: 'var(--cinnabar-d)' }} disabled={busy} onClick={deleteElem}>删除</button>
                            )}
                        </>
                    ) : (
                        <button style={btnStyle(busy, false)} disabled={busy}
                            onClick={() => setElemEdit({ kind, id: e.id, name: e.name, alias: (Array.isArray(e[aliasField]) ? e[aliasField] : []).join('、'), desc: e.desc || '' })}>
                            编辑
                        </button>
                    )}
                </div>
            </div>
        )
    }

    // 主布局：薄顶栏 + 创作(左侧栏+中间+右操作) + 系统 / 右侧创作助手常驻
    return (
        <div className="wb-app">
            {/* 薄顶栏（两模块 + 中央快捷入口） */}
            <header className="wb-topbar">
                <div className="wb-brand">
                    <span className="seal">稿</span>
                    <div className="name">AInovel <em>Harness</em></div>
                </div>
                {/* 返回主页：放在品牌区与创作区之间，按钮加大 */}
                <a href="#/" className="wb-homebtn">⇦ 主页</a>
                {/* 返回初始化页面（书未初始化时显示） */}
                {initStatus && !initStatus.initialized && (
                    <a href="#/create-book" className="wb-homebtn" style={{ marginLeft: 4 }}>初始化</a>
                )}
                <div className="wb-mswitch">
                    <button type="button" className={`wb-mbtn wb-mb-dai ${tab === 'create' ? 'active' : ''}`}
                        onClick={() => setTab('create')}>
                        创作
                    </button>
                    <button type="button" className={`wb-mbtn wb-mb-xuan ${tab === 'adapt' ? 'active' : ''}`}
                        onClick={() => setTab('adapt')}>
                        改编
                    </button>
                    <button type="button" className={`wb-mbtn wb-mb-xuan ${tab === 'system' ? 'active' : ''}`}
                        onClick={() => { setTab('system'); setSyTab('overview') }}>
                        系统
                    </button>
                </div>
                <div className="wb-toputils">
                    {/* 文字模型预设切换器 */}
                    <div className="wb-presetwrap" onClick={e => e.stopPropagation()}>
                        <button type="button" className="wb-presetbtn"
                            onClick={() => setPresetOpen(!presetOpen)}
                            title="切换文字模型预设">
<span className="pname">{presetSwitching ? '切换中…' : (currentPreset?.name || '模型预设')}</span>
                            <span className="parrow">▾</span>
                        </button>
                        {presetOpen && (
                            <div className="wb-presetmenu">
                                <div className="wb-pmtitle">文字模型预设</div>
                                {(apiLib?.text_presets || []).map(p => (
                                    <div key={p.id}
                                        className={`wb-pmitem ${p.is_current ? 'current' : ''} ${presetSwitching ? 'disabled' : ''}`}
                                        onClick={() => !presetSwitching && !p.is_current && applyPreset(p.id)}>
                                        <span className="pmname">
                                            <span>{p.name}</span>
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
                    {/* 向量模型预设切换器（独立按钮，多预设） */}
                    <div className="wb-presetwrap" onClick={e => e.stopPropagation()}>
                        <button type="button" className="wb-presetbtn"
                            onClick={() => setEmbedPresetOpen(!embedPresetOpen)}
                            title="切换向量模型预设">
                            <span className="pname">{embedSwitching ? '切换中…' : (currentEmbedPreset?.fields?.EMBED_MODEL || currentEmbedPreset?.name || '向量')}</span>
                            <span className="parrow">▾</span>
                        </button>
                        {embedPresetOpen && (
                            <div className="wb-presetmenu">
                                <div className="wb-pmtitle">向量模型预设</div>
                                {(apiLib?.embed_presets || []).map(p => (
                                    <div key={p.id}
                                        className={`wb-pmitem ${p.is_current ? 'current' : ''} ${embedSwitching ? 'disabled' : ''}`}
                                        onClick={() => !embedSwitching && !p.is_current && applyEmbedPreset(p.id)}>
                                        <span className="pmname">
                                            <span>{p.name}</span>
                                            {p.is_current && <span className="pmmark">✓ 当前</span>}
                                        </span>
                                        <span className="pmmodel">{p.fields?.EMBED_MODEL || ''}</span>
                                    </div>
                                ))}
                                <div className="wb-pmfooter">
                                    <a href="#/api-presets" onClick={() => setEmbedPresetOpen(false)}>管理全部预设 →</a>
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            </header>

            {/* error / notice 浮层 */}
            {error && <div style={{ position: 'fixed', top: 50, left: '50%', transform: 'translateX(-50%)', zIndex: 9999,
                padding: '8px 16px', borderRadius: 4, background: 'var(--cinnabar-wash)', color: 'var(--cinnabar-d)',
                fontSize: 13, border: '1px solid var(--cinnabar-border)', boxShadow: '0 4px 20px rgba(0,0,0,.1)' }}>⚠ {error}</div>}
            {notice && <div style={{ position: 'fixed', top: 50, left: '50%', transform: 'translateX(-50%)', zIndex: 9999,
                padding: '8px 16px', borderRadius: 4, background: 'var(--green-wash)', color: 'var(--green)',
                fontSize: 13, border: '1px solid var(--green-border)', boxShadow: '0 4px 20px rgba(0,0,0,.1)' }}>ℹ {notice}</div>}

            <div className="wb-body">
                {bookLoading ? (
                    <div className="wb-stage" style={{ justifyContent: 'center', alignItems: 'center', color: 'var(--ink-sub)', fontSize: 13 }}>
                        加载当前书…
                    </div>
                ) : !bookRoot ? (
                    <div className="wb-stage" style={{ justifyContent: 'center', alignItems: 'center' }}>
                        <div style={{ textAlign: 'center', maxWidth: 480 }}>
                            <div style={{ fontFamily: 'var(--font-serif)', fontSize: 22, fontWeight: 700, letterSpacing: '.08em', marginBottom: 12 }}>请先选择一本书</div>
                            <div style={{ fontSize: 13, color: 'var(--ink-sub)', marginBottom: 16, lineHeight: 1.8 }}>
                                Harness 工作台：创作 · 系统。先在主页选书或新建测试书。
                            </div>
                            <a href="#/project-select" className="btn" style={{ background: 'var(--dai)', color: 'var(--paper-raised)', border: 'none' }}>去选书 / 新建测试书</a>
                        </div>
                    </div>
                ) : (
                    <>
                        {/* 主舞台区（书级讨论模式不渲染——全部书级内容在 wb-bookleft 左栏，两栏布局 2026-09-07） */}
                        {!(tab === 'create' && bookLevelMode) && (
                            <div className="wb-stage" style={{ position: 'relative' }}>
                                <div className={`wb-view ${tab === 'create' ? 'active' : ''}`}>
                                    {renderCreateHub()}
                                </div>
                                <div className={`wb-view ${tab === 'adapt' ? 'active' : ''}`}>
                                    <AdaptPanel bookRoot={bookRoot} arcs={arcs} onAdvance={() => setTab('create')} />
                                </div>
                                <div className={`wb-view ${tab === 'system' ? 'active' : ''}`}>
                                    {renderSystemHub()}
                                </div>
                            </div>
                        )}

                        {/* 左栏槽位：情节模式 arcbar / 书级模式 wb-bookleft（方案二：单页滚动+锚点） */}
                        {tab === 'create' && !bookLevelMode && renderArcBar()}
                        {tab === 'create' && bookLevelMode && renderBookLeft()}
                        {tab === 'create' && renderElemPop()}

                        {/* 右侧创作助手（常驻，增强版） */}
                        <aside className="wb-aicol">
                            <div className="wb-aihead">
                                <span className="dot"></span>
                                <span className="t">创作助手</span>
                                <span className="ctx">
                                    {bookLevelMode ? '书级讨论' : (tab === 'create' ? (activeArc?.name || '未选情节') : '系统助手')}
                                </span>
                                {/* 书级讨论入口（情节模式侧；书级模式侧的开关在左栏 bk-head 分段钮 2026-09-07） */}
                                {tab === 'create' && !bookLevelMode && (
                                    <button
                                        type="button"
                                        className="btn btn-small"
                                        onClick={() => {
                                            setBookLevelMode(true)
                                            // 切到书级模式时若还没有消息，发引导语开启书级对话
                                            if (messages.length === 0) {
                                                handleChat('我新开了一本书，请你帮我讨论全书的设定、人物、世界观和地图。')
                                            }
                                        }}
                                        style={{ marginLeft: 'auto', fontSize: 11, padding: '2px 8px' }}
                                    >
                                        书级讨论
                                    </button>
                                )}
                            </div>

                            {/* 上下文条（创作模块才显示） */}
                            {tab === 'create' && !bookLevelMode && activeArc && (
                                <div className="wb-aimeta" style={{ paddingTop: 4, paddingBottom: 4 }}>
                                    <span className="wb-ctxchip on">{activeArc.name}</span>
                                    <span className="wb-ctxchip">第{(arcState?.active_chapter ?? 0) + 1}章</span>
                                    <span className="wb-ctxchip">l{(() => {
                                        // 显示最高的已展开层（展开哪层显示哪层）
                                        const order = ['l5', 'l4', 'l3', 'l2', 'l1']
                                        for (const lv of order) {
                                            if (ladderExpanded?.[lv]) return lv.slice(1)
                                        }
                                        return ladderState(activeArc).filter(s => s !== 'no').length + 1 || 1
                                    })()}</span>
                                </div>
                            )}
                            {tab === 'create' && bookLevelMode && (
                                <div className="wb-aimeta" style={{ paddingTop: 4, paddingBottom: 4 }}>
                                    <span className="wb-ctxchip on">书级讨论模式</span>
                                    <span className="wb-ctxchip">讨论全书设定/人物/世界观</span>
                                </div>
                            )}

                            {/* 三态模式切换：定向（讨论） / 自由对话（脑暴） / 扩写（工具） */}
                            <div className="wb-modebar">
                                <button type="button" className={`wb-mode ${aiTab === 'targeted' ? 'active' : ''}`}
                                    onClick={() => setAiTab('targeted')}>定向</button>
                                <button type="button" className={`wb-mode ${aiTab === 'free' ? 'active' : ''}`}
                                    onClick={() => setAiTab('free')}>自由对话</button>
                                <button type="button" className={`wb-mode ${aiTab === 'expand' ? 'active' : ''}`}
                                    onClick={() => setAiTab('expand')}>扩写</button>
                            </div>

                            {/* 定向：访问控制 + 讨论对象 + 对话（对元素/情节/阶梯的讨论修改） */}
                            {aiTab === 'targeted' && (
                                <div className="wb-aibody" style={{ display: 'flex', flexDirection: 'column' }}>
                                    {/* 权限区（AI 参考范围控制） */}
                                    <div className="wb-access">
                                        <div className="wb-acc-hint">
                                            <span>AI 参考范围</span>
                                            <span className="acc-tip" title="控制AI能看到哪些参考资料">ⓘ</span>
                                        </div>
                                        <div className="wb-acc-scale">
                                            <span>纯聊天</span>
                                            <div className="rail"></div>
                                            <span>全开卷</span>
                                        </div>
                                        <div className="wb-acc-tgs">
                                            <span className={`wb-atg ${access.web ? 'on' : ''}`} onClick={() => setAcc('web', !access.web)}>联网</span>
                                            <span className={`wb-atg ${access.book ? 'on' : ''}`} onClick={() => setAcc('book', !access.book)}>本书</span>
                                            <span className={`wb-atg ${access.memory ? 'on' : ''}`} onClick={() => setAcc('memory', !access.memory)}>记忆</span>
                                            <span className={`wb-atg ${access.corpus ? 'on' : ''}`} onClick={() => setAcc('corpus', !access.corpus)}>语料·模板</span>
                                        </div>
                                        <div className="wb-acc-quick">
                                            <span onClick={() => setAccess({ web: false, book: false, memory: false, corpus: false })} title="不给AI任何参考资料，只能看你发的消息">闭卷考</span>
                                            <span onClick={() => setAccess({ web: true, book: true, memory: true, corpus: true })} title="所有参考资料全给AI">全开卷</span>
                                            <span className="rec" onClick={() => setAccess({ web: false, book: true, memory: true, corpus: true })} title="不开联网，其他全开——平时写小说就用这个">写小说</span>
                                        </div>
                                        <details className="wb-acc-sel">
                                            <summary>选择性注入 · 勾选才给助手看（不勾 = 不限制）</summary>
                                            {selOpts?.settings?.length ? (
                                                <div className="wb-sel-row"><span className="sg">设定</span><span className="chips">
                                                    {selOpts.settings.map(v => (
                                                        <span key={v} className={`pick ${selAccess.settings.includes(v) ? 'on' : ''}`} onClick={() => toggleAccessSel('settings', v)}>{v}</span>
                                                    ))}
                                                </span></div>
                                            ) : null}
                                            {selOpts?.arcs?.length ? (
                                                <div className="wb-sel-row"><span className="sg">情节</span><span className="chips">
                                                    {selOpts.arcs.map(a => (
                                                        <span key={a.id} className={`pick ${selAccess.arcs.includes(a.id) ? 'on' : ''}`} onClick={() => toggleAccessSel('arcs', a.id)}>{a.name}</span>
                                                    ))}
                                                </span></div>
                                            ) : null}
                                            {selOpts?.memory?.length ? (
                                                <div className="wb-sel-row"><span className="sg">记忆</span><span className="chips">
                                                    {selOpts.memory.map(m => (
                                                        <span key={m.id} title={m.text} className={`pick ${selAccess.memory.includes(m.id) ? 'on' : ''}`} onClick={() => toggleAccessSel('memory', m.id)}>{m.key || m.text}</span>
                                                    ))}
                                                </span></div>
                                            ) : null}
                                            {(selOpts?.corpus?.length || selOpts?.templates?.length) ? (
                                                <div className="wb-sel-row"><span className="sg">语料·模板</span><span className="chips">
                                                    {(selOpts.corpus || []).map(c => (
                                                        <span key={c} title={c} className={`pick ${selAccess.corpus.includes(c) ? 'on' : ''}`} onClick={() => toggleAccessSel('corpus', c)}>{c.split('/').pop()}</span>
                                                    ))}
                                                    {(selOpts.templates || []).map(t => (
                                                        <span key={t.id} className={`pick ${selAccess.templates.includes(t.id) ? 'on' : ''}`} onClick={() => toggleAccessSel('templates', t.id)}>{t.name}</span>
                                                    ))}
                                                </span></div>
                                            ) : null}
                                        </details>
                                    </div>
                                    {/* 出场元素区 + 场景面板（l4 场景级，l4 展开时显示） */}
                                    {tab === 'create' && ladderExpanded?.l4 && arcState?.levels?.l4?.scenes?.length > 0 && (() => {
                                        const scenes = arcState.levels.l4.scenes
                                        const curSceneIdx = l4Cursor?.sceneIdx ?? 0
                                        const curScene = scenes[curSceneIdx] || scenes[0]
                                        const selectedIds = new Set(curScene.elements || [])
                                        const elemGroups = [
                                            { key: 'characters', label: '角色', icon: '' },
                                            { key: 'items', label: '物品', icon: '' },
                                            { key: 'settings', label: '设定', icon: '' },
                                        ]
                                        const toggleElem = (eid) => {
                                            const next = JSON.parse(JSON.stringify(scenes))
                                            const elems = next[curSceneIdx].elements || []
                                            const has = elems.includes(eid)
                                            next[curSceneIdx].elements = has ? elems.filter(x => x !== eid) : [...elems, eid]
                                            setWbL4Draft(next)
                                        }
                                        return (
                                            <>
                                                {/* 出场元素 */}
                                                <div style={{
                                                    padding: '8px 0',
                                                    borderBottom: '1px solid var(--line-soft)',
                                                    flexShrink: 0,
                                                }}>
                                                    <div style={{
                                                        fontSize: 11,
                                                        fontWeight: 600,
                                                        color: 'var(--ink-sub)',
                                                        marginBottom: 6,
                                                        display: 'flex',
                                                        alignItems: 'center',
                                                        gap: 4,
                                                    }}>
                                                        出场元素
                                                        <span style={{ fontWeight: 400, color: 'var(--ink-mute)', fontSize: 10 }}>
                                                            · 已选 {selectedIds.size} / 共 {flatElements.length}
                                                        </span>
                                                    </div>
                                                    {elemGroups.map(g => {
                                                        const elems = flatElements.filter(e => e.kind === g.key)
                                                        if (elems.length === 0) return null
                                                        return (
                                                            <div key={g.key} style={{ marginBottom: 4 }}>
                                                                <div style={{ fontSize: 10, color: 'var(--ink-mute)', marginBottom: 2 }}>{g.label}</div>
                                                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                                                                    {elems.map(e => {
                                                                        const bound = selectedIds.has(e.id)
                                                                        return (
                                                                            <span key={e.id}
                                                                                onClick={() => toggleElem(e.id)}
                                                                                style={{
                                                                                    padding: '2px 8px',
                                                                                    borderRadius: 12,
                                                                                    fontSize: 11,
                                                                                    cursor: 'pointer',
                                                                                    border: `1px solid ${bound ? 'var(--dai)' : 'var(--line-soft)'}`,
                                                                                    background: bound ? 'var(--dai-wash)' : 'var(--paper-raised)',
                                                                                    color: bound ? 'var(--dai-dark)' : 'var(--ink-sub)',
                                                                                    fontWeight: bound ? 600 : 400,
                                                                                    userSelect: 'none',
                                                                                }}>
                                                                                {e.name || e.id}
                                                                            </span>
                                                                        )
                                                                    })}
                                                                </div>
                                                            </div>
                                                        )
                                                    })}
                                                    {flatElements.length === 0 && (
                                                        <div style={{ fontSize: 11, color: 'var(--ink-mute)' }}>（暂无元素）</div>
                                                    )}
                                                </div>

                                                {/* 场景面板 Tab */}
                                                <div style={{
                                                    display: 'flex',
                                                    borderBottom: '1px solid var(--line-soft)',
                                                    flexShrink: 0,
                                                }}>
                                                    {[
                                                        { k: 'note', l: '备注' },
                                                        { k: 'beat', l: '节拍' },
                                                        { k: 'frag', l: '素材' },
                                                    ].map(t => (
                                                        <button key={t.k}
                                                            onClick={() => setSceneTab(t.k)}
                                                            style={{
                                                                flex: 1,
                                                                padding: '6px 4px',
                                                                fontSize: 11,
                                                                border: 'none',
                                                                background: 'transparent',
                                                                color: sceneTab === t.k ? 'var(--dai-dark)' : 'var(--ink-sub)',
                                                                fontWeight: sceneTab === t.k ? 600 : 400,
                                                                cursor: 'pointer',
                                                                borderBottom: sceneTab === t.k ? '2px solid var(--dai)' : '2px solid transparent',
                                                                marginBottom: -1,
                                                                fontFamily: 'inherit',
                                                            }}>
                                                            {t.l}
                                                        </button>
                                                    ))}
                                                </div>

                                                {/* Tab 内容 */}
                                                <div style={{
                                                    padding: '8px 0',
                                                    borderBottom: '1px solid var(--line-soft)',
                                                    fontSize: 11.5,
                                                    flexShrink: 0,
                                                    maxHeight: 180,
                                                    overflowY: 'auto',
                                                }}>
                                                    {sceneTab === 'note' && (
                                                        <textarea
                                                            value={curScene.scene_note || ''}
                                                            onChange={e => {
                                                                const next = JSON.parse(JSON.stringify(scenes))
                                                                next[curSceneIdx].scene_note = e.target.value
                                                                setWbL4Draft(next)
                                                            }}
                                                            placeholder="场景备注（AI 参考，不进正文）"
                                                            rows={3}
                                                            style={{
                                                                width: '100%',
                                                                boxSizing: 'border-box',
                                                                padding: 6,
                                                                borderRadius: 4,
                                                                border: '1px solid var(--line-soft)',
                                                                fontSize: 11.5,
                                                                resize: 'none',
                                                                background: 'var(--paper)',
                                                                color: 'var(--ink)',
                                                                fontFamily: 'inherit',
                                                                lineHeight: 1.6,
                                                            }}
                                                        />
                                                    )}
                                                    {sceneTab === 'beat' && (
                                                        <div>
                                                            {(curScene.beats || []).map((b, bi) => (
                                                                <div key={bi} style={{
                                                                    display: 'flex',
                                                                    alignItems: 'flex-start',
                                                                    gap: 4,
                                                                    padding: '2px 0',
                                                                    fontSize: 11.5,
                                                                    borderBottom: '1px solid var(--line-soft)',
                                                                }}>
                                                                    <span style={{ color: 'var(--ink-mute)', flexShrink: 0, fontSize: 10, paddingTop: 2 }}>{bi + 1}.</span>
                                                                    <span style={{ flex: 1 }}>{b}</span>
                                                                </div>
                                                            ))}
                                                            {(curScene.beats || []).length === 0 && (
                                                                <div style={{ color: 'var(--ink-mute)', fontSize: 11 }}>（暂无节拍）</div>
                                                            )}
                                                        </div>
                                                    )}
                                                    {sceneTab === 'frag' && (
                                                        <div>
                                                            {(fragments || []).filter(f => f.scene_idx === curSceneIdx).map((f, fi) => (
                                                                <div key={fi} style={{
                                                                    padding: '3px 0',
                                                                    fontSize: 11,
                                                                    borderBottom: '1px solid var(--line-soft)',
                                                                    display: 'flex',
                                                                    gap: 4,
                                                                }}>
                                                                    <span style={{ color: 'var(--dai)', fontWeight: 600, flexShrink: 0 }}>[{f.type}]</span>
                                                                    <span style={{
                                                                        flex: 1,
                                                                        overflow: 'hidden',
                                                                        textOverflow: 'ellipsis',
                                                                        whiteSpace: 'nowrap',
                                                                        color: 'var(--ink-sub)',
                                                                    }}>{(f.content || '').slice(0, 40)}{(f.content || '').length > 40 ? '…' : ''}</span>
                                                                </div>
                                                            ))}
                                                            {(fragments || []).filter(f => f.scene_idx === curSceneIdx).length === 0 && (
                                                                <div style={{ color: 'var(--ink-mute)', fontSize: 11 }}>（暂无素材）</div>
                                                            )}
                                                        </div>
                                                    )}
                                                </div>
                                            </>
                                        )
                                    })()}

                                    {/* 红果短剧参考库：热播剧名+套路，点击设讨论对象；可录剧情 */}
                                    <details className="wb-sd" open={sdOpen}>
                                        <summary onClick={(e) => { e.preventDefault(); setSdOpen(!sdOpen) }}>
                                            红果热播
                                            <span className="wb-sd-count">{sdList ? `${sdList.auto.length + (sdList.user || []).length} 部` : '加载…'}</span>
                                            <span className="wb-sd-hint">点剧名发助手 · 录剧情存参考</span>
                                        </summary>
                                        {sdOpen && (
                                            <div className="wb-sd-body">
                                                <div className="wb-sd-actions">
                                                    <button type="button" className="wb-sd-btn" disabled={sdBusy} onClick={fetchShortDramas}>抓取红果热播</button>
                                                    <button type="button" className="wb-sd-btn" disabled={sdBusy} onClick={() => setSdShowForm(!sdShowForm)}>{sdShowForm ? '✕ 收起' : '录剧情'}</button>
                                                </div>
                                                {sdShowForm && (
                                                    <div className="wb-sd-form">
                                                        <input placeholder="剧名（如 母凭子贵后被皇家宠上天）" value={sdDraft.name} onChange={(e) => setSdDraft({ ...sdDraft, name: e.target.value })} />
                                                        <input placeholder="类型标签，逗号分隔（爱情、古风爱情、日久生情）" value={sdDraft.tags} onChange={(e) => setSdDraft({ ...sdDraft, tags: e.target.value })} />
                                                        <textarea rows={2} placeholder="剧情简介（红果详情界面复制粘贴）" value={sdDraft.intro} onChange={(e) => setSdDraft({ ...sdDraft, intro: e.target.value })} />
                                                        <div className="wb-sd-formrow">
                                                            <button type="button" className="wb-sd-btn primary" disabled={sdBusy} onClick={addShortDrama}>存入短剧库</button>
                                                            <span className="wb-sd-note">存入后创作助手搜语料可参考该剧情</span>
                                                        </div>
                                                    </div>
                                                )}
                                                {sdList ? (
                                                    <div className="wb-sd-list">
                                                        {(sdList.user || []).map(d => (
                                                            <span key={`u-${d.name}`} className={`wb-sd-chip user ${(discuss?.name || '').includes(d.name) ? 'discussing' : ''}`}
                                                                title={`${d.intro || '（无简介）'}`} onClick={() => discussShortDrama(d)}>
                                                                {d.name}
                                                                {(d.tags || []).slice(0, 2).map(t => <i key={t}>{t}</i>)}
                                                            </span>
                                                        ))}
                                                        {(sdList.auto || []).map(d => (
                                                            <span key={`a-${d.name}`} className={`wb-sd-chip ${(discuss?.name || '').includes(d.name) ? 'discussing' : ''}`}
                                                                title={`${d.year}年 · ${(d.tags || []).join('、') || '待看'}`} onClick={() => discussShortDrama(d)}>
                                                                {d.name}
                                                                {(d.tags || []).slice(0, 2).map(t => <i key={t}>{t}</i>)}
                                                            </span>
                                                        ))}
                                                    </div>
                                                ) : (
                                                    <div className="wb-sd-note">短剧库为空——点「抓取红果热播」拉取当前热播剧名。</div>
                                                )}
                                            </div>
                                        )}
                                    </details>
                                    {!activeArcId && !discuss && (
                                        <div style={{ fontSize: 11, color: 'var(--ink-mute)', padding: '4px 2px 6px', lineHeight: 1.6 }}>
                                            还没有情节——描述你的想法，AI 帮你规划并创建第一个情节，再一路写到正文。
                                        </div>
                                    )}
                                    {discuss && (
                                        <div className="wb-discussbar">
<span className="db-name">讨论对象：{discuss.name}</span>
                                            <span className="db-prev">{String(discuss.content || '').slice(0, 50)}</span>
                                            <span className="db-close" title="清除讨论对象" onClick={() => setDiscuss(null)}>✕</span>
                                        </div>
                                    )}
                                    <div className="wb-pickstrip">
                                        <span className="ps-t">点选对象：</span>
                                        {(() => {
                                            // 【2026-09-07】接真实数据（原硬编码演示值）：首个情节 + 首个角色 + 首个设定
                                            const picks = []
                                            const a0 = arcList[0]
                                            if (a0) picks.push({ n: a0.name || '情节1', t: { kind: 'arc', arcId: a0.id, name: `情节「${a0.name || '情节1'}」`, content: a0.l1 || '', icon: '' } })
                                            const c0 = (elements.characters || [])[0]
                                            if (c0) picks.push({ n: c0.name, t: { kind: 'element', elemId: c0.id, name: `角色·${c0.name}`, content: c0.desc || '', icon: '' } })
                                            const s0 = (elements.settings || [])[0]
                                            if (s0) picks.push({ n: s0.name, t: { kind: 'element', elemId: s0.id, name: `设定·${s0.name}`, content: s0.desc || '', icon: '' } })
                                            return picks.map((x, i) => (
                                                <span key={i} className="ps" title={x.t.name} onClick={() => setDiscussTarget(x.t)}>{x.n}</span>
                                            ))
                                        })()}
                                    </div>
                                    {/* 插入工具栏（l4 层级显示） */}
                                    {tab === 'create' && ladderExpanded?.l4 && arcState?.levels?.l4?.scenes?.length > 0 && (
                                        <InsertToolbar
                                            cursor={l4Cursor}
                                            onInsert={(kind) => insertFromToolbar.current?.(kind)}
                                            disabled={!l4Cursor}
                                            sceneName={l4Cursor ? (arcState?.levels?.l4?.scenes?.[l4Cursor.sceneIdx]?.name || `场景${l4Cursor.sceneIdx + 1}`) : ''}
                                        />
                                    )}
                                    <ChatWindow messages={messages} onSend={handleChat} busy={chatBusy}
                                        disabled={!bookRoot}
                                        placeholder={discuss ? `针对「${discuss.name}」说点什么…（会基于它回复/修改）` : '说点什么…（基于访问范围）'}
                                        pendingConfirm={pendingConfirm}
                                        onConfirm={handleConfirmPending}
                                        onCancel={() => setPendingConfirm(null)}
                                        sessions={chatSessions} currentSessionId={chatSessionId}
                                        onNewSession={newChatSession} onSelectSession={switchChatSession} onDeleteSession={deleteChatSession}
                                        onDeleteMessage={deleteMessagePair} compact />
                                </div>
                            )}

                            {/* 自由对话：开放脑暴 · 全量+联网 · 灵感快捷 */}
                            {aiTab === 'free' && (
                                <div className="wb-aibody" style={{ display: 'flex', flexDirection: 'column' }}>
                                    <div className="wb-freeq">
                                        <span className="wb-fq" onClick={() => handleChat('请用「推演」机制，从当前设定延伸三种可能走向')}>推演</span>
                                        <span className="wb-fq" onClick={() => handleChat('请用「类比」机制，找一个同类名作桥段参考')}>类比</span>
                                        <span className="wb-fq" onClick={() => handleChat('请用「反转」机制，把当前设定反转出新设定')}>反转</span>
                                        <span className="wb-fq" onClick={() => handleChat('随便聊聊这本小说的整体思路')}>自由</span>
                                    </div>
                                    <div className="wb-freehint">开放脑暴 · 全量上下文（含联网）· 无需设对象</div>
                                    <ChatWindow messages={messages} onSend={handleChat} busy={chatBusy}
                                        disabled={!bookRoot}
                                        placeholder="随便聊聊…（自由对话 · 全量+联网）"
                                        pendingConfirm={pendingConfirm}
                                        onConfirm={handleConfirmPending}
                                        onCancel={() => setPendingConfirm(null)}
                                        sessions={chatSessions} currentSessionId={chatSessionId}
                                        onNewSession={newChatSession} onSelectSession={switchChatSession} onDeleteSession={deleteChatSession}
                                        onDeleteMessage={deleteMessagePair} compact />
                                </div>
                            )}

                            {/* 扩写面板（压缩版 · 右栏适配） */}
                            {aiTab === 'expand' && (
                                <div className="wb-aibody wb-exppanel">
                                    <div className="wb-exp-head">
                                        <span className="ttl">片段锚定扩写</span>
                                        <span className="sub">只扩写【】内的内容</span>
                                    </div>
                                    <textarea className="wb-exp-input"
                                        value={fragText}
                                        onChange={e => { setFragText(e.target.value); fragReset() }}
                                        placeholder={'粘贴半成品：\n“对白……”\n【要扩写的描写】\n……'}
                                        rows={6} />
                                    <div className="wb-exp-btns">
                                        <button className="wb-btn-sm" disabled={fragBusy} onClick={doFragParse}>解析</button>
                                        <button className="wb-btn-sm" disabled={fragBusy} onClick={doFragUnderstand}>理解</button>
                                        <button className="wb-btn-sm primary" disabled={fragBusy} onClick={doFragExpand}>
                                            {fragBusy ? '…' : '扩写'}
                                        </button>
                                    </div>
                                    {fragParse?.ok && (
                                        <div className="wb-exp-slots">
                                            <div className="wb-exp-slots-title">
                                                解析：{fragParse.n_slots} 个【】
                                            </div>
                                            {(fragParse.slots || []).slice(0, 5).map(s => (
                                                <div key={s.id} className="wb-exp-slot">
                                                    <code>{s.id}</code>
                                                    <span>{s.kind === 'inline' ? '对白内' : '描写'}</span>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                    {fragResult && (
                                        <div className="wb-exp-result">
                                            <div className="wb-exp-slots-title">扩写完成 </div>
                                            <div style={{ fontSize: 11, color: 'var(--ink-mute)' }}>
                                                锚点 {fragResult.verify?.anchor_fidelity?.pass ? '' : ''} ·
                                                覆盖 {fragResult.verify?.coverage?.filled}/{fragResult.verify?.coverage?.total}
                                            </div>
                                            <button className="wb-btn-sm" style={{ marginTop: 6 }}
                                                disabled={fragBusy || !fragResult}
                                                onClick={doFragFinalize}>落盘当前书</button>
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* 记忆面板（【Phase 3】D1 三级分层：全局/情节/单对话 + 未决冲突卡处置） */}
                            {memOpen && (
                                <MemoryPanel bookRoot={bookRoot} arcId={activeArcId || ''} sessionId={chatSessionId}
                                    onChanged={loadMemory} />
                            )}

                            {/* 快捷操作（两种模式共享，默认折叠） */}
                            <div className="wb-quickpanel">
                                <div className="wb-qphead" onClick={() => setQuickOpen(!quickOpen)}>
                                    ＋ 快捷操作
                                    <span className="arr" style={{ marginLeft: 'auto', transform: quickOpen ? 'rotate(0deg)' : 'rotate(-90deg)', transition: 'transform .2s' }}>▾</span>
                                </div>
                                <div className={`wb-qpbody ${quickOpen ? '' : 'collapsed'}`}>
                                    <div className="wb-qpbtn grp">阶梯</div>
                                    <div className="wb-qpbtn" onClick={handleStep}>生成下一级</div>
                                    <div className="wb-qpbtn" onClick={handleScore}>评分本章</div>
                                    <div className="wb-qpbtn" onClick={handlePollution}>污染检查</div>
                                    <div className="wb-qpbtn" onClick={() => setL5Draft(l5Text)}>编辑正文</div>

                                    <div className="wb-qpbtn grp">元素</div>
                                    <div className="wb-qpbtn" onClick={() => setOvTab('gallery')}>元素图鉴</div>
                                    <div className="wb-qpbtn" onClick={() => setAiTab('free')}>自由对话（灵感）</div>
                                    <div className="wb-qpbtn" onClick={handleFinalize}>落盘本章</div>
                                    <div className="wb-qpbtn" onClick={handleFinishArc}>完结本情节</div>

                                    <div className="wb-qpbtn grp">辅助</div>
                                    <div className="wb-qpbtn" onClick={() => setOvTab('search')}>检索</div>
                                    <div className="wb-qpbtn" onClick={() => setPaceExpand(!paceExpand)}>节奏雷达</div>
                                    <div className="wb-qpbtn" onClick={() => setMemOpen(v => !v)}>{memOpen ? '收起记忆' : '查看记忆'}
                                    </div>
                                    <div className="wb-qpbtn" onClick={() => setSyTab('health')}>检索体检</div>
                                </div>
                            </div>

                        </aside>
                    </>
                )}
            </div>
            {/* Electron 无 window.prompt：文本输入弹窗统一走 PromptModal */}
            <PromptModal />
        </div>
    )
}
