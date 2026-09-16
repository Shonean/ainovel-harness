import React, { useEffect, useState } from "react";
import { on, restPost, state as bstate, PH } from "../shared/bridge.js";

// 片段扩写：半成品片段（对白+叙述+【】标记）→ 解析 → 理解 → 扩写（锚点逐字保留）→ 落盘

export default function FragmentPanel() {
  const [bookRoot, setBookRoot] = useState(bstate.bookRoot);
  const [text, setText] = useState("");
  const [slots, setSlots] = useState(null);   // 解析结果
  const [understanding, setUnderstanding] = useState(null);
  const [expanded, setExpanded] = useState(null); // {segments:[{type:'anchor'|'fill',text}]}
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => on("init", (s) => setBookRoot(s.bookRoot)), []);
  useEffect(() => on("bookChanged", (s) => setBookRoot(s.bookRoot)), []);

  const run = async (step) => {
    if (!bookRoot) return;
    setBusy(step);
    try {
      if (step === "parse") {
        const r = await restPost(`${PH}/ai-creation/fragment/parse`, { book_root: bookRoot, content: text });
        setSlots(r);
        setUnderstanding(null);
        setNotice(`解析出 ${r.slots?.length ?? r.count ?? "?"} 个【】槽位`);
      } else if (step === "understand") {
        const r = await restPost(`${PH}/ai-creation/fragment/understand`, { book_root: bookRoot, content: text });
        setUnderstanding(r);
      } else if (step === "expand") {
        const r = await restPost(`${PH}/ai-creation/fragment/expand`, { book_root: bookRoot, content: text });
        setExpanded(r);
        setNotice(r.check ? `锚点保真 ${r.check.anchor_ok ?? "?"} · 覆盖 ${r.check.coverage ?? "?"}` : "扩写完成");
      }
    } catch (e) {
      setNotice(`⚠ ${e.message}`);
    } finally {
      setBusy("");
    }
  };

  const finalize = async () => {
    try {
      setBusy("finalize");
      await restPost(`${PH}/ai-creation/fragment/finalize`, { book_root: bookRoot });
      setNotice("已落盘到当前书");
    } catch (e) {
      setNotice(`⚠ 落盘失败：${e.message}`);
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="an-col">
      <div className="an-row" style={{ padding: "6px 12px" }}>
        <b>🧩 片段扩写</b>
        <span className="an-muted">锚点逐字保留，只扩写【】内</span>
      </div>
      {!bookRoot && <div className="emptyguide"><h3>未选书</h3></div>}
      {bookRoot && (
        <div className="an-scroll">
          <div className="an-card">
            <div className="an-title">① 粘贴片段</div>
            <textarea rows={8} style={{ width: "100%", marginTop: 6 }} value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={'例：他握紧刀柄。【抬头看向城门】"你来了。"'} />
            <button className="primary" onClick={() => void run("parse")} disabled={!!busy || !text.trim()}>解析【】</button>
          </div>

          {slots && (
            <div className="an-card">
              <div className="an-title">② 理解标记</div>
              {(slots.slots || []).map((s, i) => (
                <div key={i} className="an-muted" style={{ marginTop: 4 }}>
                  槽{i + 1}：{String(s.raw ?? s.text ?? JSON.stringify(s)).slice(0, 60)}
                </div>
              ))}
              <button onClick={() => void run("understand")} disabled={!!busy}>让 AI 先理解</button>
              {understanding && <pre className="an-muted" style={{ marginTop: 6 }}>{JSON.stringify(understanding.slots ?? understanding, null, 2).slice(0, 1200)}</pre>}
            </div>
          )}

          {slots && (
            <div className="an-card">
              <div className="an-title">③ 扩写</div>
              <button className="primary" onClick={() => void run("expand")} disabled={!!busy}>
                {busy === "expand" ? "扩写中…" : "开始扩写"}
              </button>
              {expanded && (
                <pre style={{ marginTop: 8, fontSize: 12.5 }}>
                  {(expanded.segments || []).map((s, i) =>
                    s.type === "anchor"
                      ? <span key={i} style={{ color: "var(--ink)", fontWeight: 600 }}>{s.text}</span>
                      : <span key={i} style={{ color: "var(--dai)" }}>{s.text}</span>)}
                </pre>
              )}
              {expanded && <button onClick={void finalize} disabled={!!busy}>④ 落盘到书</button>}
            </div>
          )}
          {notice && <div className="an-muted">{notice}</div>}
        </div>
      )}
    </div>
  );
}
