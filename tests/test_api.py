import io
import json
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from backend import app as server
from prototype import slidededupe as sd
from prototype.gate import CaptureGate


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA", tmp_path)
    server.RUNTIMES.clear()
    with TestClient(server.app) as c:
        yield c


def photo(n=1, black=False):
    image = np.full((480, 800, 3), 0 if black else 28, np.uint8)
    if not black:
        cv2.rectangle(image, (100, 80), (700, 420), (240, 240, 240), -1)
        cv2.putText(
            image,
            f"Lecture page {n}",
            (140, 170),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (30, 60, 80),
            3,
        )
        for i in range(5):
            cv2.putText(
                image,
                f"Point {n*7+i} learning",
                (145, 215 + i * 33),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 0, 0),
                2,
            )
    return image


def upload(c, sid, img, t, force=False):
    return c.post(
        f"/api/sessions/{sid}/frames",
        files={
            "jpeg": ("frame.jpg", cv2.imencode(".jpg", img)[1].tobytes(), "image/jpeg")
        },
        data={"timestamp": t, "force": str(force).lower()},
    )


def finish(c, sid):
    assert c.post(f"/api/sessions/{sid}/finish").status_code == 200
    for _ in range(300):
        d = c.get(f"/api/sessions/{sid}/slides").json()
        if d["meta"]["state"] == "review":
            return d
        assert d["meta"]["state"] != "error", d["meta"]
        time.sleep(0.05)
    pytest.fail("processing timed out")


def test_gate_matches_original_decisions():
    g = CaptureGate()
    last, saved_at, burst, expected = None, -1e9, 0, []
    for i in range(40):
        img = photo(1 if i < 20 else 2)
        yes, crop, _ = g.consider(img, i)
        th = sd.thumb(cv2.resize(crop, (640, 360)))
        changed = last is None or sd.sim(th, last) < 0.9
        if changed:
            burst = 3
        save = burst > 0 or i - saved_at >= 15
        if save:
            last, saved_at, burst = th, i, max(0, burst - 1)
        assert yes == save
        expected.append(yes)
    assert sum(expected) < 15


def test_full_edit_export_and_persistence(client):
    c = client
    q = [[100, 80], [700, 80], [700, 420], [100, 420]]
    m = c.post(
        "/api/sessions",
        json={"title": "测试课程", "width": 800, "height": 480, "quad": q},
    ).json()
    sid = m["id"]
    for t in range(16):
        assert (
            upload(c, sid, photo(1 if t < 8 else 2), t, force=t == 8).status_code == 200
        )
    d = finish(c, sid)
    assert len(d["slides"]) >= 2
    slides = d["slides"]
    first = slides[0]
    assert len(first["candidates"]) >= 2
    changed = c.patch(
        f"/api/sessions/{sid}/slides",
        json={
            "action": "replace",
            "ids": [first["id"]],
            "frame": first["candidates"][-1],
            "revision": 0,
        },
    )
    assert changed.status_code == 200
    assert changed.json()["slides"][0]["frame"] == first["candidates"][-1]
    assert (
        c.patch(
            f"/api/sessions/{sid}/slides",
            json={"action": "delete", "ids": [], "revision": 0},
        ).status_code
        == 409
    )
    d = c.patch(
        f"/api/sessions/{sid}/slides",
        json={
            "action": "reorder",
            "ids": [s["id"] for s in slides[::-1]],
            "revision": 1,
        },
    ).json()
    assert d["slides"][0]["id"] == slides[-1]["id"]
    d = c.patch(
        f"/api/sessions/{sid}/slides",
        json={"action": "delete", "ids": [d["slides"][-1]["id"]], "revision": 2},
    ).json()
    ppt = c.get(f"/api/sessions/{sid}/export?fmt=pptx")
    assert ppt.status_code == 200
    prs = Presentation(io.BytesIO(ppt.content))
    assert len(prs.slides) == len(d["slides"])
    for s, page in zip(d["slides"], prs.slides):
        # Export image bytes must be the actual selected frame in visible order.
        image = cv2.imread(str(server.DATA / sid / "slides_out" / s["file"]))
        exported = cv2.imdecode(
            np.frombuffer(page.shapes[0].image.blob, np.uint8), cv2.IMREAD_COLOR
        )
        assert np.array_equal(image, exported)
    assert c.get(f"/api/sessions/{sid}/export?fmt=pdf").content.startswith(b"%PDF")
    z = zipfile.ZipFile(
        io.BytesIO(c.get(f"/api/sessions/{sid}/export?fmt=zip").content)
    )
    assert len([n for n in z.namelist() if n.endswith(".png")]) == len(d["slides"])
    server.RUNTIMES.clear()
    persisted = c.get(f"/api/sessions/{sid}/slides").json()
    assert persisted["slides"] == d["slides"]
    assert c.get("/api/sessions").json()[0]["count"] == len(d["slides"])


def test_merge_pause_force_and_errors(client):
    c = client
    sid = c.post("/api/sessions", json={}).json()["id"]
    assert (
        c.post(f"/api/sessions/{sid}/pause", json={"paused": True}).status_code == 200
    )
    assert upload(c, sid, photo(), 0).status_code == 409
    c.post(f"/api/sessions/{sid}/pause", json={"paused": False})
    for i in range(12):
        upload(c, sid, photo(1 if i < 6 else 3), i, force=i == 6)
    d = finish(c, sid)
    assert len(d["slides"]) >= 2
    ids = [s["id"] for s in d["slides"]]
    all_candidates = {i for s in d["slides"] for i in s["candidates"]}
    d = c.patch(
        f"/api/sessions/{sid}/slides",
        json={"action": "merge", "ids": ids, "revision": 0},
    ).json()
    assert len(d["slides"]) == 1
    assert set(d["slides"][0]["candidates"]) == all_candidates
    assert (
        c.patch(
            f"/api/sessions/{sid}/slides",
            json={"action": "replace", "ids": [ids[0]], "frame": 99999, "revision": 1},
        ).status_code
        == 422
    )
    assert c.get(f"/api/sessions/{sid}/export?fmt=exe").status_code == 422
    assert c.get("/api/sessions/not-found/slides").status_code == 404
    assert (
        c.post(
            "/api/sessions", json={}, headers={"Origin": "https://example.com"}
        ).status_code
        == 403
    )
    assert c.post("/api/sessions", json={"quad": [[0, 0]]}).status_code == 422


def test_empty_and_black_session(client):
    c = client
    sid = c.post("/api/sessions", json={}).json()["id"]
    assert finish(c, sid)["slides"] == []
    assert c.get(f"/api/sessions/{sid}/export").status_code == 422
    sid = c.post("/api/sessions", json={}).json()["id"]
    for i in range(7):
        r = upload(c, sid, photo(black=True), i)
    assert "全黑" in r.json()["status"]["warning"]
    assert upload(c, sid, photo(), 6).status_code == 409


def test_manual_quad_and_lazy_pipeline(tmp_path):
    imgs = [photo() for _ in range(5)] + [photo(4) for _ in range(5)]
    names = [str(i) for i in range(10)]
    q = np.float32([[100, 80], [700, 80], [700, 420], [100, 420]])
    a = sd.run(imgs, names, quad=q, forced_cuts=[5])
    b = sd.run(imgs, names, quad=q, forced_cuts=[5], work_dir=str(tmp_path))
    assert a[1] == b[1]
    assert [s["frame"] for s in a[0]] == [s["frame"] for s in b[0]]
    assert len(a[0]) == 2
    assert np.array_equal(a[2], q)
    for x, y in zip(a[0], b[0]):
        assert np.array_equal(x["img"], y["img"])


def test_exposure_websocket_suppresses_two_frames_and_resets_references(client):
    q = [[100, 80], [700, 80], [700, 420], [100, 420]]
    sid = client.post('/api/sessions', json={'quad': q}).json()['id']
    for t in range(4):
        assert upload(client, sid, photo(1), t).status_code == 200
    r = server.runtime(sid)
    before = len(r.provisional)
    with client.websocket_connect(f'/api/sessions/{sid}/events') as ws:
        ws.receive_json()
        ws.send_json({'type': 'exposure_changed', 'id': 'test-1'})
        ack = ws.receive_json()
        assert ack == {'type': 'exposure_ack', 'id': 'test-1'}
        assert r.exposure_frames == 2
        for t in (4, 5):
            assert upload(client, sid, photo(9), t).status_code == 200
            assert len(r.provisional) == before
            assert r.pending == 0
        assert r.exposure_frames == 0
        expected = sd.thumb(sd.warp(photo(9), r.gate.detected_quad))
        # JPEG upload is lossy; compare normalized thumbnail similarity.
        assert sd.sim(expected, r.ref) > .98
        assert upload(client, sid, photo(9), 6).status_code == 200
        assert len(r.provisional) == before
        different = np.random.default_rng(1).integers(0, 256, (480, 800, 3), dtype=np.uint8)
        for t in (7, 8):
            assert upload(client, sid, different, t).status_code == 200
        assert len(r.provisional) > before


def test_exposure_gate_preserves_forced_shot():
    g = CaptureGate()
    saved, _, changed = g.consider(photo(), 0, exposure_settling=True)
    assert not saved and not changed
    saved, _, changed = g.consider(photo(2), 1, force=True, exposure_settling=True)
    assert saved and not changed
    assert g.burst_left == 0
