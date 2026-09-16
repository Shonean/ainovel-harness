import React, { useCallback, useEffect, useState } from "react";
import { on, hostCommand, restGet, restPost, state as bstate, PH } from "../shared/bridge.js";

/** 首页 = 原版 ProjectSelectPage：书卡网格，点书进工作台 */
function HomeView({ onPick }) {
  const [projects, setProjects] = useState(null);
  const [currentRoot, setCurrentRoot] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    setErr("");
    restGet("/api/projects").then((r) => {
      setProjects(r.projects || []);
      return restGet("/api/project/current");
    }).then((cur) => {
      setCurrentRoot(String(cur?.project_root || ""));
    }).catch((e) => setErr(e.message || "加载失败"));
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="home-wrap">
      <div className="home-head">
        <div className="home-title">AInovel Harness</div>
        <div className="an-muted">选择一本书进入工作台，或新建一本书开始创作</div>
      </div>
      {err && <div className="an-bad" style={{ margin: "10px 0" }}>❌ {err} <button onClick={load}>重试</button></div>}
      <div className="home-grid">
        {(projects || []).map((p) => (
          <div key={p.project_root} className={`home-card ${p.project_root === currentRoot ? "current" : ""}`}
            onClick={() => onPick(p.project_root, p.name)}>
            <div className="home-card-name">📖 {p.name}</div>
            <div className="home-card-path" title={p.project_root}>{p.project_root}</div>
            {p.project_root === currentRoot && <span className="home-card-badge">当前</span>}
            <div className="home-card-go">进入工作台 →</div>
          </div>
        ))}
        {projects && projects.length === 0 && <div className="an-muted">还没有书，从右侧新建一本。</div>}
        <div className="home-card new" onClick={() => hostCommand("ainovel.openAssistantPanel")}>
          <div className="home-card-name">＋ 新建书</div>
          <div className="home-card-path">打开创作助手（书级模式），聊着聊着就把书建好</div>
        </div>
      </div>
    </div>
  );
}

const LEVELS = [
  { id: "l1", label: "l1", name: "极简" },
  { id: "l2", label: "l2", name: "情节概要" },
  { id: "l3", label: "l3", name: "章核心" },
  { id: "l4", label: "l4", name: "场景分解" },
  { id: "l5", label: "l5", name: "正文" },
];

function levelText(arcState, lv) {
  if (!arcState) return "";
  const blk = (arcState.levels || {})[lv] || {};
  if (lv === "l3") {
    const data = blk.data || {};
    if (Array.isArray(data.chapters)) {
      return data.chapters.map((c, i) => `第${i + 1}章 ${c.title || ""}：${c.core || ""}`).join("\n");
    }
    return `${data.title || ""}${data.core ? `：${data.core}` : ""}`;
  }
  if (lv === "l4") {
    return (blk.scenes || []).map((s, i) => `场景${i + 1} ${s.name || ""}\n  ${s.beats || s.desc || ""}`).join("\n");
  }
  if (lv === "l5") {
    const chapters = blk.chapters || [];
    const cur = Math.min(arcState.active_chapter || 0, Math.max(chapters.length - 1, 0));
    return String(chapters[cur]?.text ?? blk.text ?? "");
  }
  return String(blk.text ?? "");
}

function levelStatus(arcState, lv) {
  if (!arcState) return "empty";
  const blk = (arcState.levels || {})[lv];
  if (!blk) return "empty";
  const t = lv === "l4" ? blk.scenes : lv === "l3" ? blk.data : blk.text;
  if (t === undefined || t === null || (typeof t === "string" && !t.trim()) || (Array.isArray(t) && !t.length)) {
    return "empty";
  }
  return blk.confirmed ? "confirmed" : "pending";
}

const arcChapterNum = (a) => (a?.state?.levels?.l5?.chapters || []).length;
const arcLadderDone = (a) => LEVELS.filter(({ id }) => levelStatus(a.state, id) === "confirmed").length;

export default function WorkbenchPanel() {
  const [bookRoot, setBookRoot] = useState(bstate.bookRoot);
  const [view, setView] = useState("home"); // home（首页书卡）| workbench（工作台）
  const [st, setSt] = useState(null);
  const [activeArcId, setActiveArcId] = useState("");
  const [busyMsg, setBusyMsg] = useState("");
  const [editingLv, setEditingLv] = useState("");
  const [editText, setEditText] = useState("");
  const [newL1, setNewL1] = useState("");
  const [notice, setNotice] = useState("");
  const [leftTab, setLeftTab] = useState("ladder"); // ladder | fragments | pending | notes
  const [fragments, setFragments] = useState([]);
  const [notes, setNotes] = useState([]);
  const [pending, setPending] = useState([]);
  const [newNote, setNewNote] = useState("");
  const [score, setScore] = useState(null);

  useEffect(() => on("init", (s) => setBookRoot(s.bookRoot)), []);
  useEffect(() => on("bookChanged", (s) => { setBookRoot(s.bookRoot); setActiveArcId(""); setView("workbench"); }), []);

  // 响应式：侧边栏窄（<720px）时上下堆叠；拖到辅助侧边栏/加宽后自动恢复双栏
  useEffect(() => {
    const apply = () => document.body.classList.toggle("narrow", window.innerWidth < 720);
    apply();
    window.addEventListener("resize", apply);
    return () => window.removeEventListener("resize", apply);
  }, []);

  const refresh = useCallback(async (keepArc) => {
    if (!bookRoot) return;
    try {
      const data = await restPost(`${PH}/ai-creation/state`, { book_root: bookRoot });
      setSt(data);
      const arcs = data.arcs?.arcs || [];
      if (!keepArc) {
        setActiveArcId((cur) => (cur && arcs.some((a) => a.id === cur) ? cur : (arcs[0]?.id || "")));
      }
    } catch (e) {
      setNotice(`加载失败：${e.message}`);
    }
  }, [bookRoot]);

  useEffect(() => { void refresh(); }, [refresh]);
  // 后端连上/恢复后自动重拉（修复「视图先于后端渲染 → fetch failed 后不再重试」）
  useEffect(() => on("connected", () => void refresh(true)), []);
  useEffect(() => on("rpcEvent:event/chat.done", () => void refresh(true)), []);
  useEffect(() => {
    const off = on("rpcEvent:event/task.event", (p) => {
      if (p?.event?.phase === "done") void refresh(true);
    });
    return off;
  }, []);

  const arc = st?.arcs?.arcs?.find((a) => a.id === activeArcId) || null;

  const loadExtras = useCallback(async () => {
    if (!bookRoot || !arc) return;
    const enc = encodeURIComponent(bookRoot);
    try {
      const [f, n, p] = await Promise.all([
        restGet(`${PH}/ai-creation/fragments?book_root=${enc}`, false).catch(() => ({ items: [] })),
        restGet(`${PH}/ai-creation/notes?book_root=${enc}&scope=arc:${arc.id}`, false).catch(() => ({ items: [] })),
        restGet(`${PH}/ai-creation/pending?book_root=${enc}`, false).catch(() => ({ items: [] })),
      ]);
      setFragments((f.items || []).filter((x) => !x.arc_id || x.arc_id === arc.id));
      setNotes(n.items || []);
      setPending(p.items || []);
    } catch { /* silent */ }
  }, [bookRoot, arc?.id]);

  useEffect(() => { void loadExtras(); }, [loadExtras]);

  const call = useCallback(async (path, body, okMsg) => {
    setBusyMsg(path);
    try {
      await restPost(`${PH}${path}`, { book_root: bookRoot, ...body }, false);
      if (okMsg) setNotice(okMsg);
      await refresh(true);
      await loadExtras();
    } catch (e) {
      setNotice(`⚠ ${path} 失败：${e.message}`);
    } finally {
      setBusyMsg("");
    }
  }, [bookRoot, refresh, loadExtras]);

  const doStep = async () => {
    if (!arc) return;
    setBusyMsg("step（LLM 生成中…）");
    try {
      await restPost(`${PH}/ai-creation/arc/step`, { book_root: bookRoot, arc_id: arc.id });
      setNotice("已推进一级");
      await refresh(true);
    } catch (e) {
      setNotice(`⚠ 推进失败：${e.message}`);
    } finally {
      setBusyMsg("");
    }
  };

  const doNewArc = async () => {
    const l1 = newL1.trim().split("\n").filter(Boolean);
    if (!l1.length) return;
    setBusyMsg("建弧…");
    try {
      let last = null;
      for (const line of l1) {
        last = await restPost(`${PH}/ai-creation/arc/new`, { book_root: bookRoot, l1: line, n_chapters: 1 });
      }
      if (last?.arc?.id) setActiveArcId(last.arc.id);
      setNewL1("");
      setNotice(l1.length > 1 ? `已批量创建 ${l1.length} 个情节` : "情节已创建");
      await refresh(true);
    } catch (e) {
      setNotice(`⚠ 建弧失败：${e.message}`);
    } finally {
      setBusyMsg("");
    }
  };

  const saveEdit = async (lv) => {
    if (!arc) return;
    setEditingLv("");
    await call("/ai-creation/arc/set-level",
      { arc_id: arc.id, level: lv, text: editText, idx: null },
      `${lv} 已保存`);
  };

  const clearLevel = async (lv) => {
    if (!arc) return;
    const body = { arc_id: arc.id, level: lv, text: "", idx: null };
    if (lv === "l3") body.data = { chapters: [] };
    if (lv === "l4") body.data = [];
    await call("/ai-creation/arc/set-level", body, `${lv} 已清空`);
  };

  const finalizeChapter = async () => {
    if (!arc) return;
    await call("/ai-creation/chapter/finalize", { arc_id: arc.id }, "本章已落盘到 正文/");
  };

  const doScore = async () => {
    if (!arc) return;
    setBusyMsg("评分中…");
    try {
      const idx = arc.state?.active_chapter || 0;
      const gen = String(arc.state?.levels?.l5?.chapters?.[idx]?.text || "");
      if (!gen.trim()) { setNotice("当前没有 l5 正文"); return; }
      const r = await restPost(`${PH}/ai-creation/chapter/score`,
        { book_root: bookRoot, arc_id: arc.id, gen_text: gen, chapter_idx: idx });
      setScore(r);
      setNotice(`评分：意图 ${r.intent_score} · 质量 ${r.quality_score} · 综合 ${r.overall}`);
    } catch (e) {
      setNotice(`⚠ 评分失败：${e.message}`);
    } finally {
      setBusyMsg("");
    }
  };

  const toggleSelect = async (kind, elemId) => {
    if (!arc) return;
    const sel = JSON.parse(JSON.stringify(arc.selected || {}));
    const list = sel[kind] || [];
    const i = list.indexOf(elemId);
    if (i >= 0) list.splice(i, 1); else list.push(elemId);
    sel[kind] = list;
    await call("/ai-creation/arc/select", { arc_id: arc.id, selected: sel });
  };

  const addNote = async () => {
    const text = newNote.trim();
    if (!text || !arc) return;
    setNewNote("");
    await call("/ai-creation/notes", { scope: `arc:${arc.id}`, content: text });
  };

  const approvePending = async (pid, edits = {}) => {
    try {
      await restPost(`${PH}/ai-creation/pending/${encodeURIComponent(pid)}/approve`,
        { book_root: bookRoot, ...edits });
      await loadExtras();
      await refresh(true);
    } catch { /* silent */ }
  };
  const rejectPending = async (pid) => {
    try {
      await restPost(`${PH}/ai-creation/pending/${encodeURIComponent(pid)}/reject?book_root=${encodeURIComponent(bookRoot)}`,
        {}, false);
      await loadExtras();
    } catch { /* silent */ }
  };

  if (!bookRoot || view === "home") {
    return (
      <HomeView onPick={(root, name) => hostCommand("ainovel.selectBook", { root, name })} />
    );
  }

  const arcs = st?.arcs?.arcs || [];
  const activeChIdx = arc?.state?.active_chapter || 0;
  const participating = arc
    ? ((arc.selected?.characters || []).length + (arc.selected?.items || []).length + (arc.selected?.settings || []).length)
    : 0;

  return (
    <div className="an-col" style={{ background: "var(--vscode-editor-background)" }}>
      {/* 顶部书头（对齐 exe 版 wb-bookhead） */}
      <div className="wb-bookhead">
        <div className="wb-bhtitle">
          <span className="vol">卷</span>
          <span>{arc?.name || "未选情节"}</span>
          <span className="sub">
            {arc ? `· 共 ${arcChapterNum(arc)} 章 · 阶梯 ${arcLadderDone(arc)}/5` : "← 左栏选一个情节"}
            {arc && ` · 第${activeChIdx + 1}章`}
          </span>
        </div>
        <div className="wb-bhmeta">
          <button onClick={() => setView("home")} title="返回首页选书">⌂ 首页</button>
          <button onClick={() => void refresh()}>↻ 刷新</button>
          <button className="primary" title="在编辑区打开全页工作台"
            onClick={() => hostCommand("ainovel.openWorkbenchEditor")}>⤢ 大窗口</button>
          {busyMsg && <span className="sb-typing"><span className="sb-dot" /><span className="sb-dot" /><span className="sb-dot" />{busyMsg}</span>}
          {notice && <span className="an-muted">{notice}</span>}
        </div>
      </div>

      {/* 双栏书页 */}
      <div className="wb-page">
        {/* 左栏 */}
        <div className="wb-col wb-col-left">
          <div className="wb-tabbar">
            {[
              { k: "ladder", l: "📐 阶梯" },
              { k: "fragments", l: "📎 素材", n: fragments.length },
              { k: "pending", l: "📋 待审核", n: pending.length },
              { k: "notes", l: "📝 备注", n: notes.length },
            ].map((t) => (
              <div key={t.k}
                className={`wb-tab ${leftTab === t.k ? "on" : ""}`}
                onClick={() => setLeftTab(t.k)}>
                {t.l}
                {t.n > 0 && <span className="wb-tab-badge">{t.n}</span>}
              </div>
            ))}
          </div>

          <div className="wb-col-body">
            {leftTab === "ladder" && (
              <>
                {/* 情节选择条（横排，替代旧左窄栏） */}
                <div className="wb-arcstrip">
                  {arcs.map((a) => (
                    <div key={a.id}
                      className={`wb-arcchip ${a.id === activeArcId ? "on" : ""}`}
                      onClick={() => setActiveArcId(a.id)}>
                      <span className="wb-arcchip-name">{a.name || "(未命名)"}</span>
                      <span className="wb-arcchip-prog">
                        {LEVELS.map(({ id }) => {
                          const s = levelStatus(a.state, id);
                          return <i key={id} className={s[0]} title={id}>{s === "confirmed" ? "●" : s === "pending" ? "◐" : "○"}</i>;
                        })}
                      </span>
                    </div>
                  ))}
                  <div className="wb-arcchip new">
                    <textarea rows={2} placeholder="一句话极简剧情（多行=批量）" value={newL1}
                      onChange={(e) => setNewL1(e.target.value)} />
                    <button className="primary" onClick={doNewArc}>➕ 新建情节</button>
                  </div>
                </div>

                {!arc && (
                  <div className="emptyguide">
                    <h3>📝 选择或新建一个情节</h3>
                    <div className="steps">
                      <span>① 新建情节 l1</span><span>② step 推进 l2→l5</span>
                      <span>③ 每级确认</span><span>④ 落盘本章</span>
                    </div>
                  </div>
                )}

                {arc && (
                  <div className="wb-ladderstack">
                    <div className="wb-rubric">五级阶梯 · 自顶向下</div>
                    {LEVELS.map(({ id: lv, label, name }) => {
                      const status = levelStatus(arc.state, lv);
                      const text = levelText(arc.state, lv);
                      const isEditing = editingLv === lv;
                      return (
                        <div key={lv} className={`lvblock ${status}`}>
                          <div className="lvhead">
                            <span className="lvtag">{label}</span>
                            <b>{name}</b>
                            <span className={`an-muted ${status === "confirmed" ? "an-ok" : ""}`}>
                              {status === "confirmed" ? "✓ 已确认" : status === "pending" ? "● 待确认" : "○ 未生成"}
                            </span>
                            <span className="lvops">
                              <button onClick={doStep} title="生成/推进">▶</button>
                              {status !== "empty" && (
                                <>
                                  <button onClick={() => { setEditingLv(lv); setEditText(text); }}>✏️</button>
                                  <button onClick={() => void call("/ai-creation/arc/confirm", { arc_id: arc.id, level: lv }, `${lv} 已确认`)}>✔</button>
                                  <button onClick={() => void clearLevel(lv)}>🗑</button>
                                </>
                              )}
                            </span>
                          </div>
                          {isEditing ? (
                            <div className="lvbody">
                              <textarea className="lvedit" value={editText}
                                onChange={(e) => setEditText(e.target.value)} />
                              <div className="an-row" style={{ marginTop: 6 }}>
                                <button className="primary" onClick={() => void saveEdit(lv)}>保存</button>
                                <button onClick={() => setEditingLv("")}>取消</button>
                              </div>
                            </div>
                          ) : (
                            <div className="lvbody">{text || <span className="an-muted">（空——点 ▶ 生成下一级）</span>}</div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </>
            )}

            {leftTab === "fragments" && (
              <div className="wb-tabpane">
                <div className="wb-rubric">素材片段</div>
                {fragments.length === 0 && <div className="an-muted">本情节还没有素材片段。</div>}
                {fragments.map((f) => (
                  <div key={f.id} className="wb-frag">
                    <div className="wb-frag-meta">{f.ftype || "素材"} · 场景{f.scene_idx ?? "-"}/拍{f.beat_idx ?? "-"}</div>
                    <div className="wb-frag-text">{f.content}</div>
                  </div>
                ))}
              </div>
            )}

            {leftTab === "pending" && (
              <div className="wb-tabpane">
                <div className="wb-rubric">待审核候选</div>
                {pending.length === 0 && <div className="an-muted">没有待审批项 🎉</div>}
                {pending.map((p) => (
                  <div key={p.id} className="approval-card">
                    <div className="sum">
                      <span className={`an-tag ${p.type === "mem" ? "an-warn" : ""}`}>{p.type === "mem" ? "记忆" : "卡片"}</span>
                      {p.name || p.text}
                    </div>
                    {p.desc && <div className="just">{p.desc}</div>}
                    {p.reason && <div className="an-muted">💡 {p.reason}</div>}
                    <div className="approval-actions">
                      <button className="primary" onClick={() => void approvePending(p.id)}>✅ 同意</button>
                      <button onClick={() => void rejectPending(p.id)}>✕ 拒绝</button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {leftTab === "notes" && (
              <div className="wb-tabpane">
                <div className="wb-rubric">本情节备注</div>
                <div className="an-row" style={{ marginBottom: 8 }}>
                  <input type="text" style={{ flex: 1 }} placeholder="写一条备注…（Enter 保存）"
                    value={newNote} onChange={(e) => setNewNote(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") void addNote(); }} />
                  <button className="primary" onClick={addNote}>＋</button>
                </div>
                {notes.length === 0 && <div className="an-muted">还没有备注。</div>}
                {notes.map((n) => (
                  <div key={n.id} className="wb-note">
                    <span>{n.content}</span>
                    <button className="an-bad" onClick={() => void call("/ai-creation/notes", { nid: n.id, content: "" })}>✕</button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* 右栏操作台 */}
        <div className="wb-col wb-col-right">
          <div className="wb-rubric">操作台</div>

          {arc ? (
            <>
              {/* 参与元素 */}
              <div className="wb-opsec">
                <div className="wb-optitle">本情节参与元素</div>
                {["characters", "items", "settings"].map((kind) => (
                  <div key={kind} className="wb-eg">
                    <div className="wb-eglabel">{{ characters: "角色", items: "物品", settings: "设定" }[kind]}</div>
                    <div className="wb-echips">
                      {(st?.elements?.[kind] || []).map((e) => {
                        const on = (arc.selected?.[kind] || []).includes(e.id);
                        return (
                          <span key={e.id} className={`wb-echip ${on ? "on" : "outline"}`}
                            title={String(e.desc || "").slice(0, 60)}
                            onClick={() => void toggleSelect(kind, e.id)}>
                            {e.name}{on ? " ✓" : ""}
                          </span>
                        );
                      })}
                      {(st?.elements?.[kind] || []).length === 0 && <span className="an-muted">暂无</span>}
                    </div>
                  </div>
                ))}
              </div>

              {/* 阶梯进度 */}
              <div className="wb-opsec">
                <div className="wb-optitle">阶梯进度</div>
                <div className="wb-lstate">
                  {LEVELS.map(({ id: lv, label, name }) => {
                    const s = levelStatus(arc.state, lv);
                    return (
                      <div key={lv} className={`wb-lsrow ${s}`}>
                        <span className="wb-lstag">{label}</span>
                        <span className="wb-lsname">{name}</span>
                        <span className="wb-lsstat">
                          {s === "confirmed" ? "✓" : s === "pending" ? "◐" : "○"}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* 主操作 */}
              <div className="wb-opsec">
                <button className="primary wb-bigbtn" onClick={doStep} disabled={!!busyMsg}>
                  ⚡ 生成下一级（step）
                </button>
                <div className="an-row" style={{ marginTop: 6 }}>
                  <button onClick={finalizeChapter} disabled={levelStatus(arc.state, "l5") === "empty"}>💾 落盘当前章</button>
                  <button onClick={doScore} disabled={levelStatus(arc.state, "l5") === "empty"}>📊 双评分</button>
                </div>
                <button className="an-bad" style={{ marginTop: 6 }}
                  onClick={async () => {
                    if (window.confirm(`删除情节「${arc.name}」？`)) {
                      await call("/ai-creation/arc/delete", { arc_id: arc.id });
                      setActiveArcId("");
                    }
                  }}>🗑 删除情节</button>
              </div>

              {/* 评分结果 */}
              {score && (
                <div className="wb-opsec">
                  <div className="wb-optitle">最近评分</div>
                  <table className="an-table">
                    <tbody>
                      <tr><td>意图</td><td>{score.intent_score}</td></tr>
                      <tr><td>质量</td><td>{score.quality_score}</td></tr>
                      <tr><td><b>综合</b></td><td><b>{score.overall}</b></td></tr>
                    </tbody>
                  </table>
                </div>
              )}

              {/* 元数据 */}
              <div className="wb-opsec wb-opmeta">
                <div>参与 <b>{participating}</b></div>
                <div>阶梯 <b>{arcLadderDone(arc)}/5</b></div>
                <div>章数 <b>{arcChapterNum(arc)}</b></div>
              </div>
            </>
          ) : (
            <div className="an-muted" style={{ padding: 10 }}>左栏选择或新建一个情节，操作台会显示对应控件。</div>
          )}
        </div>
      </div>
    </div>
  );
}
