#!/usr/bin/env python3
"""Start SlideCap on localhost. Build the UI once if necessary."""

from pathlib import Path
import argparse
import subprocess
import threading
import webbrowser
import sys

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if not (ROOT / "frontend/dist/index.html").exists():
        try:
            subprocess.run(["npm", "ci"], cwd=ROOT / "frontend", check=True)
            subprocess.run(["npm", "run", "build"], cwd=ROOT / "frontend", check=True)
        except (OSError, subprocess.CalledProcessError):
            sys.exit("前端构建失败，请安装 Node.js 20.19+ 并按 README 安装依赖。")
    import uvicorn

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open("http://localhost:8765")).start()
    print("SlideCap → http://localhost:8765  （Ctrl+C 停止）")
    uvicorn.run("backend.app:app", host="127.0.0.1", port=8765)
