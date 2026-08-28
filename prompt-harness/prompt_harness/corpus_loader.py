"""语料加载：把相邻段落合并成 300–800 字的优化单元，以及文档分段提取。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import SETTINGS


TARGET_MIN_LEN = 2000
TARGET_MAX_LEN = 3000

# 模块所在目录（用于回退 corpus_dir）
_MODULE_DIR = Path(__file__).resolve().parent


def _resolve_corpus_dir(corpus_dir: Path | None = None) -> Path:
    """解析语料目录，确保返回有效 Path。

    优先级：
    1. 传入的 corpus_dir
    2. SETTINGS.corpus_dir（如果已初始化）
    3. 模块目录下的 ../corpus（开发环境回退）
    """
    from .config import SETTINGS as _s

    if corpus_dir is not None:
        return corpus_dir

    configured = _s.corpus_dir
    # 检查 SETTINGS.corpus_dir 是否已被初始化（非空 Path 且存在）
    if configured.parts and configured.is_dir():
        # 检查目录中是否真的有语料结构（防止指向错误目录）
        # 如果包含 .txt/.md 文件或预期的子目录，则认为是有效的
        try:
            has_content = any(
                configured.glob("**/*.txt")
            ) or any(
                configured.glob("**/*.md")
            ) or any(
                p.is_dir() and p.name not in ("__pycache__", ".git", ".pytest_cache")
                for p in configured.iterdir()
            )
            if has_content:
                return configured
        except (OSError, PermissionError):
            pass

    # 回退：模块目录下的 ../corpus
    fallback = (_MODULE_DIR.parent / "corpus").resolve()
    if fallback.is_dir():
        return fallback

    # 最终回退：返回配置的目录（即使可能为空）
    return configured


# 场景分隔标记：独立成行的 ***, ---, ◇◆※, ===, --分隔符-- 等
_SCENE_MARKER_RE = re.compile(
    r'(?:^|\n)(?:\*{3,}|-{3,}|◇+|◆+|※+|={3,}|-+分隔符-+)\s*\n',
    re.MULTILINE,
)

# 章节标题正则：行首「第N章 章名」格式。
# 支持：前导空白（'  第1章 仙从何处来'）+ 中文数字章号（'第一章 我们之中出了一个叛徒！'）。
# 注意：正则含中文，用普通字符串（非 raw），避免 Edit \uXXXX 转义在 raw 里变字面。
_CHAPTER_HEADER_RE = re.compile(
    '^[ \\t\\u3000]*第([一二三四五六七八九十百千万零〇两0-9]+)章[ \\t\\u3000]*(.*)',
    re.MULTILINE,
)


# 中文数字转整数：第一章→1、第一百二十六章→126、第一千一百八十章→1180
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT_VALUES = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def _cn2num(s: str) -> int:
    """中文数字转整数；纯数字直接 int；无法解析的兜底返回 len(s)（保留非零章数）。"""
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    total = 0
    num = 0
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
        elif ch in _CN_UNIT_VALUES:
            unit = _CN_UNIT_VALUES[ch]
            if unit == 10000:
                total = (total + num) * 10000
            else:
                total += (num or 1) * unit
            num = 0
        else:
            return len(s)
    return total + num


def _split_paragraphs(text: str) -> list[str]:
    """按空行拆分自然段，保留每段内部换行。"""
    paragraphs: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            current.append(line.rstrip())
        else:
            if current:
                paragraphs.append("\n".join(current))
                current = []
    if current:
        paragraphs.append("\n".join(current))
    return [p for p in paragraphs if p.strip()]


def _merge_paragraphs(paragraphs: list[str], min_len: int, max_len: int) -> list[str]:
    """把相邻段落合并成长度在 [min_len, max_len] 之间的 segment。"""
    if not paragraphs:
        return []

    segments: list[str] = []
    buffer: list[str] = []
    buffer_len = 0

    def flush() -> None:
        nonlocal buffer, buffer_len
        if buffer:
            segments.append("\n\n".join(buffer))
            buffer = []
            buffer_len = 0

    def _split_long_paragraph(text: str, limit: int) -> list[str]:
        """把过长的段落按句子边界切分。"""
        parts: list[str] = []
        # 先按句子结束符切
        sents = re.split(r'(?<=[。！？；])', text)
        buf: list[str] = []
        buf_len = 0
        for s in sents:
            s = s.strip()
            if not s:
                continue
            s_len = len(s)
            if buf_len + s_len > limit and buf:
                parts.append("".join(buf))
                buf = []
                buf_len = 0
            buf.append(s)
            buf_len += s_len
        if buf:
            parts.append("".join(buf))
        # 如果还有单段超长（没有句子边界），按字符硬切
        result: list[str] = []
        for p in parts:
            while len(p) > limit:
                result.append(p[:limit])
                p = p[limit:]
            if p:
                result.append(p)
        return result

    for p in paragraphs:
        p_len = len(p)
        # 单段过长：按句子边界切分
        if p_len >= max_len:
            flush()
            for sub in _split_long_paragraph(p, max_len):
                segments.append(sub)
            continue

        # 加入后可能超限，先落盘
        if buffer_len + p_len > max_len and buffer:
            flush()

        buffer.append(p)
        buffer_len += p_len

        if buffer_len >= min_len:
            flush()

    flush()

    # 最后一段如果太短，尝试和上一段合并；合并且超限就算了
    if len(segments) >= 2 and len(segments[-1]) < min_len:
        last = segments.pop()
        segments[-1] = segments[-1] + "\n\n" + last

    return [s.strip() for s in segments if s.strip()]


def load_file_segments(path: Path, min_len: int, max_len: int) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    paragraphs = _split_paragraphs(text)
    segments = _merge_paragraphs(paragraphs, min_len, max_len)
    return [
        {
            "text": seg,
            "source": str(path),
            "word_count": len(seg),
        }
        for seg in segments
    ]


def load_corpus(
    corpus_dir: Path | None = None,
    *,
    min_len: int = TARGET_MIN_LEN,
    max_len: int = TARGET_MAX_LEN,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """加载语料目录，返回 (segments, meta)。"""
    corpus_dir = _resolve_corpus_dir(corpus_dir)
    if not corpus_dir.is_dir():
        return [], {"error": f"corpus dir not found: {corpus_dir}"}

    segments: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"files": []}

    meta_file = corpus_dir / "meta.json"
    if meta_file.is_file():
        try:
            meta.update(json.loads(meta_file.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass

    for path in sorted(corpus_dir.glob("**/*")):
        if not path.is_file() or path.suffix.lower() not in (".txt", ".md"):
            continue
        file_segments = load_file_segments(path, min_len, max_len)
        # 从路径中提取 genre 和 source_book
        rel = path.relative_to(corpus_dir)
        parts = rel.parts
        path_genre = parts[0] if len(parts) >= 2 else ""
        path_book = parts[1] if len(parts) >= 3 else ""
        for seg in file_segments:
            seg["genre"] = seg.get("genre") or meta.get("genre") or path_genre
            seg["source_book"] = seg.get("source_book") or meta.get("source") or path_book
        segments.extend(file_segments)
        meta["files"].append({
            "path": str(rel).replace("\\", "/"),
            "segments": len(file_segments),
            "genre": path_genre,
            "source_book": path_book,
        })

    meta["total_segments"] = len(segments)
    return segments, meta


def list_corpus_files(corpus_dir: Path | None = None, flat: bool = True) -> list[dict[str, Any]]:
    """列出 corpus 目录下所有可用的 .txt/.md 文件。

    Args:
        corpus_dir: 语料目录
        flat: True=扁平列表（向后兼容），False=树结构

    Returns:
        flat=True: [{path, size, genre, novel}]
        flat=False: 树结构 list_corpus_tree()
    """
    if not flat:
        return list_corpus_tree(corpus_dir)  # type: ignore[return-value]
    return list_corpus_flat(corpus_dir)


def list_corpus_tree(corpus_dir: Path | None = None) -> list[dict[str, Any]]:
    """递归扫描 corpus 目录，返回层级树结构。

    Returns:
        [{name, type: "genre_dir"|"novel_dir"|"file",
          children?: [...], files?: [...], path?: str, size?: int}]
    """
    corpus_dir = _resolve_corpus_dir(corpus_dir)
    if not corpus_dir.is_dir():
        return []

    tree: list[dict[str, Any]] = []
    # 收集所有文件（递归）
    all_files: list[Path] = []
    for path in sorted(corpus_dir.glob("**/*")):
        if path.is_file() and path.suffix.lower() in (".txt", ".md"):
            all_files.append(path)

    # 构建树结构
    # 第一层：类型目录 或 根级文件
    genre_dirs: dict[str, dict[str, Any]] = {}
    root_files: list[dict[str, Any]] = []

    for fpath in all_files:
        try:
            rel = fpath.relative_to(corpus_dir)
        except ValueError:
            continue
        parts = rel.parts
        try:
            size = fpath.stat().st_size
        except OSError:
            size = 0

        if len(parts) == 1:
            # 根级文件（旧格式兼容）
            root_files.append({
                "name": parts[0],
                "type": "file",
                "path": parts[0].replace("\\", "/"),
                "size": size,
                "genre": "",
                "novel": "",
            })
        elif len(parts) >= 2:
            # 有子目录：类型名/小说名/文件（或 类型名/文件）
            genre_name = parts[0]
            if genre_name not in genre_dirs:
                genre_dirs[genre_name] = {
                    "name": genre_name,
                    "type": "genre_dir",
                    "children": [],
                    "novels": {},
                }

            if len(parts) == 2:
                # 类型名/文件（无小说子目录）
                genre_dirs[genre_name]["novels"].setdefault("__root__", {
                    "name": "__root__",
                    "type": "novel_dir",
                    "files": [],
                })
                genre_dirs[genre_name]["novels"]["__root__"]["files"].append({
                    "name": parts[1],
                    "type": "file",
                    "path": str(rel).replace("\\", "/"),
                    "size": size,
                })
            elif len(parts) >= 3:
                # 类型名/小说名/文件
                novel_name = parts[1]
                file_name = "/".join(parts[2:])  # 支持更深嵌套
                if novel_name not in genre_dirs[genre_name]["novels"]:
                    genre_dirs[genre_name]["novels"][novel_name] = {
                        "name": novel_name,
                        "type": "novel_dir",
                        "files": [],
                    }
                genre_dirs[genre_name]["novels"][novel_name]["files"].append({
                    "name": file_name,
                    "type": "file",
                    "path": str(rel).replace("\\", "/"),
                    "size": size,
                })

    # 确保两个强制类型始终出现（即使空目录）
    for required_genre in ("玄幻武侠", "都市日常"):
        if required_genre not in genre_dirs:
            genre_dirs[required_genre] = {
                "name": required_genre,
                "type": "genre_dir",
                "children": [],
            }

    # 组装最终树
    for genre_name in sorted(genre_dirs):
        g = genre_dirs[genre_name]
        children = []
        for novel_name in sorted(g.get("novels", {})):
            n = g["novels"][novel_name]
            if novel_name == "__root__":
                children.extend(n["files"])
            else:
                children.append(n)
        g["children"] = children
        direct_files = [c for c in children if c["type"] == "file"]
        if direct_files:
            g["files"] = direct_files
        if "novels" in g:
            del g["novels"]
        tree.append(g)

    # 根级文件也加入（如果没有任何子目录，它们就是全部）
    if root_files:
        # 如果已经有 genre 目录，把根文件放在一个特殊条目
        if tree:
            tree.append({
                "name": "（根目录旧文件）",
                "type": "genre_dir",
                "files": root_files,
                "children": root_files,
            })
        else:
            # 没有子目录：纯文件列表
            tree.append({
                "name": "corpus",
                "type": "genre_dir",
                "files": root_files,
                "children": root_files,
            })

    return tree


def list_corpus_flat(corpus_dir: Path | None = None) -> list[dict[str, Any]]:
    """列出 corpus/ 下所有文件（扁平列表，向后兼容）。"""
    corpus_dir = _resolve_corpus_dir(corpus_dir)
    if not corpus_dir.is_dir():
        return []
    files: list[dict[str, Any]] = []
    for path in sorted(corpus_dir.glob("**/*")):
        if not path.is_file() or path.suffix.lower() not in (".txt", ".md"):
            continue
        try:
            rel = str(path.relative_to(corpus_dir)).replace("\\", "/")
        except ValueError:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        # 提取 genre 和 novel
        parts = rel.split("/")
        genre = parts[0] if len(parts) >= 2 else ""
        novel = parts[1] if len(parts) >= 3 else ""
        files.append({
            "path": rel,
            "size": size,
            "genre": genre,
            "novel": novel,
        })
    return files


def get_novel_files(genre: str, novel_name: str, corpus_dir: Path | None = None) -> list[str]:
    """获取某本小说的所有文件路径（相对路径列表）。

    Args:
        genre: 类型名（目录名）
        novel_name: 小说名（子目录名）
        corpus_dir: 语料目录

    Returns:
        相对路径列表，如 ["玄幻武侠/示例书/第一章.txt", ...]
    """
    corpus_dir = _resolve_corpus_dir(corpus_dir)
    novel_dir = (corpus_dir / genre / novel_name).resolve()
    try:
        novel_dir.relative_to(corpus_dir.resolve())
    except ValueError:
        return []
    if not novel_dir.is_dir():
        return []
    files: list[str] = []
    for path in sorted(novel_dir.glob("**/*")):
        if not path.is_file() or path.suffix.lower() not in (".txt", ".md"):
            continue
        try:
            files.append(str(path.relative_to(corpus_dir)).replace("\\", "/"))
        except ValueError:
            continue
    return files


def load_corpus_files(
    file_paths: list[str],
    corpus_dir: Path | None = None,
    *,
    min_len: int = TARGET_MIN_LEN,
    max_len: int = TARGET_MAX_LEN,
) -> list[dict[str, Any]]:
    """只加载指定文件的 segments。

    Args:
        file_paths: 相对路径列表（相对于 corpus_dir），如 ["玄幻/ch1.txt"]
        corpus_dir: 语料目录，默认自动解析
        min_len / max_len: 段落长度范围

    Returns:
        与 load_corpus() 相同格式的 segments 列表
    """
    corpus_dir = _resolve_corpus_dir(corpus_dir)
    segments: list[dict[str, Any]] = []
    for rel_path in file_paths:
        full_path = (corpus_dir / rel_path).resolve()
        # 安全检查：确保文件在 corpus_dir 内
        try:
            full_path.relative_to(corpus_dir.resolve())
        except ValueError:
            continue  # 跳过越界路径
        if not full_path.is_file():
            continue
        file_segments = load_file_segments(full_path, min_len, max_len)
        for seg in file_segments:
            seg["source"] = rel_path.replace("\\", "/")
        segments.extend(file_segments)
    return segments


# ---------------------------------------------------------------------------
# 文档分段提取 —— 从长文档中按自然断点逐段提取 2000-3000 字
# ---------------------------------------------------------------------------


def _read_file_text(path: Path, max_size_mb: int = 50) -> str:
    """读取文件文本，自动检测编码（UTF-8 → GBK → latin-1）。

    Args:
        path: 文件路径
        max_size_mb: 最大文件大小（MB），超过时发出警告但仍尝试读取
    """
    file_size = path.stat().st_size
    if file_size > max_size_mb * 1024 * 1024:
        import warnings
        warnings.warn(
            f"文件 {path.name} 大小为 {file_size / 1024 / 1024:.1f}MB，"
            f"超过建议上限 {max_size_mb}MB，读取可能较慢"
        )
    raw = path.read_bytes()
    for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _read_file_window(
    path: Path,
    start_byte: int,
    window_bytes: int,
) -> str:
    """流式读取文件的一个窗口范围，自动检测编码。

    对于大文件，这比全文读取高效得多——只读取需要的字节范围。
    由于编码边界问题，窗口可能需要略大于请求范围。

    Args:
        path: 文件路径
        start_byte: 起始字节偏移（近似，按字符位置折算）
        window_bytes: 窗口大小（字节数）

    Returns:
        解码后的文本窗口
    """
    file_size = path.stat().st_size
    actual_start = max(0, start_byte)
    actual_end = min(file_size, actual_start + window_bytes)

    with open(path, "rb") as fh:
        fh.seek(actual_start)
        raw = fh.read(actual_end - actual_start)

    # 检测编码
    for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def get_file_info(
    filepath: str | Path,
    corpus_dir: Path | None = None,
) -> dict[str, Any]:
    """获取文件元信息（不加载全文）。

    Returns:
        {filename, path, size_bytes, size_mb, encoding, exists}
    """
    corpus_dir = _resolve_corpus_dir(corpus_dir)
    full_path = (corpus_dir / filepath).resolve()

    try:
        full_path.relative_to(corpus_dir.resolve())
    except ValueError:
        return {"error": "路径越界", "filename": str(filepath), "exists": False}

    if not full_path.is_file():
        return {"error": "文件不存在", "filename": str(filepath), "exists": False}

    file_size = full_path.stat().st_size

    # 检测编码（只读文件头）
    encoding = "unknown"
    try:
        with open(full_path, "rb") as fh:
            head = fh.read(4096)
        # Try UTF-8 first (with BOM detection), then GBK/GB2312
        # Skip latin-1 as a detection target since it never fails
        for enc in ("utf-8", "gbk", "gb2312"):
            try:
                decoded = head.decode(enc)
                # Heuristic: if the decoded text contains common Chinese chars, it's likely correct
                if any('一' <= ch <= '鿿' or '　' <= ch <= '〿' for ch in decoded):
                    encoding = enc
                    break
            except (UnicodeDecodeError, LookupError):
                continue
        # Fallback: count decoded length — shorter is usually better (less replacement chars)
        if encoding == "unknown":
            best_enc = "utf-8"
            best_len = float("inf")
            for enc in ("utf-8", "gbk", "gb2312"):
                try:
                    decoded = head.decode(enc)
                    if len(decoded) < best_len:
                        best_len = len(decoded)
                        best_enc = enc
                except (UnicodeDecodeError, LookupError):
                    continue
            encoding = best_enc
    except OSError:
        pass

    # 估算总字符数（基于前 4KB 的字节/字符比）
    estimated_chars = 0
    try:
        with open(full_path, "rb") as fh:
            head = fh.read(4096)
        for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
            try:
                text = head.decode(enc)
                ratio = len(text) / len(head) if head else 1
                estimated_chars = int(file_size * ratio)
                break
            except (UnicodeDecodeError, LookupError):
                continue
    except OSError:
        pass

    # 估算段落数（每段 2500 字）
    estimated_segments = max(1, estimated_chars // 2500)

    # 尝试检测章节数量
    chapter_count = 0
    try:
        if file_size < 20 * 1024 * 1024:  # <20MB: 全文件扫描
            text = _read_file_text(full_path)
            chapter_count = len(_CHAPTER_HEADER_RE.findall(text))
        else:  # 大文件：只扫描前 5MB
            with open(full_path, "rb") as fh:
                head = fh.read(5 * 1024 * 1024).decode(encoding or "utf-8", errors="replace")
            chapter_count = len(_CHAPTER_HEADER_RE.findall(head))
    except Exception:
        pass

    return {
        "filename": str(filepath),
        "path": str(full_path),
        "size_bytes": file_size,
        "size_mb": round(file_size / 1024 / 1024, 2),
        "encoding": encoding,
        "estimated_chars": estimated_chars,
        "estimated_segments": estimated_segments,
        "chapter_count": chapter_count,
        "exists": True,
    }


def _find_natural_break(window: str, min_chars: int, max_chars: int) -> int:
    """在 window 中寻找 [min_chars, max_chars] 范围内的自然断点。

    优先级：场景标记 > 段落空行 > 句子结束 > 精确 max_chars。
    返回 window 内的偏移量（字符数）。
    """
    limit = min(len(window), max_chars)
    search_text = window[:limit]

    # Priority 1: 场景分隔标记（独立成行的 ***, ---, ◇◆※, === 等）
    best = -1
    for m in _SCENE_MARKER_RE.finditer(search_text):
        pos = m.start()
        if min_chars <= pos <= max_chars:
            best = pos
        elif pos > max_chars:
            break
    if best >= 0:
        return best

    # Priority 2: 段落空行（\n\n 或 \n\r?\n）
    for m in re.finditer(r"\n\s*\n", search_text):
        pos = m.start()
        if min_chars <= pos <= max_chars:
            return pos

    # Priority 3: 句子结束（。）
    for m in re.finditer(r"。", search_text):
        pos = m.end()
        if min_chars <= pos <= max_chars:
            return pos

    # Fallback: 精确 max_chars
    return min(max_chars, len(window))


# 流式读取阈值：超过此大小的文件使用窗口读取
_STREAMING_THRESHOLD_BYTES = 5 * 1024 * 1024  # 5MB


def _estimate_byte_offset(text_position: int, file_size: int, estimated_chars: int) -> int:
    """根据字符位置估算字节偏移量。"""
    if estimated_chars <= 0:
        return 0
    ratio = text_position / estimated_chars
    return min(file_size, int(file_size * ratio))


def extract_segment(
    filepath: str | Path,
    start_pos: int = 0,
    min_chars: int = TARGET_MIN_LEN,
    max_chars: int = TARGET_MAX_LEN,
    corpus_dir: Path | None = None,
) -> dict[str, Any]:
    """从文件中提取一个自然段，返回位置信息。

    对大文件（>5MB）自动使用流式窗口读取，避免全文加载。

    Args:
        filepath: 相对于 corpus_dir 的文件路径，如 "玄幻/ch1.txt"
        start_pos: 起始字符位置
        min_chars / max_chars: 段落长度范围
        corpus_dir: 语料目录

    Returns:
        {text, start_pos, end_pos, has_more, total_chars, filename}
    """
    corpus_dir = _resolve_corpus_dir(corpus_dir)
    full_path = (corpus_dir / filepath).resolve()

    # 安全检查：确保文件在 corpus_dir 内
    try:
        full_path.relative_to(corpus_dir.resolve())
    except ValueError:
        return {
            "error": "路径越界",
            "text": "",
            "start_pos": start_pos,
            "end_pos": start_pos,
            "has_more": False,
            "total_chars": 0,
            "filename": str(filepath),
        }

    if not full_path.is_file():
        return {
            "error": "文件不存在",
            "text": "",
            "start_pos": start_pos,
            "end_pos": start_pos,
            "has_more": False,
            "total_chars": 0,
            "filename": str(filepath),
        }

    file_size = full_path.stat().st_size

    # 大文件：使用流式窗口读取
    if file_size > _STREAMING_THRESHOLD_BYTES:
        return _extract_segment_streaming(
            full_path, start_pos, min_chars, max_chars, file_size, str(filepath)
        )

    # 小文件：全文读取（简单高效）
    text = _read_file_text(full_path)
    total_chars = len(text)

    if start_pos >= total_chars:
        return {
            "text": "",
            "start_pos": start_pos,
            "end_pos": start_pos,
            "has_more": False,
            "total_chars": total_chars,
            "filename": str(filepath),
        }

    # 取窗口：max_chars + 500 字符缓冲区用于寻找断点
    window = text[start_pos : start_pos + max_chars + 500]
    break_offset = _find_natural_break(window, min_chars, max_chars)
    actual_end = start_pos + break_offset

    # 不超过文件末尾
    if actual_end > total_chars:
        actual_end = total_chars

    segment = text[start_pos:actual_end].strip()

    # 防御性兜底：确保不超过 max_chars
    if len(segment) > max_chars:
        segment = segment[:max_chars]

    return {
        "text": segment,
        "start_pos": start_pos,
        "end_pos": actual_end,
        "has_more": actual_end < total_chars,
        "total_chars": total_chars,
        "filename": str(filepath),
    }


def _extract_segment_streaming(
    full_path: Path,
    start_pos: int,
    min_chars: int,
    max_chars: int,
    file_size: int,
    filename: str,
) -> dict[str, Any]:
    """流式版本：只读取需要的窗口范围，不加载整个文件。

    策略：从文件中读取 start_pos 附近的一个窗口（~max_chars * 4 字节），
    解码后在窗口内寻找自然断点。返回的 end_pos 是相对于文件开头的字符偏移。
    """
    # UTF-8 中文每字约 3 字节，GBK 约 2 字节，取 4 字节/字作为安全上限
    bytes_per_char_est = 4
    window_bytes = (max_chars + 500) * bytes_per_char_est
    start_byte = start_pos * bytes_per_char_est

    # 读取窗口
    window_text = _read_file_window(full_path, start_byte, window_bytes)

    if not window_text:
        # 回退：全文读取
        text = _read_file_text(full_path)
        total_chars = len(text)
        if start_pos >= total_chars:
            return {
                "text": "", "start_pos": start_pos, "end_pos": start_pos,
                "has_more": False, "total_chars": total_chars, "filename": filename,
            }
        window_text = text[start_pos : start_pos + max_chars + 500]

    # 在窗口内寻找自然断点
    break_offset = _find_natural_break(window_text, min_chars, max_chars)
    actual_end = start_pos + break_offset

    # 读取完整文本获取 total_chars（缓存优化：只在第一次读取时获取）
    # 对于流式提取，我们通过文件大小估算 total_chars
    # 精确值首次读取后缓存
    if not hasattr(_extract_segment_streaming, "_total_chars_cache"):
        _extract_segment_streaming._total_chars_cache = {}  # type: ignore[attr-defined]
    cache = _extract_segment_streaming._total_chars_cache  # type: ignore[attr-defined]

    cache_key = str(full_path)
    if cache_key not in cache:
        try:
            text = _read_file_text(full_path)
            cache[cache_key] = len(text)
        except Exception:
            # 估算
            cache[cache_key] = int(file_size / 2)  # 中文约 2 字节/字

    total_chars = cache[cache_key]
    if actual_end > total_chars:
        actual_end = total_chars

    # 提取段落文本（用 break_offset 截断，避免字节估算导致的超长）
    window_text_full = _read_file_window(
        full_path,
        start_pos * bytes_per_char_est,
        (actual_end - start_pos + 1) * bytes_per_char_est,
    )
    segment = window_text_full[:break_offset].strip()

    # 防御性兜底：仍超 max_chars 则强制截断
    if len(segment) > max_chars:
        segment = segment[:max_chars]

    return {
        "text": segment,
        "start_pos": start_pos,
        "end_pos": actual_end,
        "has_more": actual_end < total_chars,
        "total_chars": total_chars,
        "filename": filename,
    }


# ---------------------------------------------------------------------------
# 章节解析 —— 自动检测「第N章 章名」格式，按章节分割文档
# ---------------------------------------------------------------------------


def parse_chapters(
    filepath: str | Path,
    corpus_dir: Path | None = None,
) -> dict[str, Any]:
    """解析文档中的章节边界，返回所有章节的位置信息。

    通过正则 ^第N章 章名 在全文匹配章节标题行来定位章边界。
    支持 500+ 章的大文档（逐行扫描，内存友好）。

    Returns:
        {filename, total_chapters, chapters: [{chapter_num, title, start_pos, end_pos, char_count}]}
    """
    corpus_dir = _resolve_corpus_dir(corpus_dir)
    full_path = (corpus_dir / filepath).resolve()

    try:
        full_path.relative_to(corpus_dir.resolve())
    except ValueError:
        return {"error": "路径越界", "filename": str(filepath), "total_chapters": 0, "chapters": []}

    if not full_path.is_file():
        return {"error": "文件不存在", "filename": str(filepath), "total_chapters": 0, "chapters": []}

    text = _read_file_text(full_path)
    total_chars = len(text)

    # 找到所有章节标题的位置
    markers: list[tuple[int, int, str, int]] = []  # (start_pos, header_end_pos, title, chapter_num)
    for m in _CHAPTER_HEADER_RE.finditer(text):
        chapter_num = _cn2num(m.group(1))
        title = m.group(2).strip()
        markers.append((m.start(), m.end(), title, chapter_num))

    if not markers:
        return {
            "error": "未检测到章节标记（需要「第N章 章名」格式）",
            "filename": str(filepath),
            "total_chapters": 0,
            "chapters": [],
        }

    # 去重：相邻且相同的章节号只保留第一个（如「第2章 亲戚」后面紧跟「第2章 亲戚」）
    deduped: list[tuple[int, int, str, int]] = []
    for i, mk in enumerate(markers):
        if i > 0 and mk[3] == markers[i - 1][3] and mk[0] - markers[i - 1][0] < 200:
            # 跳过紧邻的重复标题
            continue
        deduped.append(mk)
    markers = deduped

    # 构建章节列表
    chapters: list[dict[str, Any]] = []
    for i, (start_pos, header_end, title, chapter_num) in enumerate(markers):
        # 章节内容从标题行的下一行开始
        content_start = header_end + 1 if header_end + 1 < total_chars else header_end

        # 结束位置：下一个章节的起始位置（或文件末尾）
        if i + 1 < len(markers):
            end_pos = markers[i + 1][0]  # 下一章的标题行起始位置
        else:
            end_pos = total_chars

        # 修剪末尾空白
        while end_pos > content_start and text[end_pos - 1] in '\n\r ':
            end_pos -= 1

        chapters.append({
            "chapter_num": chapter_num,
            "title": title,
            "start_pos": content_start,
            "end_pos": end_pos,
            "char_count": end_pos - content_start,
        })

    return {
        "filename": str(filepath),
        "total_chapters": len(chapters),
        "chapters": chapters,
    }


def build_chapter_groups(
    filepath: str | Path,
    group_size: int = 50,
    corpus_dir: Path | None = None,
) -> dict[str, Any]:
    """将文档章节目录按指定数量分组，返回层级结构。

    Args:
        filepath: 相对路径
        group_size: 每组多少章（默认 50）

    Returns:
        {filename, total_chapters, total_groups,
         groups: [{group_num, label: "第1-50章", start_chapter, end_chapter,
                   chapters: [{chapter_num, title, char_count, index}]}]}
    """
    parsed = parse_chapters(filepath, corpus_dir)
    if "error" in parsed:
        return parsed

    chapters = parsed["chapters"]
    total = parsed["total_chapters"]

    groups: list[dict[str, Any]] = []
    for offset in range(0, total, group_size):
        batch = chapters[offset : offset + group_size]
        group_num = offset // group_size + 1
        start_ch = batch[0]["chapter_num"]
        end_ch = batch[-1]["chapter_num"]

        groups.append({
            "group_num": group_num,
            "label": f"第{start_ch}-{end_ch}章",
            "start_chapter": start_ch,
            "end_chapter": end_ch,
            "chapters": [
                {
                    "chapter_num": ch["chapter_num"],
                    "title": ch["title"],
                    "char_count": ch["char_count"],
                    "index": offset + i,
                }
                for i, ch in enumerate(batch)
            ],
        })

    return {
        "filename": str(filepath),
        "total_chapters": total,
        "total_groups": len(groups),
        "groups": groups,
    }


def extract_chapter(
    filepath: str | Path,
    chapter_index: int = 0,
    corpus_dir: Path | None = None,
) -> dict[str, Any]:
    """提取文档中指定索引的章节全文。

    Args:
        filepath: 相对路径
        chapter_index: 章节在 chapters 列表中的索引（0-based）
        corpus_dir: 语料目录

    Returns:
        {chapter_num, title, text, start_pos, end_pos, char_count, has_prev, has_next, filename}
    """
    parsed = parse_chapters(filepath, corpus_dir)
    if "error" in parsed:
        return {
            "error": parsed.get("error", "解析失败"),
            "text": "",
            "chapter_num": 0,
            "title": "",
            "start_pos": 0,
            "end_pos": 0,
            "char_count": 0,
            "has_prev": False,
            "has_next": False,
            "filename": str(filepath),
        }

    chapters = parsed["chapters"]
    if chapter_index < 0 or chapter_index >= len(chapters):
        return {
            "error": f"章节索引越界：{chapter_index}（共 {len(chapters)} 章）",
            "text": "",
            "chapter_num": 0,
            "title": "",
            "start_pos": 0,
            "end_pos": 0,
            "char_count": 0,
            "has_prev": chapter_index > 0,
            "has_next": chapter_index < len(chapters) - 1,
            "filename": str(filepath),
        }

    ch = chapters[chapter_index]
    corpus_dir = _resolve_corpus_dir(corpus_dir)
    full_path = (corpus_dir / filepath).resolve()

    text = _read_file_text(full_path)
    chapter_text = text[ch["start_pos"]:ch["end_pos"]].strip()

    return {
        "chapter_num": ch["chapter_num"],
        "title": ch["title"],
        "text": chapter_text,
        "start_pos": ch["start_pos"],
        "end_pos": ch["end_pos"],
        "char_count": ch["char_count"],
        "has_prev": chapter_index > 0,
        "has_next": chapter_index < len(chapters) - 1,
        "total_chapters": len(chapters),
        "filename": str(filepath),
    }


# 段落映射上限：超过此数量则合并相邻段
_MAX_SEGMENTS_MAP = 2000


def build_segments_map(
    filepath: str | Path,
    min_chars: int = TARGET_MIN_LEN,
    max_chars: int = TARGET_MAX_LEN,
    corpus_dir: Path | None = None,
) -> dict[str, Any]:
    """预扫描整个文档，返回所有自然段落边界。

    用于前端段落浏览器和自由导航——一次性计算所有分段位置，
    后续可通过 segment-at 按需提取任意段。

    对大文件自动限制段落数（超过 {_MAX_SEGMENTS_MAP} 段时合并）。

    Returns:
        {filename, segments: [{index, start_pos, end_pos, char_count, preview}],
         total_segments, total_chars, truncated (bool)}
    """
    corpus_dir = _resolve_corpus_dir(corpus_dir)
    full_path = (corpus_dir / filepath).resolve()

    try:
        full_path.relative_to(corpus_dir.resolve())
    except ValueError:
        return {"error": "路径越界", "filename": str(filepath), "segments": [], "total_segments": 0, "total_chars": 0}

    if not full_path.is_file():
        return {"error": "文件不存在", "filename": str(filepath), "segments": [], "total_segments": 0, "total_chars": 0}

    file_size = full_path.stat().st_size

    # 大文件：使用更大的段长以减少段落数
    effective_max = max_chars
    if file_size > 10 * 1024 * 1024:  # >10MB
        effective_max = max(max_chars, 4000)  # 每段最多 4000 字

    text = _read_file_text(full_path)
    total_chars = len(text)
    segments: list[dict[str, Any]] = []
    pos = 0
    index = 0
    truncated = False

    while pos < total_chars:
        window = text[pos : pos + effective_max + 500]
        break_offset = _find_natural_break(window, min_chars, effective_max)
        actual_end = min(pos + break_offset, total_chars)
        segment_text = text[pos:actual_end].strip()

        segments.append({
            "index": index,
            "start_pos": pos,
            "end_pos": actual_end,
            "char_count": len(segment_text),
            "preview": segment_text[:80],
        })

        pos = actual_end
        index += 1

        # 段落数超限：扩大段长重新扫描
        if index >= _MAX_SEGMENTS_MAP:
            truncated = True
            # 合并现有段落：每 2 段合并为 1 段
            merged: list[dict[str, Any]] = []
            for i in range(0, len(segments), 2):
                batch = segments[i:i + 2]
                if len(batch) == 1:
                    merged.append(batch[0])
                else:
                    merged.append({
                        "index": len(merged),
                        "start_pos": batch[0]["start_pos"],
                        "end_pos": batch[-1]["end_pos"],
                        "char_count": sum(s["char_count"] for s in batch),
                        "preview": batch[0]["preview"],
                    })
            segments = merged

            # 如果合并后仍超限，继续合并
            if len(segments) > _MAX_SEGMENTS_MAP // 2:
                # 取前 _MAX_SEGMENTS_MAP 段，剩余合并为一段
                segments = segments[:_MAX_SEGMENTS_MAP - 1]
                if pos < total_chars:
                    last_segment_text = text[segments[-1]["end_pos"]:total_chars].strip()
                    segments.append({
                        "index": len(segments),
                        "start_pos": segments[-1]["end_pos"],
                        "end_pos": total_chars,
                        "char_count": len(last_segment_text),
                        "preview": last_segment_text[:80] if last_segment_text else "",
                    })
                pos = total_chars  # 结束循环

    return {
        "filename": str(filepath),
        "segments": segments,
        "total_segments": len(segments),
        "total_chars": total_chars,
        "file_size_bytes": file_size,
        "file_size_mb": round(file_size / 1024 / 1024, 2),
        "truncated": truncated,
    }
