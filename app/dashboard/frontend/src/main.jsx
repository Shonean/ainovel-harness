import React, { Suspense, lazy, useEffect, useRef } from 'react'
import ReactDOM from 'react-dom/client'
import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import RebuildReloadButton from './components/RebuildReloadButton.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import StatusLine from './components/StatusLine.jsx'
import './index.css'

// 前端热更新检测：轮询 /api/version，标记变化（应用新代码后 build 更新版本）→ 自动 reload。
// 解决「一键应用新代码后 WebView 不自动刷新」——无需手动 Ctrl+Shift+R。
function VersionWatcher() {
    const lastRef = useRef(null)
    useEffect(() => {
        const check = async () => {
            try {
                const res = await fetch('http://127.0.0.1:8765/api/version')
                const d = await res.json()
                const v = String(d.version || '0')
                if (lastRef.current === null) {
                    lastRef.current = v  // 首次记录基线，不触发
                    return
                }
                if (v !== lastRef.current) {
                    window.location.reload()
                }
            } catch (e) { /* 后端重启中，忽略 */ }
        }
        const iv = setInterval(check, 3000)
        return () => clearInterval(iv)
    }, [])
    return null
}

const ProjectSelectPage = lazy(() => import('./pages/ProjectSelectPage.jsx'))
const CreateBookPage = lazy(() => import('./pages/CreateBookPage.jsx'))
const AICreationPage = lazy(() => import('./pages/AICreationPage.jsx'))
const MassWorkspacePage = lazy(() => import('./pages/MassWorkspacePage.jsx'))
const PromptHarnessPage = lazy(() => import('./pages/PromptHarnessPage.jsx'))
const ApiPresetsPage = lazy(() => import('./pages/ApiPresetsPage.jsx'))
const AdaptationHubPage = lazy(() => import('./pages/AdaptationHubPage.jsx'))
const DramaWorkbenchPage = lazy(() => import('./pages/DramaWorkbenchPage.jsx'))
const FilmManagerPage = lazy(() => import('./pages/FilmManagerPage.jsx'))
const PublishPage = lazy(() => import('./pages/PublishPage.jsx'))
const BatchPage = lazy(() => import('./pages/BatchPage.jsx'))

function LoadingScreen() {
    return (
        <div className="loading-screen">
            <div className="loading-card">
                <div className="section-label">LOADING</div>
                <p>正在加载…</p>
            </div>
        </div>
    )
}

// ── 前端错误上报（白屏诊断）─────────────────────────────
// window.onerror 捕获渲染/运行时异常，unhandledrejection 捕获懒加载失败；
// 转发到后端 logs/tasks/frontend_errors.jsonl，供远程排查白屏。
function reportFrontendError(kind, message, stack = '') {
    try {
        fetch('/api/prompt-harness/log-frontend-error', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                kind,
                message: String(message || '').slice(0, 2000),
                stack: String(stack || '').slice(0, 4000),
                url: window.location.hash || window.location.pathname,
            }),
        }).catch(() => {})
    } catch { /* 忽略上报自身错误 */ }
}

window.addEventListener('error', event => {
    console.error('[window.error]', event.message, event.error)
    reportFrontendError('error', event.message, event.error?.stack)
})

window.addEventListener('unhandledrejection', event => {
    console.error('[unhandledrejection]', event.reason)
    const msg = event.reason?.message || String(event.reason)
    reportFrontendError('unhandledrejection', msg, event.reason?.stack)
})

ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
        <HashRouter>
            {/** 全局右上角「应用新代码」按钮 —— 所有页面可见 */}
            <RebuildReloadButton />
            {/** 前端热更新检测：应用新代码后自动 reload */}
            <VersionWatcher />
            <ErrorBoundary>
                <Suspense fallback={<LoadingScreen />}>
                <Routes>
                    {/** 系统级工具：AI 创作 + Prompt Harness + API 预设（不绑定任何书） */}
                    <Route path="/ai-creation" element={<AICreationPage />} />
                    <Route path="/prompt-harness/*" element={<PromptHarnessPage />} />
                    <Route path="/prompt-train" element={<Navigate to="/prompt-harness" replace />} />
                    <Route path="/prompt-analyze" element={<Navigate to="/prompt-harness" replace />} />
                    <Route path="/api-presets" element={<ApiPresetsPage />} />

                    {/** 启动流程：项目库 + 全屏向导 */}
                    <Route path="/" element={<ProjectSelectPage />} />
                    <Route path="/create-book" element={<CreateBookPage />} />
                    {/** 改编中心（T36 v9 漫剧线）：书墙 / 漫剧工作台 / 成片 / 发布 / 批量 */}
                    <Route path="/adaptation" element={<AdaptationHubPage />} />
                    <Route path="/adaptation/workbench" element={<DramaWorkbenchPage />} />
                    <Route path="/adaptation/films" element={<FilmManagerPage />} />
                    <Route path="/adaptation/publish" element={<PublishPage />} />
                    <Route path="/adaptation/batch" element={<BatchPage />} />
                    {/** 量产工作台：生产线/路线图/章节/设置 */}
                    <Route path="/mass" element={<MassWorkspacePage />} />

                    {/** 旧路径兼容：统一走 AI 创作工作台 */}
                    <Route path="/create-test-book" element={<Navigate to="/create-book" replace />} />
                    <Route path="/generate-book" element={<Navigate to="/ai-creation" replace />} />
                    <Route path="/init" element={<Navigate to="/create-book" replace />} />
                    <Route path="/auto-generate" element={<Navigate to="/ai-creation" replace />} />
                    <Route path="/app/*" element={<Navigate to="/ai-creation" replace />} />

                    {/** 旧顶层路由重定向 */}
                    <Route path="/prompt-bench" element={<Navigate to="/prompt-harness" replace />} />
                    <Route path="/api-bench" element={<Navigate to="/api-presets" replace />} />
                    <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
                </Suspense>
            </ErrorBoundary>
            {/** 全局底部状态条：当日 LLM 用量/缓存/花费（所有页面可见，点击展开按类型明细） */}
            <StatusLine />
        </HashRouter>
    </React.StrictMode>,
)
