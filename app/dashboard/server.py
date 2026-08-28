"""
Dashboard 启动脚本

用法：
    python -m dashboard.server --project-root /path/to/novel-project
    python -m dashboard.server                   # 自动从 .claude 指针读取
"""

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path


def _resolve_project_root(cli_root: str | None) -> Path | None:
    """按优先级解析 PROJECT_ROOT：CLI > 环境变量 > .claude 指针。

    如果没有指定项目则返回 None（后端以"无项目"模式启动，
    系统级功能如 prompt-harness 正常可用，书籍功能需先选书）。
    """
    if cli_root:
        return Path(cli_root).resolve()

    env = os.environ.get("AINOVEL_PROJECT_ROOT")
    if env:
        return Path(env).resolve()

    cwd = Path.cwd()

    # 尝试从 .claude 指针读取
    pointer = cwd / ".claude" / ".ainovel-current-project"
    if pointer.is_file():
        target = pointer.read_text(encoding="utf-8").strip()
        if target:
            p = Path(target)
            if p.is_dir() and (p / ".ainovel" / "state.json").is_file():
                return p.resolve()

    # 后备：从用户级 registry 恢复上次的当前书（不依赖 cwd）
    # —— cwd 指针可能因工作目录不同而读不到，registry 与 app.py 写入一致。
    try:
        reg = Path.home() / ".claude" / "ainovel-write" / "workspaces.json"
        if reg.is_file():
            data = json.loads(reg.read_text(encoding="utf-8") or "{}")
            last = data.get("last_used_project_root") or ""
            if last:
                p = Path(last)
                if p.is_dir() and (p / ".ainovel" / "state.json").is_file():
                    return p.resolve()
    except Exception:
        pass

    # 无项目选中，返回 None
    return None


def main():
    parser = argparse.ArgumentParser(description="AInovel Harness Server")
    parser.add_argument("--project-root", type=str, default=None, help="小说项目根目录")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    project_root = _resolve_project_root(args.project_root)
    if project_root:
        print(f"项目路径: {project_root}")
    else:
        print("无预选项目（系统模式：prompt-harness 等系统工具可用，书籍功能需先选书）")

    # 延迟导入，以便先处理路径
    import uvicorn
    from .app import create_app

    app = create_app(project_root)

    url = f"http://{args.host}:{args.port}"
    print(f"Dashboard 启动: {url}")
    print(f"API 文档: {url}/docs")

    # AINOVEL_RESTARTED=1 表示这是「一键应用新代码」自重启拉起的进程——
    # 桌面应用已有 WebView2，不要再弹外部浏览器。
    if not args.no_browser and not os.environ.get("AINOVEL_RESTARTED"):
        webbrowser.open(url)

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
