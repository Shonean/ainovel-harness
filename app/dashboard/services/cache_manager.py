#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缓存管理模块
提供缓存扫描、删除、重建、清理、内容预览、向量库单元格编辑功能
"""
import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
from dashboard.core.path_guard import safe_resolve
from dashboard.services.task_manager import TASKS

# 预览/编辑相关常量
MAX_TEXT_BYTES = 256 * 1024          # 单文件预览最大字节数（256KB）
SQLITE_SAMPLE_ROWS = 5               # SQLite 每表预览行数
SQLITE_READONLY_ROWS = 50            # 可编辑表的分页大小
SQLITE_TEXT_CELL_MAX = 4 * 1024      # 单元格文本截断长度

class CacheItem:
    """缓存项元数据定义"""
    def __init__(self, cache_id: str, name: str, cache_type: str, path: str, glob: str,
                 description: str, impact: str, rebuild_cmd: Optional[str] = None,
                 dangerous: bool = False, recursive: bool = True,
                 kind: str = "files", editable: bool = False,
                 editable_columns: Optional[List[str]] = None,
                 content_ext: Optional[List[str]] = None):
        self.id = cache_id
        self.name = name
        self.type = cache_type
        self.path = path
        self.glob = glob
        self.description = description
        self.impact = impact
        self.rebuild_cmd = rebuild_cmd
        self.dangerous = dangerous
        self.recursive = recursive
        # 新增：预览/编辑相关
        self.kind = kind                    # files | single_file | sqlite | binary
        self.editable = editable            # 是否支持单元格编辑
        self.editable_columns = editable_columns or []
        self.content_ext = content_ext or [] # 多文件时哪些后缀可读（如 .json .md）
# 所有缓存项定义
CACHE_ITEMS: List[CacheItem] = [
    # 安全可删缓存
    CacheItem(
        cache_id="tmp_files",
        name="临时生成产物",
        cache_type="安全可删",
        path=".ainovel/tmp/",
        glob="*",
        description="单次写章流程的临时文件（审查结果、提取结果、lint结果等）",
        impact="无任何影响，下次生成时自动重建",
        dangerous=False,
        kind="files",
        content_ext=[".json", ".md", ".txt"],
    ),
    CacheItem(
        cache_id="observability_logs",
        name="可观测性日志",
        cache_type="安全可删",
        path=".ainovel/observability/",
        glob="*.jsonl",
        description="性能监控、工具调用统计日志",
        impact="无任何影响，新的操作会自动生成新日志",
        dangerous=False,
        kind="files",
        content_ext=[".jsonl", ".log", ".txt"],
    ),
    CacheItem(
        cache_id="faiss_index",
        name="FAISS向量索引",
        cache_type="安全可删",
        path=".ainovel/faiss_index/",
        glob="*",
        description="向量相似度搜索的二进制索引文件",
        impact="向量搜索临时变慢，下次搜索时自动重建索引",
        rebuild_cmd="rebuild-vectors",
        dangerous=False,
        kind="binary",
    ),
    CacheItem(
        cache_id="chapter_summaries",
        name="章节摘要",
        cache_type="安全可删",
        path=".ainovel/summaries/",
        glob="ch*.md",
        description="每章的自动生成摘要",
        impact="无影响，重建向量时自动重新生成",
        rebuild_cmd="rebuild-vectors",
        dangerous=False,
        kind="files",
        content_ext=[".md", ".txt"],
    ),
    CacheItem(
        cache_id="pycache",
        name="Python字节码缓存",
        cache_type="安全可删",
        path=".",
        glob="__pycache__/",
        recursive=True,
        description="Python运行时编译的字节码文件",
        impact="无任何影响，下次运行自动重新编译",
        dangerous=False,
        kind="binary",
    ),
    # 谨慎删除缓存
    CacheItem(
        cache_id="vectors_db",
        name="向量数据库",
        cache_type="谨慎删除",
        path=".ainovel/vectors.db",
        glob="vectors.db",
        description="语义搜索向量数据库",
        impact="语义搜索功能临时失效，RAG检索降级",
        rebuild_cmd="rebuild-vectors --rebuild",
        dangerous=False,
        kind="sqlite",
        editable=True,
        editable_columns=["content"],
    ),
    CacheItem(
        cache_id="reference_vectors_db",
        name="参考向量库",
        cache_type="谨慎删除",
        path=".ainovel/reference_vectors.db",
        glob="reference_vectors.db",
        description="参考段落的向量索引库",
        impact="参考段落匹配功能临时降级",
        rebuild_cmd=None,
        dangerous=False,
        kind="sqlite",
        editable=True,
        editable_columns=["content"],
    ),
    CacheItem(
        cache_id="style_samples_db",
        name="风格样本库",
        cache_type="谨慎删除",
        path=".ainovel/style_samples.db",
        glob="style_samples.db",
        description="AI写作风格样本库",
        impact="风格参考功能临时降级，需要重新标记样本恢复",
        rebuild_cmd=None,
        dangerous=False,
        kind="sqlite",
    ),
    CacheItem(
        cache_id="memory_scratchpad",
        name="语义记忆便签",
        cache_type="谨慎删除",
        path=".ainovel/memory_scratchpad.json",
        glob="memory_scratchpad.json",
        description="AI对故事状态、人物、伏笔的记忆缓存",
        impact="AI可能暂时忘记故事状态，生成内容出现不一致，提交章节时自动重建",
        rebuild_cmd=None,
        dangerous=False,
        kind="single_file",
    ),
    CacheItem(
        cache_id="state_backups",
        name="状态备份文件",
        cache_type="谨慎删除",
        path=".ainovel/backups/",
        glob="*.json",
        description="state.json的历史备份快照",
        impact="丢失历史备份，无法回滚到旧版本状态，不影响当前运行",
        dangerous=False,
        kind="files",
        content_ext=[".json"],
    ),
    # 危险缓存（禁止删除）
    CacheItem(
        cache_id="index_db",
        name="主索引数据库",
        cache_type="危险",
        path=".ainovel/index.db",
        glob="index.db",
        description="核心运行时数据库，包含实体、关系、债务、审查指标等所有派生数据",
        impact="系统核心功能失效，部分数据永久丢失无法恢复",
        dangerous=True,
        kind="sqlite",
    ),
    CacheItem(
        cache_id="state_json",
        name="项目状态文件",
        cache_type="危险",
        path=".ainovel/state.json",
        glob="state.json",
        description="项目配置和进度记录",
        impact="项目进度、配置全部丢失，无法继续写作",
        dangerous=True,
        kind="single_file",
    ),
    CacheItem(
        cache_id="chapter_directives",
        name="结构化章纲",
        cache_type="危险",
        path=".story-system/chapters/",
        glob="*.json",
        description="每章的结构化大纲指令",
        impact="所有章纲丢失，无法继续写作",
        dangerous=True,
        kind="files",
        content_ext=[".json"],
    ),
    CacheItem(
        cache_id="commit_history",
        name="提交历史记录",
        cache_type="危险",
        path=".story-system/commits/",
        glob="*.json",
        description="章节提交的权威记录，是重建索引和向量的唯一数据源",
        impact="提交历史丢失，无法重建索引和向量，版本回滚失效",
        dangerous=True,
        kind="files",
        content_ext=[".json"],
    ),
    CacheItem(
        cache_id="anti_patterns",
        name="反模式配置",
        cache_type="危险",
        path=".story-system/anti_patterns.json",
        glob="anti_patterns.json",
        description="项目定制的写作禁忌配置",
        impact="回退到默认反模式规则，定制禁忌丢失",
        dangerous=True,
        kind="single_file",
    ),
]
# 缓存ID到对象的映射
CACHE_ID_MAP: Dict[str, CacheItem] = {item.id: item for item in CACHE_ITEMS}
class CacheManager:
    """缓存管理类"""
    def __init__(self):
        pass
    async def list_cache(self, project_root: Path) -> List[Dict]:
        """列出所有缓存项及其大小信息"""
        result = []
        for item in CACHE_ITEMS:
            try:
                # 安全解析路径
                full_path = safe_resolve(project_root, item.path)
                size = 0
                count = 0
                if full_path.exists():
                    if full_path.is_file():
                        # 单个文件
                        size = full_path.stat().st_size
                        count = 1
                    elif full_path.is_dir():
                        # 目录，按glob匹配
                        if item.recursive:
                            files = list(full_path.rglob(item.glob))
                        else:
                            files = list(full_path.glob(item.glob))
                        for file in files:
                            if file.is_file():
                                size += file.stat().st_size
                                count += 1
                result.append({
                    "id": item.id,
                    "name": item.name,
                    "type": item.type,
                    "path": item.path,
                    "description": item.description,
                    "impact": item.impact,
                    "rebuild_cmd": item.rebuild_cmd,
                    "dangerous": item.dangerous,
                    "kind": item.kind,
                    "editable": item.editable,
                    "editable_columns": item.editable_columns,
                    "content_ext": item.content_ext,
                    "size": size,
                    "count": count
                })
            except Exception as e:
                result.append({
                    "id": item.id,
                    "name": item.name,
                    "type": item.type,
                    "path": item.path,
                    "description": item.description,
                    "impact": item.impact,
                    "rebuild_cmd": item.rebuild_cmd,
                    "dangerous": item.dangerous,
                    "kind": item.kind,
                    "editable": item.editable,
                    "editable_columns": item.editable_columns,
                    "content_ext": item.content_ext,
                    "size": 0,
                    "count": 0,
                    "error": str(e)
                })
        return result
    async def delete_cache(self, project_root: Path, cache_id: str) -> bool:
        """删除指定缓存项"""
        item = CACHE_ID_MAP.get(cache_id)
        if not item:
            raise ValueError(f"未知缓存项：{cache_id}")
        if item.dangerous:
            raise ValueError(f"危险缓存项禁止删除：{item.name}")
        try:
            full_path = safe_resolve(project_root, item.path)
            if not full_path.exists():
                return True
            if full_path.is_file():
                full_path.unlink()
                return True
            elif full_path.is_dir():
                if item.recursive:
                    files = list(full_path.rglob(item.glob))
                else:
                    files = list(full_path.glob(item.glob))
                for file in files:
                    if file.is_file():
                        file.unlink()
                    elif file.is_dir() and item.glob.endswith("/"):
                        # 删除目录本身
                        import shutil
                        shutil.rmtree(file)
                return True
            return False
        except Exception as e:
            raise RuntimeError(f"删除缓存失败：{str(e)}")
    async def rebuild_cache(self, project_root: Path, cache_id: str) -> Optional[str]:
        """重建指定缓存，返回task_id（如果是后台任务）"""
        item = CACHE_ID_MAP.get(cache_id)
        if not item:
            raise ValueError(f"未知缓存项：{cache_id}")
        if not item.rebuild_cmd:
            return None
        try:
            # 启动重建任务
            from dashboard.routes.actions import _run_action_exec
            cmd_parts = item.rebuild_cmd.split()
            task_id = await _run_action_exec(
                action_name=f"重建{item.name}",
                cmd=cmd_parts[0],
                args=cmd_parts[1:],
                project_root=project_root,
                timeout=3600.0
            )
            return task_id
        except Exception as e:
            raise RuntimeError(f"重建缓存失败：{str(e)}")
    async def clean_safe(self, project_root: Path) -> Dict:
        """一键清理所有安全可删缓存，返回释放空间大小"""
        total_freed = 0
        total_deleted = 0
        errors = []
        for item in CACHE_ITEMS:
            if item.type == "安全可删" and not item.dangerous:
                try:
                    # 先计算大小
                    full_path = safe_resolve(project_root, item.path)
                    size_before = 0
                    if full_path.exists():
                        if full_path.is_file():
                            size_before = full_path.stat().st_size
                        elif full_path.is_dir():
                            if item.recursive:
                                files = list(full_path.rglob(item.glob))
                            else:
                                files = list(full_path.glob(item.glob))
                            for file in files:
                                if file.is_file():
                                    size_before += file.stat().st_size
                    # 执行删除
                    await self.delete_cache(project_root, item.id)
                    total_freed += size_before
                    total_deleted += 1
                except Exception as e:
                    errors.append(f"{item.name}: {str(e)}")
        return {
            "freed_size": total_freed,
            "deleted_count": total_deleted,
            "errors": errors
        }

    # ===== 预览/编辑 API =====
    def _resolve_cache_root(self, project_root: Path, item: CacheItem) -> Path:
        """解析缓存根目录（用于多文件缓存）"""
        return safe_resolve(project_root, item.path)

    def _validate_file_within_cache(
        self, project_root: Path, item: CacheItem, file_rel: str
    ) -> Path:
        """验证 file_rel 在 cache 范围内，并返回绝对路径（防穿越）"""
        # 缓存根目录
        cache_root = self._resolve_cache_root(project_root, item).resolve()
        if item.kind == "single_file":
            # single_file 模式下，只返回唯一的那个文件
            return cache_root
        # files / sqlite / binary
        if not file_rel:
            return cache_root
        # 拼接 file_rel 后规范化（Path 会自动处理 ..）
        requested = (cache_root / file_rel).resolve()
        # 验证：requested 必须 == cache_root 或在 cache_root 子树内
        try:
            is_within = requested == cache_root or requested.is_relative_to(cache_root)
        except AttributeError:
            # 兼容旧 Python
            try:
                requested.relative_to(cache_root)
                is_within = True
            except ValueError:
                is_within = False
        if not is_within:
            raise ValueError(f"路径越界：{file_rel} 不在 {item.path} 范围内")
        return requested

    def _read_text_file(self, path: Path, max_bytes: int = MAX_TEXT_BYTES) -> Dict:
        """读取文本文件，截断到 max_bytes"""
        try:
            total_size = path.stat().st_size
        except OSError as e:
            return {"error": f"无法读取文件信息：{e}"}
        if total_size == 0:
            return {"content": "", "total_size": 0, "truncated": False}
        if total_size > max_bytes:
            with open(path, "rb") as f:
                content = f.read(max_bytes).decode("utf-8", errors="replace")
            return {
                "content": content,
                "total_size": total_size,
                "truncated": True,
                "max_bytes": max_bytes,
            }
        content = path.read_text(encoding="utf-8", errors="replace")
        return {"content": content, "total_size": total_size, "truncated": False}

    def _list_sqlite_tables(self, db_path: Path) -> List[str]:
        """列出 sqlite db 中所有表名"""
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()

    def _get_sqlite_schema(self, db_path: Path, table: str) -> List[Dict]:
        """获取表的 schema（列名 + 类型）"""
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.execute(f'PRAGMA table_info("{table}")')
            return [
                {"name": r[1], "type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])}
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    def _sample_sqlite_table(
        self, db_path: Path, table: str, limit: int, skip_blobs: bool = True
    ) -> List[Dict]:
        """采样 sqlite 表的前 limit 行，跳过 BLOB 列"""
        schema = self._get_sqlite_schema(db_path, table)
        if not schema:
            return []
        # 决定要返回的列：跳过 BLOB
        select_cols = [
            f'"{c["name"]}"'
            for c in schema
            if not (skip_blobs and "BLOB" in c["type"].upper())
        ]
        if not select_cols:
            return []
        # 必须有主键列（用于编辑/定位）
        pk_cols = [c["name"] for c in schema if c["pk"]]
        if not pk_cols:
            # 没有主键，强制加入第一列作为标识
            pk_cols = [schema[0]["name"]]
        conn = sqlite3.connect(str(db_path))
        try:
            # 找到合适的排序列：有 created_at/rowid/id 等
            order_candidates = [c["name"] for c in schema
                                if c["name"].lower() in ("rowid", "created_at", "id", "chunk_id")]
            order_by = ""
            if order_candidates:
                order_by = f'ORDER BY "{order_candidates[0]}"'
            elif "rowid" not in [c["name"] for c in schema]:
                order_by = "ORDER BY rowid"
            sql = (
                f'SELECT {", ".join(select_cols)} FROM "{table}" '
                f"{order_by} LIMIT ?"
            )
            cur = conn.execute(sql, (limit,))
            rows = cur.fetchall()
            col_names = [c["name"] for c in schema if c["name"] in [
                col.strip('"') for col in select_cols
            ]]
            result = []
            for row in rows:
                record = {}
                for col_name, val in zip(col_names, row):
                    # 截断超长文本
                    if isinstance(val, str) and len(val) > SQLITE_TEXT_CELL_MAX:
                        record[col_name] = val[:SQLITE_TEXT_CELL_MAX] + "…"
                    else:
                        record[col_name] = val
                result.append(record)
            return result
        finally:
            conn.close()

    def _get_sqlite_pk(self, db_path: Path, table: str) -> List[str]:
        """获取表的主键列名"""
        schema = self._get_sqlite_schema(db_path, table)
        pks = [c["name"] for c in schema if c["pk"]]
        if not pks:
            return [schema[0]["name"]] if schema else []
        return pks

    async def preview_cache(
        self, project_root: Path, cache_id: str, file_path: Optional[str] = None
    ) -> Dict:
        """预览缓存内容。根据 kind 返回不同结构。"""
        item = CACHE_ID_MAP.get(cache_id)
        if not item:
            raise ValueError(f"未知缓存项：{cache_id}")
        base = {
            "cache_id": cache_id,
            "name": item.name,
            "kind": item.kind,
            "editable": item.editable,
            "editable_columns": item.editable_columns,
        }
        # 路径存在性检查
        try:
            cache_root = self._resolve_cache_root(project_root, item)
        except Exception as e:
            return {**base, "error": f"路径解析失败：{e}"}
        if item.kind == "single_file":
            # 单文件：直接读
            if not cache_root.exists():
                return {**base, "exists": False, "files": [], "content": ""}
            text = self._read_text_file(cache_root)
            return {
                **base,
                "exists": True,
                "files": [{
                    "name": cache_root.name,
                    "path": str(cache_root.relative_to(project_root)),
                    "size": text["total_size"],
                    "truncated": text.get("truncated", False),
                }],
                "content": text["content"],
                "total_size": text["total_size"],
                "truncated": text.get("truncated", False),
            }
        if item.kind == "binary":
            # 二进制：只列文件
            files = self._list_cache_files(project_root, item)
            return {**base, "exists": bool(files), "files": files, "content": None}
        if item.kind == "files":
            # 多文件：列文件 + 可选读一个
            files = self._list_cache_files(project_root, item)
            content = None
            content_file = None
            total_size = 0
            truncated = False
            if file_path and files:
                # 显式路径验证（防穿越）
                target = self._validate_file_within_cache(project_root, item, file_path)
                if not target.is_file():
                    raise ValueError(f"不是一个文件：{file_path}")
                # 仅当后缀在白名单内才读
                if any(file_path.lower().endswith(ext) for ext in item.content_ext):
                    text = self._read_text_file(target)
                    content = text["content"]
                    content_file = file_path
                    total_size = text["total_size"]
                    truncated = text.get("truncated", False)
            return {
                **base,
                "exists": bool(files),
                "files": files,
                "selected_file": content_file,
                "content": content,
                "total_size": total_size,
                "truncated": truncated,
            }
        if item.kind == "sqlite":
            # SQLite：列所有表 + 每表 schema + 前 N 行
            if not cache_root.exists():
                return {**base, "exists": False, "tables": [], "rows": {}}
            try:
                table_names = self._list_sqlite_tables(cache_root)
            except Exception as e:
                return {**base, "exists": True, "error": f"打开数据库失败：{e}"}
            tables_info = []
            rows_by_table = {}
            for tname in table_names:
                try:
                    schema = self._get_sqlite_schema(cache_root, tname)
                    tables_info.append({
                        "name": tname,
                        "columns": schema,
                        "pk_columns": [c["name"] for c in schema if c["pk"]] or (
                            [schema[0]["name"]] if schema else []
                        ),
                    })
                    rows_by_table[tname] = self._sample_sqlite_table(
                        cache_root, tname,
                        limit=SQLITE_READONLY_ROWS if item.editable else SQLITE_SAMPLE_ROWS,
                    )
                except Exception as e:
                    tables_info.append({"name": tname, "error": str(e), "columns": [], "pk_columns": []})
            return {
                **base,
                "exists": True,
                "tables": tables_info,
                "rows": rows_by_table,
                "sample_limit": SQLITE_READONLY_ROWS if item.editable else SQLITE_SAMPLE_ROWS,
            }
        return {**base, "error": f"未知 kind: {item.kind}"}

    def _list_cache_files(self, project_root: Path, item: CacheItem) -> List[Dict]:
        """列出缓存目录下的所有匹配文件（带大小/修改时间）"""
        try:
            cache_root = self._resolve_cache_root(project_root, item)
        except Exception:
            return []
        if not cache_root.exists():
            return []
        result = []
        if cache_root.is_file():
            try:
                st = cache_root.stat()
                result.append({
                    "name": cache_root.name,
                    "path": str(cache_root.relative_to(project_root)),
                    "size": st.st_size,
                    "modified": int(st.st_mtime),
                })
            except OSError:
                pass
            return result
        # 目录
        try:
            if item.recursive:
                files = list(cache_root.rglob(item.glob))
            else:
                files = list(cache_root.glob(item.glob))
        except Exception:
            return []
        for f in files:
            if f.is_file():
                try:
                    st = f.stat()
                    rel = f.relative_to(project_root)
                    result.append({
                        "name": f.name,
                        "path": str(rel).replace("\\", "/"),
                        "size": st.st_size,
                        "modified": int(st.st_mtime),
                    })
                except (OSError, ValueError):
                    continue
        # 按修改时间倒序
        result.sort(key=lambda x: x.get("modified", 0), reverse=True)
        return result

    async def update_sqlite_cell(
        self,
        project_root: Path,
        cache_id: str,
        table: str,
        row_pk: Dict[str, Any],
        column: str,
        value: str,
    ) -> Dict:
        """编辑 sqlite 单元格（仅 editable 缓存的白名单列）"""
        item = CACHE_ID_MAP.get(cache_id)
        if not item:
            raise ValueError(f"未知缓存项：{cache_id}")
        if not item.editable:
            raise ValueError(f"该缓存项不允许编辑：{item.name}")
        if column not in item.editable_columns:
            raise ValueError(f"列「{column}」不在白名单 {item.editable_columns} 内")
        if item.kind != "sqlite":
            raise ValueError(f"非 SQLite 缓存，不能编辑：{item.name}")
        db_path = self._resolve_cache_root(project_root, item)
        if not db_path.exists():
            raise FileNotFoundError(f"数据库不存在：{db_path}")
        # 验证表名确实存在（防注入）
        table_names = self._list_sqlite_tables(db_path)
        if table not in table_names:
            raise ValueError(f"表「{table}」不存在")
        # 验证主键列匹配
        pk_cols = self._get_sqlite_pk(db_path, table)
        if not pk_cols:
            raise ValueError(f"表「{table}」无主键")
        for pk in pk_cols:
            if pk not in row_pk:
                raise ValueError(f"主键「{pk}」缺失")
        # 验证列是 TEXT
        schema = self._get_sqlite_schema(db_path, table)
        col_info = next((c for c in schema if c["name"] == column), None)
        if not col_info:
            raise ValueError(f"列「{column}」不存在")
        if "BLOB" in col_info["type"].upper():
            raise ValueError(f"BLOB 列不可编辑：{column}")
        # 执行 UPDATE
        conn = sqlite3.connect(str(db_path))
        try:
            # 构造 WHERE：主键全部匹配
            where_clause = " AND ".join([f'"{pk}" = ?' for pk in pk_cols])
            where_values = [row_pk[pk] for pk in pk_cols]
            # 先 SELECT 验证行存在
            check_sql = f'SELECT 1 FROM "{table}" WHERE {where_clause} LIMIT 1'
            cur = conn.execute(check_sql, where_values)
            if not cur.fetchone():
                raise ValueError("目标行不存在（主键不匹配）")
            # 限制 value 长度
            if isinstance(value, str) and len(value) > 100_000:
                raise ValueError("文本过长（>100KB），拒绝写入")
            update_sql = f'UPDATE "{table}" SET "{column}" = ? WHERE {where_clause}'
            conn.execute(update_sql, [value] + where_values)
            conn.commit()
            return {
                "ok": True,
                "cache_id": cache_id,
                "table": table,
                "row_pk": row_pk,
                "column": column,
                "new_size": len(value) if isinstance(value, str) else 0,
            }
        finally:
            conn.close()
    async def purge_stale_chapter_directives(self, project_root: Path) -> List[int]:
        """清理失效的章节指令缓存：源章纲已删除但缓存仍存在的文件
        返回被删除的失效章号列表
        """
        # 1. 获取chapter_directives配置
        item = CACHE_ID_MAP.get("chapter_directives")
        if not item:
            raise ValueError("未找到chapter_directives缓存配置")
        # 2. 解析大纲目录下的所有存在的章纲源文件，提取章号
        outline_dir = safe_resolve(project_root, "大纲/")
        existing_chapters: set[int] = set()
        if outline_dir.exists() and outline_dir.is_dir():
            for outline_file in outline_dir.glob("第????章-章纲.md"):
                # 匹配文件名中的四位数字：第NNNN章-章纲.md
                stem = outline_file.stem
                if stem.startswith("第") and stem.endswith("章-章纲"):
                    chapter_str = stem[1:-4]  # 去掉"第"和"章-章纲"
                    if chapter_str.isdigit():
                        existing_chapters.add(int(chapter_str))
        # 3. 解析缓存目录下的所有chapter_NNN.json文件，提取章号
        cache_dir = self._resolve_cache_root(project_root, item)
        cached_chapters: set[int] = set()
        chapter_files: Dict[int, Path] = {}
        if cache_dir.exists() and cache_dir.is_dir():
            for cache_file in cache_dir.glob("chapter_*.json"):
                stem = cache_file.stem
                if stem.startswith("chapter_"):
                    chapter_str = stem[8:]
                    if chapter_str.isdigit():
                        chapter = int(chapter_str)
                        cached_chapters.add(chapter)
                        chapter_files[chapter] = cache_file
        # 4. 计算失效章号（缓存存在但源文件已删除）
        stale_chapters = sorted(list(cached_chapters - existing_chapters))
        # 5. 删除失效文件
        deleted: List[int] = []
        for chapter in stale_chapters:
            file = chapter_files.get(chapter)
            if file and file.exists() and file.is_file():
                try:
                    file.unlink()
                    deleted.append(chapter)
                except Exception as e:
                    raise RuntimeError(f"删除第{chapter}章缓存失败：{str(e)}")
        return deleted
# 全局实例
CACHE_MANAGER = CacheManager()
