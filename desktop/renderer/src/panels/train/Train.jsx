import React, { useEffect, useState } from "react";
import { on, restGet, restPost, state as bstate, PH } from "../../shared/bridge.js";

// 训练台：榨干提取（语料→模板库）+ 经验库浏览
// 端点与老前端一致：POST /plot-templates/extract（提取出弧）→ POST /plot-templates/extract-store（逐弧入库）

export default function TrainPanel() {
  const [bookRoot, setBookRoot] = useState(bstate.bookRoot);
  const [tab, setTab] = useState("extract");
  const [corpusFiles, setCorpusFiles] = useState([]);
  const [fileIdx, setFileIdx] = useState(0);
  const [minScore, setMinScore] = useState("0.65");
  const [chFrom, setChFrom] = useState("1");
  const [chTo, setChTo] = useState("");
  const [running, setRunning] = useState(false);
  const [arcsFound, setArcsFound] = useState([]); // 提取出的弧（含 task_id）
  const [taskId, setTaskId] = useState("");
  const [storedIds, setStoredIds] = useState(new Set());
  const [notice, setNotice] = useState("");

  useEffect(() => on("init", (s) => setBookRoot(s.bookRoot)), []);
  useEffect(() => on("bookChanged", (s) => setBookRoot(s.bookRoot)), []);

  useEffect(() => {
    if (tab !== "extract") return;
    void restGet(`${PH}/corpus/files`).then((d) => {
      const files = d?.files ?? d?.items ?? [];
      setCorpusFiles(files);
    }).catch(() => {});
  }, [tab]);

  const runExtract = async () => {
    const f = corpusFiles[fileIdx];
    if (!f || running) return;
    setRunning(true);
    setNotice(`提取中：${f.name ?? f.filename ?? ""} ${chFrom}-${chTo || "∞"}…`);
    try {
      const body = {
        filepath: f.path ?? f.filename ?? f,
        start_chapter: Number(chFrom) || 1,
        min_score: Number(minScore) || 0.65,
        budget: 80,
      };
      if (chTo) body.end_chapter = Number(chTo);
      const r = await restPost(`${PH}/plot-templates/extract`, body);
      setTaskId(String(r.task_id ?? ""));
      setArcsFound(r.arcs ?? r.result?.arcs ?? []);
      setNotice(`提取完成：${(r.arcs ?? []).length} 条弧候选（达标线 ${minScore}）`);
    } catch (e) {
      setNotice(`⚠ ${e.message}`);
    } finally {
      setRunning(false);
    }
  };

  const storeArc = async (arc) => {
    try {
      await restPost(`${PH}/plot-templates/extract-store`, { task_id: taskId, arc });
      setStoredIds((s) => new Set([...s, arc.arc_id ?? JSON.stringify(arc).slice(0, 40)]));
      setNotice("✓ 已入模板库");
    } catch (e) {
      setNotice(`⚠ 入库失败：${e.message}`);
    }
  };

  return (
    <div className="an-col">
      <div className="modebar">
        {[["extract", "⛏ 榨干提取"], ["exp", "📚 经验库"]].map(([k, label]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      <div className="an-scroll">
        {tab === "extract" && (
          <>
            <div className="an-card an-row">
              <label className="an-muted">语料</label>
              <select value={fileIdx} onChange={(e) => setFileIdx(Number(e.target.value))}>
                {(corpusFiles.length ? corpusFiles : ["（无语料）"]).map((f, i) => (
                  <option key={i} value={i}>{typeof f === "string" ? f : f.name ?? f.filename}</option>
                ))}
              </select>
              <label className="an-muted">章节</label>
              <input type="text" style={{ width: 60 }} value={chFrom} onChange={(e) => setChFrom(e.target.value)} />
              <span className="an-muted">-</span>
              <input type="text" style={{ width: 60 }} placeholder="∞" value={chTo} onChange={(e) => setChTo(e.target.value)} />
              <label className="an-muted">达标线</label>
              <input type="text" style={{ width: 60 }} value={minScore} onChange={(e) => setMinScore(e.target.value)} />
              <button className="primary" disabled={running || !corpusFiles.length} onClick={() => void runExtract()}>
                {running ? "提取中…" : "▶ 开始提取"}
              </button>
            </div>
            {notice && <p className="an-muted">{notice}</p>}
            <table className="an-table">
              <thead><tr><th>弧</th><th>章数</th><th>分数</th><th></th></tr></thead>
              <tbody>
                {arcsFound.map((a, i) => {
                  const key = a.arc_id ?? i;
                  return (
                    <tr key={key}>
                      <td>{a.name ?? a.title ?? `弧${i + 1}`}</td>
                      <td>{a.chapter_count ?? (a.chapters?.length ?? "?")}</td>
                      <td>{typeof a.score === "number" ? a.score.toFixed(2) : ""}</td>
                      <td>
                        {storedIds.has(key)
                          ? <span className="an-ok">✓ 已入库</span>
                          : <button className="primary" onClick={() => void storeArc(a)}>入库</button>}
                      </td>
                    </tr>
                  );
                })}
                {!arcsFound.length && <tr><td colSpan={4} className="an-muted">尚无提取结果</td></tr>}
              </tbody>
            </table>
          </>
        )}
        {tab === "exp" && <Experiences />}
      </div>
    </div>
  );
}

function Experiences() {
  const [exps, setExps] = useState(null);
  useEffect(() => {
    void restGet(`${PH}/experiences`).then(setExps).catch(() => {});
  }, []);
  const items = exps?.experiences ?? exps?.items ?? [];
  return (
    <table className="an-table">
      <thead><tr><th>ID</th><th>来源</th><th>分数</th></tr></thead>
      <tbody>
        {!exps ? (
          <tr><td colSpan={3} className="an-muted">加载中…</td></tr>
        ) : items.length === 0 ? (
          <tr><td colSpan={3} className="an-muted">空（训练后自动入库）</td></tr>
        ) : items.slice(0, 50).map((e) => (
          <tr key={e.id}>
            <td>{String(e.id).slice(0, 8)}</td>
            <td className="an-muted">{e.source_file ?? e.section ?? ""}</td>
            <td>{typeof e.score === "number" ? e.score.toFixed(3) : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
