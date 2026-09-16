import React, { useEffect, useMemo, useState } from "react";
import { on, restPost, state as bstate, PH } from "../shared/bridge.js";

// 分析三件套：全书概览（统计+综合分走势）· 角色图鉴（四类元素）· 节奏（字数分布）

function collectChapters(st) {
  const rows = [];
  for (const a of st?.arcs?.arcs || []) {
    const chs = a.chapters?.length ? a.chapters : (a.state?.levels?.l5?.chapters || []);
    chs.forEach((c, i) => {
      rows.push({
        arc: a.name, idx: i,
        num: c.chapter_num ?? c.num ?? rows.length + 1,
        words: String(c.text || c.content || "").length,
        overall: c.overall ?? c.score?.overall ?? null,
        intent: c.intent_score ?? c.score?.intent_score ?? null,
        polluted: !!c.polluted,
      });
    });
  }
  return rows.sort((x, y) => x.num - y.num);
}

export default function AnalysisPanel() {
  const [bookRoot, setBookRoot] = useState(bstate.bookRoot);
  const [st, setSt] = useState(null);
  const [tab, setTab] = useState("overview");

  useEffect(() => on("init", (s) => setBookRoot(s.bookRoot)), []);
  useEffect(() => on("bookChanged", (s) => setBookRoot(s.bookRoot)), []);
  useEffect(() => {
    if (!bookRoot) return;
    void restPost(`${PH}/ai-creation/state`, { book_root: bookRoot }).then(setSt).catch(() => {});
  }, [bookRoot]);

  const chapters = useMemo(() => collectChapters(st), [st]);
  const totalWords = chapters.reduce((n, c) => n + c.words, 0);
  const maxW = Math.max(...chapters.map((c) => c.words), 1);
  const elems = st?.elements || {};

  return (
    <div className="an-col">
      <div className="modebar">
        {[["overview", "📊 全书概览"], ["gallery", "🎭 角色图鉴"], ["pacing", "📈 节奏"]].map(([k, label]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      <div className="an-scroll">
        {!bookRoot && <div className="emptyguide"><h3>未选书</h3></div>}

        {tab === "overview" && bookRoot && (
          <>
            <div className="an-row" style={{ marginBottom: 12 }}>
              <span className="an-tag">情节 {(st?.arcs?.arcs || []).length}</span>
              <span className="an-tag">章节 {chapters.length}</span>
              <span className="an-tag">总字数 {totalWords.toLocaleString()}</span>
              <span className="an-tag">污染 {chapters.filter((c) => c.polluted).length} 章</span>
              <span className="an-tag">元素 {Object.values(elems).reduce((n, l) => n + (l?.length || 0), 0)}</span>
              <button onClick={() => void restPost(`${PH}/ai-creation/state`, { book_root: bookRoot }).then(setSt)}>刷新</button>
            </div>
            <div className="an-card">
              <div className="an-title">每章综合分走势</div>
              {chapters.length === 0 && <p className="an-muted">还没有落盘章。</p>}
              <svg width="100%" height="120" preserveAspectRatio="none" viewBox={`0 0 ${Math.max(chapters.length, 1)} 100`}>
                {chapters.map((c, i) => {
                  if (c.overall == null) return null;
                  const h = Math.max(4, c.overall * 95);
                  return <rect key={i} x={i + 0.15} y={98 - h} width={0.7} height={h}
                    fill={c.overall >= 0.7 ? "var(--green)" : c.overall >= 0.6 ? "var(--amber)" : "var(--ink-mute)"}>
                    <title>{`第${c.num}章 综合 ${c.overall.toFixed(2)}`}</title>
                  </rect>;
                })}
              </svg>
            </div>
            <table className="an-table">
              <thead><tr><th>#</th><th>情节</th><th>字数</th><th>意图</th><th>质量</th><th>综合</th><th>污染</th></tr></thead>
              <tbody>
                {chapters.map((c) => (
                  <tr key={`${c.arc}-${c.idx}`}>
                    <td>{c.num}</td><td>{c.arc}</td>
                    <td>{c.words.toLocaleString()}</td>
                    <td>{fmt(c.intent)}</td><td>{fmt(c.quality ?? c.intent)}</td>
                    <td>{fmt(c.overall)}</td>
                    <td>{c.polluted ? "⚠" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        {tab === "gallery" && bookRoot && (
          <>
            {[["characters", "角色"], ["items", "物品"], ["settings", "设定"], ["maps", "🗺️ 地图"]].map(([kind, label]) => (
              <div className="an-card" key={kind}>
                <div className="an-title">{label}（{(elems[kind] || []).length}）</div>
                <div className="an-row" style={{ marginTop: 6 }}>
                  {(elems[kind] || []).map((e) => {
                    const usedIn = (st?.arcs?.arcs || []).filter((a) =>
                      Object.values(a.selected || {}).some((l) => l?.includes(e.id)));
                    return (
                      <span key={e.id} className="accswitch" title={String(e.desc || "")}>
                        {e.name}{usedIn.length ? ` · ${usedIn.length}弧` : ""}
                      </span>
                    );
                  })}
                  {!(elems[kind] || []).length && <span className="an-muted">暂无</span>}
                </div>
              </div>
            ))}
          </>
        )}

        {tab === "pacing" && bookRoot && (
          <div className="an-card">
            <div className="an-title">每章字数分布</div>
            {chapters.length === 0 && <p className="an-muted">暂无数据。</p>}
            <div className="an-row" style={{ alignItems: "flex-end", gap: 3, height: 140, marginTop: 8 }}>
              {chapters.map((c, i) => (
                <div key={i} style={{
                  width: Math.max(10, 600 / Math.max(chapters.length, 1)),
                  height: `${(c.words / maxW) * 100}%`,
                  background: c.words > 2500 ? "var(--amber)" : c.words < 800 ? "var(--ink-mute)" : "var(--dai)",
                  borderRadius: "2px 2px 0 0", position: "relative",
                }} title={`第${c.num}章 ${c.words} 字`} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function fmt(v) {
  return typeof v === "number" ? v.toFixed(2) : "";
}
