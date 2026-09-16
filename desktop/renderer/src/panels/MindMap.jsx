import React, { useEffect, useMemo, useRef, useState } from "react";
import { on, restPost, state as bstate, PH } from "../shared/bridge.js";

// 放射状全书思维导图：根=书，主枝=情节（按章数占扇区），叶=章。
// 状态色：✓绿已确认 ●黄待确认 ○灰未生成；综合分 ≥0.7 绿 / ≥0.6 黄 / else 灰。

function scoreColor(s) {
  if (s >= 0.7) return "var(--green)";
  if (s >= 0.6) return "var(--amber)";
  return "var(--ink-mute)";
}

export default function MindMapPanel() {
  const [bookRoot, setBookRoot] = useState(bstate.bookRoot);
  const [st, setSt] = useState(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const dragRef = useRef(null);
  const wrapRef = useRef(null);

  useEffect(() => on("init", (s) => setBookRoot(s.bookRoot)), []);
  useEffect(() => on("bookChanged", (s) => setBookRoot(s.bookRoot)), []);
  useEffect(() => {
    if (!bookRoot) return;
    void restPost(`${PH}/ai-creation/state`, { book_root: bookRoot }).then(setSt).catch(() => {});
  }, [bookRoot]);

  const graph = useMemo(() => {
    const arcs = st?.arcs?.arcs || [];
    const nodes = [];
    const edges = [];
    // 半径按内容量自适应
    const totalLeaves = Math.max(arcs.reduce((n, a) => n + Math.max((a.chapters?.length || a.state?.levels?.l5?.chapters?.length || 1), 1), 0), arcs.length);
    const R1 = Math.max(170, Math.min(320, totalLeaves * 6));
    const R2 = R1 + Math.max(120, Math.min(260, totalLeaves * 4));
    nodes.push({ id: "__root", kind: "root", label: st?.settings?.name || bstate.bookName || "书", r: R1 });
    let ang = -Math.PI / 2;
    arcs.forEach((a, ai) => {
      const leaves = a.chapters?.length
        ? a.chapters.map((c, i) => ({ idx: i, title: c.title || c.name || `第${i + 1}章`, score: c.overall ?? c.quality_score }))
        : (a.state?.levels?.l5?.chapters || []).map((c, i) => ({ idx: i, title: `第${i + 1}章`, score: c.score?.overall }));
      const span = ((leaves.length || 1) / totalLeaves) * Math.PI * 2 * 0.92;
      const midA = ang + span / 2;
      const ax = R1 * Math.cos(midA), ay = R1 * Math.sin(midA);
      const prog = LEVEL_SIGNS.map(({ id }) => levelChar(a.state, id)).join("");
      nodes.push({
        id: a.id, kind: "arc", label: `${a.name || "(未命名)"}`, prog,
        x: ax, y: ay, angle: midA, span, arcIdx: ai,
        elemCount: Object.values(a.selected || {}).reduce((n, l) => n + (l?.length || 0), 0),
      });
      edges.push(["__root", a.id]);
      const nL = Math.max(leaves.length, 1);
      leaves.forEach((lf, li) => {
        const la = ang + (span * (li + 0.5)) / nL;
        const lx = R2 * Math.cos(la), ly = R2 * Math.sin(la);
        nodes.push({
          id: `${a.id}:${li}`, kind: "leaf", parentArc: a.id,
          label: lf.title, score: typeof lf.score === "number" ? lf.score : null,
          x: lx, y: ly,
        });
        edges.push([a.id, `${a.id}:${li}`]);
      });
      ang += span + (Math.PI * 2 * 0.08) / Math.max(arcs.length, 1);
    });
    return { nodes, edges };
  }, [st]);

  const onWheel = (e) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.12 : 0.9;
    setView((v) => ({ ...v, k: Math.min(3, Math.max(0.25, v.k * factor)) }));
  };

  const onMouseDown = (e) => {
    dragRef.current = { sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y };
    const move = (ev) => {
      if (!dragRef.current) return;
      setView((v) => ({
        ...v, k: v.k,
        x: dragRef.current.ox + (ev.clientX - dragRef.current.sx),
        y: dragRef.current.oy + (ev.clientY - dragRef.current.sy),
      }));
    };
    const up = () => {
      dragRef.current = null;
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  if (!bookRoot) {
    return <div className="emptyguide"><h3>未选书</h3><p>在左侧书目树选择一本书。</p></div>;
  }

  return (
    <div>
      <div className="an-row" style={{ padding: "6px 12px" }}>
        <b>🌳 全书思维导图</b>
        <span className="an-muted">{graph.nodes.filter((n) => n.kind === "arc").length} 情节 · 滚轮缩放 / 拖拽平移</span>
        <button onClick={() => setView({ x: 0, y: 0, k: 1 })}>🎯 复位</button>
      </div>
      <div className="mindmap-wrap" ref={wrapRef} onWheel={onWheel} onMouseDown={onMouseDown}>
        <svg width="100%" height="100%">
          <g transform={`translate(${wrapRef.current ? wrapRef.current.clientWidth / 2 + view.x : view.x},${wrapRef.current ? wrapRef.current.clientHeight / 2 + view.y : view.y}) scale(${view.k})`}>
            {graph.edges.map(([from, to], i) => {
              const a = graph.nodes.find((n) => n.id === from);
              const b = graph.nodes.find((n) => n.id === to);
              if (!a || !b) return null;
              if (a.kind === "root") {
                const bx = b.x * 0.35, by = b.y * 0.35;
                return <path key={i} d={`M0,0 Q${bx},${by} ${b.x},${b.y}`} stroke="var(--dai)" strokeWidth={2} fill="none" opacity={0.8} />;
              }
              const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
              return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="var(--line)" strokeWidth={1.2} />;
            })}
            {graph.nodes.map((n) => {
              if (n.kind === "root") {
                return (
                  <g key={n.id} transform="translate(0,0)">
                    <circle r={46} fill="var(--paper-raised)" stroke="var(--cinnabar)" strokeWidth={2} />
                    <text textAnchor="middle" dy={5} style={{ fontWeight: 700 }}>{n.label}</text>
                  </g>
                );
              }
              if (n.kind === "arc") {
                return (
                  <g key={n.id} transform={`translate(${n.x},${n.y})`} className="mm-node">
                    <rect x={-58} y={-16} width={116} height={34} rx={8}
                      fill="var(--paper-raised)" stroke="var(--dai)" />
                    <text textAnchor="middle" dy={-1}>{truncate(n.label, 9)}</text>
                    <text textAnchor="middle" dy={13} fontSize={10} fill="var(--ink-mute)">{n.prog}</text>
                  </g>
                );
              }
              return (
                <g key={n.id} transform={`translate(${n.x},${n.y})`} className="mm-node">
                  <circle r={7}
                    fill={n.score != null ? scoreColor(n.score) : "var(--paper-raised)"}
                    stroke={n.score != null ? "transparent" : "var(--amber)"} strokeDasharray={n.score == null ? "3 2" : undefined} />
                  <title>{n.label}{n.score != null ? ` · 综合 ${n.score.toFixed(2)}` : " · 草稿"}</title>
                </g>
              );
            })}
          </g>
        </svg>
        <div style={{ position: "absolute", right: 10, bottom: 10 }} className="an-muted">
          ● 实线=已落盘（颜色=综合分）　◌ 虚线=草稿/规划中
        </div>
      </div>
    </div>
  );
}

const LEVEL_SIGNS = [
  { id: "l1" }, { id: "l2" }, { id: "l3" }, { id: "l4" }, { id: "l5" },
];
function levelChar(arcState, lv) {
  if (!arcState) return "○";
  const blk = (arcState.levels || {})[lv];
  if (!blk) return "○";
  const has = lv === "l4" ? blk.scenes : lv === "l3" ? blk.data : blk.text;
  const nonEmpty = has && (!Array.isArray(has) || has.length);
  if (!nonEmpty) return "○";
  return blk.confirmed ? "✓" : "●";
}
function truncate(s, n) {
  s = String(s || "");
  return s.length > n ? `${s.slice(0, n)}…` : s;
}
