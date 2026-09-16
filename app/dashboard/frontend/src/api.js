import { createReconnectingSSE } from './lib/sse.js'

// Tauri 生产构建下 window.location.origin 为 tauri://localhost（或 https://tauri.localhost），
// 需硬编码后端地址，否则所有 /api/* 请求会 404。
const BASE = 'http://127.0.0.1:8765'

// ============================================================
// 统一响应格式（v5.2 渐进迁移）
//
// 目标：所有 API 响应统一为 { ok, data, error } 格式
// 现状：部分端点已迁移，部分还是旧格式
// 策略：normalizeApiResponse() 自动检测 + 兼容
//
// 使用方式：
//   旧代码（直接返回原始数据）：fetchJSON / postJSON — 继续可用
//   新代码（统一格式）：apiFetch / apiPost — 返回 { ok, data, error }
// ============================================================

/**
 * 把 API 响应标准化为 { ok, data, error } 格式。
 * 自动检测后端返回的是新格式还是旧格式，做兼容。
 */
export function normalizeApiResponse(raw) {
    // 已经是新格式
    if (raw && typeof raw === 'object' && 'ok' in raw) {
        return {
            ok: !!raw.ok,
            data: raw.data !== undefined ? raw.data : null,
            error: raw.error || null,
        }
    }
    // FastAPI 错误格式 { detail: "..." }
    if (raw && typeof raw === 'object' && 'detail' in raw) {
        return { ok: false, data: null, error: String(raw.detail) }
    }
    // 旧格式：直接作为 data
    return { ok: true, data: raw, error: null }
}

/**
 * 统一 GET 请求，返回 { ok, data, error }。
 * 新端点优先使用这个，旧端点逐步迁移。
 */
export async function apiGet(path, params = {}) {
    try {
        const raw = await fetchJSON(path, params)
        return normalizeApiResponse(raw)
    } catch (err) {
        return { ok: false, data: null, error: err.message || String(err) }
    }
}

/**
 * 统一 POST 请求，返回 { ok, data, error }。
 */
export async function apiPost(path, body = {}) {
    try {
        const raw = await postJSON(path, body)
        return normalizeApiResponse(raw)
    } catch (err) {
        return { ok: false, data: null, error: err.message || String(err) }
    }
}

// 网络错误自动重试（后端 Python 初始化慢，前端可能先就绪）
async function fetchWithRetry(url, retries = 5) {
    let lastErr
    for (let i = 0; i <= retries; i++) {
        try {
            const response = await fetch(url.toString())
            if (!response.ok) {
                throw new Error(`${response.status} ${response.statusText}`)
            }
            return response
        } catch (err) {
            lastErr = err
            // 只重试网络错误（fetch 抛 TypeError），HTTP 错误直接抛出
            if (!(err instanceof TypeError)) throw err
            if (i < retries) {
                const delay = Math.min(1000 * Math.pow(2, i), 8000)
                await new Promise(r => setTimeout(r, delay))
            }
        }
    }
    throw lastErr
}

export async function fetchJSON(path, params = {}) {
    const url = new URL(`${BASE}${path}`, window.location.origin)
    for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null && value !== '') {
            url.searchParams.set(key, value)
        }
    }

    const response = await fetchWithRetry(url)
    return response.json()
}

export function fetchProjectInfo() {
    return fetchJSON('/api/project/info')
}

export function fetchCurrentProject() {
    return fetchJSON('/api/project/current')
}

export function fetchInitData() {
    return fetchJSON('/api/project/init-data')
}

export function fetchProjects() {
    return fetchJSON('/api/projects')
}

export function switchProject(projectRoot) {
    return postJSON('/api/project/switch', { project_root: projectRoot })
}

export function registerProject(projectRoot) {
    return postJSON('/api/project/register', { project_root: projectRoot })
}

export function createProject(name, brief, referenceTextPath, model, testBook = false, bookMode = 'premium') {
    return postJSON('/api/project/create', {
        name,
        brief: brief || {},
        reference_text_path: referenceTextPath || null,
        model: model || null,
        test_book: testBook,
        book_mode: bookMode,
    })
}

export function switchBookMode(projectRoot, mode) {
    return postJSON('/api/project/mode', { project_root: projectRoot, mode })
}

export function fetchStoryRuntimeHealth() {
    return fetchJSON('/api/story-runtime/health')
}

export function fetchChapterTrend(params = {}) {
    return fetchJSON('/api/stats/chapter-trend', params)
}

export function fetchChapters() {
    return fetchJSON('/api/chapters')
}

export function fetchEntities(params = {}) {
    return fetchJSON('/api/entities', params)
}

export function fetchStateChanges(params = {}) {
    return fetchJSON('/api/state-changes', params)
}

export function fetchRelationships(params = {}) {
    return fetchJSON('/api/relationships', params)
}

export function fetchRelationshipEvents(params = {}) {
    return fetchJSON('/api/relationship-events', params)
}

export function fetchCommits(params = {}) {
    return fetchJSON('/api/commits', params)
}

export function fetchContractsSummary() {
    return fetchJSON('/api/contracts/summary')
}

export function fetchEnvStatus() {
    return fetchJSON('/api/env-status')
}

export function probeEnvStatus() {
    return fetchJSON('/api/env-status/probe')
}

export function fetchUsage(windowHours = 24, recent = 50) {
    return fetchJSON('/api/usage', { window_hours: windowHours, recent })
}

export function fetchFilesTree() {
    return fetchJSON('/api/files/tree')
}

export function fetchFileContent(path) {
    return fetchJSON('/api/files/read', { path })
}

// ===== 章节重置 =====

export function resetChapter(chapter, purgeDirective = false) {
    return postJSON('/api/chapter/reset', { chapter, purge_directive: purgeDirective })
}

// ===== 一键生成全书 checkpoint =====

export function fetchAutoGenerateCheckpoint() {
    return fetchJSON('/api/auto-generate/checkpoint')
}

export function updateAutoGenerateCheckpoint(data) {
    return postJSON('/api/auto-generate/checkpoint', data)
}

export function clearAutoGenerateCheckpoint() {
    return postJSON('/api/auto-generate/checkpoint/clear', {})
}

// ===== 缓存管理 =====
export function fetchCacheList() {
    return fetchJSON('/api/cache/list')
}

export function fetchCachePreview(cacheId, filePath = null) {
    return postJSON('/api/cache/preview', { cache_id: cacheId, file_path: filePath })
}

export function updateSqliteCell(cacheId, table, rowPk, column, value) {
    return postJSON('/api/cache/sqlite/update', {
        cache_id: cacheId,
        table,
        row_pk: rowPk,
        column,
        value,
    })
}

export function purgeStaleChapterDirectives() {
    return postJSON('/api/cache/chapter_directive/purge_stale', {})
}

export function subscribeSSE(onMessage, handlers = {}) {
    return createReconnectingSSE(`${BASE}/api/events`, onMessage, handlers)
}

// ===== Phase A1: actions / tasks / workflow =====

export async function postJSON(path, body = {}) {
    const response = await fetch(`${BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    })
    if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`
        try {
            const data = await response.json()
            if (data?.detail) {
                // FastAPI 422 返回 detail 为数组，需提取可读信息
                if (Array.isArray(data.detail)) {
                    detail = data.detail.map(d =>
                        `${d.loc?.join('.') || '?'}: ${d.msg}`
                    ).join('; ')
                } else if (typeof data.detail === 'string') {
                    detail = data.detail
                } else {
                    detail = JSON.stringify(data.detail)
                }
            }
        } catch { /* ignore */ }
        throw new Error(detail)
    }
    return response.json()
}

export function postAction(name, body = {}) {
    return postJSON(`/api/actions/${name}`, body)
}

export function postWorkflow(name, body = {}) {
    return postJSON(`/api/workflows/${name}`, body)
}

export function postPolish(chapter, model = null) {
    return postJSON('/api/agents/polish', { chapter, model })
}

export function resumeTask(taskId, answer) {
    return postJSON(`/api/tasks/${taskId}/resume`, { answer })
}

export function cancelTask(taskId) {
    return postJSON(`/api/tasks/${taskId}/cancel`, {})
}

export function fetchTask(taskId) {
    return fetchJSON(`/api/tasks/${taskId}`)
}

export function fetchTaskList(params = {}) {
    return fetchJSON('/api/tasks', params)
}

export function fetchChapterStatus(chapter) {
    return fetchJSON(`/api/workflow/chapter/${chapter}/status`)
}

export function fetchChapterList() {
    return fetchJSON('/api/workflow/chapters')
}

export function fetchNextStep() {
    return fetchJSON('/api/project/next-step')
}

export function fetchChecklist() {
    return fetchJSON('/api/project/checklist')
}

export function postFileWrite(path, content) {
    return postJSON('/api/files/write', { path, content })
}

export function postAgent(name, body = {}) {
    return postJSON(`/api/agents/${name}`, body)
}

/**
 * Subscribe to a task's SSE stream. Returns an unsubscribe function.
 * onEvent receives parsed JSON event payloads.
 */
export function subscribeTaskStream(taskId, onEvent, handlers = {}) {
    return createReconnectingSSE(`${BASE}/api/tasks/${taskId}/stream`, onEvent, handlers)
}

// ===== Phase 6: 通道配置 =====

export function fetchChannelProfiles() {
    return fetchJSON('/api/channel/profiles')
}

export function fetchChannelConfig() {
    return fetchJSON('/api/channel/config')
}

export function updateChannelConfig(channel) {
    return postJSON('/api/channel/config', { channel })
}

// ===== Phase 7: 测试书功能 =====

export function initializeProject(projectRoot) {
    return postJSON('/api/project/initialize', { project_root: projectRoot })
}

export function deleteProject(projectRoot) {
    return postJSON('/api/project/delete', { project_root: projectRoot })
}

export function fetchExport(includeChapters = true) {
    return fetchJSON('/api/project/export', { include_chapters: includeChapters })
}

export function fetchPromptFiles() {
    return fetchJSON('/api/prompts/list')
}

export function readPromptFile(category, filename) {
    return postJSON('/api/prompts/read', { category, filename })
}

export function writePromptFile(category, filename, content) {
    return postJSON('/api/prompts/write', { category, filename, content })
}

export function resetPromptFile(category, filename) {
    return postJSON('/api/prompts/reset', { category, filename })
}

export function resetAllPromptFiles() {
    return postJSON('/api/prompts/reset-all', {})
}

// ===== Prompt 审阅台（预览 / 捕获 / 可编辑片段） =====
export function fetchPromptStages() {
    return fetchJSON('/api/prompts/stages')
}

export function fetchPromptPreview(stage, chapter = 1, batch = null) {
    return postJSON('/api/prompts/preview', { stage, chapter, batch })
}

export function fetchPromptLog(stage = null, chapter = null, limit = 50) {
    return fetchJSON('/api/prompts/log', { stage, chapter, limit })
}

export function fetchPromptFragments() {
    return fetchJSON('/api/prompts/fragments')
}

export function readPromptFragment(name) {
    return postJSON('/api/prompts/fragment/read', { name })
}

export function writePromptFragment(name, content) {
    return postJSON('/api/prompts/fragment/write', { name, content })
}

export function resetPromptFragment(name) {
    return postJSON('/api/prompts/fragment/reset', { name })
}

// ===== API 预设库（文字模型多预设 + 向量模型全局共用）=====

// 获取完整结构：文字预设列表 + 向量配置 + 字段元数据
export function fetchApiLibrary() {
    return fetchJSON('/api/api-library')
}

// 文字模型预设：保存（新建/更新）
export function saveApiPreset(id, name, fields) {
    return postJSON('/api/api-library', { id: id || null, name: name || '', fields: fields || {} })
}

// 文字模型预设：删除
export function deleteApiPreset(id) {
    return fetch(`${BASE}/api/api-library/${encodeURIComponent(id)}`, { method: 'DELETE' })
        .then(r => {
            if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
            return r.json()
        })
}

// 文字模型预设：应用到当前
export function applyApiPreset(id) {
    return postJSON('/api/api-library/apply', { id })
}

// 向量模型配置：保存（全局共用一套）
export function saveEmbedConfig(fields) {
    return postJSON('/api/api-library/embed-config', { fields: fields || {} })
}

// 向量模型预设：保存（新建/更新）
export function saveApiEmbedPreset(id, name, fields) {
    return postJSON('/api/api-library/embed', { id: id || null, name: name || '', fields: fields || {} })
}

// 向量模型预设：删除
export function deleteApiEmbedPreset(id) {
    return fetch(`${BASE}/api/api-library/embed/${encodeURIComponent(id)}`, { method: 'DELETE' })
        .then(r => {
            if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
            return r.json()
        })
}

// 向量模型预设：应用到当前
export function applyApiEmbedPreset(id) {
    return postJSON('/api/api-library/embed/apply', { id })
}

// ── 系统级：一键应用新代码 + 重启后端 ──

export function systemRebuildAndRestart() {
    return postJSON('/api/system/rebuild-and-restart', {})
}

export function systemShutdown() {
    return postJSON('/api/system/shutdown', {})
}

export function systemHealth() {
    return fetchJSON('/api/story-runtime/health')
}

// ── 系统级：统一日志（UniversalLogger）──

export function fetchLogSummary(date = '') {
    const qs = date ? `?date=${encodeURIComponent(date)}` : ''
    return fetchJSON(`/api/logs/summary${qs}`)
}

export function fetchLogFiles() {
    return fetchJSON('/api/logs/files')
}

export function fetchLogContent(file, limit = 100) {
    return fetchJSON('/api/logs/read', { file, limit })
}

// ============================================================
// Prompt Harness API (mounted at /api/prompt-harness)
// ============================================================

function phRequest(path, options = {}) {
    return fetch(`${BASE}/api/prompt-harness${path}`, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    }).then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(`${r.status}: ${t}`) })
        return r.json()
    })
}

// ── Corpus ──

export function phGetCorpusFiles(mode = 'flat') {
    return phRequest(`/corpus/files?mode=${mode}`)
}

export function phGetCorpusTree() {
    return phRequest('/corpus/tree')
}

export async function phUploadCorpusFile(file, genre = '', novel = '') {
    const formData = new FormData()
    formData.append('file', file)
    const params = new URLSearchParams()
    if (genre) params.set('genre', genre)
    if (novel) params.set('novel', novel)
    const qs = params.toString()
    const url = qs ? `${BASE}/api/prompt-harness/corpus/upload?${qs}` : `${BASE}/api/prompt-harness/corpus/upload`
    const resp = await fetch(url, { method: 'POST', body: formData })
    if (!resp.ok) {
        const text = await resp.text()
        throw new Error(`${resp.status}: ${text}`)
    }
    return resp.json()
}

// ── 文件信息 ──

export function phGetFileInfo(filename) {
    return phRequest(`/corpus/file-info/${encodeURIComponent(filename)}`)
}

// ── 章节导航 ──

export function phGetChapters(filename, groupSize = 50) {
    return phRequest('/corpus/chapters', {
        method: 'POST',
        body: JSON.stringify({ filename, group_size: groupSize }),
    })
}

export function phGetChapterAt(filename, chapterIndex) {
    return phRequest('/corpus/chapter-at', {
        method: 'POST',
        body: JSON.stringify({ filename, chapter_index: chapterIndex }),
    })
}

// ── 运行日志 ──

export function phListLogs(category = 'all', limit = 50, sourceFilter = '') {
    const params = new URLSearchParams()
    if (category && category !== 'all') params.set('category', category)
    if (limit) params.set('limit', limit)
    if (sourceFilter) params.set('source_filter', sourceFilter)
    const qs = params.toString()
    return phRequest(`/logs${qs ? '?' + qs : ''}`)
}

export function phGetLog(filepath, eventType = '', limit = 0) {
    const params = new URLSearchParams()
    if (eventType) params.set('event_type', eventType)
    if (limit) params.set('limit', limit)
    const qs = params.toString()
    return phRequest(`/logs/${filepath}${qs ? '?' + qs : ''}`)
}

// 会话日志
export function phListSessions(sourceFilter = '', limit = 50, summary = true) {
    const params = new URLSearchParams()
    if (sourceFilter) params.set('source_filter', sourceFilter)
    if (limit) params.set('limit', limit)
    params.set('summary', summary ? 'true' : 'false')
    return phRequest(`/logs/sessions?${params.toString()}`)
}

export function phGetSessionLog(sessionId, eventType = '', limit = 0) {
    const params = new URLSearchParams()
    if (eventType) params.set('event_type', eventType)
    if (limit) params.set('limit', limit)
    const qs = params.toString()
    return phRequest(`/logs/sessions/${encodeURIComponent(sessionId)}${qs ? '?' + qs : ''}`)
}

export function phGetSessionSummary(sessionId) {
    return phRequest(`/logs/sessions/${encodeURIComponent(sessionId)}/summary`)
}

export function phEndSession(sessionId, reason = 'manual') {
    return phRequest(`/logs/sessions/${encodeURIComponent(sessionId)}/end`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
    })
}

// LLM 日志
export function phListLlmLogs(date = '', limit = 100, statusFilter = '', callType = '') {
    const params = new URLSearchParams()
    if (date) params.set('date', date)
    if (limit) params.set('limit', limit)
    if (statusFilter) params.set('status_filter', statusFilter)
    if (callType) params.set('call_type', callType)
    return phRequest(`/logs/llm?${params.toString()}`)
}

export function phListLlmLogDates() {
    return phRequest('/logs/llm/dates')
}

export function phRestartServer() {
    return phRequest('/restart', { method: 'POST' })
}

export function phRebuildAndReload() {
    return phRequest('/rebuild-and-reload', { method: 'POST' })
}

export function phHealth() {
    return phRequest('/health')
}
// ── v5.33 情节模板库 + 阶梯桥接（双模块连接） ──

export function phListPlotTemplates() {
    return phRequest('/plot-templates')
}

export function phPlotTemplatePerf() {
    return phRequest('/plot-templates/perf')
}

export function phDeletePlotTemplate(id) {
    return phRequest(`/plot-templates/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function phMatchPlotTemplates({ query, style = '', role_setting = '', top_k = 8 }) {
    return phRequest('/plot-templates/match', {
        method: 'POST',
        body: JSON.stringify({ query, style, role_setting, top_k }),
    })
}

export function phExtractPlotTemplates({ filepath, start_chapter = 1, end_chapter = null, style = '', role_setting = '', budget = 80, min_score = 0.65 }) {
    const body = { filepath, start_chapter, style, role_setting, budget, min_score }
    if (end_chapter) body.end_chapter = end_chapter
    return phRequest('/plot-templates/extract', {
        method: 'POST',
        body: JSON.stringify(body),
    })
}

// 【v5.33.7】用户确认后整弧入库：按 task_id+arc 去实体化存库（弧内全部章内容）
export function phStoreExtractTemplate(task_id, arc) {
    return phRequest('/plot-templates/extract-store', {
        method: 'POST',
        body: JSON.stringify({ task_id, arc }),
    })
}

// ── 炼工台·连接模块（v7.8）──────────────────────────────────
export function phConnectionsOverview() {
    return phRequest('/connections/overview')
}

// 工作台日志查询（连接模块「命中记录」/底部日志条用）
export function phWorkbenchLogsBooks() {
    return phRequest('/ai-creation/logs/books')
}
export function phWorkbenchLogsList({ book = '', date = '', call_type = '', limit = 50 } = {}) {
    const qs = new URLSearchParams()
    if (book) qs.set('book', book)
    if (date) qs.set('date', date)
    if (call_type) qs.set('call_type', call_type)
    if (limit) qs.set('limit', limit)
    return phRequest(`/ai-creation/logs/list${qs.toString() ? '?' + qs.toString() : ''}`)
}
export function phWorkbenchLogsSummary({ book = '', date = '' } = {}) {
    const qs = new URLSearchParams()
    if (book) qs.set('book', book)
    if (date) qs.set('date', date)
    return phRequest(`/ai-creation/logs/summary${qs.toString() ? '?' + qs.toString() : ''}`)
}

export function phBridgeState({ l1 = '', style = '', role_setting = '', archetype = '', template = null, n_chapters = 1 }) {
    return phRequest('/bridge/state', {
        method: 'POST',
        body: JSON.stringify({ l1, style, role_setting, archetype, template, n_chapters }),
    })
}

export function phBridgeSetActiveChapter(state, idx) {
    return phRequest('/bridge/active-chapter', {
        method: 'POST',
        body: JSON.stringify({ state, idx }),
    })
}

export function phBridgeStep(state) {
    return phRequest('/bridge/step', {
        method: 'POST',
        body: JSON.stringify({ state }),
    })
}

export function phBridgeConfirm(state, level) {
    return phRequest('/bridge/confirm', {
        method: 'POST',
        body: JSON.stringify({ state, level }),
    })
}

export function phBridgeModify(state, level, instruction) {
    return phRequest('/bridge/modify', {
        method: 'POST',
        body: JSON.stringify({ state, level, instruction }),
    })
}

export function phBridgeChat({ state = {}, messages = [], template = null }) {
    return phRequest('/bridge/chat', {
        method: 'POST',
        body: JSON.stringify({ state, messages, template }),
    })
}

export function phTaskStatus(taskId) {
    return phRequest(`/optimize/status/${encodeURIComponent(taskId)}`)
}

// ════════════════════════════════════════════════════════════════
// AI 创作模块（新测试书）：逐弧创作 + 元素选择隔离 + 双评分
// 所有调用绑定主系统书根目录（book_root）
// ════════════════════════════════════════════════════════════════

export function phAiInit({ book_root, brief, title = '', genre = '', settings = null }) {
    return phRequest('/ai-creation/init', {
        method: 'POST',
        body: JSON.stringify({ book_root, brief, title, genre, settings }),
    })
}

// 量产快速初始化：表单 → 基本设定 + 元素卡（待审，不自动批准）；返回 { task_id }
export function phAiInitQuick(book_root, { title, genre = '', protagonist = '', style = '', one_liner = '', target_chapters = 0 }) {
    return phRequest('/ai-creation/init/quick', {
        method: 'POST',
        body: JSON.stringify({ book_root, title, genre, protagonist, style, one_liner, target_chapters }),
    })
}

// ── 量产路线图（roadmap：幕/弧卡/伏笔/冻结）────────────────────
export function phAiRoadmapGet(book_root) {
    return phRequest(`/ai-creation/roadmap?book_root=${encodeURIComponent(book_root)}`)
}

export function phAiRoadmapGenerate(book_root, { target_chapters = 30, n_chapters_per_arc = 3, brief = '' } = {}) {
    return phRequest('/ai-creation/roadmap/generate', {
        method: 'POST',
        body: JSON.stringify({ book_root, target_chapters, n_chapters_per_arc, brief }),
    })
}

export function phAiRoadmapSave(book_root, roadmap) {
    return phRequest('/ai-creation/roadmap', {
        method: 'PUT',
        body: JSON.stringify({ book_root, roadmap }),
    })
}

export function phAiRoadmapFreeze(book_root, force = false) {
    return phRequest('/ai-creation/roadmap/freeze', {
        method: 'POST',
        body: JSON.stringify({ book_root, force }),
    })
}

export function phAiRoadmapCardRegenerate(book_root, arc_id, instruction = '') {
    return phRequest('/ai-creation/roadmap/card/regenerate', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, instruction }),
    })
}

export function phAiRoadmapContinue(book_root, count = 1) {
    return phRequest('/ai-creation/roadmap/continue', {
        method: 'POST',
        body: JSON.stringify({ book_root, count }),
    })
}

export function phAiSettingsGet(book_root) {
    return phRequest(`/ai-creation/settings?book_root=${encodeURIComponent(book_root)}`)
}

export function phAiSettingFiles(book_root) {
    return phRequest(`/ai-creation/setting-files?book_root=${encodeURIComponent(book_root)}`)
}

// 条目级选择性注入候选项：settings/arcs/memory/corpus/templates（创作助手访问控制 chips）
export function phAiSelAccessOptions(book_root) {
    return phRequest(`/ai-creation/sel-access-options?book_root=${encodeURIComponent(book_root)}`)
}

// ── 红果短剧参考库 ─────────────────────────────────────────
export function phAiShortDramas() {
    return phRequest('/ai-creation/short-dramas')
}
export function phAiShortDramaAdd(name, tags, intro) {
    return phRequest('/ai-creation/short-dramas', { method: 'POST', body: JSON.stringify({ name, tags, intro }) })
}
export function phAiShortDramaFetch() {
    return phRequest('/ai-creation/short-dramas/fetch', { method: 'POST', body: '{}' })
}

export function phAiSettingsPut(book_root, settings) {
    return phRequest('/ai-creation/settings', {
        method: 'PUT',
        body: JSON.stringify({ book_root, settings }),
    })
}

export function phAiSettingsGenerate({ book_root, settings = null, title = '', genre = '' }) {
    return phRequest('/ai-creation/settings/generate', {
        method: 'POST',
        body: JSON.stringify({ book_root, settings, title, genre }),
    })
}

export function phAiState(book_root) {
    return phRequest('/ai-creation/state', {
        method: 'POST',
        body: JSON.stringify({ book_root }),
    })
}

export function phAiElementsGet(book_root) {
    return phRequest(`/ai-creation/elements?book_root=${encodeURIComponent(book_root)}`)
}

export function phAiElementsPut(book_root, elements) {
    return phRequest('/ai-creation/elements', {
        method: 'PUT',
        body: JSON.stringify({ book_root, elements }),
    })
}

// ── 灵感工坊接入：元素单卡 CRUD / pending / memory / inspire chat ──
export function phAiElementAdd(book_root, kind, { name, desc = '', fields = null, relations = null }) {
    return phRequest('/ai-creation/element', {
        method: 'POST',
        body: JSON.stringify({ book_root, kind, name, desc, fields, relations }),
    })
}

export function phAiElementPut(book_root, kind, id, { name, desc = '', fields = null, relations = null, alias = null, terms = null }) {
    const body = { book_root, kind, id, name, desc }
    if (fields !== null) body.fields = fields
    if (relations !== null) body.relations = relations
    if (alias !== null) body.alias = alias
    if (terms !== null) body.terms = terms
    return phRequest(`/ai-creation/element/${kind}/${encodeURIComponent(id)}`, {
        method: 'PUT',
        body: JSON.stringify(body),
    })
}

export function phAiElementDelete(book_root, kind, id) {
    return phRequest(`/ai-creation/element/${kind}/${encodeURIComponent(id)}?book_root=${encodeURIComponent(book_root)}`, {
        method: 'DELETE',
    })
}

export function phAiPendingGet(book_root) {
    return phRequest(`/ai-creation/pending?book_root=${encodeURIComponent(book_root)}`)
}

export function phAiChatSessionsGet(book_root) {
    return phRequest(`/ai-creation/chat-sessions?book_root=${encodeURIComponent(book_root)}`)
}

export function phAiChatSessionsSave(book_root, sessions) {
    return phRequest('/ai-creation/chat-sessions', {
        method: 'POST',
        body: JSON.stringify({ book_root, sessions }),
    })
}

// 【Phase 3】删除一整段会话（含其全部消息）
export function phAiChatSessionsDelete(book_root, session_id) {
    return phRequest(`/ai-creation/chat-sessions/${encodeURIComponent(session_id)}?book_root=${encodeURIComponent(book_root)}`, {
        method: 'DELETE',
    })
}

export function phAiPendingApprove(book_root, pid, edits = {}) {
    return phRequest(`/ai-creation/pending/${encodeURIComponent(pid)}/approve`, {
        method: 'POST',
        body: JSON.stringify({ book_root, ...edits }),
    })
}

export function phAiPendingReject(book_root, pid) {
    return phRequest(`/ai-creation/pending/${encodeURIComponent(pid)}/reject?book_root=${encodeURIComponent(book_root)}`, {
        method: 'POST',
    })
}

export function phAiPendingApproveAll(book_root) {
    return phRequest('/ai-creation/pending/approve-all', {
        method: 'POST',
        body: JSON.stringify({ book_root }),
    })
}

export function phAiPendingRejectAll(book_root) {
    return phRequest(`/ai-creation/pending/reject-all?book_root=${encodeURIComponent(book_root)}`, {
        method: 'POST',
    })
}

export function phAiExtractCards(book_root, text) {
    return phRequest('/ai-creation/extract-cards', {
        method: 'POST',
        body: JSON.stringify({ book_root, text }),
    })
}

export function phAiMemoryGet(book_root) {
    return phRequest(`/ai-creation/memory?book_root=${encodeURIComponent(book_root)}`)
}

export function phAiMemoryAdd(book_root_or_obj, text, { scope = 'book', key = '', arc_id = '', tags = null } = {}) {
    // 兼容两种调用方式：
    //   1. phAiMemoryAdd(book_root, text)            - 灵感工坊旧用法
    //   2. phAiMemoryAdd({ book_root, text, scope, key, arc_id, tags }) - 创作助手新用法
    let body
    if (typeof book_root_or_obj === 'object' && book_root_or_obj !== null) {
        body = {
            book_root: book_root_or_obj.book_root,
            text: book_root_or_obj.text,
            scope: book_root_or_obj.scope ?? 'book',
            key: book_root_or_obj.key ?? '',
            arc_id: book_root_or_obj.arc_id ?? '',
            tags: book_root_or_obj.tags ?? null,
        }
    } else {
        body = { book_root: book_root_or_obj, text, scope, key, arc_id, tags }
    }
    return phRequest('/ai-creation/memory', {
        method: 'POST',
        body: JSON.stringify(body),
    })
}

export function phAiMemoryDelete(book_root, id) {
    return phRequest(`/ai-creation/memory/${encodeURIComponent(id)}?book_root=${encodeURIComponent(book_root)}`, {
        method: 'DELETE',
    })
}

export function phAiInspireChat(book_root, messages, mech, ctx = null, signal = null) {
    return phRequest('/ai-creation/inspire/chat', {
        method: 'POST',
        body: JSON.stringify({ book_root, messages, mech, ctx }),
        signal,
    })
}

export function phAiInspireDepollute(book_root, text, ctx = null) {
    return phRequest('/ai-creation/inspire/depollute', {
        method: 'POST',
        body: JSON.stringify({ book_root, text, ctx }),
    })
}

export function phAiArcNew({ book_root, l1, n_chapters = 1, style = '', role_setting = '', selected = null, carry_prev = true }) {
    return phRequest('/ai-creation/arc/new', {
        method: 'POST',
        body: JSON.stringify({ book_root, l1, n_chapters, style, role_setting, selected, carry_prev }),
    })
}

export function phAiArcSelect(book_root, arc_id, selected) {
    return phRequest('/ai-creation/arc/select', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, selected }),
    })
}

export function phAiArcStep(book_root, arc_id) {
    return phRequest('/ai-creation/arc/step', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id }),
    })
}

export function phAiArcConfirm(book_root, arc_id, level) {
    return phRequest('/ai-creation/arc/confirm', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, level }),
    })
}

export function phAiArcSetActiveChapter(book_root, arc_id, idx) {
    return phRequest('/ai-creation/arc/active-chapter', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, idx }),
    })
}

export function phAiArcFinish(book_root, arc_id) {
    return phRequest('/ai-creation/arc/finish', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id }),
    })
}

export function phAiArcRegenerateL5(book_root, arc_id) {
    return phRequest('/ai-creation/arc/regenerate-l5', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id }),
    })
}

export function phAiArcSetLevel(book_root, arc_id, level, text = '', data = null, idx = null) {
    return phRequest('/ai-creation/arc/set-level', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, level, text, data, idx }),
    })
}

export function phAiArcUpdate(book_root, arc_id, fields) {
    return phRequest('/ai-creation/arc/update', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, ...fields }),
    })
}
export function phAiArcDelete(book_root, arc_id) {
    return phRequest('/ai-creation/arc/delete', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id }),
    })
}
export function phAiArcDeleteChapter(book_root, arc_id, idx) {
    return phRequest('/ai-creation/arc/delete-chapter', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, idx }),
    })
}

export function phAiArcSetTemplate(book_root, arc_id, template_id = '') {
    return phRequest('/ai-creation/arc/set-template', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, template_id }),
    })
}

export function phAiArcModify(book_root, arc_id, level, instruction) {
    return phRequest('/ai-creation/arc/modify', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, level, instruction }),
    })
}

// 【Phase 3】session_id：工作记忆按会话隔离注入（单对话层记忆）
export function phAiArcChat(book_root, arc_id, messages, web_search = false, access = null, sel_access = null, mode = 'normal', session_id = '') {
    return phRequest('/ai-creation/arc/chat', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, messages, web_search, access, sel_access, mode, session_id }),
    })
}

// 【Phase 4】SSE 流式对话：POST + ReadableStream 手工解析（createReconnectingSSE 是 GET 语义，不适用）。
// handlers: {onToken(text), onToolCall(event), onPendingProposal(proposal), onDone(result), onError(msg)}
// 返回 Promise<{ok:true, result}|{ok:false, error}>，resolve 即流结束；REST 兜底由调用方处理。
export function phAiArcChatStream(book_root, arc_id, messages, handlers = {}, opts = {}) {
    return new Promise(resolve => {
        ;(async () => {
            try {
                const res = await fetch(`${BASE}/api/prompt-harness/ai-creation/arc/chat/stream`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        book_root, arc_id, messages,
                        web_search: !!opts.web_search, access: opts.access ?? null,
                        sel_access: opts.sel_access ?? null, mode: opts.mode || 'normal',
                        session_id: opts.session_id || '',
                    }),
                })
                if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
                const reader = res.body.getReader()
                const dec = new TextDecoder()
                let buf = ''
                for (;;) {
                    const { done, value } = await reader.read()
                    if (done) break
                    buf += dec.decode(value, { stream: true })
                    let idx
                    while ((idx = buf.indexOf('\n\n')) >= 0) {
                        const chunk = buf.slice(0, idx)
                        buf = buf.slice(idx + 2)
                        const line = chunk.split('\n').find(l => l.startsWith('data:'))
                        if (!line) continue
                        let ev
                        try { ev = JSON.parse(line.slice(5).trim()) } catch { continue }
                        if (ev.type === 'token') handlers.onToken?.(ev.text || '')
                        else if (ev.type === 'tool_call') handlers.onToolCall?.(ev.event)
                        else if (ev.type === 'pending_proposal') handlers.onPendingProposal?.(ev.proposal)
                        else if (ev.type === 'done') {
                            handlers.onDone?.(ev.result)
                            resolve({ ok: true, result: ev.result })
                            return
                        } else if (ev.type === 'error') throw new Error(ev.error || '流式对话失败')
                    }
                }
                // 流关闭但没收到 done → 视为异常（调用方落回 REST）
                throw new Error('流式连接中断（未收到 done）')
            } catch (e) {
                handlers.onError?.(e.message || String(e))
                resolve({ ok: false, error: e.message || String(e) })
            }
        })()
    })
}

// 【2026-08-15 初始化创作助手】初始化状态查询（前端判断是否进初始化模式）
export function phAiInitStatus(book_root) {
    return phRequest(`/ai-creation/init/status?book_root=${encodeURIComponent(book_root)}`)
}

// 用户同意后执行创作助手提案的动作（工具真执行）
export function phAiArcChatApply(book_root, arc_id, tool, args = {}, session_id = '') {
    return phRequest('/ai-creation/arc/chat/apply', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, tool, args, session_id }),
    })
}

// ── 创作助手：记忆列表（按 scope/arc_id 过滤，走 GET 端点） ───────────
export function phAiMemoryList(book_root, { scope = null, arc_id = '', key = '' } = {}) {
    let url = `/ai-creation/memory?book_root=${encodeURIComponent(book_root)}`
    if (scope) url += `&scope=${encodeURIComponent(scope)}`
    if (arc_id) url += `&arc_id=${encodeURIComponent(arc_id)}`
    if (key) url += `&key=${encodeURIComponent(key)}`
    return phRequest(url)
}

// ── 【Phase 3】D1 三级记忆面板 + 冲突卡处置 + 单对话工作记忆 ──────────
export function phAiMemoryTiers(book_root, { arc_id = '', session_id = '' } = {}) {
    const url = `/ai-creation/memory/tiers?book_root=${encodeURIComponent(book_root)}`
        + `&arc_id=${encodeURIComponent(arc_id)}&session_id=${encodeURIComponent(session_id)}`
    return phRequest(url)
}

// 处置未决冲突卡：action ∈ coexist_layered / version_rewrite / revert / add_slot
export function phAiMemoryCardResolve(book_root, card_id, action, note = '', slot = '') {
    return phRequest('/ai-creation/memory/card/resolve', {
        method: 'POST',
        body: JSON.stringify({ book_root, card_id, action, note, slot }),
    })
}

export function phAiMemoryWorkingAdd(book_root, session_id, text) {
    return phRequest('/ai-creation/memory/session', {
        method: 'POST',
        body: JSON.stringify({ book_root, session_id, text }),
    })
}

export function phAiMemoryWorkingDelete(book_root, session_id, key) {
    return phRequest(`/ai-creation/memory/session/${encodeURIComponent(session_id)}?book_root=${encodeURIComponent(book_root)}&key=${encodeURIComponent(key)}`, {
        method: 'DELETE',
    })
}

// ── 写书讨论检索（六源联邦 + 可选 LLM 精排） ──────────────────────
export function phAiSearch({ book_root, query, sources, top_k, rerank }) {
    return phRequest('/ai-creation/search', {
        method: 'POST',
        body: JSON.stringify({ book_root, query, sources, top_k, rerank: !!rerank }),
    })
}

export function phAiSearchRebuild(book_root) {
    return phRequest('/ai-creation/search/rebuild', {
        method: 'POST',
        body: JSON.stringify({ book_root }),
    })
}

export function phAiSearchSources(book_root) {
    return phRequest(`/ai-creation/search/sources?book_root=${encodeURIComponent(book_root)}`)
}

export function phAiChapterScore(book_root, arc_id, gen_text, chapter_idx = null) {
    return phRequest('/ai-creation/chapter/score', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, gen_text, chapter_idx }),
    })
}

export function phAiChapterPollution(book_root, arc_id, gen_text) {
    return phRequest('/ai-creation/chapter/pollution', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, gen_text }),
    })
}

export function phAiChapterFinalize(book_root, arc_id, chapter_idx = null) {
    return phRequest('/ai-creation/chapter/finalize', {
        method: 'POST',
        body: JSON.stringify({ book_root, arc_id, chapter_idx }),
    })
}

// ── v6.5 片段锚定扩写（只扩写【】内的内容） ────────────────────
export function phFragmentParse(text) {
    return phRequest('/ai-creation/fragment/parse', {
        method: 'POST', body: JSON.stringify({ text }),
    })
}

export function phFragmentUnderstand(text) {
    return phRequest('/ai-creation/fragment/understand', {
        method: 'POST', body: JSON.stringify({ text }),
    })
}

export function phFragmentExpand(text, directives = null) {
    return phRequest('/ai-creation/fragment/expand', {
        method: 'POST', body: JSON.stringify({ text, directives }),
    })
}

export function phFragmentFinalize({ book_root, title = '', text, output }) {
    return phRequest('/ai-creation/fragment/finalize', {
        method: 'POST', body: JSON.stringify({ book_root, title, text, output }),
    })
}

// ── 融合模块：批量生成全书 + 任务状态轮询 + 导出 ──
export function phAiBatchGenerate({ book_root, target_chapters, n_chapters_per_arc = 3, arc_briefs = null, select_all_elements = true, resume_run_id = null, model = null }) {
    return phRequest('/ai-creation/batch-generate', {
        method: 'POST', body: JSON.stringify({
            book_root, target_chapters, n_chapters_per_arc, arc_briefs, select_all_elements,
            resume_run_id, model,
        }),
    })
}

export function phAiRuns(book_root, kind = 'batch_generate', limit = 20) {
    return phRequest('/ai-creation/runs', {
        method: 'POST', body: JSON.stringify({ book_root, kind, limit }),
    })
}

export function phAiRunDetail(book_root, run_id) {
    return phRequest('/ai-creation/run-detail', {
        method: 'POST', body: JSON.stringify({ book_root, run_id }),
    })
}

// ── 量产补评（P5）──
export function phAiDeferredCount(book_root) {
    return phRequest(`/ai-creation/deferred-count?book_root=${encodeURIComponent(book_root)}`)
}

export function phAiRescoreDeferred(book_root, arc_id = '') {
    return phRequest('/ai-creation/rescore-deferred', {
        method: 'POST', body: JSON.stringify({ book_root, arc_id }),
    })
}

export function phAiOptimizeStatus(taskId) {
    return phRequest(`/optimize/status/${encodeURIComponent(taskId)}`)
}

export function phAiCancelTask(taskId) {
    return phRequest(`/tasks/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' })
}

// ── 素材 / 留空 / 备注 / 元素作用域（arc-element-scope Task 6）──────

// fragments
export const phFragmentsGet = (bookRoot, arcId = '') =>
    phRequest(`/ai-creation/fragments?book_root=${encodeURIComponent(bookRoot)}&arc_id=${encodeURIComponent(arcId)}`)

export const phFragmentsPut = (data) =>
    phRequest('/ai-creation/fragments', { method: 'PUT', body: JSON.stringify(data) })

// notes
export const phNotesGet = (bookRoot, scope = '') =>
    phRequest(`/ai-creation/notes?book_root=${encodeURIComponent(bookRoot)}&scope=${encodeURIComponent(scope)}`)

export const phNotesPut = (data) =>
    phRequest('/ai-creation/notes', { method: 'PUT', body: JSON.stringify(data) })

// elements scope
export const phElementsScope = (data) =>
    phRequest('/ai-creation/elements/scope', { method: 'PATCH', body: JSON.stringify(data) })

// ── 改编中心（T35：主页三层入口 + 全局总览）──────

export function fetchAdaptationOverview() {
    return fetchJSON('/api/adaptation/overview')
}

export async function saveAdaptationBudget(cfg) {
    const res = await fetch(`${BASE}/api/adaptation/budget`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfg),
    })
    return normalizeApiResponse(await res.json().catch(() => null))
}

// ── 改编 Pack（T35：书级改编页签；端点见 prompt-harness server.py §改编层 v0）──────

export const phAdaptBuild = (bookRoot, arcIds, endingCount = 2) =>
    phRequest('/ai-creation/adaptation/build', {
        method: 'POST',
        body: JSON.stringify({ book_root: bookRoot, arc_ids: arcIds, ending_count: endingCount }),
    })

export const phAdaptPacks = (bookRoot) =>
    phRequest('/ai-creation/adaptation/packs', {
        method: 'POST',
        body: JSON.stringify({ book_root: bookRoot }),
    })

export const phAdaptPack = (bookRoot, packId, include = []) =>
    phRequest('/ai-creation/adaptation/pack', {
        method: 'POST',
        body: JSON.stringify({ book_root: bookRoot, pack_id: packId, include }),
    })

export const phAdaptPackDelete = (bookRoot, packId) =>
    phRequest('/ai-creation/adaptation/pack/delete', {
        method: 'POST',
        body: JSON.stringify({ book_root: bookRoot, pack_id: packId }),
    })

export const phAdaptPreviewUrl = (bookRoot, packId) =>
    `${BASE}/api/prompt-harness/ai-creation/adaptation/preview?book_root=${encodeURIComponent(bookRoot)}&pack_id=${encodeURIComponent(packId)}`

// ── 漫剧线收口（T36）：drama 域 ──────

export const phDramaWorkbench = (bookRoot, packId) =>
    phRequest('/ai-creation/drama/workbench', {
        method: 'POST', body: JSON.stringify({ book_root: bookRoot, pack_id: packId }),
    })

export const phDramaCompose = (bookRoot, packId, config = {}) =>
    phRequest('/ai-creation/drama/compose', {
        method: 'POST', body: JSON.stringify({ book_root: bookRoot, pack_id: packId, config }),
    })

export const phDramaKeyshot = (bookRoot, packId, action, index = 0) =>
    phRequest('/ai-creation/drama/keyshot', {
        method: 'POST', body: JSON.stringify({ book_root: bookRoot, pack_id: packId, action, index }),
    })

export const phDramaFilms = (bookRoot) =>
    phRequest('/ai-creation/drama/films', {
        method: 'POST', body: JSON.stringify({ book_root: bookRoot }),
    })

export const phDramaFilmExport = (bookRoot, packId) =>
    phRequest('/ai-creation/drama/film/export', {
        method: 'POST', body: JSON.stringify({ book_root: bookRoot, pack_id: packId }),
    })

export const phDramaCompilation = (bookRoot, action, extra = {}) =>
    phRequest('/ai-creation/drama/compilation', {
        method: 'POST', body: JSON.stringify({ book_root: bookRoot, action, ...extra }),
    })

export const phDramaPublish = (bookRoot, packId, action, extra = {}) =>
    phRequest('/ai-creation/drama/publish', {
        method: 'POST', body: JSON.stringify({ book_root: bookRoot, pack_id: packId, action, ...extra }),
    })

export const phDramaBatch = (bookRoot, action) =>
    phRequest('/ai-creation/drama/batch', {
        method: 'POST', body: JSON.stringify({ book_root: bookRoot, action }),
    })

// ── 漫剧线收口（T36）：ark 控制台 ──────

export const phArk = (action, payload = {}, taskId = '') =>
    phRequest('/ai-creation/ark', {
        method: 'POST', body: JSON.stringify({ action, payload, task_id: taskId }),
    })

export const phArkChat = (message, history = []) =>
    phRequest('/ai-creation/ark/chat', {
        method: 'POST', body: JSON.stringify({ message, history }),
    })

export const phTasks = () => phRequest('/tasks')

// 成片管理跨书汇总（dashboard 8765 直连）
export function fetchAdaptationFilms() {
    return fetchJSON('/api/adaptation/films')
}

/** 漫剧媒体 URL：pack 内白名单文件，或 name='films/<文件名>' 取导出产物。 */
export function dramaMediaUrl(bookRoot, packId, name) {
    return `${BASE}/api/prompt-harness/ai-creation/drama/media?book_root=${encodeURIComponent(bookRoot)}&pack_id=${encodeURIComponent(packId || '')}&name=${encodeURIComponent(name)}`
}
