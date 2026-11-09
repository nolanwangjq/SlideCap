"""Real uvicorn/WebSocket transport; TestClient alone cannot catch missing extras."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
from websockets.sync.client import connect


def test_real_uvicorn_websocket(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "SLIDECAP_DATA": str(tmp_path)}
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", trust_env=False
        ) as client:
            for _ in range(100):
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError("uvicorn did not start")
            r = client.post("/api/sessions", json={"title": "真实 WebSocket 传输测试"})
            r.raise_for_status()
            sid = r.json()["id"]
            with connect(
                f"ws://127.0.0.1:{port}/api/sessions/{sid}/events",
                origin="http://localhost:8765",
                open_timeout=5,
                proxy=None,
            ) as ws:
                event = json.loads(ws.recv(timeout=5))
                assert event["meta"]["id"] == sid
                assert event["type"] == "state"
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
