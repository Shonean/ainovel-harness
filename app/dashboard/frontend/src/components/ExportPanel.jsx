import { useState } from 'react'

export default function ExportPanel({ bookRoot, bookTitle, arcs, elements, settings, onNotice, onError }) {
    const [exporting, setExporting] = useState(false)

    const btnStyle = (disabled, primary) => ({
        padding: '6px 12px',
        borderRadius: 4,
        border: '1px solid var(--line)',
        background: primary ? 'var(--dai)' : 'var(--paper)',
        color: primary ? 'var(--paper-raised)' : 'var(--ink)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        fontSize: 13,
    })

    const doExport = () => {
        setExporting(true)
        try {
            const payload = {
                export_metadata: {
                    exported_at: new Date().toISOString(),
                    book_name: bookTitle || bookRoot
                },
                basic_settings: settings,
                elements,
                arcs: {
                    next_chapter_num: arcs?.next_chapter_num,
                    arcs: (arcs?.arcs || []).map(a => ({
                        id: a.id,
                        name: a.name,
                        l1: a.l1,
                        l2: a.l2,
                        n_chapters: a.n_chapters,
                        status: a.status,
                        selected: a.selected,
                        finalized: a.finalized,
                        chapters: (a.chapters || []).map(c => ({
                            num: c.num,
                            title: c.title,
                            core: c.core,
                            intent_score: c.intent_score,
                            quality_score: c.quality_score,
                            overall: c.overall,
                            polluted: c.polluted,
                            text: c.text,
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
            onNotice?.('导出成功')
        } catch (e) {
            onError?.(`导出失败: ${e.message || e}`)
        } finally {
            setExporting(false)
        }
    }

    return (
        <div className="section-block" style={{ maxWidth: 920 }}>
            <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>
                导出完整书数据
            </div>
            <div style={{ fontSize: 13, color: 'var(--ink-sub)', lineHeight: 1.9, marginBottom: 12 }}>
                导出内容：基本设定 · 元素清单（角色/物品/设定）· 全部情节（含逐章双评分、污染标记、正文）·
                已落盘章号。JSON 下载便于备份/迁移。
            </div>
            <button
                style={btnStyle(exporting, true)}
                disabled={exporting}
                onClick={doExport}
            >
                {exporting ? '导出中…' : '导出 JSON'}
            </button>
        </div>
    )
}
