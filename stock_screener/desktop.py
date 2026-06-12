#!/usr/bin/env python3
"""MoneyManager 桌面应用入口。

启动 FastAPI 后端 + pywebview 原生窗口，跨平台（macOS / Windows）。
"""

from __future__ import annotations

import os
import sys
import time
import threading
from pathlib import Path


def _is_packaged() -> bool:
    """PyInstaller 打包后 sys._MEIPASS 会被设置。"""
    return hasattr(sys, "_MEIPASS") and sys._MEIPASS is not None


def _resource_path(relative: str) -> Path:
    """获取资源文件路径，兼容开发模式与 PyInstaller 打包。"""
    if _is_packaged():
        return Path(sys._MEIPASS) / relative  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent / relative


def _ensure_dotenv() -> None:
    """确保 .env 在打包后能被找到。"""
    env_path = _resource_path(".env")
    if env_path.exists():
        os.environ.setdefault("MONEYMANAGER_DOTENV", str(env_path))

    # 开发模式：需要 chdir 到项目根目录，让 load_dotenv 找到 .env
    if not _is_packaged():
        root = Path(__file__).resolve().parent
        os.chdir(str(root))


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """在后台线程启动 uvicorn。"""
    import uvicorn
    from web.main import app

    # 桌面模式：挂载前端静态文件
    _mount_static(app)

    uvicorn.run(app, host=host, port=port, log_level="warning")


def _mount_static(app) -> None:
    """将前端 dist 目录挂载到 / 路由。"""
    from fastapi.staticfiles import StaticFiles
    import os

    static_dir = _resource_path("web_frontend/dist")
    if not static_dir.exists():
        print(f"[desktop] 前端静态文件不存在: {static_dir}，请先运行 npm run build")
        return
    # 设置环境变量告知 web/main.py 处于桌面模式
    os.environ["MONEYMANAGER_DESKTOP"] = "1"
    try:
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="frontend")
        print(f"[desktop] 已挂载前端: {static_dir}")
    except RuntimeError:
        # 已经挂载过（开发模式重启）
        pass


def main():
    import os
    os.environ.setdefault("MONEYMANAGER_DESKTOP", "1")

    _ensure_dotenv()

    port = 8000
    host = "127.0.0.1"

    # 启动后端
    server = threading.Thread(target=run_server, args=(host, port), daemon=True)
    server.start()

    # 等待后端就绪
    _wait_for_server(host, port)

    # 创建原生窗口
    import webview
    url = f"http://{host}:{port}"
    window = webview.create_window(
        "MoneyManager 选股器",
        url,
        width=1400,
        height=900,
        min_size=(1024, 680),
        text_select=True,
    )
    webview.start(debug=False)


def _wait_for_server(host: str, port: int, timeout: float = 10.0) -> None:
    """轮询等待 uvicorn 就绪。"""
    import urllib.request

    deadline = time.time() + timeout
    url = f"http://{host}:{port}/healthz"
    while time.time() < deadline:
        try:
            resp = urllib.request.urlopen(url, timeout=0.5)
            if resp.status == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    print("[desktop] 警告: 后端启动超时，尝试继续...")


if __name__ == "__main__":
    # macOS pyinstaller 打包后 multiprocessing 需要
    from multiprocessing import freeze_support
    freeze_support()
    main()
