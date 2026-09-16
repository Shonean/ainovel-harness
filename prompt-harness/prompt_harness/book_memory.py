# -*- coding: utf-8 -*-
"""D1 三级记忆系统（book 级落盘，对齐 Medical AGI g4_memory_v0.py 的最小闭环）。

三级映射（用户 2026-09-07 拍板「完整 D1 移植 + 前端可见分层」）：
  全局记忆  = book 语义库（nodes，scope=book / element:<id>）
  情节记忆  = arc 语义库（nodes，scope=arc:<id>）
  单对话记忆 = session 工作记忆卡（working.json，按 session_id 隔离，每轮必注入不参与召回）
另：经情账本 episodes.jsonl 全书统一 append-only。

四组件与状态机（D1 设计书）：
  EpisodicLedger   episodes.jsonl  append-only，永不物理改写
  SemanticStore    nodes.json      {id, entity, attr, scope, version_chain[{value,via,ctx,ts}], status, conflict_links}
  ConflictCards    cards.json      状态机 open → consolidating → resolved；处置四类：
                                   coexist_layered / version_rewrite / revert / add_slot
  WorkingMemory    working.json    {sessions: {session_id: [{text, ts, source}]}}

慢系统只认两条写路径（slow_write 的 via 断言）：consolidation | user_edit。
v1 巩固通道 = 显式 remember（user_edit 走直写 + 冲突产卡）；LLM 自动巩固由
AINOVEL_CONSOLIDATION 开关控制（默认 off，骨架预留）。检索反方通道不可关闭：
open 卡的冲突双值带 ⚠ 并存注入，不静默取一（SW1 消融开关 CONFLICT_PIPELINE
只控制「是否产卡」，关掉时复刻旧实现的静默覆盖反面教材，供对照测试）。

落盘位置：<书根>/.ainovel/memory_v2/；旧 memory.json 首次访问自动迁移为语义
初始节点（幂等，via="user_edit"+ctx.source="migrate"，旧文件保留只读）。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

# E3-SW1 消融开关：True=冲突产卡走 D1 管线；False=静默覆盖（反面教材，仅测试用）
CONFLICT_PIPELINE = True

_CARD_ACTIONS = ("coexist_layered", "version_rewrite", "revert", "add_slot")


def memory_v2_dir(book_root: str | Path) -> Path:
    return Path(book_root) / ".ainovel" / "memory_v2"


class BookMemory:
    """一本书一个实例；文件小，不做缓存，每次落盘读写（与 ai_creation 同风格）。"""

    def __init__(self, book_root: str | Path):
        self.dir = memory_v2_dir(book_root)
        self._episodes_path = self.dir / "episodes.jsonl"
        self._nodes_path = self.dir / "nodes.json"
        self._cards_path = self.dir / "cards.json"
        self._working_path = self.dir / "working.json"

    # ── 底层 IO ──
    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    # ════════════════════════════════════════════════════════════════
    # 经情账本（episodic，append-only）
    # ════════════════════════════════════════════════════════════════
    def log_episode(self, text: str, *, source: str = "chat", kind: str = "user_msg",
                    arc_id: str = "", session_id: str = "") -> dict[str, Any] | None:
        text = str(text or "").strip()
        if not text:
            return None
        entry = {
            "id": f"E{uuid.uuid4().hex[:8]}",
            "text": text[:500],
            "ctx": {"arc_id": str(arc_id or ""), "session_id": str(session_id or "")},
            "source": source,
            "kind": kind,
            "ts": time.time(),
        }
        self._episodes_path.parent.mkdir(parents=True, exist_ok=True)
        with self._episodes_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def recent_episodes(self, k: int = 8, *, arc_id: str = "", session_id: str = "") -> list[dict[str, Any]]:
        """近因读取（倒序取 k 条）；arc_id/session_id 过滤为可选偏好，不匹配的排后。"""
        if not self._episodes_path.is_file():
            return []
        out: list[dict[str, Any]] = []
        try:
            lines = self._episodes_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            out.append(e)
            if len(out) >= k * 3:
                break
        if arc_id or session_id:
            pref = [e for e in out if e.get("ctx", {}).get("arc_id") == arc_id
                    or (session_id and e.get("ctx", {}).get("session_id") == session_id)]
            rest = [e for e in out if e not in pref]
            out = pref + rest
        return out[:k]

    # ════════════════════════════════════════════════════════════════
    # 语义库（semantic，慢系统——只认两条写路径）
    # ════════════════════════════════════════════════════════════════
    def _load_nodes(self) -> list[dict[str, Any]]:
        return self._read_json(self._nodes_path, [])

    def _save_nodes(self, nodes: list[dict[str, Any]]) -> None:
        self._write_json(self._nodes_path, nodes)

    def slow_write(self, entity: str, attr: str, value: str, *, via: str,
                   scope: str = "book", ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        """慢系统唯一入口。同实体同属性不同值 → 冲突产卡（D1）；返回 node 或 card。"""
        assert via in ("consolidation", "user_edit"), "推理期无权直写慢系统"
        entity = str(entity or "").strip() or "随记"
        attr = str(attr or "").strip() or "value"
        value = str(value or "").strip()
        ctx = dict(ctx or {})
        nodes = self._load_nodes()
        now = time.time()
        for n in nodes:
            if n.get("entity") == entity and n.get("attr") == attr and n.get("status") == "active":
                if node_value(n) != value:  # 冲突
                    if via == "consolidation" and not self._consolidation_enabled():
                        # 自动巩固未开启：不动库，返回标记（骨架预留，不产卡不覆盖）
                        return {"node": n, "consolidation_deferred": True}
                    n.setdefault("version_chain", []).append(
                        {"value": value, "via": via, "ctx": ctx, "ts": now})
                    if CONFLICT_PIPELINE:
                        card = self._open_card(n, value, via, ctx)
                        n.setdefault("conflict_links", []).append(card["id"])
                        self._save_nodes(nodes)
                        return {"node": n, "card": card}
                    # SW1 关闭：recency 隐式覆盖（无卡无标记，反面教材路径）
                    n["version_chain"][-1]["via"] = "silent_overwrite"
                    self._save_nodes(nodes)
                    return {"node": n}
                return {"node": n}  # 同值幂等
        node = {
            "id": f"N{uuid.uuid4().hex[:8]}",
            "entity": entity, "attr": attr, "scope": scope,
            "version_chain": [{"value": value, "via": via, "ctx": ctx, "ts": now}],
            "status": "active", "conflict_links": [],
        }
        nodes.append(node)
        self._save_nodes(nodes)
        return {"node": node}

    @staticmethod
    def _consolidation_enabled() -> bool:
        return os.environ.get("AINOVEL_CONSOLIDATION", "").lower() in ("1", "true", "on")

    def _open_card(self, node: dict[str, Any], new_value: str, via: str,
                   ctx: dict[str, Any]) -> dict[str, Any]:
        cards = self._read_json(self._cards_path, [])
        old = node["version_chain"][-2] if len(node.get("version_chain", [])) >= 2 else {}
        card = {
            "id": f"CC{uuid.uuid4().hex[:8]}",
            "node_id": node["id"],
            "entity": node["entity"], "attr": node["attr"], "scope": node.get("scope", "book"),
            "claim_new": {"text": new_value, "via": via, "ctx": ctx},
            "claim_old": {"text": old.get("value", ""), "via": old.get("via", ""), "ctx": old.get("ctx", {})},
            "detection": {"anchor": "同实体同属性不同值（不可同真）"},
            "axis_hypothesis": self._refine_axis(node, ctx),
            "status": "open", "resolution": None,
            "detected_ts": time.time(),
        }
        cards.append(card)
        self._write_json(self._cards_path, cards)
        return card

    @staticmethod
    def _refine_axis(node: dict[str, Any], ctx: dict[str, Any]) -> dict[str, str]:
        old_ctx = (node.get("version_chain") or [{}])[-1].get("ctx") or {}
        for dim, v in ctx.items():
            if dim in old_ctx and old_ctx[dim] != v and v:
                return {"dim": dim, "detail": f"{old_ctx[dim]} → {v}"}
        return {"dim": "unknown", "detail": "无显式维度差异，需作者仲裁"}

    # ── 矛盾卡：读取与处置 ──
    def all_cards(self, *, status: str | None = None) -> list[dict[str, Any]]:
        cards = self._read_json(self._cards_path, [])
        if status:
            cards = [c for c in cards if c.get("status") == status]
        return cards

    def resolve_card(self, card_id: str, action: str, *, note: str = "",
                     decided_by: str = "user", slot: str = "") -> dict[str, Any] | None:
        """处置矛盾卡。action ∈ coexist_layered / version_rewrite / revert / add_slot。
        - coexist_layered：双值分层并存（节点记 layer_values，检索双值都给）
        - version_rewrite：新值定版（链已在，仅关卡）
        - revert：撤销新值，回退旧值（链弹出，弹出值记入卡留档）
        - add_slot：属性加槽（旧值拆到 attr（slot） 兄弟节点）
        卡先置 consolidating 再落 resolved；动作与决定人写入 resolution。
        """
        if action not in _CARD_ACTIONS:
            raise ValueError(f"未知处置动作：{action}")
        cards = self._read_json(self._cards_path, [])
        card = next((c for c in cards if c.get("id") == card_id), None)
        if card is None:
            return None
        card["status"] = "consolidating"
        nodes = self._load_nodes()
        node = next((n for n in nodes if n.get("id") == card.get("node_id")), None)
        resolution: dict[str, Any] = {"action": action, "note": note, "decided_by": decided_by}
        if node is not None:
            chain = node.setdefault("version_chain", [])
            if action == "revert" and len(chain) >= 2:
                popped = chain.pop()
                resolution["reverted_value"] = popped.get("value", "")
                node["conflict_links"] = [c for c in node.get("conflict_links", []) if c != card_id]
            elif action == "coexist_layered":
                node["layered"] = True
                node["layer_values"] = [
                    {"value": card["claim_old"]["text"], "label": "旧"},
                    {"value": card["claim_new"]["text"], "label": "新"},
                ]
            elif action == "add_slot":
                slot_label = str(slot or note or "分支").strip()[:20]
                sibling = {
                    "id": f"N{uuid.uuid4().hex[:8]}",
                    "entity": node["entity"], "attr": f"{node['attr']}（{slot_label}）",
                    "scope": node.get("scope", "book"),
                    "version_chain": [{"value": card["claim_old"]["text"], "via": "user_edit",
                                       "ctx": {"source": "add_slot", "card": card_id}, "ts": time.time()}],
                    "status": "active", "conflict_links": [],
                }
                nodes.append(sibling)
                resolution["slot_node_id"] = sibling["id"]
        card["resolution"] = resolution
        card["status"] = "resolved"
        self._save_nodes(nodes)
        self._write_json(self._cards_path, cards)
        return card

    # ════════════════════════════════════════════════════════════════
    # 工作记忆（单对话层，session 隔离）
    # ════════════════════════════════════════════════════════════════
    def _load_working(self) -> dict[str, Any]:
        return self._read_json(self._working_path, {"sessions": {}})

    def add_working(self, session_id: str, text: str, *, source: str = "user_edit") -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            raise ValueError("工作记忆内容不能为空")
        sid = str(session_id or "default")
        data = self._load_working()
        cards = data.setdefault("sessions", {}).setdefault(sid, [])
        if any(c.get("text") == text for c in cards):  # 幂等
            return cards[0]
        card = {"id": f"W{uuid.uuid4().hex[:6]}", "text": text[:300],
                "source": source, "ts": time.time()}
        cards.insert(0, card)
        self._write_json(self._working_path, data)
        return card

    def get_working(self, session_id: str) -> list[dict[str, Any]]:
        sid = str(session_id or "default")
        return self._load_working().get("sessions", {}).get(sid, [])

    def remove_working(self, session_id: str, key: str) -> bool:
        """按 id 或文本精确/前缀匹配删一条。"""
        sid = str(session_id or "default")
        data = self._load_working()
        cards = data.get("sessions", {}).get(sid, [])
        before = len(cards)
        cards = [c for c in cards if c.get("id") != key and c.get("text") != key
                 and not str(c.get("text", "")).startswith(key)]
        if len(cards) == before:
            return False
        data.setdefault("sessions", {})[sid] = cards
        self._write_json(self._working_path, data)
        return True

    # ════════════════════════════════════════════════════════════════
    # 三级检索（working 必回 + semantic 关键词/实体 + episodic 近因 + ⚠冲突卡）
    # ════════════════════════════════════════════════════════════════
    def recall(self, query: str, *, arc_id: str = "", session_id: str = "",
               top_k: int = 8) -> dict[str, Any]:
        q = str(query or "").strip()
        q_terms = {q[i:i + 2] for i in range(len(q) - 1)} | ({q} if q else set())

        # 1) 工作记忆：每轮必回，不参与召回排序
        working = self.get_working(session_id) if session_id else []

        # 2) 语义库：关键词命中 + scope 加权（本弧 > 全书 > 元素）+ 冲突增益
        arc_scope = f"arc:{arc_id}" if arc_id else ""
        scored: list[tuple[float, dict[str, Any]]] = []
        for n in self._load_nodes():
            if n.get("status") != "active":
                continue
            sc = n.get("scope", "book")
            text = f"{n.get('entity', '')} {n.get('attr', '')} {n.get('value', '')}"
            score = sum(1.0 for t in q_terms if t and t in text)
            if not score and not q:
                score = 0.5  # 空查询给基础分（少而新优先靠排序）
            if arc_scope and sc == arc_scope:
                score += 3.0
            elif sc == "book":
                score += 1.0
            elif sc.startswith("element:"):
                score += 0.5
            if n.get("layered"):
                score += 0.3
            if score > 0:
                scored.append((score, n))
        scored.sort(key=lambda x: (-x[0], -(n_ts(x[1]))))
        semantic = [n for _, n in scored[:top_k]]

        # 3) 未决冲突卡（反方通道，不可关闭）：双值带 ⚠ 并存
        conflicts = [c for c in self.all_cards(status="open")]

        # 4) 情景近因
        episodic = self.recent_episodes(5, arc_id=arc_id, session_id=session_id)

        return {"working": working, "semantic": semantic,
                "conflicts": conflicts, "episodic": episodic}

    def stats(self) -> dict[str, int]:
        nodes = self._load_nodes()
        ep_count = 0
        if self._episodes_path.is_file():
            try:
                ep_count = sum(1 for line in self._episodes_path.read_text(encoding="utf-8").splitlines() if line.strip())
            except Exception:
                ep_count = 0
        return {
            "episodes": ep_count,
            "nodes": sum(1 for n in nodes if n.get("status") == "active"),
            "cards_open": len(self.all_cards(status="open")),
            "cards_resolved": len(self.all_cards(status="resolved")),
        }

    # ════════════════════════════════════════════════════════════════
    # 旧 memory.json 迁移（幂等，旧文件只读保留）
    # ════════════════════════════════════════════════════════════════
    def ensure_migrated(self, legacy_items: list[dict[str, Any]] | None = None) -> int:
        """把旧平表记忆导入为语义初始节点。以 nodes.json 里的 migrate 标记保证幂等。
        返回本次导入条数（0=已迁移过或无旧数据）。"""
        nodes = self._load_nodes()
        if any(n.get("ctx_migrated") for n in nodes):
            return 0
        legacy = legacy_items if legacy_items is not None else []
        imported = 0
        now = time.time()
        for m in legacy:
            text = str(m.get("text") or "").strip()
            if not text:
                continue
            key = str(m.get("key") or "").strip()
            node = {
                "id": f"N{uuid.uuid4().hex[:8]}",
                "legacy_id": str(m.get("id") or ""),
                "entity": key or text[:12],
                "attr": "value",
                "scope": str(m.get("scope") or "book"),
                "version_chain": [{"value": text, "via": "user_edit",
                                   "ctx": {"source": "migrate"}, "ts": now}],
                "status": "active", "conflict_links": [],
                "ctx_migrated": True,
            }
            nodes.append(node)
            imported += 1
        if imported:
            self._save_nodes(nodes)
        # 无论导入与否都落标记文件，避免旧文件存在但全空时反复扫描
        marker = self.dir / "_migrated"
        if not marker.exists():
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("migrated\n", encoding="utf-8")
        return imported

    def is_migrated(self) -> bool:
        if (self.dir / "_migrated").exists():
            return True
        return any(n.get("ctx_migrated") for n in self._load_nodes())

    # ════════════════════════════════════════════════════════════════
    # 面板/工具侧的显式操作（user_edit 家族）
    # ════════════════════════════════════════════════════════════════
    def user_rewrite(self, node_id: str, value: str, *, ctx: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """面板显式改写：链追加（via=user_edit），并自动以 version_rewrite 关掉该节点 open 卡
        （面板编辑是作者主动定版，不开新卡；卡片只留给「助手记入时发现的矛盾」）。"""
        value = str(value or "").strip()
        if not value:
            return None
        nodes = self._load_nodes()
        n = next((x for x in nodes if x.get("id") == node_id or x.get("legacy_id") == node_id), None)
        if n is None:
            return None
        if node_value(n) != value:
            n.setdefault("version_chain", []).append(
                {"value": value, "via": "user_edit", "ctx": ctx or {"source": "panel_edit"}, "ts": time.time()})
            self._save_nodes(nodes)
        for c in self.all_cards(status="open"):
            if c.get("node_id") == n["id"]:
                self.resolve_card(c["id"], "version_rewrite", note="作者面板显式改写", decided_by="user")
        return n

    def archive_node(self, node_id: str) -> bool:
        """按 id / legacy_id 归档语义节点（不物理删，账本可溯）。"""
        nodes = self._load_nodes()
        hit = False
        for n in nodes:
            if n.get("status") == "active" and node_id in (n.get("id"), n.get("legacy_id")):
                n["status"] = "archived"
                hit = True
        if hit:
            self._save_nodes(nodes)
        return hit

    def forget(self, key: str) -> tuple[bool, str]:
        """forget 工具后端：语义节点按 id/legacy_id/entity 归档 + 工作记忆按 id/文本删。
        返回 (是否有删除, 说明)。"""
        key = str(key or "").strip()
        if not key:
            return False, "没给 key/id"
        nodes = self._load_nodes()
        removed_nodes = 0
        for n in nodes:
            if n.get("status") == "active" and key in (n.get("id"), n.get("legacy_id"), n.get("entity")):
                n["status"] = "archived"
                removed_nodes += 1
        if removed_nodes:
            self._save_nodes(nodes)
        removed_work = 0
        data = self._load_working()
        changed = False
        for sid, cards in (data.get("sessions") or {}).items():
            keep = [c for c in cards if c.get("id") != key and key not in str(c.get("text", ""))]
            if len(keep) != len(cards):
                removed_work += len(cards) - len(keep)
                data.setdefault("sessions", {})[sid] = keep
                changed = True
        if changed:
            self._write_json(self._working_path, data)
        if not removed_nodes and not removed_work:
            return False, f"没找到这条记忆（key={key}）"
        parts = []
        if removed_nodes:
            parts.append(f"语义记忆 {removed_nodes} 条")
        if removed_work:
            parts.append(f"会话约束 {removed_work} 条")
        return True, "；".join(parts)


def node_value(node: dict[str, Any]) -> str:
    """节点当前生效值（链尾）。"""
    chain = node.get("version_chain") or []
    return str(chain[-1].get("value", "")) if chain else ""


def n_ts(node: dict[str, Any]) -> float:
    """节点最新版本的 ts（排序用，模块级便于 sort key 引用）。"""
    chain = node.get("version_chain") or []
    return float(chain[-1].get("ts", 0)) if chain else 0.0
