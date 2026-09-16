import React, { useEffect, useState } from "react";
import { on, restGet, restPost, restDel, state as bstate, PH } from "../shared/bridge.js";

// 系统面板：检索体检 · API 预设库（文字+向量完整CRUD） · 批量生成

function maskDisplay(val, isSecret) {
  if (!val) return "(未设)";
  if (!isSecret) return val;
  if (val.length <= 8) return "*".repeat(val.length);
  return val.slice(0, 4) + "****" + val.slice(-4);
}

export default function SystemPanel() {
  const [bookRoot, setBookRoot] = useState(bstate.bookRoot);
  const [tab, setTab] = useState("health");
  const [, setNotice] = useState("");
  const [health, setHealth] = useState(null);
  const [batchTarget, setBatchTarget] = useState("6");
  const [batchPerArc, setBatchPerArc] = useState("3");
  const [batchBriefs, setBatchBriefs] = useState("");
  const [batchStatus, setBatchStatus] = useState(null);

  // ── API 预设库 ──
  const [lib, setLib] = useState(null);
  const [libMsg, setLibMsg] = useState("");
  const [libErr, setLibErr] = useState("");
  const [editingText, setEditingText] = useState(null);  // {id, name, fields, secretsConfigured}
  const [editingEmbed, setEditingEmbed] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => on("init", (s) => setBookRoot(s.bookRoot)), []);
  useEffect(() => on("bookChanged", (s) => setBookRoot(s.bookRoot)), []);

  useEffect(() => {
    if (!bookRoot || tab !== "health") return;
    void restGet(`${PH}/ai-creation/search/sources?book_root=${encodeURIComponent(bookRoot)}`, false)
      .then(setHealth).catch(() => {});
  }, [bookRoot, tab]);

  const reloadLib = () => {
    setLibErr(""); setLibMsg("");
    restGet("/api/api-library")
      .then((r) => setLib(r))
      .catch((e) => setLibErr(e.message || "加载失败"));
  };
  useEffect(() => { if (tab === "presets") reloadLib(); }, [tab]);

  const startNewText = () => setEditingText({ id: null, name: "", fields: {}, secretsConfigured: {} });
  const startEditText = (p) => {
    const f = {};
    for (const m of lib?.text_fields || []) f[m.key] = m.secret ? "" : (p.fields?.[m.key] || "");
    setEditingText({ id: p.id, name: p.name, fields: f, secretsConfigured: p.fields || {} });
  };
  const startNewEmbed = () => setEditingEmbed({ id: null, name: "", fields: {}, secretsConfigured: {} });
  const startEditEmbed = (p) => {
    const f = {};
    for (const m of lib?.embed_fields || []) f[m.key] = m.secret ? "" : (p.fields?.[m.key] || "");
    setEditingEmbed({ id: p.id, name: p.name, fields: f, secretsConfigured: p.fields || {} });
  };

  const saveText = () => {
    if (!editingText.name.trim()) { setLibErr("请填预设名称"); return; }
    setSaving(true); setLibErr("");
    restPost("/api/api-library", { id: editingText.id, name: editingText.name, fields: editingText.fields })
      .then(() => { setEditingText(null); setLibMsg(editingText.id ? "已更新文字模型预设" : "已新建文字模型预设"); reloadLib(); })
      .catch((e) => setLibErr(e.message || "保存失败"))
      .finally(() => setSaving(false));
  };
  const saveEmbed = () => {
    if (!editingEmbed.name.trim()) { setLibErr("请填预设名称"); return; }
    setSaving(true); setLibErr("");
    restPost("/api/api-library/embed", { id: editingEmbed.id, name: editingEmbed.name, fields: editingEmbed.fields })
      .then(() => { setEditingEmbed(null); setLibMsg(editingEmbed.id ? "已更新向量模型预设" : "已新建向量模型预设"); reloadLib(); })
      .catch((e) => setLibErr(e.message || "保存失败"))
      .finally(() => setSaving(false));
  };
  const delText = (p) => {
    if (!window.confirm(`删除文字模型预设「${p.name}」？`)) return;
    restDel(`/api/api-library/${encodeURIComponent(p.id)}`)
      .then(() => { setLibMsg(`已删除「${p.name}」`); reloadLib(); })
      .catch((e) => setLibErr(e.message || "删除失败"));
  };
  const delEmbed = (p) => {
    if (!window.confirm(`删除向量模型预设「${p.name}」？`)) return;
    restDel(`/api/api-library/embed/${encodeURIComponent(p.id)}`)
      .then(() => { setLibMsg(`已删除「${p.name}」`); reloadLib(); })
      .catch((e) => setLibErr(e.message || "删除失败"));
  };
  const applyText = (p) => {
    restPost("/api/api-library/apply", { id: p.id })
      .then(() => { setLibMsg(`已应用文字模型预设「${p.name}」`); reloadLib(); })
      .catch((e) => setLibErr(e.message || "应用失败"));
  };
  const applyEmbed = (p) => {
    restPost("/api/api-library/embed/apply", { id: p.id })
      .then(() => { setLibMsg(`已应用向量模型预设「${p.name}」`); reloadLib(); })
      .catch((e) => setLibErr(e.message || "应用失败"));
  };

  const rebuildIndex = async () => {
    try {
      await restPost(`${PH}/ai-creation/search/rebuild`, { book_root: bookRoot });
      alertMsg("检索索引重建已启动（语料在后台）");
    } catch (e) { alertMsg(`⚠ ${e.message}`); }
  };

  const startBatch = async () => {
    try {
      const r = await restPost(`${PH}/ai-creation/batch-generate`, {
        book_root: bookRoot,
        target_chapters: Number(batchTarget) || 6,
        n_chapters_per_arc: Number(batchPerArc) || 3,
        arc_briefs: batchBriefs.split("\n").map((s) => s.trim()).filter(Boolean),
      });
      if (r.task_id) poll(r.task_id);
      else setBatchStatus(JSON.stringify(r));
    } catch (e) { alertMsg(`⚠ ${e.message}`); }
  };

  const poll = async (taskId) => {
    for (;;) {
      try {
        const s = await restGet(`/api/prompt-harness/optimize/status/${taskId}`, false);
        setBatchStatus(s?.progress ? `${s.status}：${JSON.stringify(s.progress)}` : s?.status);
        if (s?.status === "done" || s?.status === "error" || s?.status === "cancelled") break;
      } catch { break; }
      await new Promise((r) => setTimeout(r, 2000));
    }
  };

  function alertMsg(m) { setNotice(m); setTimeout(() => setNotice(""), 4000); }

  /** 预设卡列表（文字/向量共用） */
  const presetList = (presets, currentId, kind) => (
    <div className="preset-list">
      {(presets || []).map((p) => (
        <div key={p.id} className={`preset-card ${p.id === currentId ? "current" : ""}`}>
          <div className="preset-head">
            <strong>{p.name}</strong>
            {p.id === currentId && <span className="an-tag" style={{ color: "var(--amber)" }}>当前</span>}
            <span style={{ flex: 1 }} />
            <button className="primary" onClick={() => (kind === "text" ? applyText(p) : applyEmbed(p))}>应用</button>
            <button onClick={() => (kind === "text" ? startEditText(p) : startEditEmbed(p))}>编辑</button>
            <button className="an-bad" onClick={() => (kind === "text" ? delText(p) : delEmbed(p))}>删除</button>
          </div>
          <div className="an-muted">
            模型：{maskDisplay(p.fields?.EMBED_MODEL || p.fields?.ARK_MODEL_PRO || p.fields?.model || "", false)}
          </div>
        </div>
      ))}
      {!(presets || []).length && <div className="an-muted">尚无预设，点「＋ 新建预设」添加。</div>}
    </div>
  );

  /** 预设编辑表单（文字/向量共用） */
  const editForm = (editing, meta, setEditing, onSave) => (
    <div className="preset-edit">
      <div className="an-title">{editing.id ? "编辑预设" : "新建预设"}</div>
      <div className="an-row" style={{ margin: "8px 0" }}>
        <label className="an-muted" style={{ minWidth: 110 }}>预设名称</label>
        <input type="text" style={{ flex: 1 }} value={editing.name} placeholder="如 豆包pro / BGE-M3"
          onChange={(e) => setEditing((ed) => ({ ...ed, name: e.target.value }))} />
      </div>
      {(meta || []).map((m) => {
        const configured = editing.secretsConfigured?.[m.key];
        return (
          <div className="an-row" style={{ marginBottom: 6 }} key={m.key}>
            <label className="an-muted" style={{ minWidth: 110 }}>{m.label || m.key}</label>
            <input type="text" style={{ flex: 1 }}
              value={editing.fields[m.key] || ""}
              placeholder={m.secret
                ? (configured ? `已配置(${maskDisplay(configured, true)})·输入新值覆盖，留空保留` : (m.placeholder || ""))
                : (m.placeholder || "")}
              onChange={(e) => setEditing((ed) => ({ ...ed, fields: { ...ed.fields, [m.key]: e.target.value } }))} />
          </div>
        );
      })}
      <div className="an-row" style={{ marginTop: 10 }}>
        <button className="primary" disabled={saving} onClick={onSave}>{saving ? "保存中…" : "💾 保存"}</button>
        <button disabled={saving} onClick={() => setEditing(null)}>取消</button>
      </div>
    </div>
  );

  return (
    <div className="an-col">
      <div className="modebar">
        {[["health", "🔍 检索体检"], ["presets", "🔌 API 预设库"], ["batch", "📦 批量生成"]].map(([k, label]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      <div className="an-scroll">
        {!bookRoot && tab !== "presets" && <div className="emptyguide"><h3>未选书</h3></div>}

        {tab === "health" && bookRoot && (
          <>
            <div className="an-row" style={{ marginBottom: 10 }}>
              <button className="primary" onClick={() => void rebuildIndex()}>重建检索索引</button>
            </div>
            <table className="an-table">
              <thead><tr><th>源</th><th>状态</th><th>详情</th></tr></thead>
              <tbody>
                {(health?.sources ? Object.entries(health.sources) : []).map(([k, v]) => (
                  <tr key={k}>
                    <td>{k}</td>
                    <td className={v?.ok ? "an-ok" : v?.ok === false ? "an-warn" : ""}>{v?.ok === false ? "⚠ 空/异常" : v?.ok ? "✓" : "-"}</td>
                    <td className="an-muted">{String(v?.detail ?? "").slice(0, 80)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        {tab === "presets" && (
          <>
            {libMsg && <div className="an-ok" style={{ marginBottom: 8 }}>✓ {libMsg}</div>}
            {libErr && <div className="an-bad" style={{ marginBottom: 8 }}>❌ {libErr}</div>}
            {!lib && <div className="an-muted">加载中…</div>}
            {lib && (
              <>
                <div className="an-card">
                  <div className="an-row">
                    <div className="an-title">📝 文字模型预设</div>
                    <span style={{ flex: 1 }} />
                    <button className="primary" onClick={startNewText}>＋ 新建预设</button>
                  </div>
                  <div className="an-muted" style={{ margin: "4px 0 8px" }}>多组文字模型 API 配置，应用后生成/对话即时切换（无需重启）。</div>
                  {presetList(lib.text_presets, lib.current_text_id, "text")}
                  {editingText && editForm(editingText, lib.text_fields, setEditingText, saveText)}
                </div>
                <div className="an-card">
                  <div className="an-row">
                    <div className="an-title">🧲 向量模型预设</div>
                    <span style={{ flex: 1 }} />
                    <button className="primary" onClick={startNewEmbed}>＋ 新建预设</button>
                  </div>
                  <div className="an-muted" style={{ margin: "4px 0 8px" }}>多组 Embedding 配置，检索/语料向量化用。</div>
                  {presetList(lib.embed_presets, lib.current_embed_id, "embed")}
                  {editingEmbed && editForm(editingEmbed, lib.embed_fields, setEditingEmbed, saveEmbed)}
                </div>
              </>
            )}
          </>
        )}

        {tab === "batch" && bookRoot && (
          <>
            <div className="an-card">
              <div className="an-title">批量生成全书（逐弧流水线 l1→l5→落盘）</div>
              <div className="an-row" style={{ marginTop: 8 }}>
                <label className="an-muted">目标章数</label>
                <input type="text" style={{ width: 70 }} value={batchTarget} onChange={(e) => setBatchTarget(e.target.value)} />
                <label className="an-muted">每弧章数</label>
                <input type="text" style={{ width: 70 }} value={batchPerArc} onChange={(e) => setBatchPerArc(e.target.value)} />
              </div>
              <textarea rows={4} style={{ width: "100%", marginTop: 8 }}
                placeholder="每行一条 = 每弧的 l1；留空自动生成"
                value={batchBriefs} onChange={(e) => setBatchBriefs(e.target.value)} />
              <button className="primary" style={{ marginTop: 8 }} onClick={() => void startBatch()}>🚀 启动</button>
            </div>
            {batchStatus && <pre className="an-card an-muted">{batchStatus}</pre>}
          </>
        )}
      </div>
    </div>
  );
}
