// _ssr_lintai_check.jsx
import { createElement } from "react";
import { renderToString } from "react-dom/server";

// src/components/ConnectionPanel.jsx
import { useState } from "react";
import { jsx, jsxs } from "react/jsx-runtime";
var K = {
  l1: { bg: "#E3D9C4", fg: "#6B4F1D" },
  l2: { bg: "#D7CDD8", fg: "#5A3E63" },
  l3: { bg: "#CDE0D9", fg: "#2F5E4A" },
  l4: { bg: "#D4E0E9", fg: "#2D4E6B" },
  l5: { bg: "#F0D6D0", fg: "#7A2E1D" }
};
var tStyle = { color: "var(--ink-mute)", fontSize: 11, lineHeight: 1.5 };
var sec = (color) => ({
  fontSize: 10.5,
  fontWeight: 700,
  letterSpacing: ".14em",
  textTransform: "uppercase",
  color: color || "var(--ink-mute)",
  margin: "2px 0 5px"
});
function InvChain({ invariants, baseline }) {
  const [open, setOpen] = useState({});
  const rows = [...invariants || []];
  return /* @__PURE__ */ jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 4 }, children: [
    rows.map((iv) => {
      const kc = K[iv.level] || K.l1;
      const o = !!open[iv.level];
      return /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "flex-start", gap: 7, padding: "5px 7px", border: "1px solid var(--line-faint)", borderRadius: 6, background: "var(--paper)" }, children: [
        /* @__PURE__ */ jsxs("div", { style: { flexShrink: 0, width: 88 }, children: [
          /* @__PURE__ */ jsxs("div", { style: { fontWeight: 700, fontSize: 11.5 }, children: [
            /* @__PURE__ */ jsx("span", { style: { display: "inline-block", minWidth: 22, textAlign: "center", borderRadius: 4, padding: "1px 4px", fontSize: 10.5, background: kc.bg, color: kc.fg }, children: iv.level }),
            " ",
            iv.name || ""
          ] }),
          /* @__PURE__ */ jsx("div", { style: { fontSize: 10, color: "var(--ink-mute)" }, children: iv.injection })
        ] }),
        /* @__PURE__ */ jsxs("div", { style: { flex: 1, fontSize: 11, color: "var(--ink-sub)", lineHeight: 1.5 }, children: [
          o ? iv.text : iv.text.slice(0, 52) + (iv.text.length > 52 ? "\u2026" : ""),
          iv.text.length > 52 && /* @__PURE__ */ jsx("span", { style: { color: "var(--dai)", cursor: "pointer", marginLeft: 4 }, onClick: () => setOpen((p) => ({ ...p, [iv.level]: !p[iv.level] })), children: o ? "\u25B3 \u6536\u8D77" : "\u25BE \u5C55\u5F00" })
        ] }),
        /* @__PURE__ */ jsxs("div", { style: { flexShrink: 0, fontSize: 10.5, color: "var(--ink-mute)" }, children: [
          iv.length,
          " \u5B57"
        ] })
      ] }, iv.level);
    }),
    baseline && /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "flex-start", gap: 7, padding: "5px 7px", border: "1px dashed var(--cin-border)", borderRadius: 6, background: "var(--cin-wash)" }, children: [
      /* @__PURE__ */ jsxs("div", { style: { flexShrink: 0, width: 88 }, children: [
        /* @__PURE__ */ jsx("div", { style: { fontWeight: 700, fontSize: 11.5, color: "var(--cin-d)" }, children: "\u2694 \u9632\u7EBF" }),
        /* @__PURE__ */ jsx("div", { style: { fontSize: 10, color: "var(--ink-mute)" }, children: "baseline_guard" })
      ] }),
      /* @__PURE__ */ jsxs("div", { style: { flex: 1, fontSize: 11, color: "var(--ink-sub)", lineHeight: 1.5 }, children: [
        baseline.text.slice(0, 60),
        baseline.text.length > 60 ? "\u2026" : ""
      ] }),
      /* @__PURE__ */ jsxs("div", { style: { flexShrink: 0, fontSize: 10.5, color: "var(--ink-mute)" }, children: [
        baseline.length,
        " \u5B57"
      ] })
    ] })
  ] });
}
function Chain() {
  return /* @__PURE__ */ jsx("div", { style: { display: "flex", flexDirection: "column", gap: 6 }, children: [
    { ic: "\u{1F4DA}", t: "\u8BED\u6599\u5E93", d: "\u5927\u5949\u6253\u66F4\u4EBA \xB7 \u9752\u5C71", r: "2 \u672C" },
    { ic: "\u{1F9C3}", t: "\u69A8\u5E72\u63D0\u53D6", d: "\u9010\u7AE0\u538B\u7F29\u9636\u68AF \u2192 \u91CD\u5EFA\u6821\u9A8C", r: "\u8FBE\u6807\u22650.65" },
    { ic: "\u{1F4E6}", t: "\u6A21\u677F\u5E93", d: "\u53BB\u5B9E\u4F53\u5316\u69FD\u4F4D\u6A21\u677F l1-l4", r: "\u5D4C\u5165\u7D22\u5F15" },
    { ic: "\u{1F517}", t: "\u547D\u4E2D \u2192 state.template", d: "new_arc \u81EA\u52A8 match\uFF08\u76F8\u4F3C\u22650.50\uFF09", r: "\u683C\u5F0F+\u7B56\u7565\u6CE8\u5165" },
    { ic: "\u270D\uFE0F", t: "\u4E3B\u7CFB\u7EDF \xB7 \u9010\u5F27\u521B\u4F5C", d: "l1\u2192l5 \u2192 \u5143\u7D20\u9694\u79BB \u2192 \u53CC\u8BC4\u5206", r: "\u843D\u76D8" }
  ].map((s, i) => /* @__PURE__ */ jsxs("div", { children: [
    i > 0 && /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "center", gap: 6, color: "var(--ink-faint)", fontSize: 11, paddingLeft: 26 }, children: [
      /* @__PURE__ */ jsx("span", { style: { width: 5, height: 5, borderRadius: "50%", background: "var(--dai)" } }),
      s.linkHint || "\u2193"
    ] }),
    /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", border: "1px solid var(--line-soft)", borderRadius: 6, background: "var(--paper)" }, children: [
      /* @__PURE__ */ jsx("span", { style: { fontSize: 15, flexShrink: 0 }, children: s.ic }),
      /* @__PURE__ */ jsxs("div", { style: { flex: 1 }, children: [
        /* @__PURE__ */ jsx("div", { style: { fontWeight: 600, fontSize: 12.5 }, children: s.t }),
        /* @__PURE__ */ jsx("div", { style: { fontSize: 11, color: "var(--ink-mute)" }, children: s.d })
      ] }),
      /* @__PURE__ */ jsx("div", { style: { flexShrink: 0, textAlign: "right", fontSize: 11, color: "var(--ink-mute)" }, children: s.r })
    ] })
  ] }, i)) });
}
function Consumption({ items }) {
  const list = items || [];
  return /* @__PURE__ */ jsxs("table", { style: { width: "100%", borderCollapse: "collapse", fontSize: 11.5 }, children: [
    /* @__PURE__ */ jsx("thead", { children: /* @__PURE__ */ jsxs("tr", { style: { textAlign: "left" }, children: [
      /* @__PURE__ */ jsx("th", { style: { padding: "3px 6px", borderBottom: "1px solid var(--line-soft)", color: "var(--ink-mute)", fontSize: 10.5 }, children: "\u4E66 / \u5F27" }),
      /* @__PURE__ */ jsx("th", { style: { padding: "3px 6px", borderBottom: "1px solid var(--line-soft)", color: "var(--ink-mute)", fontSize: 10.5 }, children: "\u6A21\u677F" }),
      /* @__PURE__ */ jsx("th", { style: { padding: "3px 6px", borderBottom: "1px solid var(--line-soft)", color: "var(--ink-mute)", fontSize: 10.5 }, children: "\u76F8\u4F3C" })
    ] }) }),
    /* @__PURE__ */ jsxs("tbody", { children: [
      list.length === 0 && /* @__PURE__ */ jsx("tr", { children: /* @__PURE__ */ jsx("td", { colSpan: "3", style: { padding: "6px 6px", color: "var(--ink-faint)", fontSize: 11 }, children: "\u7A7A \u2014 \u5728\u4E3B\u7CFB\u7EDF\u5DE5\u4F5C\u53F0\u65B0\u5EFA\u5F27\u3001\u547D\u4E2D\u6A21\u677F\u540E\u81EA\u52A8\u663E\u793A\uFF08\u6570\u636E\u6E90\uFF1A\u4E3B\u7CFB\u7EDF arcs.json state.template\uFF09" }) }),
      list.slice(0, 8).map((c, i) => /* @__PURE__ */ jsxs("tr", { style: { borderBottom: "1px dashed var(--line-faint)" }, children: [
        /* @__PURE__ */ jsxs("td", { style: { padding: "3px 6px" }, children: [
          c.book,
          " \xB7 ",
          c.arc_name
        ] }),
        /* @__PURE__ */ jsx("td", { style: { padding: "3px 6px", color: "var(--dai-dark)" }, children: c.template_name }),
        /* @__PURE__ */ jsx("td", { style: { padding: "3px 6px", color: "var(--green)", fontWeight: 700 }, children: c.similarity != null ? (+c.similarity).toFixed(3) : "\u2014" })
      ] }, i))
    ] })
  ] });
}
function Matches({ items }) {
  const list = items || [];
  if (list.length === 0) {
    return /* @__PURE__ */ jsx("div", { style: tStyle, children: "\uFF08\u6682\u65E0 plot_template_match \u8BB0\u5F55\uFF09" });
  }
  return /* @__PURE__ */ jsx("div", { children: list.slice(0, 4).map((m, i) => /* @__PURE__ */ jsxs("div", { style: { marginBottom: 7 }, children: [
    /* @__PURE__ */ jsxs("div", { style: { fontSize: 11.5, background: "var(--dai-wash)", padding: "5px 7px", borderRadius: 6, marginBottom: 4, color: "var(--ink-sub)" }, children: [
      /* @__PURE__ */ jsx("span", { style: { fontSize: 10, color: "var(--ink-mute)", marginRight: 6 }, children: m.date }),
      m.user.slice(0, 70),
      m.user.length > 70 ? "\u2026" : ""
    ] }),
    m.matched.length > 0 ? /* @__PURE__ */ jsxs("div", { style: { fontSize: 11.5, padding: "4px 7px", borderLeft: "3px solid var(--green)", background: "var(--green-wash)", borderRadius: "0 5px 5px 0" }, children: [
      "\u{1F3AF} \u5019\u9009 ",
      m.matched.length,
      "\uFF1A",
      m.matched.slice(0, 2).join("\uFF1B"),
      /* @__PURE__ */ jsx("span", { style: { color: "var(--ink-mute)", fontSize: 10.5, marginLeft: 4 }, children: "\uFF08LLM \u4EF2\u88C1\u5019\u9009 \xB7 \u662F\u5426\u5957\u7528\u770B\u76F8\u4F3C\u5EA6 \u22650.50\uFF09" })
    ] }) : /* @__PURE__ */ jsx("div", { style: { fontSize: 11.5, padding: "4px 7px", borderLeft: "3px solid var(--amber)", background: "var(--amber-wash)", borderRadius: "0 5px 5px 0" }, children: "\u26D4 \u65E0\u5019\u9009" })
  ] }, i)) });
}
function ConnectionPanel({ overview }) {
  const o = overview || {};
  const counts = o.counts || {};
  return /* @__PURE__ */ jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 10 }, children: [
    /* @__PURE__ */ jsx("div", { style: { display: "flex", gap: 8, flexWrap: "wrap" }, children: [
      ["\u8BED\u6599", counts.corpus_file_count ?? "\u2014", "\u672C"],
      ["\u6A21\u677F\u5E93", counts.template_count ?? "\u2014", "\u6761"],
      ["\u4E3B\u7CFB\u7EDF\u6D88\u8D39", counts.consumption_count ?? "\u2014", "\u5F27"]
    ].map(([k, v, u], i) => /* @__PURE__ */ jsxs("div", { style: { flex: 1, minWidth: 70, textAlign: "center", padding: "6px 4px", border: "1px solid var(--line-soft)", borderRadius: 6, background: "var(--paper-raised)" }, children: [
      /* @__PURE__ */ jsx("div", { style: { fontSize: 18, fontWeight: 700, color: "var(--dai-dark)" }, children: v }),
      /* @__PURE__ */ jsxs("div", { style: { fontSize: 10.5, color: "var(--ink-mute)" }, children: [
        k,
        "\uFF08",
        u,
        "\uFF09"
      ] })
    ] }, k)) }),
    /* @__PURE__ */ jsxs("div", { children: [
      /* @__PURE__ */ jsx("div", { style: sec("var(--dai-dark)"), children: "\u2460 \u4E94\u7EA7\u4E0D\u53D8 prompt \u2192 \u751F\u6210\u94FE\u8DEF" }),
      /* @__PURE__ */ jsx(InvChain, { invariants: o.invariants, baseline: o.baseline_guard })
    ] }),
    /* @__PURE__ */ jsxs("div", { children: [
      /* @__PURE__ */ jsx("div", { style: { ...sec("var(--dai-dark)"), marginTop: 8 }, children: "\u2461 \u6A21\u677F \u2192 \u4E3B\u7CFB\u7EDF \u94FE\u8DEF" }),
      /* @__PURE__ */ jsx(Chain, {})
    ] }),
    /* @__PURE__ */ jsxs("div", { children: [
      /* @__PURE__ */ jsx("div", { style: { ...sec("var(--dai-dark)"), marginTop: 8 }, children: "\u2462 \u6A21\u677F\u6D88\u8D39\uFF08\u4E66 \u2192 \u5F27 \u2192 \u6A21\u677F\uFF09" }),
      /* @__PURE__ */ jsx(Consumption, { items: o.consumption })
    ] }),
    /* @__PURE__ */ jsxs("div", { children: [
      /* @__PURE__ */ jsx("div", { style: { ...sec("var(--dai-dark)"), marginTop: 8 }, children: "\u2463 \u6700\u8FD1\u547D\u4E2D\u8BB0\u5F55\uFF08workbench \u65E5\u5FD7\uFF09" }),
      /* @__PURE__ */ jsx(Matches, { items: o.recent_matches })
    ] })
  ] });
}

// src/pages/PromptHarnessPage.jsx
import { useCallback as useCallback3, useEffect as useEffect4, useRef as useRef2, useState as useState5 } from "react";

// src/api.js
var BASE = "http://127.0.0.1:8765";
async function fetchWithRetry(url, retries = 5) {
  let lastErr;
  for (let i = 0; i <= retries; i++) {
    try {
      const response = await fetch(url.toString());
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`);
      }
      return response;
    } catch (err) {
      lastErr = err;
      if (!(err instanceof TypeError)) throw err;
      if (i < retries) {
        const delay = Math.min(1e3 * Math.pow(2, i), 8e3);
        await new Promise((r) => setTimeout(r, delay));
      }
    }
  }
  throw lastErr;
}
async function fetchJSON(path, params = {}) {
  const url = new URL(`${BASE}${path}`, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value !== void 0 && value !== null && value !== "") {
      url.searchParams.set(key, value);
    }
  }
  const response = await fetchWithRetry(url);
  return response.json();
}
async function postJSON(path, body = {}) {
  const response = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      if (data?.detail) {
        if (Array.isArray(data.detail)) {
          detail = data.detail.map(
            (d) => `${d.loc?.join(".") || "?"}: ${d.msg}`
          ).join("; ");
        } else if (typeof data.detail === "string") {
          detail = data.detail;
        } else {
          detail = JSON.stringify(data.detail);
        }
      }
    } catch {
    }
    throw new Error(detail);
  }
  return response.json();
}
function fetchApiLibrary() {
  return fetchJSON("/api/api-library");
}
function applyApiPreset(id) {
  return postJSON("/api/api-library/apply", { id });
}
function phRequest(path, options = {}) {
  return fetch(`${BASE}/api/prompt-harness${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options
  }).then((r) => {
    if (!r.ok) return r.text().then((t) => {
      throw new Error(`${r.status}: ${t}`);
    });
    return r.json();
  });
}
function phGetCorpusFiles(mode = "flat") {
  return phRequest(`/corpus/files?mode=${mode}`);
}
function phListLogs(category = "all", limit = 50, sourceFilter = "") {
  const params = new URLSearchParams();
  if (category && category !== "all") params.set("category", category);
  if (limit) params.set("limit", limit);
  if (sourceFilter) params.set("source_filter", sourceFilter);
  const qs = params.toString();
  return phRequest(`/logs${qs ? "?" + qs : ""}`);
}
function phGetLog(filepath, eventType = "", limit = 0) {
  const params = new URLSearchParams();
  if (eventType) params.set("event_type", eventType);
  if (limit) params.set("limit", limit);
  const qs = params.toString();
  return phRequest(`/logs/${filepath}${qs ? "?" + qs : ""}`);
}
function phListLlmLogs(date = "", limit = 100, statusFilter = "", callType = "") {
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  if (limit) params.set("limit", limit);
  if (statusFilter) params.set("status_filter", statusFilter);
  if (callType) params.set("call_type", callType);
  return phRequest(`/logs/llm?${params.toString()}`);
}
function phListLlmLogDates() {
  return phRequest("/logs/llm/dates");
}
function phListPlotTemplates() {
  return phRequest("/plot-templates");
}
function phDeletePlotTemplate(id) {
  return phRequest(`/plot-templates/${encodeURIComponent(id)}`, { method: "DELETE" });
}
function phExtractPlotTemplates({ filepath, start_chapter = 1, end_chapter = null, style = "", role_setting = "", budget = 80, min_score = 0.65 }) {
  const body = { filepath, start_chapter, style, role_setting, budget, min_score };
  if (end_chapter) body.end_chapter = end_chapter;
  return phRequest("/plot-templates/extract", {
    method: "POST",
    body: JSON.stringify(body)
  });
}
function phStoreExtractTemplate(task_id, arc) {
  return phRequest("/plot-templates/extract-store", {
    method: "POST",
    body: JSON.stringify({ task_id, arc })
  });
}
function phConnectionsOverview() {
  return phRequest("/connections/overview");
}
function phTaskStatus(taskId) {
  return phRequest(`/optimize/status/${encodeURIComponent(taskId)}`);
}

// src/pages/PromptLogsPage.jsx
import { useEffect, useState as useState2 } from "react";
import { Fragment, jsx as jsx2, jsxs as jsxs2 } from "react/jsx-runtime";
var EVENT_CATEGORIES = [
  { key: "tasks", label: "\u{1F9EA} \u4EFB\u52A1\u65E5\u5FD7" },
  { key: "llm", label: "\u{1F916} LLM \u8C03\u7528" }
];
function formatTime(isoStr) {
  if (!isoStr) return "";
  try {
    const d = new Date(isoStr);
    return d.toLocaleString("zh-CN", { hour12: false });
  } catch {
    return isoStr;
  }
}
function formatBytes(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}
function PromptLogsPage() {
  const [activeCategory, setActiveCategory] = useState2("tasks");
  const [sourceFilter, setSourceFilter] = useState2("");
  return /* @__PURE__ */ jsxs2("div", { className: "prompt-logs-page", children: [
    /* @__PURE__ */ jsxs2("div", { className: "plp-header", children: [
      /* @__PURE__ */ jsx2("h2", { children: "\u{1F4CB} \u65E5\u5FD7\u4E2D\u5FC3" }),
      /* @__PURE__ */ jsx2("p", { className: "plp-subtitle", children: "\u69A8\u5E72\u63D0\u53D6 / \u60C5\u8282\u6A21\u677F\u5E93 / LLM \u8C03\u7528 \u5168\u91CF\u65E5\u5FD7 \u2014\u2014 \u6BCF\u6B21\u4EFB\u52A1\u3001\u6BCF\u6B21 LLM \u8C03\u7528\u90FD\u53EF\u8FFD\u6EAF" })
    ] }),
    /* @__PURE__ */ jsx2("div", { className: "plp-categories", children: EVENT_CATEGORIES.map((c) => /* @__PURE__ */ jsx2(
      "button",
      {
        className: `plp-cat-btn ${activeCategory === c.key ? "active" : ""}`,
        onClick: () => setActiveCategory(c.key),
        children: c.label
      },
      c.key
    )) }),
    activeCategory !== "llm" && /* @__PURE__ */ jsx2("div", { className: "plp-filter-bar", children: /* @__PURE__ */ jsx2(
      "input",
      {
        type: "text",
        placeholder: "\u{1F50D} \u6309\u6765\u6E90\u6587\u6863\u540D\u8FC7\u6EE4...",
        value: sourceFilter,
        onChange: (e) => setSourceFilter(e.target.value),
        className: "plp-filter-input"
      }
    ) }),
    /* @__PURE__ */ jsxs2("div", { className: "plp-content", children: [
      activeCategory === "tasks" && /* @__PURE__ */ jsx2(GenericLogsView, { category: "tasks", sourceFilter }),
      activeCategory === "llm" && /* @__PURE__ */ jsx2(LlmLogsView, {})
    ] })
  ] });
}
function GenericLogsView({ category, sourceFilter }) {
  const [logs, setLogs] = useState2([]);
  const [loading, setLoading] = useState2(true);
  const [selectedPath, setSelectedPath] = useState2(null);
  const [events, setEvents] = useState2([]);
  const [detailLoading, setDetailLoading] = useState2(false);
  const load = () => {
    setLoading(true);
    phListLogs(category, 50, sourceFilter).then((r) => {
      setLogs(r || []);
    }).catch(() => {
      setLogs([]);
    }).finally(() => setLoading(false));
  };
  useEffect(() => {
    load();
  }, [category, sourceFilter]);
  const selectLog = (filepath) => {
    setSelectedPath(filepath);
    setDetailLoading(true);
    setEvents([]);
    phGetLog(filepath, "", 500).then((r) => {
      setEvents(r || []);
    }).catch(() => {
    }).finally(() => setDetailLoading(false));
  };
  return /* @__PURE__ */ jsxs2("div", { className: "plp-two-col", children: [
    /* @__PURE__ */ jsxs2("div", { className: "plp-list-panel", children: [
      /* @__PURE__ */ jsxs2("div", { className: "plp-list-header", children: [
        /* @__PURE__ */ jsxs2("span", { children: [
          "\u65E5\u5FD7\u6587\u4EF6 (",
          logs.length,
          ")"
        ] }),
        /* @__PURE__ */ jsx2("button", { className: "plp-refresh-btn", onClick: load, children: "\u{1F504} \u5237\u65B0" })
      ] }),
      loading ? /* @__PURE__ */ jsx2("div", { className: "plp-empty", children: "\u52A0\u8F7D\u4E2D..." }) : logs.length === 0 ? /* @__PURE__ */ jsx2("div", { className: "plp-empty", children: "\u6682\u65E0\u65E5\u5FD7" }) : /* @__PURE__ */ jsx2("div", { className: "plp-list", children: logs.map((l) => /* @__PURE__ */ jsxs2(
        "div",
        {
          className: `plp-list-item ${selectedPath === l.filepath ? "selected" : ""}`,
          onClick: () => selectLog(l.filepath),
          children: [
            /* @__PURE__ */ jsxs2("div", { className: "plp-item-title", children: [
              /* @__PURE__ */ jsx2("span", { className: `plp-status-badge status-${l.status || "unknown"}`, children: l.status || "unknown" }),
              /* @__PURE__ */ jsx2("span", { className: "plp-item-name", children: l.filename })
            ] }),
            /* @__PURE__ */ jsxs2("div", { className: "plp-item-meta", children: [
              /* @__PURE__ */ jsxs2("span", { children: [
                "\u{1F4C1} ",
                l.category
              ] }),
              /* @__PURE__ */ jsxs2("span", { children: [
                "\u{1F4E6} ",
                formatBytes(l.size)
              ] }),
              /* @__PURE__ */ jsxs2("span", { children: [
                "\u23F1 ",
                formatTime(l.modified_at)
              ] })
            ] })
          ]
        },
        l.filepath
      )) })
    ] }),
    /* @__PURE__ */ jsx2("div", { className: "plp-detail-panel", children: !selectedPath ? /* @__PURE__ */ jsx2("div", { className: "plp-empty", children: "\u2190 \u9009\u62E9\u4E00\u4E2A\u65E5\u5FD7\u6587\u4EF6\u67E5\u770B" }) : detailLoading ? /* @__PURE__ */ jsx2("div", { className: "plp-empty", children: "\u52A0\u8F7D\u4E2D..." }) : /* @__PURE__ */ jsxs2("div", { className: "plp-raw-view", children: [
      /* @__PURE__ */ jsxs2("div", { className: "plp-raw-header", children: [
        "\u5171 ",
        events.length,
        " \u6761\u4E8B\u4EF6"
      ] }),
      /* @__PURE__ */ jsx2("pre", { className: "plp-json-preview", children: JSON.stringify(events, null, 2) })
    ] }) })
  ] });
}
function LlmLogsView() {
  const [dates, setDates] = useState2([]);
  const [selectedDate, setSelectedDate] = useState2("");
  const [data, setData] = useState2(null);
  const [loading, setLoading] = useState2(false);
  const [statusFilter, setStatusFilter] = useState2("");
  const [callType, setCallType] = useState2("");
  useEffect(() => {
    phListLlmLogDates().then((r) => {
      const list = r || [];
      setDates(list);
      if (list.length > 0 && !selectedDate) {
        setSelectedDate(list[0]);
      }
    }).catch(() => {
    });
  }, []);
  const load = () => {
    if (!selectedDate) return;
    setLoading(true);
    phListLlmLogs(selectedDate, 200, statusFilter, callType).then((r) => {
      setData(r);
    }).catch(() => {
      setData(null);
    }).finally(() => setLoading(false));
  };
  useEffect(() => {
    if (selectedDate) load();
  }, [selectedDate, statusFilter, callType]);
  const stats = data?.stats || {};
  const events = data?.events || [];
  return /* @__PURE__ */ jsxs2("div", { className: "plp-llm-view", children: [
    /* @__PURE__ */ jsxs2("div", { className: "plp-llm-controls", children: [
      /* @__PURE__ */ jsxs2("div", { className: "plp-control-group", children: [
        /* @__PURE__ */ jsx2("label", { children: "\u65E5\u671F" }),
        /* @__PURE__ */ jsxs2(
          "select",
          {
            value: selectedDate,
            onChange: (e) => setSelectedDate(e.target.value),
            className: "plp-filter-select",
            children: [
              dates.map((d) => /* @__PURE__ */ jsx2("option", { value: d, children: d }, d)),
              dates.length === 0 && /* @__PURE__ */ jsx2("option", { value: "", children: "\u6682\u65E0\u6570\u636E" })
            ]
          }
        )
      ] }),
      /* @__PURE__ */ jsxs2("div", { className: "plp-control-group", children: [
        /* @__PURE__ */ jsx2("label", { children: "\u72B6\u6001" }),
        /* @__PURE__ */ jsxs2(
          "select",
          {
            value: statusFilter,
            onChange: (e) => setStatusFilter(e.target.value),
            className: "plp-filter-select",
            children: [
              /* @__PURE__ */ jsx2("option", { value: "", children: "\u5168\u90E8" }),
              /* @__PURE__ */ jsx2("option", { value: "success", children: "\u6210\u529F" }),
              /* @__PURE__ */ jsx2("option", { value: "error", children: "\u5931\u8D25" })
            ]
          }
        )
      ] }),
      /* @__PURE__ */ jsxs2("div", { className: "plp-control-group", children: [
        /* @__PURE__ */ jsx2("label", { children: "\u7C7B\u578B" }),
        /* @__PURE__ */ jsxs2(
          "select",
          {
            value: callType,
            onChange: (e) => setCallType(e.target.value),
            className: "plp-filter-select",
            children: [
              /* @__PURE__ */ jsx2("option", { value: "", children: "\u5168\u90E8" }),
              stats.by_call_type && Object.keys(stats.by_call_type).map((ct) => /* @__PURE__ */ jsx2("option", { value: ct, children: ct }, ct))
            ]
          }
        )
      ] }),
      /* @__PURE__ */ jsx2("button", { className: "plp-refresh-btn", onClick: load, children: "\u{1F504} \u5237\u65B0" })
    ] }),
    data && /* @__PURE__ */ jsxs2("div", { className: "plp-summary-cards", children: [
      /* @__PURE__ */ jsxs2("div", { className: "plp-stat-card", children: [
        /* @__PURE__ */ jsx2("div", { className: "plp-stat-label", children: "\u603B\u8C03\u7528\u6570" }),
        /* @__PURE__ */ jsx2("div", { className: "plp-stat-value", children: stats.total_calls?.toLocaleString() || 0 })
      ] }),
      /* @__PURE__ */ jsxs2("div", { className: "plp-stat-card", children: [
        /* @__PURE__ */ jsx2("div", { className: "plp-stat-label", children: "Token \u603B\u91CF" }),
        /* @__PURE__ */ jsx2("div", { className: "plp-stat-value", children: stats.total_tokens?.toLocaleString() || 0 })
      ] }),
      /* @__PURE__ */ jsxs2("div", { className: "plp-stat-card", children: [
        /* @__PURE__ */ jsx2("div", { className: "plp-stat-label", children: "\u5E73\u5747\u5EF6\u8FDF" }),
        /* @__PURE__ */ jsxs2("div", { className: "plp-stat-value", children: [
          stats.avg_latency_ms || 0,
          " ms"
        ] })
      ] }),
      /* @__PURE__ */ jsxs2("div", { className: "plp-stat-card", children: [
        /* @__PURE__ */ jsx2("div", { className: "plp-stat-label", children: "\u9519\u8BEF\u7387" }),
        /* @__PURE__ */ jsxs2("div", { className: `plp-stat-value ${stats.error_rate > 0.01 ? "error" : ""}`, children: [
          ((stats.error_rate || 0) * 100).toFixed(2),
          "%"
        ] })
      ] }),
      /* @__PURE__ */ jsxs2("div", { className: "plp-stat-card", children: [
        /* @__PURE__ */ jsx2("div", { className: "plp-stat-label", children: "\u9519\u8BEF\u6570" }),
        /* @__PURE__ */ jsx2("div", { className: `plp-stat-value ${stats.error_count ? "error" : ""}`, children: stats.error_count || 0 })
      ] })
    ] }),
    stats.by_call_type && Object.keys(stats.by_call_type).length > 0 && /* @__PURE__ */ jsxs2("div", { className: "plp-llm-type-breakdown", children: [
      /* @__PURE__ */ jsx2("h4", { children: "\u{1F4CA} \u6309\u8C03\u7528\u7C7B\u578B\u5206\u5E03" }),
      /* @__PURE__ */ jsx2("div", { className: "plp-type-grid", children: Object.entries(stats.by_call_type).map(([type, info]) => /* @__PURE__ */ jsxs2("div", { className: "plp-type-card", children: [
        /* @__PURE__ */ jsx2("div", { className: "plp-type-name", children: type || "unknown" }),
        /* @__PURE__ */ jsxs2("div", { className: "plp-type-stats", children: [
          /* @__PURE__ */ jsxs2("span", { children: [
            "\u8C03\u7528 ",
            info.count
          ] }),
          /* @__PURE__ */ jsxs2("span", { children: [
            "Token ",
            (info.total_tokens || 0).toLocaleString()
          ] }),
          info.errors > 0 && /* @__PURE__ */ jsxs2("span", { className: "error", children: [
            "\u9519\u8BEF ",
            info.errors
          ] })
        ] })
      ] }, type)) })
    ] }),
    /* @__PURE__ */ jsxs2("div", { className: "plp-llm-list", children: [
      /* @__PURE__ */ jsxs2("h4", { children: [
        "\u{1F4CB} \u8C03\u7528\u8BB0\u5F55\uFF08",
        events.length,
        " / ",
        data?.total || 0,
        "\uFF09"
      ] }),
      loading ? /* @__PURE__ */ jsx2("div", { className: "plp-empty", children: "\u52A0\u8F7D\u4E2D..." }) : events.length === 0 ? /* @__PURE__ */ jsx2("div", { className: "plp-empty", children: "\u6682\u65E0 LLM \u8C03\u7528\u8BB0\u5F55" }) : /* @__PURE__ */ jsxs2("div", { className: "plp-llm-table", children: [
        /* @__PURE__ */ jsxs2("div", { className: "plp-llm-row header", children: [
          /* @__PURE__ */ jsx2("span", { children: "\u65F6\u95F4" }),
          /* @__PURE__ */ jsx2("span", { children: "\u6A21\u578B" }),
          /* @__PURE__ */ jsx2("span", { children: "\u7C7B\u578B" }),
          /* @__PURE__ */ jsx2("span", { children: "\u72B6\u6001" }),
          /* @__PURE__ */ jsx2("span", { children: "Token" }),
          /* @__PURE__ */ jsx2("span", { children: "\u5EF6\u8FDF" }),
          /* @__PURE__ */ jsx2("span", { children: "\u63D0\u793A\u8BCD" })
        ] }),
        events.map((evt, i) => /* @__PURE__ */ jsx2(LlmCallRow, { evt }, i))
      ] })
    ] })
  ] });
}
function LlmCallRow({ evt }) {
  const [expanded, setExpanded] = useState2(false);
  const isError = evt.status === "error";
  return /* @__PURE__ */ jsxs2(Fragment, { children: [
    /* @__PURE__ */ jsxs2(
      "div",
      {
        className: `plp-llm-row ${isError ? "error-row" : ""}`,
        onClick: () => setExpanded(!expanded),
        children: [
          /* @__PURE__ */ jsx2("span", { className: "plp-llm-time", children: formatTime(evt.ts).split(" ")[1] || "" }),
          /* @__PURE__ */ jsx2("span", { className: "plp-llm-model", children: evt.model || "-" }),
          /* @__PURE__ */ jsx2("span", { className: "plp-llm-type", children: evt.call_type || "-" }),
          /* @__PURE__ */ jsx2("span", { children: /* @__PURE__ */ jsx2("span", { className: `plp-status-badge status-${evt.status}`, children: evt.status }) }),
          /* @__PURE__ */ jsx2("span", { className: "plp-llm-tokens", children: evt.total_tokens?.toLocaleString() || 0 }),
          /* @__PURE__ */ jsxs2("span", { className: "plp-llm-latency", children: [
            evt.latency_ms,
            " ms"
          ] }),
          /* @__PURE__ */ jsx2("span", { className: "plp-llm-prompt-preview", children: evt.error ? `\u274C ${evt.error.substring(0, 50)}` : `\u{1F4DD} ${evt.prompt_len || 0} \u5B57 \u2192 ${evt.completion_len || 0} \u5B57` })
        ]
      }
    ),
    expanded && /* @__PURE__ */ jsx2("div", { className: "plp-llm-expanded", children: /* @__PURE__ */ jsx2("pre", { className: "plp-json-preview", children: JSON.stringify(evt, null, 2) }) })
  ] });
}

// src/components/PlotExtractHub.jsx
import { useCallback, useEffect as useEffect2, useRef, useState as useState3 } from "react";
import { jsx as jsx3, jsxs as jsxs3 } from "react/jsx-runtime";
var btn = (disabled, primary) => ({
  padding: "8px 16px",
  borderRadius: 6,
  border: "1px solid var(--line-soft)",
  background: primary ? "var(--green)" : "var(--paper-raised)",
  color: primary ? "var(--paper-raised)" : "var(--ink)",
  cursor: disabled ? "not-allowed" : "pointer",
  opacity: disabled ? 0.5 : 1,
  fontSize: 14,
  fontWeight: primary ? 600 : 400
});
var card = { border: "1px solid var(--line)", borderRadius: 8, padding: 12, background: "var(--paper-raised)", marginBottom: 10 };
var pre = { whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.7, margin: 0 };
var badge = (ok) => ({
  padding: "2px 8px",
  borderRadius: 10,
  fontSize: 12,
  background: ok ? "var(--green-wash)" : "var(--cinnabar-wash)",
  color: ok ? "var(--green)" : "var(--cinnabar-d)"
});
function PlotExtractHub() {
  const [files, setFiles] = useState3([]);
  const [filepath, setFilepath] = useState3("");
  const [startChapter, setStartChapter] = useState3(1);
  const [endChapter, setEndChapter] = useState3("");
  const [minScore, setMinScore] = useState3(0.65);
  const [taskId, setTaskId] = useState3(null);
  const [running, setRunning] = useState3(false);
  const [status, setStatus] = useState3(null);
  const [error, setError] = useState3("");
  const [storing, setStoring] = useState3({});
  const pollTimer = useRef(null);
  const loadFiles = useCallback(() => {
    phGetCorpusFiles("flat").then((r) => {
      const arr = r?.files || [];
      setFiles(arr);
      if (!filepath && arr.length) setFilepath(arr[0]?.path || arr[0]?.name || "");
    }).catch(() => setFiles([]));
  }, [filepath]);
  useEffect2(() => {
    loadFiles();
  }, []);
  useEffect2(() => {
    if (!taskId || !running) return;
    pollTimer.current = setInterval(async () => {
      try {
        const s = await phTaskStatus(taskId);
        setStatus(s);
        if (s?.status === "done" || s?.status === "failed") {
          setRunning(false);
          clearInterval(pollTimer.current);
        }
      } catch {
      }
    }, 4e3);
    return () => clearInterval(pollTimer.current);
  }, [taskId, running]);
  const handleExtract = async (overrides = {}) => {
    if (!filepath) {
      setError("\u8BF7\u9009\u62E9\u8981\u69A8\u5E72\u7684\u6587\u6863");
      return;
    }
    setError("");
    setRunning(true);
    setStatus(null);
    try {
      const r = await phExtractPlotTemplates({
        filepath,
        start_chapter: overrides.start_chapter ?? startChapter,
        end_chapter: overrides.end_chapter !== void 0 ? overrides.end_chapter : endChapter ? Number(endChapter) : null,
        min_score: minScore
      });
      setTaskId(r?.task_id);
    } catch (e) {
      setError(`\u542F\u52A8\u69A8\u5E72\u5931\u8D25\uFF1A${e.message || e}`);
      setRunning(false);
    }
  };
  const handleExtractAll = async () => {
    if (!filepath) {
      setError("\u8BF7\u9009\u62E9\u8981\u69A8\u5E72\u7684\u6587\u6863");
      return;
    }
    const ok = window.confirm("\u{1F4DA} \u5168\u6587\u6863\u4E00\u952E\u69A8\u5E72\uFF1A\u5C06\u4ECE\u7B2C 1 \u7AE0\u69A8\u5230\u6574\u672C\u672B\u5C3E\uFF08\u51E0\u767E\u7AE0\u53EF\u80FD\u8017\u65F6\u5341\u51E0\u5C0F\u65F6\uFF09\u3002\n\u786E\u8BA4\u7EE7\u7EED\uFF1F");
    if (!ok) return;
    setStartChapter(1);
    setEndChapter("");
    await handleExtract({ start_chapter: 1, end_chapter: null });
  };
  const handleStore = async (arc) => {
    if (!taskId) {
      setError("\u65E0\u69A8\u5E72\u4EFB\u52A1\uFF08\u5148\u70B9\u300C\u69A8\u5E72\u63D0\u53D6\u300D\uFF09");
      return;
    }
    setError("");
    setStoring((prev) => ({ ...prev, [arc]: true }));
    try {
      const r = await phStoreExtractTemplate(taskId, arc);
      setStatus((prev) => {
        if (!prev) return prev;
        const rep = (prev.result?.report || []).map((rr) => rr.arc === arc ? { ...rr, template_id: r?.template?.id, template_name: r?.template?.name } : rr);
        return { ...prev, result: { ...prev.result || {}, report: rep } };
      });
    } catch (e) {
      setError(`\u5165\u5E93\u5931\u8D25\uFF1A${e.message || e}`);
    } finally {
      setStoring((prev) => ({ ...prev, [arc]: false }));
    }
  };
  const leafLabels = [
    ["actions", "\u52A8\u4F5C"],
    ["dialogues", "\u5BF9\u767D"],
    ["narration", "\u539F\u6587\u53D9\u8FF0\u539F\u53E5"],
    ["psychologies", "\u5FC3\u7406"],
    ["conflicts", "\u51B2\u7A81"],
    ["details", "\u7EC6\u8282"]
  ];
  const renderScene = (sc) => {
    if (!sc) return null;
    return /* @__PURE__ */ jsxs3("div", { style: { marginTop: 4, padding: 6, background: "var(--bg-main)", borderRadius: 6 }, children: [
      /* @__PURE__ */ jsx3("b", { children: sc.name || "\u573A\u666F" }),
      sc.environment && /* @__PURE__ */ jsxs3("div", { style: { marginTop: 2 }, children: [
        "\u73AF\u5883\uFF1A",
        sc.environment
      ] }),
      leafLabels.map(([key, label]) => {
        const items = Array.isArray(sc[key]) ? sc[key].filter(Boolean) : [];
        if (!items.length) return null;
        return /* @__PURE__ */ jsxs3("div", { style: { marginTop: 2 }, children: [
          /* @__PURE__ */ jsxs3("b", { children: [
            label,
            "\uFF08",
            items.length,
            "\uFF09\uFF1A"
          ] }),
          items.map((it, j) => /* @__PURE__ */ jsxs3("div", { style: { marginLeft: 10 }, children: [
            "\xB7 ",
            it
          ] }, j))
        ] }, key);
      })
    ] });
  };
  const renderSkeleton = (sk) => {
    if (!sk) return null;
    const l3 = sk.l3 || {};
    return /* @__PURE__ */ jsxs3("div", { style: { fontSize: 13 }, children: [
      sk.l1 && /* @__PURE__ */ jsxs3("div", { children: [
        /* @__PURE__ */ jsx3("b", { children: "l1 \u6781\u7B80\uFF1A" }),
        sk.l1
      ] }),
      sk.l2 && /* @__PURE__ */ jsxs3("div", { style: { marginTop: 4 }, children: [
        /* @__PURE__ */ jsx3("b", { children: "l2 \u5F27\u7EBF\u6982\u8981\uFF1A" }),
        sk.l2
      ] }),
      (l3.title || l3.core) && /* @__PURE__ */ jsxs3("div", { style: { marginTop: 4 }, children: [
        /* @__PURE__ */ jsx3("b", { children: "l3 \u7AE0\u6838\u5FC3\uFF1A" }),
        l3.title && /* @__PURE__ */ jsxs3("span", { children: [
          l3.title,
          "\uFF5C"
        ] }),
        l3.core,
        Array.isArray(l3.beats) && l3.beats.length > 0 && /* @__PURE__ */ jsxs3("div", { children: [
          "\u62CD\uFF1A",
          l3.beats.join("\uFF1B")
        ] })
      ] }),
      Array.isArray(sk.l4) && sk.l4.length > 0 && /* @__PURE__ */ jsxs3("div", { style: { marginTop: 6 }, children: [
        /* @__PURE__ */ jsxs3("b", { children: [
          "l4 \u573A\u666F\u5206\u89E3\uFF08",
          sk.l4.length,
          "\uFF09\uFF1A"
        ] }),
        sk.l4.map((sc, i) => /* @__PURE__ */ jsxs3("div", { style: { marginTop: 4 }, children: [
          /* @__PURE__ */ jsxs3("b", { children: [
            i + 1,
            "."
          ] }),
          renderScene(sc)
        ] }, i))
      ] })
    ] });
  };
  const result = status?.result || {};
  const report = result.report || [];
  return /* @__PURE__ */ jsxs3("div", { children: [
    /* @__PURE__ */ jsxs3("div", { style: card, children: [
      /* @__PURE__ */ jsx3("div", { style: { fontSize: 15, fontWeight: 700, marginBottom: 8 }, children: "\u{1F9C3} \u5355\u6309\u94AE\u69A8\u5E72\uFF1A\u9009\u4E2D\u6587\u6863 \u2192 \u9010\u7AE0\u63D0\u53D6\u538B\u7F29\u9636\u68AF \u2192 \u91CD\u5EFA\u6821\u9A8C \u2192 \u5408\u683C\u53BB\u5B9E\u4F53\u5316\u5165\u5E93" }),
      /* @__PURE__ */ jsxs3("div", { style: { display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }, children: [
        /* @__PURE__ */ jsxs3(
          "select",
          {
            value: filepath,
            onChange: (e) => setFilepath(e.target.value),
            style: { padding: 8, borderRadius: 6, border: "1px solid var(--line-soft)", fontSize: 13, flex: 1, minWidth: 240 },
            children: [
              /* @__PURE__ */ jsx3("option", { value: "", children: "\u9009\u62E9\u8BED\u6599\u6587\u6863\u2026" }),
              files.map((f) => /* @__PURE__ */ jsx3("option", { value: f.path || f.name, children: f.name || f.path }, f.path || f.name))
            ]
          }
        ),
        /* @__PURE__ */ jsx3(
          "input",
          {
            type: "number",
            value: startChapter,
            min: 1,
            onChange: (e) => setStartChapter(Number(e.target.value)),
            placeholder: "\u8D77\u59CB\u7AE0",
            style: { width: 70, padding: 8, borderRadius: 6, border: "1px solid var(--line-soft)", fontSize: 13 }
          }
        ),
        /* @__PURE__ */ jsx3(
          "input",
          {
            type: "number",
            value: endChapter,
            min: 1,
            onChange: (e) => setEndChapter(e.target.value),
            placeholder: "\u7ED3\u675F\u7AE0(\u7A7A=\u672B\u5C3E)",
            style: { width: 120, padding: 8, borderRadius: 6, border: "1px solid var(--line-soft)", fontSize: 13 }
          }
        ),
        /* @__PURE__ */ jsx3(
          "input",
          {
            type: "number",
            step: 0.05,
            value: minScore,
            onChange: (e) => setMinScore(Number(e.target.value)),
            placeholder: "\u5408\u683C\u5206",
            style: { width: 80, padding: 8, borderRadius: 6, border: "1px solid var(--line-soft)", fontSize: 13 }
          }
        )
      ] }),
      /* @__PURE__ */ jsx3("button", { style: btn(running || !filepath, true), disabled: running || !filepath, onClick: () => handleExtract(), children: running ? "\u23F3 \u69A8\u5E72\u4E2D\u2026" : "\u{1F9C3} \u69A8\u5E72\u63D0\u53D6\uFF08\u9010\u7AE0 \u2192 \u9AA8\u67B6+\u6B63\u6587 \u2192 \u5408\u683C\u5165\u5E93\uFF09" }),
      /* @__PURE__ */ jsx3(
        "button",
        {
          style: { ...btn(running || !filepath, true), background: "var(--cinnabar)", marginLeft: 8 },
          disabled: running || !filepath,
          onClick: handleExtractAll,
          title: "\u6574\u672C txt \u4ECE\u7B2C 1 \u7AE0\u69A8\u5230\u672B\u5C3E\uFF08\u6309\u5267\u60C5\u5F27\u5206\u7EC4\uFF0C\u5F27\u5185\u5168\u7AE0\u8FBE\u6807\u53EF\u6574\u5F27\u5165\u5E93\uFF09",
          children: running ? "\u23F3 \u69A8\u5E72\u4E2D\u2026" : "\u{1F4DA} \u5168\u6587\u6863\u4E00\u952E\u69A8\u5E72"
        }
      ),
      /* @__PURE__ */ jsx3("span", { style: { marginLeft: 8, fontSize: 12, color: "var(--ink-sub)" }, children: "\u6BCF\u7AE0\u7EA6 2-4 \u5206\u949F\uFF08build_ladder \u63D0\u53D6 + verify_ladder \u91CD\u5EFA\u6821\u9A8C\uFF09" }),
      error && /* @__PURE__ */ jsxs3("div", { style: { marginTop: 8, padding: 8, borderRadius: 6, background: "var(--cinnabar-wash)", color: "var(--cinnabar-d)", fontSize: 13 }, children: [
        "\u26A0 ",
        error
      ] })
    ] }),
    running && status?.status === "running" && /* @__PURE__ */ jsxs3("div", { style: card, children: [
      /* @__PURE__ */ jsx3("div", { style: { fontSize: 14, fontWeight: 600, marginBottom: 4 }, children: "\u23F3 \u69A8\u5E72\u8FDB\u884C\u4E2D\u2026" }),
      /* @__PURE__ */ jsxs3("div", { style: { fontSize: 13, color: "var(--ink-sub)" }, children: [
        "\u8FDB\u5EA6\uFF1A\u7B2C ",
        status?.progress?.chapter || 0,
        "/",
        status?.progress?.total || "?",
        " \u7AE0",
        status?.progress?.phase ? `\uFF08${status?.progress?.phase}\uFF09` : ""
      ] }),
      (status?.progress?.messages || []).filter(Boolean).slice(-3).map((m, i) => /* @__PURE__ */ jsx3("div", { style: { fontSize: 12, color: "var(--ink-sub)" }, children: m }, i))
    ] }),
    status?.status === "failed" && /* @__PURE__ */ jsxs3("div", { style: { ...card, borderColor: "var(--cinnabar-border)", background: "var(--cinnabar-wash)" }, children: [
      /* @__PURE__ */ jsx3("b", { style: { color: "var(--cinnabar-d)" }, children: "\u69A8\u5E72\u5931\u8D25\uFF1A" }),
      /* @__PURE__ */ jsx3("pre", { style: { ...pre, color: "var(--cinnabar-d)", fontSize: 12 }, children: status.error })
    ] }),
    status?.status === "done" && /* @__PURE__ */ jsxs3("div", { style: { ...card, borderColor: "var(--green)", background: "var(--green-wash)" }, children: [
      /* @__PURE__ */ jsxs3("b", { style: { color: "var(--green)" }, children: [
        "\u69A8\u5E72\u5B8C\u6210\uFF1A",
        result.qualified ?? 0,
        "/",
        result.arc_count ?? report.length,
        " \u4E2A\u5267\u60C5\u5F27\u8FBE\u6807 \uFF08\u5F27\u5185\u5168\u90E8\u7AE0 \u2265 \u5408\u683C\u5206\u624D\u53EF\u6574\u5F27\u5165\u5E93\uFF09\uFF0C",
        result.skipped?.length ?? 0,
        " \u7AE0\u8DF3\u8FC7"
      ] }),
      (result.style || result.role_setting) && /* @__PURE__ */ jsxs3("div", { style: { marginTop: 6, fontSize: 12, color: "var(--ink-sub)" }, children: [
        "\u81EA\u52A8\u63D0\u53D6\uFF1A\u98CE\u683C\u300C",
        result.style || "\u2014",
        "\u300D\uFF5C\u89D2\u8272\u300C",
        result.role_setting || "\u2014",
        "\u300D"
      ] })
    ] }),
    report.length > 0 && /* @__PURE__ */ jsxs3("div", { children: [
      /* @__PURE__ */ jsx3("div", { style: { fontSize: 15, fontWeight: 700, margin: "10px 0 6px" }, children: "\u{1F4DC} \u69A8\u5E72\u4EA7\u7269\uFF1A\u5267\u60C5\u5F27\u5206\u7EC4\uFF08\u6BCF\u5F27\u4E00\u4E2A\u6A21\u677F\uFF0C\u70B9\u300C\u{1F4BE} \u6574\u5F27\u5165\u5E93\u300D\u542B\u5F27\u5185\u5168\u90E8\u7AE0\uFF09" }),
      report.map((arc, i) => /* @__PURE__ */ jsxs3("div", { style: { ...card, borderLeft: arc.qualified || arc.template_id ? "3px solid var(--green)" : "3px solid var(--line-soft)" }, children: [
        /* @__PURE__ */ jsxs3("div", { style: { display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }, children: [
          /* @__PURE__ */ jsxs3("b", { style: { fontSize: 14 }, children: [
            "\u5267\u60C5\u5F27 ",
            arc.arc,
            "\uFF1A",
            arc.name
          ] }),
          arc.archetype && /* @__PURE__ */ jsxs3("span", { style: { fontSize: 12, color: "var(--cinnabar)" }, children: [
            "\u539F\u578B\uFF1A",
            arc.archetype
          ] }),
          /* @__PURE__ */ jsxs3("span", { style: { fontSize: 12, color: "var(--ink-sub)" }, children: [
            "\u7B2C ",
            arc.start_chapter,
            "-",
            arc.end_chapter,
            " \u7AE0"
          ] }),
          /* @__PURE__ */ jsx3("span", { style: badge(arc.qualified || arc.template_id), children: arc.template_id ? `\u5DF2\u5165\u5E93\uFF08${arc.template_name || arc.name}\uFF09` : arc.qualified ? "\u2705 \u8FBE\u6807\uFF08\u5F85\u5165\u5E93\uFF09" : "\u672A\u5408\u683C" }),
          arc.qualified && !arc.template_id && /* @__PURE__ */ jsx3(
            "button",
            {
              style: { ...btn(storing[arc.arc], true) },
              disabled: storing[arc.arc],
              onClick: () => handleStore(arc.arc),
              children: storing[arc.arc] ? "\u5165\u5E93\u4E2D\u2026" : "\u{1F4BE} \u6574\u5F27\u5165\u5E93"
            }
          )
        ] }),
        (arc.l1 || arc.l2) && /* @__PURE__ */ jsxs3("div", { style: { fontSize: 13, background: "var(--bg-card-2)", padding: 8, borderRadius: 6, marginBottom: 6 }, children: [
          arc.l1 && /* @__PURE__ */ jsxs3("div", { children: [
            /* @__PURE__ */ jsx3("b", { children: "\u5F27 l1 \u6781\u7B80\uFF1A" }),
            arc.l1
          ] }),
          arc.l2 && /* @__PURE__ */ jsxs3("div", { style: { marginTop: 2 }, children: [
            /* @__PURE__ */ jsx3("b", { children: "\u5F27 l2 \u6982\u8981\uFF1A" }),
            arc.l2
          ] })
        ] }),
        arc.chapters?.map((ch, j) => /* @__PURE__ */ jsxs3("div", { style: { marginTop: 6, paddingTop: 6, borderTop: "1px dashed var(--line)" }, children: [
          /* @__PURE__ */ jsxs3("div", { style: { display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }, children: [
            /* @__PURE__ */ jsxs3("b", { style: { fontSize: 13 }, children: [
              "\u7B2C ",
              ch.chapter_num ?? ch.chapter,
              " \u7AE0"
            ] }),
            /* @__PURE__ */ jsx3("span", { style: badge(ch.qualified), children: ch.qualified ? "\u2705 \u8FBE\u6807" : "\u672A\u5408\u683C" }),
            /* @__PURE__ */ jsxs3("span", { style: { fontSize: 12, color: "var(--ink-sub)" }, children: [
              "score ",
              ch.score?.toFixed?.(3) ?? ch.score,
              "\uFF5C\u6B63\u6587 ",
              ch.prose?.length ?? 0,
              " \u5B57"
            ] })
          ] }),
          /* @__PURE__ */ jsxs3("details", { children: [
            /* @__PURE__ */ jsx3("summary", { style: { fontSize: 13, color: "var(--dai)", cursor: "pointer", marginTop: 2 }, children: "\u538B\u7F29\u9636\u68AF\uFF08\u9AA8\u67B6\uFF09" }),
            /* @__PURE__ */ jsx3("div", { style: { background: "var(--bg-card-2)", padding: 8, borderRadius: 6, marginTop: 4 }, children: renderSkeleton(ch.skeleton) })
          ] }),
          /* @__PURE__ */ jsxs3("details", { children: [
            /* @__PURE__ */ jsxs3("summary", { style: { fontSize: 13, color: "var(--green)", cursor: "pointer", marginTop: 4 }, children: [
              "\u91CD\u5EFA\u6B63\u6587\uFF08",
              ch.prose?.length ?? 0,
              " \u5B57\uFF09"
            ] }),
            /* @__PURE__ */ jsx3("pre", { style: { ...pre, background: "var(--bg-card-2)", padding: 8, borderRadius: 6, marginTop: 4 }, children: ch.prose })
          ] })
        ] }, j))
      ] }, i))
    ] })
  ] });
}

// src/pages/TemplateLibraryPage.jsx
import { useCallback as useCallback2, useEffect as useEffect3, useState as useState4 } from "react";

// src/plotFields.js
var TEMPLATE_FIELDS = [
  { key: "protagonist", label: "\u4E3B\u89D2" },
  { key: "cast", label: "\u51FA\u573A\u4EBA\u7269" },
  { key: "time_setting", label: "\u65F6\u95F4\u80CC\u666F" },
  { key: "location", label: "\u5173\u952E\u5730\u70B9" },
  { key: "conflict", label: "\u6838\u5FC3\u51B2\u7A81" },
  { key: "goal", label: "\u76EE\u6807" },
  { key: "twist", label: "\u8F6C\u6298" },
  { key: "ending", label: "\u7ED3\u5C40/\u60AC\u5FF5" },
  { key: "key_item", label: "\u5173\u952E\u9053\u5177" }
];
function templateFields(template) {
  const f = template && template.fields || {};
  return TEMPLATE_FIELDS.map((x) => ({ ...x, hint: f[x.key] || "" }));
}

// src/pages/TemplateLibraryPage.jsx
import { jsx as jsx4, jsxs as jsxs4 } from "react/jsx-runtime";
var card2 = { border: "1px solid var(--line)", borderRadius: 8, padding: 12, background: "var(--paper-raised)", marginBottom: 10 };
var btn2 = (primary) => ({
  padding: "8px 16px",
  borderRadius: 6,
  border: "1px solid var(--line-soft)",
  background: primary ? "var(--green)" : "var(--paper-raised)",
  color: primary ? "var(--paper-raised)" : "var(--ink)",
  cursor: "pointer",
  fontSize: 14,
  fontWeight: primary ? 600 : 400
});
function TemplateLibraryPage() {
  const [templates, setTemplates] = useState4([]);
  const [loading, setLoading] = useState4(false);
  const [error, setError] = useState4("");
  const loadTemplates = useCallback2(async () => {
    setLoading(true);
    setError("");
    try {
      const r = await phListPlotTemplates();
      setTemplates(Array.isArray(r) ? r : []);
    } catch (e) {
      setError(String(e.message || e));
    }
    setLoading(false);
  }, []);
  useEffect3(() => {
    loadTemplates();
  }, [loadTemplates]);
  const handleDelete = async (id) => {
    try {
      await phDeletePlotTemplate(id);
      loadTemplates();
    } catch (e) {
      setError(`\u5220\u9664\u5931\u8D25\uFF1A${e.message || e}`);
    }
  };
  return /* @__PURE__ */ jsxs4("div", { children: [
    /* @__PURE__ */ jsxs4("div", { style: { display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }, children: [
      /* @__PURE__ */ jsx4("div", { style: { fontSize: 15, fontWeight: 700 }, children: "\u{1F4E6} \u60C5\u8282\u6A21\u677F\u5E93\uFF08\u552F\u4E00\u80FD\u4E0E\u4E3B\u7CFB\u7EDF\u8FDE\u63A5\u7684\u5185\u5BB9\uFF09" }),
      /* @__PURE__ */ jsxs4("span", { style: { fontSize: 12, color: "var(--ink-sub)" }, children: [
        templates.length,
        " \u6761"
      ] }),
      /* @__PURE__ */ jsx4("button", { style: { marginLeft: "auto", ...btn2(false) }, onClick: loadTemplates, disabled: loading, children: loading ? "\u5237\u65B0\u4E2D\u2026" : "\u21BB \u5237\u65B0" })
    ] }),
    error && /* @__PURE__ */ jsxs4("div", { style: { marginBottom: 8, padding: 8, borderRadius: 6, background: "var(--cinnabar-wash)", color: "var(--cinnabar-d)", fontSize: 13 }, children: [
      "\u26A0 ",
      error
    ] }),
    templates.length === 0 && !loading && /* @__PURE__ */ jsx4("div", { style: { color: "var(--ink-mute)", fontSize: 13, padding: 8 }, children: "\uFF08\u5E93\u4E3A\u7A7A\uFF1A\u5148\u5230\u300C\u{1F9C3} \u69A8\u5E72\u63D0\u53D6\u300D\u4ECE\u8BED\u6599\u4E2D\u69A8\u53D6\u6A21\u677F\uFF0C\u8FBE\u6807\u5267\u60C5\u5F27 \u{1F4BE} \u6574\u5F27\u5165\u5E93\u540E\u51FA\u73B0\u5728\u8FD9\u91CC\uFF09" }),
    templates.map((t) => /* @__PURE__ */ jsxs4("div", { style: card2, children: [
      /* @__PURE__ */ jsxs4("div", { style: { display: "flex", alignItems: "center", gap: 8 }, children: [
        /* @__PURE__ */ jsx4("b", { style: { fontSize: 14 }, children: t.name }),
        /* @__PURE__ */ jsxs4("span", { style: { fontSize: 12, color: "var(--ink-sub)" }, children: [
          "score ",
          t.qualified?.score?.toFixed?.(3),
          "\uFF5Cs_char ",
          t.qualified?.s_char?.toFixed?.(3)
        ] }),
        /* @__PURE__ */ jsx4("button", { style: { marginLeft: "auto", ...btn2(false) }, onClick: () => handleDelete(t.id), children: "\u5220\u9664" })
      ] }),
      /* @__PURE__ */ jsx4("div", { style: { fontSize: 13, color: "var(--ink-sub)", marginTop: 2 }, children: t.description }),
      /* @__PURE__ */ jsxs4("div", { style: { fontSize: 12, color: "var(--dai)", marginTop: 2 }, children: [
        "\u6765\u6E90\uFF1A",
        t.source?.corpus ? `${t.source.corpus} \xB7 \u7B2C ${t.source.chapter_start && t.source.chapter_end ? `${t.source.chapter_start}-${t.source.chapter_end}` : t.source.chapter ?? "?"} \u7AE0` : "\uFF08\u672A\u8BB0\u5F55\u6765\u6E90\u4E66\uFF09",
        t.archetype ? ` \uFF5C \u539F\u578B\uFF1A${t.archetype}` : ""
      ] }),
      /* @__PURE__ */ jsxs4("details", { style: { marginTop: 4 }, children: [
        /* @__PURE__ */ jsx4("summary", { style: { fontSize: 13, color: "var(--cinnabar)", cursor: "pointer" }, children: "\u4E13\u4E1A\u5B57\u6BB5\u8868\u5355\uFF089 \u5B57\u6BB5 \xB7 \u4E3B\u7CFB\u7EDF\u586B\u8FD9\u4E2A\uFF09" }),
        /* @__PURE__ */ jsx4("div", { style: { background: "var(--cinnabar-wash)", padding: 8, borderRadius: 6, marginTop: 4 }, children: templateFields(t).map((f) => /* @__PURE__ */ jsxs4("div", { style: { fontSize: 13, marginBottom: 3 }, children: [
          /* @__PURE__ */ jsxs4("b", { children: [
            f.label,
            "\uFF1A"
          ] }),
          f.hint || "\uFF08\u65E0\uFF09"
        ] }, f.key)) })
      ] }),
      /* @__PURE__ */ jsxs4("details", { style: { marginTop: 4 }, children: [
        /* @__PURE__ */ jsx4("summary", { style: { fontSize: 13, color: "var(--dai)", cursor: "pointer" }, children: "\u69FD\u4F4D\u5316\u6A21\u677F\uFF084 \u7EA7\uFF09" }),
        /* @__PURE__ */ jsx4("div", { style: { background: "var(--bg-card-2)", padding: 8, borderRadius: 6, marginTop: 4 }, children: ["l1", "l2", "l3", "l4"].map((k) => /* @__PURE__ */ jsxs4("div", { style: { fontSize: 13, marginBottom: 4 }, children: [
          /* @__PURE__ */ jsxs4("b", { children: [
            k,
            ":"
          ] }),
          " ",
          t.levels_text?.[k] || ""
        ] }, k)) })
      ] }),
      /* @__PURE__ */ jsxs4("div", { style: { fontSize: 12, color: "var(--ink-sub)", marginTop: 4 }, children: [
        "\u7B56\u7565\uFF1A",
        JSON.stringify(t.strategy || {})
      ] })
    ] }, t.id)),
    loading && /* @__PURE__ */ jsx4("div", { style: { color: "var(--ink-mute)", fontSize: 13, padding: 8 }, children: "\u52A0\u8F7D\u4E2D\u2026" })
  ] });
}

// src/pages/PromptHarnessPage.jsx
import { jsx as jsx5, jsxs as jsxs5 } from "react/jsx-runtime";
function TextPresetSelector() {
  const [open, setOpen] = useState5(false);
  const [presets, setPresets] = useState5([]);
  const [currentId, setCurrentId] = useState5(null);
  const [switching, setSwitching] = useState5(false);
  const ref = useRef2(null);
  useEffect4(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);
  const loadPresets = useCallback3(() => {
    fetchApiLibrary().then((r) => {
      setPresets(r.text_presets || []);
      setCurrentId(r.current_text_id || null);
    }).catch(() => {
    });
  }, []);
  const handleApply = async (presetId) => {
    setSwitching(true);
    try {
      await applyApiPreset(presetId);
      setCurrentId(presetId);
      setOpen(false);
    } catch (e) {
      alert("\u5207\u6362\u6587\u5B57\u6A21\u578B\u9884\u8BBE\u5931\u8D25\uFF1A" + e.message);
    }
    setSwitching(false);
  };
  const currentPreset = presets.find((p) => p.id === currentId);
  return /* @__PURE__ */ jsxs5("div", { className: "ph-api-preset-selector", ref, children: [
    /* @__PURE__ */ jsxs5(
      "button",
      {
        className: "btn btn-small",
        style: { background: "var(--cinnabar)", color: "var(--paper-raised)", border: "none" },
        onClick: () => {
          setOpen(!open);
          if (!open) loadPresets();
        },
        disabled: switching,
        title: "\u5207\u6362\u6587\u5B57\u6A21\u578B\u9884\u8BBE",
        children: [
          switching ? "\u23F3" : "\u{1F4DD}",
          " ",
          currentPreset?.name || "\u6587\u5B57\u6A21\u578B",
          " \u25BE"
        ]
      }
    ),
    open && /* @__PURE__ */ jsxs5("div", { className: "prompt-harness-menu", style: { right: 0, left: "auto", minWidth: 220 }, children: [
      /* @__PURE__ */ jsx5("div", { className: "prompt-harness-item", style: {
        fontWeight: 600,
        color: "var(--ink-sub)",
        fontSize: "0.75rem",
        padding: "4px 12px",
        cursor: "default"
      }, children: "\u6587\u5B57\u6A21\u578B\u9884\u8BBE" }),
      presets.length === 0 ? /* @__PURE__ */ jsx5("div", { className: "prompt-harness-item", style: { color: "var(--ink-mute)", cursor: "default" }, children: "\u6682\u65E0\u9884\u8BBE" }) : presets.map((p) => /* @__PURE__ */ jsxs5(
        "div",
        {
          className: "prompt-harness-item",
          onClick: () => handleApply(p.id),
          style: {
            flexDirection: "column",
            alignItems: "flex-start",
            gap: 2,
            background: p.id === currentId ? "var(--cinnabar-wash)" : void 0
          },
          children: [
            /* @__PURE__ */ jsxs5("span", { style: { fontWeight: 600 }, children: [
              p.id === currentId ? "\u2B50 " : "",
              p.name
            ] }),
            /* @__PURE__ */ jsx5("span", { style: { fontSize: "0.7rem", color: "var(--ink-mute)" }, children: p.fields?.ARK_MODEL_PRO || "(\u672A\u8BBE\u6A21\u578B)" })
          ]
        },
        p.id
      )),
      /* @__PURE__ */ jsx5(
        "a",
        {
          href: "#/api-presets",
          className: "prompt-harness-item",
          style: { borderTop: "1px solid var(--line)", color: "var(--dai)" },
          onClick: () => setOpen(false),
          children: "\u2699\uFE0F \u7BA1\u7406\u9884\u8BBE\u2026"
        }
      )
    ] })
  ] });
}
function EmbedPresetIndicator() {
  const [embedModel, setEmbedModel] = useState5("");
  const [loaded, setLoaded] = useState5(false);
  const load = useCallback3(() => {
    fetchApiLibrary().then((r) => {
      const fields = r.embed_config && r.embed_config.fields || {};
      setEmbedModel(fields.EMBED_MODEL || "");
      setLoaded(true);
    }).catch(() => {
    });
  }, []);
  useEffect4(() => {
    load();
  }, [load]);
  return /* @__PURE__ */ jsxs5(
    "a",
    {
      href: "#/api-presets",
      className: "btn btn-small",
      style: {
        background: "var(--green-wash)",
        color: "var(--green)",
        border: "1px solid var(--green)",
        cursor: "pointer"
      },
      title: "\u5411\u91CF\u6A21\u578B\u914D\u7F6E\uFF08\u5168\u5C40\u5171\u7528\uFF0C\u70B9\u6B64\u53BB\u4FEE\u6539\uFF09",
      children: [
        "\u{1F9EE} ",
        loaded ? embedModel || "\u672A\u914D\u7F6E" : "..."
      ]
    }
  );
}
function PromptHarnessPage() {
  const [overview, setOverview] = useState5(null);
  const [overviewErr, setOverviewErr] = useState5("");
  const [logsOpen, setLogsOpen] = useState5(false);
  const loadOverview = useCallback3(() => {
    phConnectionsOverview().then((r) => {
      setOverview(r.overview || null);
      setOverviewErr("");
    }).catch((e) => setOverviewErr(String(e.message || e)));
  }, []);
  useEffect4(() => {
    loadOverview();
  }, [loadOverview]);
  const o = overview || {};
  const counts = o.counts || {};
  const logSum = o.log_summary || {};
  return /* @__PURE__ */ jsxs5("div", { className: "prompt-system-layout lintai", children: [
    /* @__PURE__ */ jsxs5("nav", { className: "prompt-system-nav", children: [
      /* @__PURE__ */ jsx5("a", { href: "#/", className: "prompt-system-back", children: "\u21E6 \u8FD4\u56DE\u4E3B\u9875" }),
      /* @__PURE__ */ jsxs5("div", { className: "ph-brand", children: [
        /* @__PURE__ */ jsx5("span", { children: "\u{1F9EA} Prompt Harness" }),
        " ",
        /* @__PURE__ */ jsx5("em", { children: "\xB7 \u70BC\u5DE5\u53F0" })
      ] }),
      /* @__PURE__ */ jsx5("span", { className: "prompt-system-spacer" }),
      /* @__PURE__ */ jsxs5("div", { style: { display: "flex", alignItems: "center", gap: 8 }, children: [
        /* @__PURE__ */ jsx5("span", { className: "ph-health", title: "\u670D\u52A1\u72B6\u6001", children: "\u25CF \u670D\u52A1\u5C31\u7EEA" }),
        /* @__PURE__ */ jsx5(EmbedPresetIndicator, {}),
        /* @__PURE__ */ jsx5(TextPresetSelector, {})
      ] })
    ] }),
    /* @__PURE__ */ jsxs5("div", { className: "ph-pipe", children: [
      /* @__PURE__ */ jsx5("span", { className: "ph-pipe-lbl", children: "\u8FDE\u63A5\u603B\u89C8" }),
      /* @__PURE__ */ jsxs5("span", { className: "st", children: [
        "\u{1F4DA} \u8BED\u6599\u5E93 ",
        /* @__PURE__ */ jsx5("b", { children: counts.corpus_file_count ?? "\u2014" })
      ] }),
      /* @__PURE__ */ jsx5("span", { className: "ph-pipe-arr", children: "\u279C" }),
      /* @__PURE__ */ jsxs5("span", { className: "st", children: [
        "\u{1F9C3} \u69A8\u5E72\u63D0\u53D6 ",
        /* @__PURE__ */ jsx5("b", { children: "\u8FBE\u6807\u5165\u5E93" })
      ] }),
      /* @__PURE__ */ jsx5("span", { className: "ph-pipe-arr", children: "\u279C" }),
      /* @__PURE__ */ jsxs5("span", { className: "st", children: [
        "\u{1F4E6} \u6A21\u677F\u5E93 ",
        /* @__PURE__ */ jsx5("b", { children: counts.template_count ?? "\u2014" }),
        " \u6761"
      ] }),
      /* @__PURE__ */ jsx5("span", { className: "ph-pipe-arr", children: "\u279C" }),
      /* @__PURE__ */ jsxs5("span", { className: "st hl", children: [
        "\u{1F517} \u4E3B\u7CFB\u7EDF\u6D88\u8D39 ",
        /* @__PURE__ */ jsx5("b", { children: counts.consumption_count ?? "\u2014" }),
        " \u5F27"
      ] }),
      /* @__PURE__ */ jsx5("span", { className: "ph-pipe-arr", children: "\u279C" }),
      /* @__PURE__ */ jsxs5("span", { className: "st", children: [
        "\u270D\uFE0F \u4E94\u7EA7\u9636\u68AF ",
        /* @__PURE__ */ jsx5("b", { children: "l1-l5" })
      ] }),
      /* @__PURE__ */ jsx5("span", { className: "ph-pipe-note", children: "\u6A21\u677F\u5E93\u547D\u4E2D \u2192 \u81EA\u52A8\u6CE8\u5165 step_ladder\uFF08\u683C\u5F0F + \u7B56\u7565\uFF09" }),
      overviewErr && /* @__PURE__ */ jsxs5("span", { className: "ph-pipe-note", style: { color: "var(--cinnabar-d)" }, children: [
        "\u26A0 ",
        overviewErr
      ] })
    ] }),
    /* @__PURE__ */ jsxs5("div", { className: "ph-cols", children: [
      /* @__PURE__ */ jsxs5("section", { className: "ph-col", children: [
        /* @__PURE__ */ jsxs5("div", { className: "ph-col-hd", children: [
          /* @__PURE__ */ jsx5("span", { children: "\u{1F9C3} \u69A8\u5E72\u63D0\u53D6" }),
          /* @__PURE__ */ jsx5("span", { className: "n", children: "\u590D\u73B0\u539F\u6587 \xB7 \u8FBE\u6807\u5165\u5E93" }),
          /* @__PURE__ */ jsx5("span", { className: "sp" }),
          /* @__PURE__ */ jsx5("span", { className: "ph-chip green", children: "\u81EA\u52A8\u63D0\u53D6\u98CE\u683C/\u89D2\u8272" })
        ] }),
        /* @__PURE__ */ jsx5("div", { className: "ph-col-bd", children: /* @__PURE__ */ jsx5(PlotExtractHub, {}) })
      ] }),
      /* @__PURE__ */ jsxs5("section", { className: "ph-col", children: [
        /* @__PURE__ */ jsxs5("div", { className: "ph-col-hd", children: [
          /* @__PURE__ */ jsx5("span", { children: "\u{1F4E6} \u60C5\u8282\u6A21\u677F\u5E93" }),
          /* @__PURE__ */ jsx5("span", { className: "n", children: "\u552F\u4E00\u4E0E\u4E3B\u7CFB\u7EDF\u8FDE\u63A5\u7684\u5185\u5BB9" }),
          /* @__PURE__ */ jsx5("span", { className: "sp" }),
          /* @__PURE__ */ jsxs5("span", { className: "ph-chip", children: [
            counts.template_count ?? "\u2014",
            " \u6761"
          ] })
        ] }),
        /* @__PURE__ */ jsx5("div", { className: "ph-col-bd", children: /* @__PURE__ */ jsx5(TemplateLibraryPage, {}) })
      ] }),
      /* @__PURE__ */ jsxs5("section", { className: "ph-col ph-col-conn", children: [
        /* @__PURE__ */ jsxs5("div", { className: "ph-col-hd ph-col-hd-conn", children: [
          /* @__PURE__ */ jsx5("span", { children: "\u{1F517} \u8FDE\u63A5 \xB7 \u4E3B\u7CFB\u7EDF" }),
          /* @__PURE__ */ jsx5("span", { className: "n", children: "prompt \u2192 \u4E3B\u7CFB\u7EDF \u53EF\u89C6\u5316" }),
          /* @__PURE__ */ jsx5("span", { className: "sp" }),
          /* @__PURE__ */ jsx5("span", { className: "ph-chip dai", children: "\u65B0\u6A21\u5757" })
        ] }),
        /* @__PURE__ */ jsx5("div", { className: "ph-col-bd", children: /* @__PURE__ */ jsx5(ConnectionPanel, { overview: o }) })
      ] })
    ] }),
    /* @__PURE__ */ jsxs5("div", { className: "ph-logbar", children: [
      /* @__PURE__ */ jsxs5("div", { className: "ph-logbar-hd", onClick: () => setLogsOpen((v) => !v), children: [
        /* @__PURE__ */ jsx5("span", { children: "\u{1F4CB} \u65E5\u5FD7\u4E2D\u5FC3" }),
        /* @__PURE__ */ jsxs5("span", { className: "dim", children: [
          logSum.total_calls ?? 0,
          " \u8C03\u7528 / ",
          logSum.total_tokens ?? 0,
          " tokens / ",
          logSum.error_count ?? 0,
          " \u9519\u8BEF"
        ] }),
        /* @__PURE__ */ jsx5("span", { className: "sp" }),
        /* @__PURE__ */ jsx5("span", { className: "dim", children: logsOpen ? "\u25BE \u6536\u8D77" : "\u25B8 \u5C55\u5F00\u5B8C\u6574\u65E5\u5FD7\u4E2D\u5FC3\uFF08\u4EFB\u52A1 / LLM / \u4F1A\u8BDD\uFF09" })
      ] }),
      logsOpen && /* @__PURE__ */ jsx5("div", { className: "ph-logbar-bd", children: /* @__PURE__ */ jsx5(PromptLogsPage, {}) })
    ] })
  ] });
}

// _ssr_lintai_check.jsx
var ov = {
  counts: { corpus_file_count: 12, template_count: 92, consumption_count: 0 },
  invariants: [{ level: "l1", length: 128, text: "\u6781\u7B80", injection: "x" }],
  baseline_guard: { length: 10, text: "guard" },
  consumption: [],
  recent_matches: [{ date: "2026-08-13", user: "q", matched: ["tpl"], status: "success", latency_ms: 1 }],
  log_summary: { total_calls: 6, total_tokens: 4562, error_count: 0 }
};
try {
  const a = renderToString(createElement(ConnectionPanel, { overview: ov }));
  console.log("ConnectionPanel SSR OK, len", a.length);
  const b = renderToString(createElement(PromptHarnessPage));
  console.log("PromptHarnessPage SSR OK, len", b.length);
} catch (e) {
  console.error("SSR ERROR:", e.message);
  process.exit(1);
}
