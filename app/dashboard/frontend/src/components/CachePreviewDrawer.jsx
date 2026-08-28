import { useCallback, useEffect, useState } from 'react'
import { fetchCachePreview } from '../api.js'
import Badge from './Badge.jsx'
import DataTable from './DataTable.jsx'
import EditableCell from './EditableCell.jsx'

function formatSize(bytes) {
    if (!bytes) return "0 B"
    const k = 1024, units = ["B", "KB", "MB", "GB"]
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + units[i]
}

function tryPrettyJson(text) {
    if (!text) return text
    const trimmed = text.trim()
    if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return text
    try {
        return JSON.stringify(JSON.parse(trimmed), null, 2)
    } catch {
        return text
    }
}

function TextContent({ content, truncated, totalSize, maxBytes }) {
    const pretty = tryPrettyJson(content)
    return (
        <div className="preview-text-wrap">
            {truncated && (
                <div className="preview-trunc-banner">
                    文件被截断：仅显示前 {formatSize(maxBytes || 0)} / 总 {formatSize(totalSize || 0)}
                </div>
            )}
            <pre className="preview-text">{pretty}</pre>
        </div>
    )
}

function pickRowPk(row, pkColumns) {
    if (!pkColumns || !pkColumns.length) return undefined
    const out = {}
    for (const pk of pkColumns) {
        if (row[pk] !== undefined) out[pk] = row[pk]
    }
    return Object.keys(out).length ? out : undefined
}

function formatCellValue(v) {
    if (v === null || v === undefined) return <em className="text-gray">NULL</em>
    if (typeof v === "object") return JSON.stringify(v)
    const s = String(v)
    if (s.length > 120) return s.slice(0, 120) + "..."
    return s
}

function SqliteView({ cacheId, data }) {
    const tables = data.tables || []
    const rowsByTable = data.rows || {}
    const [activeTable, setActiveTable] = useState(tables[0]?.name || "")
    if (!tables.length) return <p className="text-gray">数据库无表</p>
    const currentTable = tables.find((t) => t.name === activeTable) || tables[0]
    const sampleRows = rowsByTable[currentTable.name] || []
    const editable = !!data.editable
    const editableCols = new Set(data.editable_columns || [])

    const columns = (currentTable.columns || [])
        .filter((c) => !(c.type || "").toUpperCase().includes("BLOB"))
        .map((c) => {
            const isEditableCol = editable && editableCols.has(c.name)
            if (isEditableCol) {
                return {
                    key: c.name,
                    label: c.name + " *",
                    render: (row) => (
                        <EditableCell
                            cacheId={cacheId}
                            table={currentTable.name}
                            rowPk={pickRowPk(row, currentTable.pk_columns)}
                            column={c.name}
                            value={row[c.name]}
                        />
                    ),
                }
            }
            return {
                key: c.name,
                label: c.name,
                render: (row) => <span className="preview-cell-mono">{formatCellValue(row[c.name])}</span>,
            }
        })

    return (
        <div className="preview-sqlite">
            {editable && (
                <div className="preview-edit-hint">
                    双击 content 单元格可编辑，停止输入 1.5 秒后自动保存
                </div>
            )}
            <div className="preview-table-tabs">
                {tables.map((t) => (
                    <button
                        key={t.name}
                        className={"preview-tab " + (t.name === activeTable ? "active" : "")}
                        onClick={() => setActiveTable(t.name)}
                    >
                        {t.name}
                        <span className="text-gray text-xs ml-1">({(rowsByTable[t.name] || []).length})</span>
                    </button>
                ))}
            </div>
            <div className="preview-table-schema">
                {currentTable.columns?.map((c) => (
                    <span key={c.name} className="preview-schema-col">
                        <code>{c.name}</code>
                        <em className="text-gray text-xs">{c.type}{c.pk ? " PK" : ""}</em>
                    </span>
                ))}
            </div>
            <DataTable
                columns={columns}
                rows={sampleRows}
                rowKey={(r, i) => pickRowPk(r, currentTable.pk_columns) || i}
                pageSize={50}
                emptyText="（空表）"
            />
        </div>
    )
}

function FileListOnly({ data }) {
    return (
        <div className="preview-files-list">
            <p className="text-gray p-2">此缓存为二进制格式，仅显示文件列表（前 50 个）</p>
            <ul>
                {(data.files || []).slice(0, 50).map((f) => (
                    <li key={f.name} className="preview-file-item">
                        <span>{f.name}</span>
                        <span className="text-gray text-xs">{formatSize(f.size)}</span>
                    </li>
                ))}
            </ul>
        </div>
    )
}

function FileSidebar({ files, contentExt, selected, onSelect }) {
    if (!files.length) return <p className="text-gray p-3">（空目录）</p>
    return (
        <ul className="preview-file-sidebar">
            {files.map((f) => {
                const readable = contentExt.length === 0 || contentExt.some((e) => f.name.toLowerCase().endsWith(e))
                return (
                    <li
                        key={f.name}
                        className={"preview-file-item " + (selected === f.name ? "active" : "") + " " + (readable ? "" : "dim")}
                        onClick={() => readable && onSelect(f.name)}
                        title={readable ? "查看 " + f.name : f.name + "（不可预览）"}
                    >
                        <span className="preview-file-name">{f.name}</span>
                        <span className="text-gray text-xs">{formatSize(f.size)}</span>
                    </li>
                )
            })}
        </ul>
    )
}

function renderBody(data, selectedFile, onSelectFile, cache) {
    if (data.exists === false) {
        return <p className="text-center text-gray py-4">（缓存项不存在于磁盘）</p>
    }
    const exts = data.content_ext || cache?.content_ext || []
    if (data.kind === "binary") return <FileListOnly data={data} />
    if (data.kind === "single_file") {
        return <TextContent content={data.content} truncated={data.truncated} totalSize={data.total_size} maxBytes={data.max_bytes} />
    }
    if (data.kind === "files") {
        return (
            <div className="preview-files-layout">
                <FileSidebar files={data.files || []} contentExt={exts} selected={selectedFile} onSelect={onSelectFile} />
                <div className="preview-content">
                    {data.content != null
                        ? <TextContent content={data.content} truncated={data.truncated} totalSize={data.total_size} maxBytes={data.max_bytes} />
                        : selectedFile
                            ? <p className="text-gray p-3">该文件类型不可预览（不在白名单 {exts.join(", ")} 内）</p>
                            : <p className="text-gray p-3">请从左侧选择文件</p>}
                </div>
            </div>
        )
    }
    if (data.kind === "sqlite") return <SqliteView cacheId={cache.id} data={data} />
    return <p className="text-gray">未知类型：{data.kind}</p>
}

export default function CachePreviewDrawer({ cache, onClose }) {
    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState(null)
    const [selectedFile, setSelectedFile] = useState(null)
    const open = !!cache

    const load = useCallback(async (cacheId, filePath = null) => {
        setLoading(true)
        setError(null)
        try {
            const res = await fetchCachePreview(cacheId, filePath)
            setData(res)
            if (res.kind === "files" && !filePath && res.files?.length) {
                const exts = res.content_ext || []
                const first = res.files.find((f) => exts.some((e) => f.name.toLowerCase().endsWith(e))) || res.files[0]
                if (first?.name) {
                    setSelectedFile(first.name)
                    const res2 = await fetchCachePreview(cacheId, first.name)
                    setData(res2)
                }
            }
        } catch (e) {
            setError(e.message || "加载失败")
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        if (cache) {
            setData(null)
            setError(null)
            setSelectedFile(null)
            load(cache.id)
        } else {
            // 关闭抽屉：同步清空 data/selectedFile，避免后续渲染访问 null.cache.id
            setData(null)
            setError(null)
            setSelectedFile(null)
        }
    }, [cache?.id])

    useEffect(() => {
        if (!open) return
        const onKey = (e) => { if (e.key === "Escape") onClose() }
        window.addEventListener("keydown", onKey)
        return () => window.removeEventListener("keydown", onKey)
    }, [open, onClose])

    const onSelectFile = async (name) => {
        setSelectedFile(name)
        if (data?.kind === "files") {
            setLoading(true)
            try {
                const res = await fetchCachePreview(cache.id, name)
                setData(res)
            } catch (e) {
                setError(e.message)
            } finally {
                setLoading(false)
            }
        }
    }

    return (
        <>
            <div className={"drawer-backdrop " + (open ? "open" : "")} onClick={onClose} />
            <aside className={"drawer " + (open ? "open" : "")}>
                <header className="drawer-header">
                    <div>
                        <h2>{cache?.name || "预览"}</h2>
                        <div className="drawer-meta">
                            {cache && <Badge tone={cache.type === "安全可删" ? "green" : cache.type === "谨慎删除" ? "amber" : "red"}>{cache.type}</Badge>}
                            {cache?.dangerous && <Badge tone="red">危险</Badge>}
                            {cache?.editable && <Badge tone="cyan">可编辑</Badge>}
                            {cache?.path && <code className="drawer-path">{cache.path}</code>}
                        </div>
                    </div>
                    <button className="drawer-close" onClick={onClose} title="关闭 (Esc)">x</button>
                </header>
                <div className="drawer-body">
                    {error && <div className="preview-error">{"⚠ " + error}</div>}
                    {loading && !data && <p className="text-center text-gray py-4">加载中…</p>}
                    {data && cache && renderBody(data, selectedFile, onSelectFile, cache)}
                </div>
            </aside>
        </>
    )
}
