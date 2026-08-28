import { Component } from 'react'

/**
 * ErrorBoundary — 捕获子组件渲染异常，显示回退 UI 而非白屏。
 *
 * 放置策略：包裹在 &lt;Routes&gt; 外侧（App 内部），这样单个页面崩溃时
 * 侧边栏、SSE 连接、localStorage 设置都不会丢失。
 */
export default class ErrorBoundary extends Component {
    constructor(props) {
        super(props)
        this.state = { error: null, errorInfo: null }
    }

    static getDerivedStateFromError(error) {
        return { error }
    }

    componentDidCatch(error, errorInfo) {
        console.error('[ErrorBoundary] 页面渲染异常:', error, errorInfo)
        this.setState({ errorInfo })
        try {
            fetch('/api/prompt-harness/log-frontend-error', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    kind: 'errorboundary',
                    message: String(error && (error.message || error)).slice(0, 2000),
                    stack: String(errorInfo?.componentStack || error?.stack || '').slice(0, 4000),
                    url: window.location.hash || window.location.pathname,
                }),
            }).catch(() => {})
        } catch { /* 忽略上报自身错误 */ }
    }

    handleReload = () => {
        this.setState({ error: null, errorInfo: null })
    }

    render() {
        if (this.state.error) {
            return (
                <div className="error-boundary-fallback">
                    <div className="error-boundary-card">
                        <h2>⚠页面渲染异常</h2>
                        <p className="error-boundary-hint">
                            当前页面组件发生未预期的错误。侧边栏导航和其他功能仍然可用。
                        </p>
                        <details className="error-boundary-details">
                            <summary>错误详情（点击展开）</summary>
                            <pre>{String(this.state.error)}</pre>
                            {this.state.errorInfo?.componentStack && (
                                <pre className="error-boundary-stack">
                                    {this.state.errorInfo.componentStack}
                                </pre>
                            )}
                        </details>
                        <div className="error-boundary-actions">
                            <button
                                className="btn btn-blue"
                                onClick={this.handleReload}
                            >
                                重试（重新渲染当前页面）
                            </button>
                            <button
                                className="btn"
                                onClick={() => window.location.reload()}
                            >
                                刷新整个页面
                            </button>
                        </div>
                    </div>
                </div>
            )
        }

        return this.props.children
    }
}
