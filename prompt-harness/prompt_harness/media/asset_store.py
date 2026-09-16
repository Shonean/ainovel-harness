"""内容寻址资产库（期 3 资产管线地基）。

设计对齐总计划 §2.3「资产库内容寻址」：
- blob 层：内容字节以 sha256 寻址落盘，天然去重，跨线（漫剧/游戏）复用；
- entry 层：语义 asset_id（如 `bg_classroom_night`，来自 pack assets.json）登记项，
  带状态机 pending/done/failed 与 used_by（哪些节点引用）；
- manifest.json 持久化，原子写；
- `ingest_pack_assets`：把 pack 的 assets.json 清单登记进库（只登记，不生成任何真实媒体）。

根目录默认 `<prompt-harness>/.asset_library/`，环境变量 `AINOVEL_ASSET_LIBRARY` 可覆盖。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1

# 与改编层 spec §5.6 的 kind 枚举保持一致；"video" 为期 3 Seedance 生成的视频片段补种
# （改编层 pack 清单不声明 video，视频片段由 seedance 驱动产出后入库）
ASSET_KINDS = ("background", "portrait", "model3d", "bgm", "sfx", "voice", "ui", "video")

# 状态机：pending（已登记未产出）→ done（内容已入库）/ failed（产出失败，error 记因）
ENTRY_STATUSES = ("pending", "done", "failed")


def default_asset_library_root() -> Path:
    """资产库根目录：env AINOVEL_ASSET_LIBRARY > prompt-harness/.asset_library/。"""
    env = os.environ.get("AINOVEL_ASSET_LIBRARY", "").strip()
    if env:
        return Path(env)
    # asset_store.py 位于 <prompt-harness>/prompt_harness/media/ → parents[2] = prompt-harness
    return Path(__file__).resolve().parents[2] / ".asset_library"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())


@dataclass
class Entry:
    """语义 asset_id 登记项。content_id 为 sha256 hex（blob 层地址），done 时非空。"""

    asset_id: str
    kind: str
    status: str = "pending"
    content_id: Optional[str] = None
    used_by: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AssetStore:
    """内容寻址资产库。

    两层结构：
    - put_bytes/get_bytes 走 blob 层（sha256 寻址、去重）；
    - register/mark_done/mark_failed/get_entry 走 entry 层（语义 id + 状态机）。
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root is not None else default_asset_library_root()
        self.blob_dir = self.root / "blobs"
        self.manifest_path = self.root / MANIFEST_NAME
        self._blobs: Dict[str, Dict[str, Any]] = {}
        self._entries: Dict[str, Entry] = {}
        self._load()

    # ------------------------------------------------------------------ 持久化

    def _load(self) -> None:
        if not self.manifest_path.exists():
            return
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self._blobs = data.get("blobs", {}) or {}
        for aid, raw in (data.get("entries", {}) or {}).items():
            self._entries[aid] = Entry(
                asset_id=raw.get("asset_id", aid),
                kind=raw.get("kind", "ui"),
                status=raw.get("status", "pending"),
                content_id=raw.get("content_id"),
                used_by=list(raw.get("used_by", []) or []),
                meta=dict(raw.get("meta", {}) or {}),
                error=raw.get("error"),
                updated_at=raw.get("updated_at"),
            )

    def save(self) -> Path:
        """manifest.json 原子落盘（tmp + os.replace）。"""
        self.root.mkdir(parents=True, exist_ok=True)
        doc = {
            "version": MANIFEST_VERSION,
            "updated_at": _now_iso(),
            "blobs": self._blobs,
            "entries": {aid: e.to_dict() for aid, e in self._entries.items()},
        }
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, self.manifest_path)
        return self.manifest_path

    # ------------------------------------------------------------------ blob 层（内容寻址）

    def put_bytes(self, data: bytes, kind: str, meta: Optional[Dict[str, Any]] = None) -> str:
        """内容字节入库：sha256 寻址 + 去重，返回 blob id（64 位 sha256 hex）。

        同内容重复 put 直接命中已有 blob（不重写文件、不新增 manifest 项）。
        """
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("put_bytes 需要 bytes 数据")
        if kind not in ASSET_KINDS:
            raise ValueError(f"未知资产 kind={kind!r}，允许：{ASSET_KINDS}")
        content_id = hashlib.sha256(bytes(data)).hexdigest()
        if content_id in self._blobs:
            return content_id
        sub = self.blob_dir / content_id[:2]
        sub.mkdir(parents=True, exist_ok=True)
        path = sub / f"{content_id}.bin"
        if not path.exists():  # manifest 丢过但文件还在的情况，直接复用
            path.write_bytes(bytes(data))
        self._blobs[content_id] = {
            "kind": kind,
            "size": len(data),
            "meta": dict(meta or {}),
            "created_at": _now_iso(),
        }
        self.save()
        return content_id

    def blob_path(self, content_id: str) -> Optional[Path]:
        return self.blob_dir / content_id[:2] / f"{content_id}.bin" if content_id in self._blobs else None

    def get_bytes(self, asset_id: str) -> bytes:
        """按 id 取内容字节。asset_id 兼容语义 id（entry 层）与 blob id。"""
        cid = asset_id
        entry = self._entries.get(asset_id)
        if entry is not None:
            cid = entry.content_id
        if cid and cid in self._blobs:
            path = self.blob_path(cid)
            if path is not None and path.exists():
                return path.read_bytes()
        raise KeyError(f"资产不可取：{asset_id!r}（无已入库内容）")

    def has_blob(self, content_id: str) -> bool:
        return content_id in self._blobs

    # ------------------------------------------------------------------ entry 层（状态机）

    def register(
        self,
        asset_id: str,
        kind: str,
        meta: Optional[Dict[str, Any]] = None,
        used_by: Optional[List[str]] = None,
        status: str = "pending",
        content_id: Optional[str] = None,
    ) -> Entry:
        """登记语义资产（幂等：已存在原样返回，不覆盖）。"""
        if kind not in ASSET_KINDS:
            raise ValueError(f"未知资产 kind={kind!r}，允许：{ASSET_KINDS}")
        if status not in ENTRY_STATUSES:
            raise ValueError(f"非法状态 {status!r}，允许：{ENTRY_STATUSES}")
        existing = self._entries.get(asset_id)
        if existing is not None:
            return existing
        entry = Entry(
            asset_id=asset_id,
            kind=kind,
            status=status,
            content_id=content_id,
            used_by=list(used_by or []),
            meta=dict(meta or {}),
            updated_at=_now_iso(),
        )
        self._entries[asset_id] = entry
        self.save()
        return entry

    def get_entry(self, asset_id: str) -> Optional[Entry]:
        return self._entries.get(asset_id)

    def entries(self) -> Dict[str, Entry]:
        return dict(self._entries)

    def mark_done(self, asset_id: str, content_id: str) -> Entry:
        entry = self._require(asset_id)
        if content_id not in self._blobs:
            raise KeyError(f"content_id {content_id!r} 不在 blob 层，请先 put_bytes")
        entry.status = "done"
        entry.content_id = content_id
        entry.error = None
        entry.updated_at = _now_iso()
        self.save()
        return entry

    def mark_failed(self, asset_id: str, reason: str) -> Entry:
        entry = self._require(asset_id)
        entry.status = "failed"
        entry.error = reason
        entry.updated_at = _now_iso()
        self.save()
        return entry

    def _require(self, asset_id: str) -> Entry:
        entry = self._entries.get(asset_id)
        if entry is None:
            raise KeyError(f"未登记的资产：{asset_id!r}（请先 register/ingest）")
        return entry

    def resolve_path(self, asset_id: str) -> Optional[Path]:
        """语义 id → 已入库内容的文件路径；无内容返回 None。"""
        entry = self._entries.get(asset_id)
        cid = entry.content_id if entry else asset_id
        if cid and cid in self._blobs:
            path = self.blob_path(cid)
            if path is not None and path.exists():
                return path
        return None

    # ------------------------------------------------------------------ pack ingest

    def ingest_pack_assets(self, pack_dir: str | Path) -> Dict[str, Any]:
        """读 pack 的 assets.json，把清单项登记进库（填充 hash/status/used_by）。

        - 只登记：不生成任何真实图片/音频；
        - 清单项 status=pending（或无 hash）→ 登记为 pending、content_id=None；
        - 清单项已带 hash（此前已产出过）→ 登记为 done 并挂 content_id=hash；
        - 幂等：库内已有同名 entry 则跳过（already 计数）。
        返回统计 dict；不回写 pack 文件。
        """
        pack = Path(pack_dir)
        assets_path = pack / "assets.json"
        if not assets_path.exists():
            raise FileNotFoundError(f"pack 缺少 assets.json：{assets_path}")
        doc = json.loads(assets_path.read_text(encoding="utf-8"))
        items = doc.get("assets", [])
        if not isinstance(items, list):
            raise ValueError(f"assets.json 的 assets 字段应为数组：{assets_path}")

        registered = already = 0
        by_kind: Dict[str, int] = {}
        for item in items:
            aid = item.get("asset_id")
            kind = item.get("kind")
            if not aid or kind not in ASSET_KINDS:
                continue
            by_kind[kind] = by_kind.get(kind, 0) + 1
            if self._entries.get(aid) is not None:
                already += 1
                continue
            content_hash = item.get("hash")
            if item.get("status") == "done" and content_hash:
                entry = self.register(
                    aid,
                    kind,
                    meta=self._pack_meta(item),
                    used_by=item.get("used_by", []),
                    status="done",
                    content_id=content_hash,
                )
            else:
                entry = self.register(
                    aid,
                    kind,
                    meta=self._pack_meta(item),
                    used_by=item.get("used_by", []),
                    status="pending",
                )
            if entry.status == "pending":
                registered += 1
        return {
            "pack_dir": str(pack),
            "total": len(items),
            "registered": registered,
            "already": already,
            "by_kind": by_kind,
        }

    @staticmethod
    def _pack_meta(item: Dict[str, Any]) -> Dict[str, Any]:
        keys = ("prompt", "ref", "provider_hint")
        return {k: item[k] for k in keys if item.get(k) is not None} | {
            "source": "adaptation_pack"
        }
