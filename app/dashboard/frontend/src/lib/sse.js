/**
 * SSE 重连管理器
 *
 * 封装浏览器原生 EventSource，添加：
 * - 指数退避重连（1s → 2s → 4s → 8s → 16s，最多 10 次）
 * - 区分手动 close 和错误断开（手动关闭不重连）
 * - 接口与现有 api.js 的 subscribeSSE / subscribeTaskStream 兼容
 */

const INITIAL_DELAY_MS = 1000
const MAX_DELAY_MS = 16000
const MAX_RETRIES = 10

/**
 * 创建一个带指数退避重连的 SSE 连接。
 *
 * @param {string} url - SSE 端点 URL
 * @param {function} onMessage - 收到消息时的回调，接收解析后的 JSON
 * @param {object} handlers - { onOpen, onError }
 * @returns {function} 取消订阅函数
 */
export function createReconnectingSSE(url, onMessage, handlers = {}) {
    const { onOpen, onError } = handlers
    let es = null
    let retries = 0
    let delay = INITIAL_DELAY_MS
    let retryTimer = null
    let closed = false // 手动关闭标记

    function connect() {
        if (closed) return

        es = new EventSource(url)

        es.onopen = () => {
            // 连接成功，重置退避计数器
            retries = 0
            delay = INITIAL_DELAY_MS
            if (onOpen) onOpen()
        }

        es.onmessage = event => {
            try {
                onMessage(JSON.parse(event.data))
            } catch {
                // 忽略非 JSON 消息（如 SSE 注释）
            }
        }

        es.onerror = () => {
            if (closed) return

            // 关闭当前连接
            if (es) {
                es.close()
                es = null
            }

            if (retries >= MAX_RETRIES) {
                if (onError) onError(new Error('SSE 重连次数已达上限'))
                return
            }

            retries++
            const currentDelay = Math.min(delay, MAX_DELAY_MS)

            retryTimer = setTimeout(() => {
                retryTimer = null
                delay = Math.min(delay * 2, MAX_DELAY_MS)
                connect()
            }, currentDelay)
        }
    }

    connect()

    // 返回取消订阅函数
    return () => {
        closed = true
        if (retryTimer) {
            clearTimeout(retryTimer)
            retryTimer = null
        }
        if (es) {
            es.close()
            es = null
        }
    }
}
