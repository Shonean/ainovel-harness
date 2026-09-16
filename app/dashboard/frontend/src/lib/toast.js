/**
 * Toast：全局轻提示（v9 设计稿 #toasts/.toast 类）。
 */
let host = null

export function toast(msg) {
    if (typeof document === 'undefined') return
    if (!host) {
        host = document.getElementById('toasts')
        if (!host) {
            host = document.createElement('div')
            host.id = 'toasts'
            document.body.appendChild(host)
        }
    }
    const t = document.createElement('div')
    t.className = 'toast'
    t.textContent = String(msg)
    host.appendChild(t)
    setTimeout(() => t.classList.add('on'), 0)
    setTimeout(() => {
        t.classList.remove('on')
        setTimeout(() => t.remove(), 300)
    }, 2800)
}
