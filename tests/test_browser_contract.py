"""Static served asset and WebSocket protocol checks; not a substitute for UI QA."""

from fastapi.testclient import TestClient
from backend import app as server


def test_built_frontend_and_live_event(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA", tmp_path)
    server.RUNTIMES.clear()
    with TestClient(server.app) as c:
        html = c.get("/")
        assert html.status_code == 200
        assert "SlideCap" in html.text and "/assets/" in html.text
        sid = c.post("/api/sessions", json={"title": "事件协议验证"}).json()["id"]
        with c.websocket_connect(f"/api/sessions/{sid}/events") as ws:
            event = ws.receive_json()
            assert event["type"] == "state" and event["meta"]["id"] == sid
            assert event["meta"]["state"] == "capturing"
        assert (
            c.get("/api/health", headers={"Host": "attacker.example"}).status_code
            == 400
        )
