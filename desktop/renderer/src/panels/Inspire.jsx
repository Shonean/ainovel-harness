import React, { useEffect, useRef, useState } from "react";
import { on, restGet, restPost, restPut, restDel, state as bstate, PH } from "../shared/bridge.js";

// 灵感工坊（完整版）：左 卡片墙+关系面板 | 中 四机制对话（可挂卡片上下文） | 右 记忆+候选审批
// 数据源与旧 dashboard 同一套端点（elements.json / pending / memory）。

const K_LABEL = { c: "角色", i: "物品", s: "设定", map: "地图" };
const K_EMOJI = { c: "👤", i: "🗡️", s: "🧩", map: "🗺️" };
const K_COLOR = { c: "var(--cinnabar)", i: "var(--amber)", s: "var(--dai)", map: "var(--green)" };
const KIND_TO_ELEM = { c: "characters", i: "items", s: "settings", map: "maps" };

const MECHS = [
  { id: "seed", icon: "🧬", name: "种子推演", desc: "从一个点按因果链条展开", scene: "有灵感没展开 / 新书起点" },
  { id: "analog", icon: "🧭", name: "类比迁移", desc: "把现实结构骨架移植到小说", scene: "搭设定体系 / 要真实感" },
  { id: "invert", icon: "🔄", name: "约束反转", desc: "列烂套路→逐条反转→找空白", scene: "大纲卡住 / 反套路" },
  { id: "free", icon: "💬", name: "自由对话", desc: "不套框架，想到什么聊什么", scene: "泛讨论 / 兜底" },
];

const est = (s) => String(s == null ? "" : s);

export default function InspirePanel() {
  const [bookRoot, setBookRoot] = useState(bstate.bookRoot);
  const [mech, setMech] = useState("seed");
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [suggestions, setSuggestions] = useState([]);
  const boxRef = useRef(null);

  // ── 卡片 ──
  const [elements, setElements] = useState(null);
  const [sel, setSel] = useState(null); // {kind, id}
  const [editCard, setEditCard] = useState(null);
  const [editFields, setEditFields] = useState([]);
  const [relFrom, setRelFrom] = useState(null);
  const [relTo, setRelTo] = useState("");
  const [relName, setRelName] = useState("");
  const [relMult, setRelMult] = useState("1:1");

  // ── 记忆 / 待审批 ──
  const [memory, setMemory] = useState([]);
  const [memInput, setMemInput] = useState("");
  const [pending, setPending] = useState([]);
  const [editingPid, setEditingPid] = useState(null);
  const [editingText, setEditingText] = useState("");
  const [editingName, setEditingName] = useState("");
  const [editingDesc, setEditingDesc] = useState("");
  const [editingFields, setEditingFields] = useState([]);

  useEffect(() => on("init", (s) => setBookRoot(s.bookRoot)), []);
  useEffect(() => on("bookChanged", (s) => setBookRoot(s.bookRoot)), []);
  useEffect(() => {
    if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [msgs, suggestions]);

  const loadAll = (root) => {
    if (!root) return;
    const enc = encodeURIComponent(root);
    restGet(`${PH}/ai-creation/elements?book_root=${enc}`, false).then(setElements).catch(() => setElements({}));
    restGet(`${PH}/ai-creation/memory?book_root=${enc}`, false).then((r) => setMemory(r.items || [])).catch(() => {});
    restGet(`${PH}/ai-creation/pending?book_root=${enc}`, false).then((r) => setPending(r.items || [])).catch(() => {});
  };
  useEffect(() => { loadAll(bookRoot); }, [bookRoot]);

  // ── 卡片数据 ──
  const allCards = () => {
    const out = [];
    for (const [kind, coll] of Object.entries(KIND_TO_ELEM)) {
      for (const e of elements?.[coll] || []) {
        out.push({ kind, id: e.id, name: e.name, desc: e.desc, fields: e.fields || [], relations: e.relations || [], layout: e.layout || [] });
      }
    }
    return out;
  };
  const findCard = (kind, id) => allCards().find((c) => c.kind === kind && c.id === id);
  const findCardByAny = (key) => allCards().find((c) => `${c.kind}:${c.id}` === key);

  const addCard = async (kind, name) => {
    if (!bookRoot || !name) return;
    try {
      await restPost(`${PH}/ai-creation/element`, { book_root: bookRoot, kind, name });
      loadAll(bookRoot);
    } catch (e) { setMsgs((m) => [...m, { role: "assistant", content: `⚠ 建卡失败：${e.message}` }]); }
  };
  const updateCard = async (kind, id, patch) => {
    if (!bookRoot) return;
    try {
      await restPut(`${PH}/ai-creation/element/${kind}/${encodeURIComponent(id)}`,
        { book_root: bookRoot, kind, id, ...patch }, false);
      loadAll(bookRoot);
    } catch (e) { setMsgs((m) => [...m, { role: "assistant", content: `⚠ 保存失败：${e.message}` }]); }
  };
  const deleteCard = async (kind, id) => {
    if (!bookRoot) return;
    try {
      await restDel(`${PH}/ai-creation/element/${kind}/${encodeURIComponent(id)}?book_root=${encodeURIComponent(bookRoot)}`, false);
      if (sel?.kind === kind && sel?.id === id) setSel(null);
      loadAll(bookRoot);
    } catch (e) { setMsgs((m) => [...m, { role: "assistant", content: `⚠ 删除失败：${e.message}` }]); }
  };

  const openEditCard = (c) => {
    setEditCard(c);
    setEditFields((c.fields || []).map((f) => ({ name: f.name || "", value: f.value || "" })));
  };
  const saveEditCard = async () => {
    const fields = editFields.filter((f) => f.name && f.value);
    await updateCard(editCard.kind, editCard.id, { name: editCard.name, desc: editCard.desc, fields, relations: editCard.relations });
    setEditCard(null);
  };
  const saveRel = async () => {
    const target = findCardByAny(relTo);
    if (!target || !relFrom) return;
    const relations = [...(relFrom.relations || []), { to_kind: target.kind, to_id: target.id, name: relName || "关联", mult: relMult }];
    await updateCard(relFrom.kind, relFrom.id,
      { name: relFrom.name, desc: relFrom.desc, fields: relFrom.fields, relations });
    setRelFrom(null);
  };

  // ── 对话 ──
  const send = async () => {
    const text = input.trim();
    if (!text || busy || !bookRoot) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", content: text }]);
    setBusy(true);
    try {
      const history = msgs.map((m) => ({ role: m.role === "user" ? "user" : "assistant", content: m.content }));
      let ctx = null;
      if (sel) {
        const c = findCard(sel.kind, sel.id);
        if (c) ctx = { kind: sel.kind, name: c.name, desc: c.desc, fields: c.fields, relations: c.relations };
      }
      const r = await restPost(`${PH}/ai-creation/inspire/chat`,
        { book_root: bookRoot, messages: [...history, { role: "user", content: text }], mech, ctx });
      setMsgs((m) => [...m, { role: "assistant", content: r.reply || "" }]);
      if (r.suggested_edits?.length) setSuggestions((s) => [...s, ...r.suggested_edits]);
      if (r.pending_count > 0) {
        restGet(`${PH}/ai-creation/pending?book_root=${encodeURIComponent(bookRoot)}`, false)
          .then((rr) => setPending(rr.items || [])).catch(() => {});
      }
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", content: `⚠ ${e.message}` }]);
    } finally {
      setBusy(false);
    }
  };

  const applySuggestion = async (s, i) => {
    const c = findCard(s.kind, s.id);
    if (c) {
      await updateCard(c.kind, c.id, {
        name: c.name, desc: s.desc ?? c.desc, fields: s.fields || c.fields, relations: s.relations || c.relations,
      });
    }
    setSuggestions((arr) => arr.filter((_, j) => j !== i));
  };

  // ── 记忆 ──
  const addMemory = async () => {
    const text = memInput.trim();
    if (!text || !bookRoot) return;
    setMemInput("");
    try {
      await restPost(`${PH}/ai-creation/memory`, { book_root: bookRoot, text });
      const r = await restGet(`${PH}/ai-creation/memory?book_root=${encodeURIComponent(bookRoot)}`, false);
      setMemory(r.items || []);
    } catch { /* 静默 */ }
  };
  const delMemory = async (id) => {
    try {
      await restDel(`${PH}/ai-creation/memory/${encodeURIComponent(id)}?book_root=${encodeURIComponent(bookRoot)}`, false);
      setMemory((m) => m.filter((x) => x.id !== id));
    } catch { /* 静默 */ }
  };

  // ── 待审批 ──
  const approve = async (p, edits = {}) => {
    try {
      await restPost(`${PH}/ai-creation/pending/${encodeURIComponent(p.id)}/approve`, { book_root: bookRoot, ...edits });
      setEditingPid(null);
      loadAll(bookRoot);
    } catch { /* 静默 */ }
  };
  const reject = async (pid) => {
    try {
      await restPost(`${PH}/ai-creation/pending/${encodeURIComponent(pid)}/reject?book_root=${encodeURIComponent(bookRoot)}`, {}, false);
      setPending((p) => p.filter((x) => x.id !== pid));
    } catch { /* 静默 */ }
  };
  const approveAll = async () => {
    try {
      await restPost(`${PH}/ai-creation/pending/approve-all`, { book_root: bookRoot });
      loadAll(bookRoot);
    } catch { /* 静默 */ }
  };
  const rejectAll = async () => {
    try {
      await restPost(`${PH}/ai-creation/pending/reject-all?book_root=${encodeURIComponent(bookRoot)}`, {}, false);
      setPending([]);
    } catch { /* 静默 */ }
  };
  const startEditPending = (p) => {
    setEditingPid(p.id);
    if (p.type === "mem") setEditingText(p.text || "");
    else {
      setEditingName(p.name || "");
      setEditingDesc(p.desc || "");
      setEditingFields((p.fields || []).map((f) => ({ name: f.name || "", value: f.value || "" })));
    }
  };
  const saveEditPending = async (p) => {
    if (p.type === "mem") {
      const text = editingText.trim();
      if (text) await approve(p, { text });
    } else {
      const fields = editingFields.filter((f) => f.name && f.value);
      await approve(p, { name: editingName.trim(), desc: editingDesc, fields });
    }
  };

  // ── 关系面板 ──
  const relPanel = () => {
    const c = sel ? findCard(sel.kind, sel.id) : null;
    if (!c) return <div className="insp-empty">点一张素材卡<br />这里显示它的关系</div>;
    const out = (c.relations || []).map((r) => {
      const t = findCard(r.to_kind, r.to_id);
      return (
        <div className="insp-rel" key={`${r.to_kind}:${r.to_id}`} onClick={() => t && setSel({ kind: t.kind, id: t.id })}>
          <span className="insp-rel-name">{est(r.name)}</span>
          <span className="insp-rel-arrow">→</span>
          <span className="insp-rel-target">{t ? `${K_EMOJI[t.kind]} ${est(t.name)}` : est(r.to_id)}</span>
          <span className="insp-rel-mult">{est(r.mult)}</span>
        </div>
      );
    });
    const incoming = allCards()
      .filter((x) => x.kind !== c.kind || x.id !== c.id)
      .filter((x) => (x.relations || []).some((r) => r.to_kind === c.kind && r.to_id === c.id))
      .map((x) => {
        const r = x.relations.find((rr) => rr.to_kind === c.kind && rr.to_id === c.id);
        return (
          <div className="insp-rel incoming" key={x.id}>
            <span className="insp-rel-name">{est(r.name)}</span>
            <span className="insp-rel-arrow">←</span>
            <span className="insp-rel-target">{K_EMOJI[x.kind]} {est(x.name)}</span>
            <button className="insp-rel-del" onClick={() => {
              const rels = (x.relations || []).filter((rr) => !(rr.to_kind === c.kind && rr.to_id === c.id));
              void updateCard(x.kind, x.id, { name: x.name, desc: x.desc, fields: x.fields, relations: rels });
            }}>✕</button>
          </div>
        );
      });
    return (
      <div>
        <div className="insp-rel-head">{K_EMOJI[c.kind]} {est(c.name)}</div>
        <button className="insp-btn-add" onClick={() => { setRelFrom(c); setRelTo(""); setRelName(""); }}>🔗 建关系</button>
        {out.length === 0 && incoming.length === 0 && <div className="insp-empty">还没有关系</div>}
        {out.length > 0 && <div className="insp-rel-group">出向（→）{out}</div>}
        {incoming.length > 0 && <div className="insp-rel-group">入向（←）{incoming}</div>}
      </div>
    );
  };

  const renderCard = (c) => (
    <div key={c.id} className={`insp-card ${sel?.kind === c.kind && sel?.id === c.id ? "sel" : ""}`}
      onClick={() => setSel({ kind: c.kind, id: c.id })} style={{ borderLeftColor: K_COLOR[c.kind] }}>
      <div className="insp-card-head">
        <span className="insp-k" style={{ background: K_COLOR[c.kind] }}>{K_LABEL[c.kind]}</span>
        <span className="insp-card-name">{K_EMOJI[c.kind]} {est(c.name)}</span>
      </div>
      <div className="insp-card-fields">
        {(c.fields || []).length === 0 && <div className="insp-frow" style={{ color: "var(--ink-mute)" }}>（无字段）</div>}
        {(c.fields || []).map((f, i) => (
          <div className="insp-frow" key={i}><span className="fn">{est(f.name)}</span><span className="fv">{est(f.value)}</span></div>
        ))}
      </div>
      <div className="insp-card-meta">{c.relations?.length || 0} 条关系</div>
      <div className="insp-card-actions">
        <button onClick={(e) => { e.stopPropagation(); openEditCard(c); }}>✏ 编辑</button>
        <button onClick={(e) => { e.stopPropagation(); setSel({ kind: c.kind, id: c.id }); setRelFrom(c); setRelTo(""); setRelName(""); }}>🔗 关系</button>
        <button className="an-bad" onClick={(e) => { e.stopPropagation(); if (window.confirm(`删除「${c.name}」？`)) void deleteCard(c.kind, c.id); }}>🗑</button>
      </div>
    </div>
  );

  const mechName = (m) => (MECHS.find((x) => x.id === m) || {}).name || m;
  const curMech = MECHS.find((m) => m.id === mech);

  return (
    <div className="an-col">
      <div className="modebar">
        <span className="an-muted" style={{ alignSelf: "center", paddingRight: 6 }}>灵感工坊</span>
        <span style={{ flex: 1 }} />
        {pending.length > 0 && <span className="an-warn">📥 {pending.length} 条候选待审批</span>}
      </div>
      {!bookRoot && <div className="emptyguide"><h3>未选书</h3></div>}
      {bookRoot && (
        <div className="insp-grid">
          {/* 左：卡片墙 + 关系 */}
          <div className="insp-left">
            <div className="insp-cards">
              {Object.keys(KIND_TO_ELEM).map((kind) => (
                <div className="insp-group" key={kind}>
                  <div className="insp-group-title">
                    {K_EMOJI[kind]} {K_LABEL[kind]}
                    <button style={{ marginLeft: "auto" }} onClick={async () => {
                      const name = window.prompt(`新建${K_LABEL[kind]}卡名称：`);
                      if (name) void addCard(kind, name.trim());
                    }}>＋</button>
                  </div>
                  <div className="insp-cards-grid">
                    {allCards().filter((c) => c.kind === kind).map(renderCard)}
                    {allCards().filter((c) => c.kind === kind).length === 0 && <div className="insp-empty">还没有{K_LABEL[kind]}卡</div>}
                  </div>
                </div>
              ))}
            </div>
            <div className="insp-relpanel">{relPanel()}</div>
          </div>

          {/* 中：对话 */}
          <div className="insp-chat">
            <div className="insp-ctx-bar">
              <span className="an-muted">上下文</span>
              <span className="insp-ctx-val">
                {sel ? `${K_EMOJI[sel.kind]} ${est(findCard(sel.kind, sel.id)?.name || "")}` : "👈 点左侧卡设为上下文"}
                {sel && <button style={{ marginLeft: 6 }} onClick={() => setSel(null)}>✕</button>}
              </span>
            </div>
            <div className="sb-msgs" ref={boxRef}>
              {msgs.length === 0 && (
                <div className="emptyguide"><h3>💡 灵感工坊</h3><p>抛个点子、戳设定漏洞、反套路找空白。<br />点左侧卡片可挂为对话上下文。</p></div>
              )}
              {msgs.map((m, i) => (
                <div key={i} className={`sb-msg ${m.role === "user" ? "user" : "ai"}`}>
                  <div className="who">{m.role === "user" ? "你" : "灵感助手"}</div>
                  <div className="bubble">{m.content}</div>
                </div>
              ))}
              {suggestions.map((s, i) => (
                <div key={`sug-${i}`} className="approval-card">
                  <div className="sum">✏️ 建议改进「{est(s.card_name || s.name)}」</div>
                  {s.reason && <div className="just">💡 {est(s.reason)}</div>}
                  <div className="approval-actions">
                    <button className="primary" onClick={() => void applySuggestion(s, i)}>✅ 应用</button>
                    <button onClick={() => setSuggestions((arr) => arr.filter((_, j) => j !== i))}>✕ 忽略</button>
                  </div>
                </div>
              ))}
            </div>
            <div className="insp-mech">
              {MECHS.map((m) => (
                <button key={m.id} className={mech === m.id ? "on" : ""} title={`${m.desc}｜适合：${m.scene}`}
                  onClick={() => setMech(m.id)}>{m.icon} {m.name}</button>
              ))}
            </div>
            {curMech && <div className="an-muted" style={{ padding: "2px 10px" }}>{curMech.icon} {curMech.desc} · 适合：{curMech.scene}</div>}
            <div className="sb-inputbar">
              <textarea value={input} onChange={(e) => setInput(e.target.value)}
                placeholder="聊剧情、聊设定、聊点子…（Enter 发送）"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault(); void send();
                  }
                }} />
              <button className="primary" disabled={busy || !input.trim()} style={{ alignSelf: "flex-end", padding: "8px 14px" }}
                onClick={() => void send()}>{busy ? "…" : "发送"}</button>
            </div>
          </div>

          {/* 右：记忆 + 待审批 */}
          <div className="insp-right">
            <div className="insp-panel-title">📥 候选审批（{pending.length}）</div>
            {pending.length > 0 && (
              <div className="an-row" style={{ marginBottom: 6 }}>
                <button className="primary" onClick={() => void approveAll()}>✓ 全部同意</button>
                <button onClick={() => void rejectAll()}>✕ 全部拒绝</button>
              </div>
            )}
            {pending.length === 0 && <div className="insp-empty">没有待审批项 🎉</div>}
            {pending.map((p) => (
              <div key={p.id} className="insp-pend">
                <div className="insp-pend-head">
                  <span className="insp-k" style={{ background: p.type === "mem" ? "var(--cinnabar)" : "var(--dai)" }}>
                    {p.type === "mem" ? "记忆" : "卡片"}
                  </span>
                  {p.type === "card" && <span>{est(p.name)}</span>}
                </div>
                <div className="insp-pend-body">
                  {p.type === "mem" ? (
                    editingPid === p.id ? (
                      <textarea value={editingText} rows={2} style={{ width: "100%" }}
                        onChange={(e) => setEditingText(e.target.value)} />
                    ) : (
                      <div className="insp-pend-text">{est(p.text)}</div>
                    )
                  ) : editingPid === p.id ? (
                    <div>
                      <input type="text" style={{ width: "100%" }} value={editingName} placeholder="卡片名称"
                        onChange={(e) => setEditingName(e.target.value)} />
                      <textarea rows={2} style={{ width: "100%", marginTop: 4 }} value={editingDesc} placeholder="描述"
                        onChange={(e) => setEditingDesc(e.target.value)} />
                      {editingFields.map((f, i) => (
                        <div className="an-row" key={i} style={{ marginTop: 4 }}>
                          <input type="text" style={{ flex: 1 }} value={f.name} placeholder="字段名"
                            onChange={(e) => { const fs = [...editingFields]; fs[i] = { ...fs[i], name: e.target.value }; setEditingFields(fs); }} />
                          <input type="text" style={{ flex: 1 }} value={f.value} placeholder="值"
                            onChange={(e) => { const fs = [...editingFields]; fs[i] = { ...fs[i], value: e.target.value }; setEditingFields(fs); }} />
                          <button className="an-bad" onClick={() => setEditingFields(editingFields.filter((_, j) => j !== i))}>✕</button>
                        </div>
                      ))}
                      <button style={{ marginTop: 4 }} onClick={() => setEditingFields([...editingFields, { name: "", value: "" }])}>＋ 字段</button>
                    </div>
                  ) : (
                    <div className="insp-pend-text">{est(p.desc)}{p.reason && <div className="an-muted">💡 {est(p.reason)}</div>}</div>
                  )}
                </div>
                <div className="an-row" style={{ marginTop: 4 }}>
                  {editingPid === p.id ? (
                    <>
                      <button className="primary" onClick={() => void saveEditPending(p)}>💾 保存</button>
                      <button onClick={() => setEditingPid(null)}>取消</button>
                    </>
                  ) : (
                    <>
                      <button onClick={() => startEditPending(p)}>✏ 编辑</button>
                      <button className="primary" onClick={() => void approve(p)}>✅ 同意</button>
                      <button onClick={() => void reject(p.id)}>✕ 拒绝</button>
                    </>
                  )}
                </div>
              </div>
            ))}

            <div className="insp-panel-title" style={{ marginTop: 12 }}>🧠 记忆（{memory.length}）</div>
            <div className="an-row" style={{ marginBottom: 6 }}>
              <input type="text" style={{ flex: 1 }} value={memInput} placeholder="手动记一条…"
                onChange={(e) => setMemInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") void addMemory(); }} />
              <button onClick={() => void addMemory()}>＋</button>
            </div>
            {memory.length === 0 && <div className="insp-empty">AI 会自动记住设定/偏好</div>}
            {memory.map((m) => (
              <div className="insp-mem-item" key={m.id}>
                <span className="insp-mem-text">{est(m.text)}</span>
                <button className="an-bad" onClick={() => void delMemory(m.id)}>✕</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 建关系模态 */}
      {relFrom && (
        <div className="insp-overlay" onClick={() => setRelFrom(null)}>
          <div className="insp-modal" onClick={(e) => e.stopPropagation()}>
            <div className="insp-modal-title">🔗 建关系：{K_EMOJI[relFrom.kind]} {est(relFrom.name)} → ?</div>
            <label>目标素材
              <select value={relTo} onChange={(e) => setRelTo(e.target.value)}>
                <option value="">请选择…</option>
                {allCards().filter((c) => c.kind !== relFrom.kind || c.id !== relFrom.id).map((c) => (
                  <option key={`${c.kind}:${c.id}`} value={`${c.kind}:${c.id}`}>{K_EMOJI[c.kind]} {est(c.name)}</option>
                ))}
              </select>
            </label>
            <label>关系名
              <input type="text" value={relName} onChange={(e) => setRelName(e.target.value)} placeholder="持有 / 宿敌 / 属于" />
            </label>
            <label>重数
              <select value={relMult} onChange={(e) => setRelMult(e.target.value)}>
                <option value="1:1">1:1 一对一</option><option value="1:N">1:N 一对多</option>
                <option value="N:1">N:1 多对一</option><option value="N:M">N:M 多对多</option>
              </select>
            </label>
            <div className="an-row" style={{ marginTop: 10 }}>
              <button onClick={() => setRelFrom(null)}>取消</button>
              <button className="primary" onClick={() => void saveRel()}>✅ 建立关系</button>
            </div>
          </div>
        </div>
      )}

      {/* 卡片编辑模态 */}
      {editCard && (
        <div className="insp-overlay" onClick={() => setEditCard(null)}>
          <div className="insp-modal" onClick={(e) => e.stopPropagation()}>
            <div className="insp-modal-title">✏ 编辑「{est(editCard.name)}」</div>
            <label>名称<input type="text" value={editCard.name} onChange={(e) => setEditCard({ ...editCard, name: e.target.value })} /></label>
            <label>描述<textarea rows={3} value={editCard.desc} onChange={(e) => setEditCard({ ...editCard, desc: e.target.value })} /></label>
            <div className="insp-modal-title">字段</div>
            {editFields.map((f, i) => (
              <div className="an-row" key={i} style={{ marginBottom: 4 }}>
                <input type="text" style={{ flex: 1 }} value={f.name} placeholder="字段名"
                  onChange={(e) => { const fs = [...editFields]; fs[i] = { ...fs[i], name: e.target.value }; setEditFields(fs); }} />
                <input type="text" style={{ flex: 1 }} value={f.value} placeholder="值"
                  onChange={(e) => { const fs = [...editFields]; fs[i] = { ...fs[i], value: e.target.value }; setEditFields(fs); }} />
                <button className="an-bad" onClick={() => setEditFields(editFields.filter((_, j) => j !== i))}>✕</button>
              </div>
            ))}
            <button style={{ marginTop: 4 }} onClick={() => setEditFields([...editFields, { name: "", value: "" }])}>＋ 字段</button>
            <div className="an-row" style={{ marginTop: 10 }}>
              <button onClick={() => setEditCard(null)}>取消</button>
              <button className="primary" onClick={() => void saveEditCard()}>💾 保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
