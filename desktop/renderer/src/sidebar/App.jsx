import React, { useCallback, useEffect, useRef, useState } from "react";
import { on, rpcCall, restGet, restPost, state as bstate, PH } from "../shared/bridge.js";

// 提案可编辑字段（与原 ChatWindow.getPendingFields 同规则）
function getPendingFields(pc) {
  if (!pc || !pc.args || typeof pc.args !== "object") return null;
  const args = pc.args;
  const t = pc.tool;
  const f = [];
  if (t === "update_element") {
    const lbl = args.field === "desc" ? "新描述" : args.field === "name" ? "新名称" : `内容（${args.field || ""}）`;
    f.push({ key: "value", label: lbl, multi: true, def: args.value ?? args.desc ?? "" });
  } else if (t === "add_element") {
    f.push({ key: "name", label: "名称", multi: false, def: args.name || "" });
    f.push({ key: "desc", label: "描述", multi: true, def: args.desc || "" });
  } else if (t === "new_arc" || t === "create_arc") {
    f.push({ key: "l1", label: "l1 一句话剧情", multi: true, def: args.l1 || "" });
    f.push({ key: "n_chapters", label: "章数", multi: false, def: args.n_chapters || 1 });
  } else if (t === "set_level") {
    f.push({ key: "text", label: `${args.level || "该级"} 内容`, multi: true, def: args.text || "" });
  } else if (t === "modify_level") {
    f.push({ key: "instruction", label: `改写指令（${args.level || ""}）`, multi: true, def: args.instruction || "" });
  } else if (t === "remember") {
    f.push({ key: "content", label: "记忆内容", multi: true, def: args.content || "" });
  }
  return f.length ? f : null;
}

const MODES = [
  { id: "book", icon: "📚", name: "书级" },
  { id: "target", icon: "🎯", name: "定向" },
  { id: "free", icon: "💬", name: "自由对话" },
  { id: "expand", icon: "🧩", name: "扩写" },
];

const MODE_HINTS = {
  book: "书级讨论：定书名/类型文风/世界观主角/元素设定/开篇剧情——助手引导你从零搭起一本书（不碰阶梯生成）",
  target: "定向：带本书上下文的评点、改写、阶梯推进",
  free: "自由对话：不注入本书上下文，随便聊",
  expand: "扩写：让助手直接动手改内容",
};

const QUICK_PROMPTS = {
  book: [
    { icon: "📖", text: "帮我定一个吸引人的书名和一句话故事核" },
    { icon: "🎭", text: "帮我确定故事类型和文风基调" },
    { icon: "🌍", text: "帮我设定世界观和主角" },
    { icon: "🧙", text: "帮我生成角色和元素设定" },
    { icon: "🚀", text: "帮我规划开篇剧情（新建情节）" },
  ],
  target: [
    { icon: "✍️", text: "润色一下正文" },
    { icon: "🎯", text: "评一下这章质量" },
    { icon: "⚠️", text: "查下污染" },
    { icon: "🔎", text: "搜点参考资料" },
    { icon: "💡", text: "帮我想个情节转折" },
  ],
};
QUICK_PROMPTS.free = QUICK_PROMPTS.target;
QUICK_PROMPTS.expand = QUICK_PROMPTS.target;

let chatIdSeq = Date.now() % 100000;

export default function SidebarApp() {
  const [book, setBook] = useState({ root: bstate.bookRoot, name: bstate.bookName });
  const [connected, setConnected] = useState(bstate.connected);
  const [messages, setMessages] = useState([]); // {role:'user'|'ai', content, tool_events?, streaming?}
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [discuss, setDiscuss] = useState(null); // DiscussPayload
  // 访问控制三态
  const [mode, setMode] = useState("target");
  const [acc, setAcc] = useState({ web: false, book: true, memory: true, corpus: true });
  // 待审批卡：{approval_id, tool, args, summary_zh, justification}
  const [approvals, setApprovals] = useState([]);
  const msgsRef = useRef(null);

  useEffect(() => on("init", (s) => { setBook({ root: s.bookRoot, name: s.bookName }); }), []);
  useEffect(() => on("bookChanged", (s) => { setBook({ root: s.bookRoot, name: s.bookName }); }), []);
  useEffect(() => on("connected", (v) => setConnected(v)), []);
  useEffect(() => on("setDiscuss", (p) => setDiscuss(p)), []);

  useEffect(() => {
    if (msgsRef.current) msgsRef.current.scrollTop = msgsRef.current.scrollHeight;
  }, [messages, approvals]);

  // ── RPC 事件订阅 ──
  useEffect(() => {
    const offs = [];
    offs.push(on("rpcEvent:event/chat.token", ({ chat_id, text }) => {
      setMessages((ms) => {
        const i = ms.findIndex((m) => m.chatId === chat_id && m.streaming);
        if (i < 0) return ms;
        const next = [...ms];
        next[i] = { ...next[i], content: next[i].content + text };
        return next;
      });
    }));
    offs.push(on("rpcEvent:event/chat.tool_call", ({ chat_id, event }) => {
      setMessages((ms) => {
        const i = ms.findIndex((m) => m.chatId === chat_id && m.streaming);
        if (i < 0) return ms;
        const next = [...ms];
        next[i] = { ...next[i], tool_events: [...(next[i].tool_events || []), event] };
        return next;
      });
    }));
    offs.push(on("rpcEvent:event/chat.pending_proposal", ({ proposal }) => {
      // 提案会以 approval/request 形式到达；此处仅提示
    }));
    offs.push(on("rpcEvent:event/chat.done", ({ chat_id, result }) => {
      setMessages((ms) => {
        const i = ms.findIndex((m) => m.chatId === chat_id && m.streaming);
        const next = [...ms];
        const finalReply = String(result?.reply ?? "");
        if (i < 0) {
          next.push({
            role: "ai", content: finalReply, chatId: chat_id,
            tool_events: result?.tool_events || [], streaming: false,
          });
        } else {
          // 用最终 reply 覆盖流式文本（流式只覆盖 reply 字段，可能带转义差异）
          next[i] = {
            ...next[i], streaming: false,
            content: finalReply || next[i].content,
            tool_events: [...(next[i].tool_events || []), ...(result?.tool_events || [])],
          };
        }
        return next;
      });
      setBusy(false);
    }));
    offs.push(on("rpcEvent:event/chat.error", ({ error }) => {
      setMessages((ms) => [...ms, { role: "ai", content: `⚠ ${error}`, streaming: false }]);
      setBusy(false);
    }));
    offs.push(on("rpcEvent:approval/request", (p) => {
      setApprovals((a) => (a.some((x) => x.approval_id === p.approval_id) ? a : [...a, p]));
    }));
    offs.push(on("rpcEvent:event/chat.applied", ({ approval_id }) => {
      setApprovals((a) => a.filter((x) => x.approval_id !== approval_id));
    }));
    return () => offs.forEach((d) => d());
  }, []);

  const send = useCallback(async (text) => {
    if (!text || busy) return;
    if (!book.root) {
      setMessages((ms) => [...ms, { role: "ai", content: "请先在书目树选择当前书。", streaming: false }]);
      return;
    }
    setBusy(true);
    const chatId = `sb-${chatIdSeq++}`;
    // 组装历史：讨论对象注入最后一条 user 消息前部（与原前端 handleChat 相同约定）
    let userMsg = text;
    if (discuss && mode === "target") {
      userMsg = `【讨论对象：${discuss.name}】\n${(discuss.content || "").slice(0, 2000)}\n\n用户指令：${text}`;
    }
    const history = [
      ...messages.filter((m) => !m.streaming).map((m) => ({ role: m.role === "user" ? "user" : "assistant", content: m.content })),
      { role: "user", content: userMsg },
    ];
    setMessages((ms) => [...ms, { role: "user", content: text }, { role: "ai", content: "", chatId, streaming: true, tool_events: [] }]);
    try {
      await rpcCall("chat.start", {
        chat_id: chatId,
        book_root: book.root,
        arc_id: "",
        messages: history,
        web_search: mode === "target" || mode === "book" ? !!acc.web : false,
        access: mode === "free"
          ? { book: true, memory: true, corpus: true, web: false }
          : { ...acc },
        sel_access: null,
        // 书级讨论 = 后端 init 模式（初始化助手：设定/元素/开篇规划，无阶梯工具）
        mode: mode === "book" ? "init" : "normal",
      });
    } catch (e) {
      setMessages((ms) => [...ms.slice(0, -1), { role: "ai", content: `⚠ 发送失败：${e.message}` }]);
      setBusy(false);
    }
  }, [busy, book, messages, discuss, mode, acc]);

  const respond = async (ap, approved, edits) => {
    try {
      await rpcCall("approval.respond", {
        approval_id: ap.approval_id,
        approved,
        ...(approved && edits ? { args: edits } : {}),
      });
    } catch (e) {
      console.error(e);
    }
  };

  const toggleAcc = (k) => setAcc((a) => ({ ...a, [k]: !a[k] }));

  return (
    <div className="sb-root">
      {/* 连接/书状态条 */}
      <div className="an-row" style={{ padding: "6px 10px", borderBottom: "1px solid var(--line-soft)", fontSize: 11 }}>
        <span className={connected ? "an-ok" : "an-bad"}>●</span>
        <span>{connected ? "已连接" : "离线"}</span>
        <span className="an-muted">·</span>
        <span style={{ fontWeight: 600 }}>{book.name || "未选书"}</span>
      </div>

      {/* 模式三态 */}
      <div className="modebar">
        {MODES.map((m) => (
          <button key={m.id} className={mode === m.id ? "on" : ""} onClick={() => setMode(m.id)}>
            {m.icon} {m.name}
          </button>
        ))}
      </div>

      {/* 访问开关（定向/书级模式） */}
      {(mode === "target" || mode === "book") && (
        <div className="accrow">
          <span className="an-muted">访问：</span>
          {[["web", "🌐联网"], ["book", "📚本书"], ["memory", "🧠记忆"], ["corpus", "📄语料"]].map(([k, label]) => (
            <span key={k} className={`accswitch ${acc[k] ? "on" : ""}`} onClick={() => toggleAcc(k)}>{label}</span>
          ))}
        </div>
      )}

      {/* 模式提示 */}
      <div className="an-muted" style={{ padding: "2px 10px 4px", fontSize: 11 }}>
        {MODE_HINTS[mode]}
      </div>

      {/* 讨论对象横幅 */}
      {discuss && (
        <div className="sb-banner">
          🎯 <span className="name">{discuss.name}</span>
          <span className="content">{(discuss.content || "").slice(0, 60)}</span>
          <button onClick={() => setDiscuss(null)}>✕</button>
        </div>
      )}

      {/* 消息流 */}
      <div className="sb-msgs" ref={msgsRef}>
        {messages.length === 0 && approvals.length === 0 && (
          <div className="emptyguide">
            {mode === "book" ? (
              <>
                <h3>📚 书级讨论</h3>
                <p>从零搭一本书：书名 → 类型文风 → 世界观主角 → 元素 → 开篇剧情。</p>
                <p className="an-muted">助手会逐步提炼成基本设定并生成设定集，改阶梯请切「定向」。</p>
              </>
            ) : (
              <>
                <h3>💬 创作助手</h3>
                <p>评点、改写、推进阶梯、检索参考——直接说。</p>
                <p className="an-muted">在编辑器里选中一段文字右键「与助手讨论此段」可注入上下文。</p>
              </>
            )}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`sb-msg ${m.role}`}>
            <div className="who">{m.role === "user" ? "你" : "助手"}</div>
            {(m.tool_events || []).map((ev, j) => (
              <div key={j} className="sb-toolcard">
                {ev.summary}
                {ev.detail && (
                  <details>
                    <summary>详情</summary>
                    <pre>{ev.detail}</pre>
                  </details>
                )}
              </div>
            ))}
            <div className="bubble">
              {m.content}
              {m.streaming && !m.content && (
                <span className="sb-typing"><span className="sb-dot" /><span className="sb-dot" /><span className="sb-dot" /></span>
              )}
              {m.streaming && m.content && <span className="sb-typing">▍</span>}
            </div>
          </div>
        ))}

        {/* 内联审批卡（可编辑草稿） */}
        {approvals.map((ap) => (
          <ApprovalCard key={ap.approval_id} ap={ap} onRespond={respond} />
        ))}
      </div>

      {/* 快捷提问（按模式） */}
      {!busy && messages.length === 0 && (
        <div className="sb-quick">
          {(QUICK_PROMPTS[mode] || QUICK_PROMPTS.target).map((q) => (
            <button key={q.text} onClick={() => send(q.text)}>{q.icon} {q.text}</button>
          ))}
        </div>
      )}

      {/* 输入区 */}
      <div className="sb-inputbar">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={`${book.name || "未选书"}｜${mode === "book" ? "聊聊你的故事想法…" : "说点什么…"}（Enter 发送）`}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send(input.trim());
              setInput("");
            }
          }}
        />
        <button className="primary" disabled={busy || !input.trim()} style={{ alignSelf: "flex-end", padding: "8px 14px" }}
          onClick={() => { send(input.trim()); setInput(""); }}>
          {busy ? "…" : "发送"}
        </button>
      </div>
    </div>
  );
}

function ApprovalCard({ ap, onRespond }) {
  const fields = getPendingFields(ap);
  const [draft, setDraft] = useState(() => {
    const d = {};
    (fields || []).forEach((f) => { d[f.key] = f.def; });
    return d;
  });
  const edited = fields?.some((f) => draft[f.key] !== undefined && String(draft[f.key]) !== String(f.def));
  return (
    <div className="approval-card">
      <div className="sum">🤖 助手想执行：{ap.summary_zh}</div>
      {ap.justification && ap.justification !== "用户同意后执行。" && (
        <div className="just">{ap.justification}</div>
      )}
      {fields && (
        <div style={{ marginTop: 6, padding: 7, border: "1px dashed var(--dai)", borderRadius: 5, background: "var(--paper)" }}>
          <div className="an-muted">✏️ 可在同意前修改{edited ? "（已改，将应用你的版本）" : ""}</div>
          {fields.map((f) => (
            <div key={f.key}>
              <div className="an-muted" style={{ margin: "4px 0 2px" }}>{f.label}</div>
              {f.multi ? (
                <textarea rows={3} value={draft[f.key] ?? f.def}
                  onChange={(e) => setDraft((p) => ({ ...p, [f.key]: e.target.value }))} />
              ) : (
                <input type="text" value={draft[f.key] ?? f.def}
                  onChange={(e) => setDraft((p) => ({ ...p, [f.key]: e.target.value }))} />
              )}
            </div>
          ))}
        </div>
      )}
      <div className="approval-actions">
        <button className="primary"
          onClick={() => onRespond(ap, true, fields ? { ...ap.args, ...Object.fromEntries(Object.entries(draft).filter(([, v]) => v !== undefined)) } : undefined)}>
          ✅ 同意执行
        </button>
        <button onClick={() => onRespond(ap, false)}>拒绝</button>
        <span className="an-muted" style={{ alignSelf: "center" }}>超时默认拒绝</span>
      </div>
    </div>
  );
}
