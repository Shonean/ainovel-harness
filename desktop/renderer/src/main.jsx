import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./shared/theme.css";
import "./shell.css";
import { on } from "./shared/bridge.js";
import WorkbenchPanel from "./panels/Workbench.jsx";
import MindMapPanel from "./panels/MindMap.jsx";
import AnalysisPanel from "./panels/Analysis.jsx";
import InspirePanel from "./panels/Inspire.jsx";
import FragmentPanel from "./panels/Fragment.jsx";
import SystemPanel from "./panels/System.jsx";
import TrainPanel from "./panels/train/Train.jsx";
import LogsPanel from "./panels/train/Logs.jsx";
import SidebarApp from "./sidebar/App.jsx";

const ROUTES = {
  workbench: WorkbenchPanel,
  mindmap: MindMapPanel,
  analysis: AnalysisPanel,
  inspire: InspirePanel,
  fragment: FragmentPanel,
  system: SystemPanel,
  train: TrainPanel,
  logs: LogsPanel,
};

const NAV = [
  { kind: "workbench", icon: "🎯", name: "工作台" },
  { kind: "mindmap", icon: "🌳", name: "思维导图" },
  { kind: "analysis", icon: "📊", name: "分析" },
  { kind: "inspire", icon: "💡", name: "灵感工坊" },
  { kind: "fragment", icon: "✂️", name: "片段扩写" },
  { kind: "system", icon: "⚙️", name: "系统面板" },
  { kind: "train", icon: "🏋️", name: "训练台" },
  { kind: "logs", icon: "📜", name: "日志中心" },
];

function App() {
  const [kind, setKind] = useState("workbench");
  const [assistantOpen, setAssistantOpen] = useState(true);
  const [connected, setConnected] = useState(false);
  const [bookName, setBookName] = useState("");

  useEffect(() => {
    const onNav = (e) => e.detail && setKind(e.detail);
    const onAssistant = () => setAssistantOpen((o) => !o);
    window.addEventListener("ainovel:nav", onNav);
    window.addEventListener("ainovel:assistant", onAssistant);
    return () => {
      window.removeEventListener("ainovel:nav", onNav);
      window.removeEventListener("ainovel:assistant", onAssistant);
    };
  }, []);

  useEffect(() => on("connected", setConnected), []);
  useEffect(() => on("init", (s) => { setConnected(s.connected); setBookName(s.bookName); }), []);
  useEffect(() => on("bookChanged", (s) => setBookName(s.bookName || "")), []);

  const Panel = ROUTES[kind] || WorkbenchPanel;

  return (
    <div className="shell">
      <nav className="shell-nav">
        <div className="shell-logo" title="AInovel Harness">📖</div>
        {NAV.map((n) => (
          <button
            key={n.kind}
            className={`nav-btn${kind === n.kind ? " active" : ""}`}
            title={n.name}
            onClick={() => setKind(n.kind)}
          >
            <span className="nav-icon">{n.icon}</span>
            <span className="nav-name">{n.name}</span>
          </button>
        ))}
        <div className="nav-spacer" />
        <div className={`nav-status${connected ? " ok" : ""}`} title={connected ? "后端已连接" : "后端未连接"}>
          <span className="status-dot" />
        </div>
      </nav>

      <main className="shell-main" key={kind}>
        <Panel />
      </main>

      {assistantOpen && (
        <aside className="shell-assistant">
          <div className="assistant-head">
            <span>创作助手</span>
            <button className="assistant-close" title="收起" onClick={() => setAssistantOpen(false)}>×</button>
          </div>
          <div className="assistant-body">
            <SidebarApp />
          </div>
        </aside>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
