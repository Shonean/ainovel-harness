import ApiLibraryPanel from '../components/ApiLibraryPanel.jsx'

export default function ApiPresetsPage() {
    return (
        <div className="prompt-system-layout">
            <nav className="prompt-system-nav">
                <a href="#/" className="prompt-system-back">⇦ 返回主页</a>
                <span className="prompt-system-brand">API 预设管理</span>
                <span className="prompt-system-spacer" />
            </nav>
            <main className="prompt-system-main">
                <ApiLibraryPanel />
            </main>
        </div>
    )
}
