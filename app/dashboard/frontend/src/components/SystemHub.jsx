import { useState, useEffect } from 'react'
import { fetchFilesTree, phAiCancelTask } from '../api.js'
import DocEditor from './DocEditor.jsx'
import ExportPanel from './ExportPanel.jsx'
import BatchPanel from './BatchPanel.jsx'

export default function SystemHub({
    bookRoot,
    bookTitle,
    arcs,
    elements,
    settings,
    apiLib,
    batchStatus,
    batchTaskId,
    onNotice,
    onError,
    onComplete,
    onSyTabChange,
    syTab: externalSyTab,
}) {
    const [syTab, setSyTab] = useState(externalSyTab || 'overview')
    const [docTree, setDocTree] = useState(null)
    const [docDirty, setDocDirty] = useState(false)
    const [exporting, setExporting] = useState(false)

    // 同步外部 syTab
    useEffect(() => {
        if (externalSyTab && externalSyTab !== syTab) {
            setSyTab(externalSyTab)
        }
    }, [externalSyTab])

    // 通知父组件 syTab 变化
    useEffect(() => {
        onSyTabChange?.(syTab)
    }, [syTab])

    // 加载文档目录树
    useEffect(() => {
        if (!bookRoot) return
        const loadTree = async () => {
            try {
                const r = await fetchFilesTree(bookRoot)
                if (r?.ok) setDocTree(r.tree || {})
            } catch (e) {
                console.error('加载文档目录失败:', e)
            }
        }
        loadTree()
    }, [bookRoot])

    // 计算统计数据
    const list = arcs?.arcs || []
    const arcList = [...list].sort((a, b) => {
        const no = (name) => { const m = String(name || '').match(/(\d+)/); return m ? parseInt(m[1], 10) : 0 }
        return no(a.name) - no(b.name)
    })
    const finChapters = arcList.flatMap(a => (a.chapters || []).filter(c => c.text))
    const totalWords = finChapters.reduce((s, c) => s + (c.text ? c.text.length : 0), 0)
    const pollutedCount = finChapters.filter(c => c.polluted).length
    const overallAvg = finChapters.length ? finChapters.reduce((s, c) => s + (c.overall || 0), 0) / finChapters.length : 0

    // API 预设信息
    const presets = apiLib?.text_presets || []
    const curPreset = presets.find(p => p.is_current) || presets.find(p => p.id === apiLib?.current_text_id) || null
    const curModel = curPreset?.fields?.ARK_MODEL_PRO || ''
    const embedModel = apiLib?.embed_config?.fields?.EMBED_MODEL || ''

    // 最近文件
    const recentFiles = []
    if (docTree && typeof docTree === 'object') {
        for (const [folder, items] of Object.entries(docTree)) {
            if (!Array.isArray(items)) continue
            for (const item of items) {
                if (item.type === 'file') {
                    recentFiles.push({ folder, name: item.name, path: item.path })
                } else if (item.children) {
                    for (const sub of item.children) {
                        if (sub.type === 'file') recentFiles.push({ folder: item.name, name: sub.name, path: sub.path })
                    }
                }
            }
        }
    }
    const recent4 = recentFiles.slice(0, 4)

    // 批量进度
    const bp = batchStatus?.progress || {}
    const batchProg = batchStatus ? {
        done: bp.done || 0,
        total: bp.target || 0,
        current: bp.arc_name || '',
        step: bp.step || '',
    } : null
    const batchPct = batchProg && batchProg.total ? Math.round(batchProg.done / batchProg.total * 100) : 0

    // 系统项列表
    const sysItems = [
        {
            key: 'docedit', no: '壹', name: '文档', desc: '设定集 · 大纲 · 正文 · 在线编辑',
            status: docDirty ? 'warn' : 'good',
            statusText: docDirty ? '未保存' : '已保存',
            body: (
                <>
                    {recent4.length > 0 ? recent4.map(f => (
                        <div key={f.path} className="wb-filerow" onClick={() => setSyTab('docedit')}>
                            <span className="fn">{f.folder} / {f.name}</span>
                            <span className="ft">{f.folder}</span>
                        </div>
                    )) : (
                        <div style={{ fontSize: 10.5, color: 'var(--ink-mute)' }}>文档目录加载中…</div>
                    )}
                    {recentFiles.length > 4 && (
                        <div style={{ fontSize: 10, color: 'var(--ink-mute)' }}>…共 {recentFiles.length} 个文件</div>
                    )}
                    <div className="wb-siaction">
                        <button className="wb-btn" onClick={() => setSyTab('docedit')}>打开编辑器</button>
                    </div>
                </>
            ),
        },
        {
            key: 'health', no: '贰', name: '检索体检', desc: '六源联邦检索 · 健康状态',
            status: 'warn', statusText: '3 / 6 就绪',
            body: (
                <>
                    <div className="wb-sixsrc">
                        <div className="wb-srcrow"><span className="sdot ok"></span><span className="sn">本书索引</span><span className="sv">{finChapters.length} 章</span></div>
                        <div className="wb-srcrow"><span className="sdot ok"></span><span className="sn">语料持久</span><span className="sv">语料 500 章</span></div>
                        <div className="wb-srcrow"><span className="sdot ok"></span><span className="sn">模板库</span><span className="sv">已有</span></div>
                        <div className="wb-srcrow"><span className="sdot no"></span><span className="sn">CSV 数据</span><span className="sv">未配置</span></div>
                        <div className="wb-srcrow"><span className="sdot no"></span><span className="sn">参考文档</span><span className="sv">空</span></div>
                        <div className="wb-srcrow"><span className="sdot warn"></span><span className="sn">Web 搜索</span><span className="sv">key过期</span></div>
                    </div>
                    <div className="wb-siaction">
                        <button className="wb-btn" onClick={() => setSyTab('health')}>查看详情</button>
                    </div>
                </>
            ),
        },
        {
            key: 'api', no: '叁', name: '通道配置', desc: 'API 预设库 · 当前生效',
            status: 'good', statusText: '连接正常',
            body: (
                <>
                    <div className="wb-apipresets">
                        {curPreset && (
                            <div className="wb-apipreset current">
                                <span className="pdot"></span>
                                <span className="pn">{curPreset.name}</span>
                                <span className="pu">{curModel || '文字生成'}</span>
                            </div>
                        )}
                        <div className="wb-apipreset">
                            <span className="pdot" style={{ background: 'var(--amber)' }}></span>
                            <span className="pn">BGE-M3 · 硅基</span>
                            <span className="pu">{embedModel || '向量嵌入'}</span>
                        </div>
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--ink-mute)', marginTop: 4 }}>
                        共 {presets.length} 个预设
                    </div>
                    <div className="wb-siaction">
                        <button className="wb-btn" onClick={() => setSyTab('api')}>管理预设</button>
                    </div>
                </>
            ),
        },
        {
            key: 'export', no: '肆', name: '导出', desc: '备份 · 迁移 · 一键下载',
            status: '', statusText: '就绪',
            body: (
                <>
                    <div style={{ marginBottom: 4 }}>
                        导出：基本设定 · 元素清单 · 情节（阶梯+评分+正文）
                    </div>
                    <div className="wb-siaction">
                        <button className="wb-btn primary" disabled={exporting} onClick={() => setSyTab('export')}>
                            {exporting ? '导出中…' : '导出 JSON'}
                        </button>
                    </div>
                </>
            ),
        },
        {
            key: 'batch', no: '伍', name: '批量生成', desc: '逐情节流水线 · 无人值守',
            status: batchStatus ? 'warn' : '',
            statusText: batchStatus ? (batchStatus.status === 'running' ? '进行中' : batchStatus.status) : '就绪',
            body: (
                <>
                    {batchStatus ? (
                        <>
                            <div className="wb-batchprog">
                                <div className="wb-batchbar"><i style={{ width: `${batchPct}%` }}></i></div>
                                <span>{batchProg.done}/{batchProg.total} 章</span>
                            </div>
                            <div className="wb-batchinfo">
                                <span>当前：<b>{batchProg.current}</b></span>
                            </div>
                        </>
                    ) : (
                        <div style={{ fontSize: 10.5, color: 'var(--ink-mute)' }}>暂无进行中的批量任务</div>
                    )}
                    <div className="wb-siaction">
                        {batchStatus?.status === 'running' ? (
                            <button className="wb-btn danger" onClick={async () => {
                                if (batchTaskId) {
                                    try { await phAiCancelTask(batchTaskId) } catch {}
                                }
                            }}>⏸ 暂停</button>
                        ) : (
                            <button className="wb-btn primary" onClick={() => setSyTab('batch')}>▶ 新建批量</button>
                        )}
                    </div>
                </>
            ),
        },
    ]

    // 渲染子页
    const renderSubPage = () => {
        switch (syTab) {
            case 'docedit':
                return (
                    <DocEditor
                        bookRoot={bookRoot}
                        bookTitle={bookTitle}
                        onNotice={onNotice}
                        onError={onError}
                    />
                )
            case 'export':
                return (
                    <ExportPanel
                        bookRoot={bookRoot}
                        bookTitle={bookTitle}
                        arcs={arcs}
                        elements={elements}
                        settings={settings}
                        onNotice={onNotice}
                        onError={onError}
                    />
                )
            case 'batch':
                return (
                    <BatchPanel
                        bookRoot={bookRoot}
                        onNotice={onNotice}
                        onError={onError}
                        onComplete={onComplete}
                    />
                )
            case 'health':
                return <div>检索体检（待接入 SearchHealthPanel）</div>
            case 'api':
                return <div>通道配置（待接入 ApiConfig）</div>
            default:
                return null
        }
    }

    return (
        <>
            {/* 书头 */}
            <div className="wb-bookhead">
                <div className="wb-bhtitle">
                    <span className="vol" style={{ color: 'var(--xuan-dark)' }}>卷三</span>
                    <span>系统</span>
                    <span className="sub">· 卷末杂录 · 五项工具</span>
                </div>
                <div className="wb-bhmeta">
                    <span>检索 <b style={{ color: 'var(--amber)' }}>3/6</b></span><span className="sep">|</span>
                    <span>文件 <b>{recentFiles.length || '—'}</b></span><span className="sep">|</span>
                    <span>预设 <b>{presets.length}</b></span><span className="sep">|</span>
                    {batchStatus ? (
                        <span>批量 <b style={{ color: 'var(--amber)' }}>{batchPct}%</b></span>
                    ) : (
                        <span>批量 <b>空闲</b></span>
                    )}
                </div>
            </div>

            {/* 双栏书页 */}
            <div className="wb-page">
                {/* 左栏：五条系统项 */}
                <div className="wb-col wb-col-left">
                    <div className="wb-rubric">五项工具</div>
                    <div className="wb-scroll">
                        {sysItems.map(item => (
                            <div key={item.key} className="wb-sysitem" onClick={() => setSyTab(item.key)}>
                                <div className="wb-sihead">
                                    <span className="wb-sino">{item.no}</span>
                                    <span className="wb-siname">{item.name}</span>
                                    <span className="wb-sidesc">{item.desc}</span>
                                    <span className={`wb-sistatus ${item.status}`}>{item.statusText}</span>
                                </div>
                                <div className="wb-sibody">{item.body}</div>
                            </div>
                        ))}
                    </div>
                </div>

                {/* 右栏：系统状态 + 日志 */}
                <div className="wb-col wb-col-right">
                    <div className="wb-rubric">系统状态</div>
                    <div className="wb-sysside">
                        <div className="wb-syskv">
                            <span className="k">书</span><span className="v">{bookTitle || '—'}</span>
                            <span className="k">情节</span><span className="v">{arcList.length}</span>
                            <span className="k">已落盘</span><span className="v">{finChapters.length} 章</span>
                            <span className="k">总字数</span><span className="v">{totalWords.toLocaleString()}</span>
                            <span className="k">元素数</span><span className="v">{Object.values(elements || {}).flat().length}</span>
                            <span className="k">污染章</span><span className={`v ${pollutedCount ? 'bad' : 'good'}`}>{pollutedCount}</span>
                            <span className="k">均分</span><span className="v">{overallAvg ? overallAvg.toFixed(3) : '—'}</span>
                        </div>

                        <div className="wb-rubric" style={{ marginTop: 2 }}>快捷操作</div>
                        <div className="wb-btnrow" style={{ flexWrap: 'wrap', gap: 4 }}>
                            <button className="wb-btn">重建索引</button>
                            <button className="wb-btn">清缓存</button>
                        </div>
                    </div>
                </div>
            </div>

            {/* 子页全屏展开 */}
            {syTab !== 'overview' && (
                <div style={{
                    position: 'absolute', inset: 0, background: 'var(--paper-deep)', zIndex: 20,
                    padding: 16, overflow: 'auto',
                }}>
                    <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 12 }}>
                        <button className="wb-btn" onClick={() => setSyTab('overview')}>← 返回系统总览</button>
                        <span style={{ fontFamily: 'var(--font-serif)', fontSize: 16, fontWeight: 700 }}>
                            {syTab === 'docedit' && '文档'}
                            {syTab === 'health' && '检索体检'}
                            {syTab === 'api' && '通道配置'}
                            {syTab === 'export' && '导出'}
                            {syTab === 'batch' && '批量生成'}
                        </span>
                    </div>
                    {renderSubPage()}
                </div>
            )}
        </>
    )
}
