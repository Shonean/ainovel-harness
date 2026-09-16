import React, { useEffect, useState } from "react";
import { restGet, PH } from "../../shared/bridge.js";

// 日志中心：LLM 调用流水（按天）+ 训练会话列表

export default function LogsPanel() {
  const [tab, setTab] = useState("llm");
  const [dates, setDates] = useState([]);
  const [curDate, setCurDate] = useState("");
  const [rows, setRows] = useState(null);
  const [sessions, setSessions] = useState(null);

  useEffect(() => {
    if (tab !== "llm") return;
    void restGet(`${PH}/logs/llm/dates`).then((d) => {
      const list = d?.dates ?? d ?? [];
      setDates(list);
      if (list.length && !curDate) setCurDate(list[0]);
    }).catch(() => {});
  }, [tab]);

  useEffect(() => {
    if (tab !== "llm" || !curDate) return;
    setRows(null);
    void restGet(`${PH}/logs/llm?date=${encodeURIComponent(curDate)}&limit=200`)
      .then(setRows).catch(() => {});
  }, [curDate, tab]);

  useEffect(() => {
    if (tab !== "session") return;
    void restGet(`${PH}/logs/sessions`).then(setSessions).catch(() => {});
  }, [tab]);

  const items = rows?.items ?? rows?.calls ?? rows ?? [];

  return (
    <div className="an-col">
      <div className="modebar">
        {[["llm", "🤖 LLM 调用"], ["session", "📊 训练会话"]].map(([k, label]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
        {tab === "llm" && dates.length > 0 && (
          <select style={{ marginLeft: "auto", marginRight: 10 }} value={curDate} onChange={(e) => setCurDate(e.target.value)}>
            {dates.slice(0, 14).map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        )}
      </div>
      <div className="an-scroll">
        {tab === "llm" && (
          <>
            {Array.isArray(items) && items.length > 0 && (() => {
              const totalTok = items.reduce((n, r) => n + Number(r.total_tokens || 0), 0);
              const errs = items.filter((r) => r.status === "error").length;
              const avgLat = Math.round(items.reduce((n, r) => n + Number(r.latency_ms || 0), 0) / items.length);
              return (
                <div className="an-row" style={{ marginBottom: 10 }}>
                  <span className="an-tag">调用 {items.length}</span>
                  <span className="an-tag">tokens {totalTok.toLocaleString()}</span>
                  <span className="an-tag">错误 {errs}</span>
                  <span className="an-tag">平均延迟 {avgLat}ms</span>
                </div>
              );
            })()}
            <table className="an-table">
              <thead><tr><th>时间</th><th>类型</th><th>模型</th><th>tokens</th><th>延迟</th><th>状态</th></tr></thead>
              <tbody>
                {!Array.isArray(items) ? (
                  <tr><td colSpan={6} className="an-muted">加载中…</td></tr>
                ) : items.map((r, i) => (
                  <tr key={i}>
                    <td className="an-muted">{String(r.ts ?? "").slice(11, 19)}</td>
                    <td>{r.call_type ?? ""}</td>
                    <td className="an-muted">{r.model ?? ""}</td>
                    <td>{r.total_tokens ?? ""}</td>
                    <td>{r.latency_ms ?? ""}ms</td>
                    <td className={r.status === "error" ? "an-bad" : "an-ok"}>{r.status ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        {tab === "session" && (
          <table className="an-table">
            <thead><tr><th>会话</th><th>来源</th><th>轮次</th><th>时间</th></tr></thead>
            <tbody>
              {(sessions?.items ?? sessions ?? []).length === 0 ? (
                <tr><td colSpan={4} className="an-muted">暂无会话记录</td></tr>
              ) : (sessions?.items ?? []).map((s) => (
                <tr key={s.session_id ?? s.id}>
                  <td>{String(s.session_id ?? s.id ?? "").slice(0, 12)}</td>
                  <td className="an-muted">{s.source_file ?? ""}</td>
                  <td>{s.round_count ?? s.rounds ?? ""}</td>
                  <td className="an-muted">{String(s.created_at ?? "").slice(0, 16)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
