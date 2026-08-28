// 专业情节模板字段（v5.33.2）
// 与 prompt-harness/prompt_harness/plot_library.py 的 _TEMPLATE_FIELDS 对应：
// 模板 = 可填写的字段表单（主角/出场人物/…），填完自动组装 l1 再走 1→12345 阶梯。
export const TEMPLATE_FIELDS = [
    { key: 'protagonist', label: '主角' },
    { key: 'cast', label: '出场人物' },
    { key: 'time_setting', label: '时间背景' },
    { key: 'location', label: '关键地点' },
    { key: 'conflict', label: '核心冲突' },
    { key: 'goal', label: '目标' },
    { key: 'twist', label: '转折' },
    { key: 'ending', label: '结局/悬念' },
    { key: 'key_item', label: '关键道具' },
]

// 从模板取出字段表单 [{key, label, hint}]，hint 是该字段「该写什么」（提炼自源剧情，供参考）
export function templateFields(template) {
    const f = (template && template.fields) || {}
    return TEMPLATE_FIELDS.map(x => ({ ...x, hint: (f[x.key] || '') }))
}
