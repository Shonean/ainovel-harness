import { useState } from 'react'
import { systemRebuildAndRestart, systemHealth } from '../api.js'

/**
 * 全局「应用新代码」按钮
 *
 * 固定在右上角（fixed），所有页面可见。
 * 通过给各页面的顶部 header/nav 增加右侧 padding，避免与页面内容重叠。
 * 运行中显示：阶段标签 + 进度条动画（不用倒计时，避免数字卡住）。
 */
export default function RebuildReloadButton() {
    const [running, setRunning] = useState(false)
    const [stage, setStage] = useState('')

    const handleClick = async () => {
        if (running) return

        const ok = confirm(
            '确定要应用新代码吗？\n\n' +
            '将依次执行：\n' +
            '  1. 前端重新构建\n' +
            '  2. 同步到运行时目录\n' +
            '  3. 后端进程自重启（加载新代码）\n\n' +
            '整个过程大约 30-60 秒，完成后页面会自动刷新。'
        )
        if (!ok) return

        setRunning(true)
        setStage('构建前端…')

        try {
            const result = await systemRebuildAndRestart()
            if (result && result.ok === false) {
                const failed = (result.steps || []).filter(s => !s.ok)
                const msgs = failed.map(s => `  ✗ ${s.step}: ${s.error}`).join('\n')
                alert('部分步骤失败：\n' + msgs + '\n\n后端仍会尝试重启，刷新后看看是否生效。')
            }
        } catch (e) {
            // API 调用可能因服务器正在关闭而失败，这是正常的
        }

        // 关键：先等 4 秒让旧服务器彻底死掉，再开始轮询新服务器
        // 否则第一个健康检查命中旧服务器 → 立即 reload → 旧服务器死 → 白屏
        setStage('等待旧服务器关闭…')
        await new Promise(resolve => setTimeout(resolve, 4000))

        setStage('等待后端重启…')
        const poll = setInterval(async () => {
            try {
                const res = await systemHealth()
                if (res) {
                    clearInterval(poll)
                    setRunning(false)
                    setStage('')
                    setTimeout(() => window.location.reload(), 500)
                }
            } catch (e) {
                // 还没起来
            }
        }, 1000)

        // 兜底：90 秒后仍未就绪则停止，提示检查
        setTimeout(() => {
            clearInterval(poll)
            setRunning(false)
            setStage('')
            alert(
                '超时了！服务还没起来。\n\n' +
                '可能的原因：\n' +
                '  1. 前端构建失败\n' +
                '  2. 后端代码有语法错误，启动失败\n' +
                '  3. 端口被占用\n\n' +
                '请手动检查程序状态。'
            )
        }, 90000)
    }

    return (
        <button
            className={`global-rebuild-btn ${running ? 'running' : ''}`}
            onClick={handleClick}
            disabled={running}
            title="一键应用新代码：前端构建 + 同步 + 后端进程自重启"
        >
            {running ? (
                <>
                    <span className="rrb-spinner"></span>
                    <span className="rrb-stage">{stage}</span>
                </>
            ) : (
                '应用新代码'
            )}
        </button>
    )
}
