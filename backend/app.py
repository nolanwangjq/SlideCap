"""SlideCap local server. No accounts, cloud services, audio or video recording."""

from __future__ import annotations
import asyncio
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
import zipfile

import cv2
import numpy as np
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from prototype import slidededupe as sd
from prototype.gate import CaptureGate

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("SLIDECAP_DATA", ROOT / "sessions"))
DATA.mkdir(parents=True, exist_ok=True)
RUNTIMES = {}


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def folder(sid):
    if not re.fullmatch(r"[\w-]+", sid) or not (DATA / sid / "meta.json").is_file():
        raise HTTPException(404, "会话不存在")
    return DATA / sid


class Runtime:
    def __init__(self, path, meta):
        self.path, self.meta = path, meta
        self.gate = CaptureGate(
            meta["heartbeat"],
            meta["burst"],
            np.float32(meta["quad"]) if meta.get("quad") else None,
        )
        self.lock = threading.RLock()
        self.records = read_json(path / "frames.json", [])
        self.provisional = []
        self.thumbs = deque(maxlen=3)
        self.ref = None
        self.pending = 0
        self.exposure_frames = 0
        self.black_since = None
        self.last_receive = None
        self.version = 0
        self.status = {}

    def persist(self):
        write_json(self.path / "meta.json", self.meta)
        write_json(self.path / "frames.json", self.records)

    def snapshot(self):
        with self.lock:
            return dict(
                type="state",
                version=self.version,
                meta=self.meta.copy(),
                slides=list(self.provisional),
                status=self.status.copy(),
            )


def runtime(sid):
    p = folder(sid)
    if sid not in RUNTIMES:
        meta = read_json(p / "meta.json")
        if meta["state"] in ("capturing", "processing", "paused"):
            meta["state"] = "interrupted"
            meta["error"] = "服务曾中断；已保存的照片可继续整理。"
            write_json(p / "meta.json", meta)
        RUNTIMES[sid] = Runtime(p, meta)
    return RUNTIMES[sid]


@asynccontextmanager
async def lifespan(app):
    # Eagerly mark interrupted sessions; frames are durable after every saved capture.
    for p in DATA.glob("*/meta.json"):
        runtime(p.parent.name)
    yield


app = FastAPI(title="SlideCap", lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"],
)


@app.middleware("http")
async def local_only(request, call_next):
    origin = request.headers.get("origin")
    if origin and origin not in (
        "http://localhost:8765",
        "http://127.0.0.1:8765",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ):
        return Response("仅接受本机界面请求", 403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = (
        "no-store" if request.url.path.startswith("/api") else "no-cache"
    )
    return response


class NewSession(BaseModel):
    title: str = Field(default="", max_length=100)
    camera: str = Field(default="", max_length=200)
    width: int = Field(default=0, ge=0, le=12000)
    height: int = Field(default=0, ge=0, le=12000)
    interval: float = Field(default=1, ge=0.5, le=30)
    heartbeat: float = Field(default=15, ge=2, le=120)
    burst: int = Field(default=3, ge=2, le=8)
    quad: list[list[float]] | None = None
    replay: bool = False


def validate_quad(quad, width=0, height=0):
    if quad is None:
        return None
    q = np.array(quad, dtype=np.float32)
    if q.shape != (4, 2) or not np.isfinite(q).all() or (q < 0).any():
        raise HTTPException(422, "屏幕四角无效")
    if width and ((q[:, 0] > width).any() or (q[:, 1] > height).any()):
        raise HTTPException(422, "四角超出画面")
    if abs(cv2.contourArea(q)) < 100 or not cv2.isContourConvex(q):
        raise HTTPException(422, "请让四个角组成有效的凸四边形")
    return q


@app.get("/api/sessions")
def sessions():
    result = []
    for p in sorted(DATA.glob("*/meta.json"), reverse=True):
        m = read_json(p)
        slides = read_json(p.parent / "slides.json", [])
        result.append(
            {**m, "count": len(slides), "cover": slides[0]["image"] if slides else None}
        )
    return result


@app.post("/api/sessions")
def create_session(body: NewSession):
    validate_quad(body.quad, body.width, body.height)
    slug = re.sub(r"[^\w-]", "_", body.title.strip())[:30] or "未命名课程"
    sid = (
        datetime.now().strftime("%Y%m%d_%H%M%S")
        + "_"
        + slug
        + "_"
        + uuid.uuid4().hex[:6]
    )
    p = DATA / sid
    (p / "raw").mkdir(parents=True)
    (p / "preview").mkdir()
    meta = {
        **body.model_dump(),
        "id": sid,
        "title": body.title.strip() or "未命名课程",
        "created_at": utc(),
        "state": "capturing",
        "count": 0,
        "bytes": 0,
        "checked": 0,
        "saved": 0,
        "progress": 0,
        "elapsed": 0,
        "gaps": [],
    }
    r = Runtime(p, meta)
    RUNTIMES[sid] = r
    r.persist()
    return meta


async def decode(upload):
    content = await upload.read(24 * 1024 * 1024 + 1)
    if len(content) > 24 * 1024 * 1024:
        raise HTTPException(413, "单张照片不能超过 24 MB")
    img = await run_in_threadpool(
        cv2.imdecode, np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR
    )
    if img is None or img.shape[0] * img.shape[1] > 50_000_000:
        raise HTTPException(422, "无法读取照片，或分辨率过大")
    return img


@app.post("/api/detect-screen")
async def detect_screen(jpeg: UploadFile = File(...)):
    img = await decode(jpeg)
    q = await run_in_threadpool(sd.find_screen_quad, img)
    w = (
        float(max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3])))
        if q is not None
        else 0
    )
    px = w * 0.027
    return dict(
        quad=q.tolist() if q is not None else None,
        text_height=px,
        readability="清晰" if px >= 16 else "勉强" if px >= 10 else "看不清",
        width=img.shape[1],
        height=img.shape[0],
    )


def ingest(r, frame, timestamp, force, source):
    with r.lock:
        if r.meta["state"] != "capturing":
            raise HTTPException(409, "会话未在捕捉中")
        if not np.isfinite(timestamp) or timestamp < 0:
            raise HTTPException(422, "时间戳无效")
        if r.meta["checked"] and timestamp <= r.meta["elapsed"]:
            raise HTTPException(409, "时间戳必须递增，请勿在多个标签页采集同一会话")
        if r.meta["width"] and (frame.shape[1], frame.shape[0]) != (
            r.meta["width"],
            r.meta["height"],
        ):
            raise HTTPException(422, "照片分辨率发生变化，请结束后新建会话")
        now = time.monotonic()
        gap = now - r.last_receive if r.last_receive else 0
        if gap > max(5, r.meta["interval"] * 3) and not r.meta["replay"]:
            r.meta["gaps"].append({"at": timestamp, "seconds": round(gap, 2)})
        r.last_receive = now
        gray = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY)
        black = float(np.mean(gray)) < 8
        r.black_since = (
            (r.black_since if r.black_since is not None else timestamp)
            if black
            else None
        )
        settling = r.exposure_frames > 0
        if settling:
            r.exposure_frames -= 1
        saved, cropped, changed = r.gate.consider(frame, timestamp, force, exposure_settling=settling)
        r.meta.update(
            checked=r.meta["checked"] + 1, elapsed=timestamp, last_detected=utc()
        )
        record = None
        if saved:
            name = f"t{timestamp:010.3f}".replace(".", "_") + ".jpg"
            dest = r.path / "raw" / name
            # Match the prototype JPEG encoding exactly; no extra recompression.
            ok = cv2.imwrite(str(dest), cropped, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if not ok:
                raise HTTPException(507, "照片保存失败，请检查磁盘空间")
            record = {
                "name": name,
                "time": timestamp,
                "force": force,
                "source": source,
                "crop": r.gate.crop,
            }
            r.records.append(record)
            r.meta["saved"] += 1
            r.meta["bytes"] += dest.stat().st_size
        # Same segment threshold, persistence and self-consistency as prototype.segment.
        q = r.gate.detected_quad
        if q is None:
            q = sd.find_screen_quad(frame)
        warped = sd.warp(frame, q)
        th = sd.thumb(warped)
        emit = force
        if settling:
            r.ref = th
            r.pending = 0
            r.thumbs.clear()
        elif r.ref is None:
            r.ref = th
            r.pending = 1
        elif sd.sim(th, r.ref) < 0.80:
            r.pending += 1
            if r.pending >= 2 and r.thumbs and sd.sim(th, r.thumbs[-1]) > 0.80:
                emit = True
                r.ref = th
                r.pending = 0
        else:
            if not r.provisional and r.thumbs:
                emit = True
            r.pending = 0
            r.ref = 0.8 * r.ref + 0.2 * th
        r.thumbs.append(th)
        if emit:
            n = len(r.provisional)
            dest = r.path / "preview" / f"{n}.jpg"
            cv2.imwrite(str(dest), cv2.resize(warped, (640, 360)))
            r.provisional.append(
                {
                    "id": str(n),
                    "time": timestamp,
                    "image": f'/api/sessions/{r.meta["id"]}/files/preview/{n}.jpg',
                    "forced": force,
                }
            )
        warning = (
            "画面持续全黑，请检查镜头或投影。"
            if r.black_since is not None and timestamp - r.black_since >= 5
            else ""
        )
        if r.meta["bytes"] > 1.7 * 1024**3:
            warning = "会话占用已接近 2 GB，请检查取景范围或结束当前会话。"
        r.status = dict(
            warning=warning,
            gap=round(gap, 2),
            saved=saved,
            provisional_count=len(r.provisional),
        )
        r.version += 1
        r.persist()
        return {**r.snapshot(), "saved": saved}


@app.post("/api/sessions/{sid}/frames")
async def frame(
    sid: str,
    jpeg: UploadFile = File(...),
    timestamp: float = Form(...),
    force: bool = Form(False),
    source: str = Form(""),
):
    r = runtime(sid)
    img = await decode(jpeg)
    return await run_in_threadpool(ingest, r, img, timestamp, force, source[:200])


class StateChange(BaseModel):
    paused: bool


@app.post("/api/sessions/{sid}/pause")
def pause(sid: str, body: StateChange):
    r = runtime(sid)
    with r.lock:
        if r.meta["state"] not in ("capturing", "paused"):
            raise HTTPException(409, "当前会话无法暂停或继续")
        r.meta["state"] = "paused" if body.paused else "capturing"
        r.last_receive = None
        r.version += 1
        r.persist()
        return r.meta


@app.websocket("/api/sessions/{sid}/events")
async def events(ws: WebSocket, sid: str):
    if ws.headers.get("origin") not in (
        "http://localhost:8765",
        "http://127.0.0.1:8765",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        None,
    ):
        await ws.close(code=1008)
        return
    r = runtime(sid)
    await ws.accept()
    try:
        while True:
            await ws.send_json(r.snapshot())
            try:
                message = await asyncio.wait_for(ws.receive_json(), timeout=1)
            except asyncio.TimeoutError:
                continue
            if isinstance(message, dict) and message.get("type") == "exposure_changed":
                with r.lock:
                    r.exposure_frames = 2
                    r.pending = 0
                    r.gate.burst_left = 0
                    r.thumbs.clear()
                await ws.send_json({"type": "exposure_ack", "id": message.get("id")})
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass


class DiskFrames:
    def __init__(self, paths):
        self.paths = paths

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [self[j] for j in range(*i.indices(len(self)))]
        img = cv2.imread(str(self.paths[i]))
        if img is None:
            raise ValueError(f"照片损坏：{self.paths[i].name}")
        return img


def process(r):
    try:
        with r.lock:
            records = list(r.records)
            r.meta.update(progress=10, processing_label="定位屏幕与分析变化")
            r.version += 1
            r.persist()
        paths = [r.path / "raw" / rec["name"] for rec in records]
        names = [rec["name"] for rec in records]
        q = np.float32(r.meta["quad"]) if r.meta.get("quad") else None
        if q is not None and records and records[0]["crop"]:
            c = records[0]["crop"]
            q = q - np.float32([c[2], c[0]])
        cuts = [i for i, rec in enumerate(records) if rec["force"]]
        kept, segs, quad = sd.run(
            DiskFrames(paths), names, quad=q, forced_cuts=cuts, work_dir=str(r.path)
        )
        out = r.path / "slides_out"
        out.mkdir(exist_ok=True)
        with r.lock:
            r.meta.update(progress=80, processing_label="生成复核页面")
            r.version += 1
        slides = []
        for i, k in enumerate(kept):
            image_name = f"page_{i:04}.png"
            cv2.imwrite(str(out / image_name), k["img"])
            adjacent_sim = float(sd.sim(kept[i - 1]["thumb"], k["thumb"])) if i else 0
            slides.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "image": f'/api/sessions/{r.meta["id"]}/files/slides_out/{image_name}',
                    "file": image_name,
                    "time": records[k["start"]]["time"],
                    "frame": k["frame"],
                    "candidates": k["candidates"],
                    "builds": k.get("builds", 0),
                    "revisits": k.get("revisits", []),
                    "possible_duplicate": 0.78 < adjacent_sim <= 0.85,
                    "forced": k.get("forced", False),
                }
            )
        with r.lock:
            write_json(r.path / "slides.json", slides)
            r.meta.update(
                state="review",
                count=len(slides),
                progress=100,
                quad_used=quad.tolist() if quad is not None else None,
                finished_at=utc(),
                segments=len(segs),
                revision=0,
            )
            r.meta.pop("error", None)
            r.version += 1
            r.persist()
    except Exception as e:
        with r.lock:
            r.meta.update(state="error", error=str(e))
            r.version += 1
            r.persist()
    finally:
        scratch = r.path / "warped.tmp"
        if scratch.exists():
            scratch.unlink()


@app.post("/api/sessions/{sid}/finish")
def finish(sid: str):
    r = runtime(sid)
    with r.lock:
        if r.meta["state"] == "processing":
            return r.meta
        if r.meta["state"] == "review":
            return r.meta
        r.meta.update(state="processing", progress=1, processing_label="读取已保存照片")
        r.version += 1
        r.persist()
        threading.Thread(target=process, args=(r,), daemon=True).start()
        return r.meta


@app.get("/api/sessions/{sid}/slides")
def get_slides(sid: str):
    r = runtime(sid)
    with r.lock:
        return {
            "slides": read_json(r.path / "slides.json", []),
            "meta": r.meta,
            "frames": [{"time": x["time"], "source": x["source"]} for x in r.records],
        }


class Edit(BaseModel):
    action: str
    ids: list[str] = Field(default_factory=list)
    frame: int | None = None
    revision: int


@app.patch("/api/sessions/{sid}/slides")
def edit_slides(sid: str, body: Edit):
    r = runtime(sid)
    with r.lock:
        if r.meta["state"] != "review":
            raise HTTPException(409, "请等待整理完成")
        if body.revision != r.meta["revision"]:
            raise HTTPException(409, "页面已更新，请重新打开会话")
        slides = read_json(r.path / "slides.json", [])
        by_id = {s["id"]: s for s in slides}
        if len(set(body.ids)) != len(body.ids) or any(i not in by_id for i in body.ids):
            raise HTTPException(422, "幻灯片选择无效")
        if body.action == "delete":
            slides = [s for s in slides if s["id"] not in body.ids]
        elif body.action == "reorder":
            if set(body.ids) != set(by_id):
                raise HTTPException(422, "排序必须包含所有幻灯片")
            slides = [by_id[i] for i in body.ids]
        elif body.action == "replace":
            if len(body.ids) != 1 or body.frame not in by_id[body.ids[0]]["candidates"]:
                raise HTTPException(422, "候选帧不属于这一页")
            s = by_id[body.ids[0]]
            img = candidate_image(r, body.frame)
            filename = f"edit_{uuid.uuid4().hex}.png"
            cv2.imwrite(str(r.path / "slides_out" / filename), img)
            s.update(
                frame=body.frame,
                file=filename,
                image=f"/api/sessions/{sid}/files/slides_out/{filename}",
            )
        elif body.action == "merge":
            if len(body.ids) < 2:
                raise HTTPException(422, "至少选择两页")
            selected = [s for s in slides if s["id"] in body.ids]
            first = selected[0]
            first["candidates"] = sorted(
                set(i for s in selected for i in s["candidates"])
            )
            first["builds"] = sum(s["builds"] for s in selected) + len(selected) - 1
            first["revisits"] = [v for s in selected for v in s["revisits"]]
            first["forced"] = any(s.get("forced") for s in selected)
            slides = [
                s for s in slides if s["id"] not in body.ids or s["id"] == first["id"]
            ]
        else:
            raise HTTPException(422, "不支持的操作")
        write_json(r.path / "slides.json", slides)
        r.meta["revision"] += 1
        r.meta["count"] = len(slides)
        r.persist()
        r.version += 1
        return {"slides": slides, "meta": r.meta}


def candidate_image(r, idx):
    if idx < 0 or idx >= len(r.records):
        raise HTTPException(404, "照片不存在")
    q = r.meta.get("quad_used")
    return sd.warp(
        cv2.imread(str(r.path / "raw" / r.records[idx]["name"])),
        np.float32(q) if q else None,
    )


@app.get("/api/sessions/{sid}/candidates/{idx}")
def candidate(sid: str, idx: int):
    r = runtime(sid)
    img = candidate_image(r, idx)
    ok, jpg = cv2.imencode(".jpg", img)
    return Response(jpg.tobytes(), media_type="image/jpeg")


@app.get("/api/sessions/{sid}/files/{area}/{name}")
def asset(sid: str, area: str, name: str):
    p = folder(sid)
    if area not in ("preview", "slides_out") or not re.fullmatch(
        r"[\w.-]+\.(png|jpg)", name
    ):
        raise HTTPException(404)
    dest = p / area / name
    if not dest.is_file():
        raise HTTPException(404)
    return FileResponse(dest)


@app.get("/api/sessions/{sid}/export")
def export(sid: str, fmt: str = "pptx"):
    if fmt not in ("pptx", "pdf", "zip"):
        raise HTTPException(422, "导出格式无效")
    r = runtime(sid)
    with r.lock:
        if r.meta["state"] != "review":
            raise HTTPException(409, "请先完成整理")
        slides = read_json(r.path / "slides.json", [])
        if not slides:
            raise HTTPException(422, "没有可导出的幻灯片")
        dest = r.path / "slides_out" / f'export_{r.meta["revision"]}'
        dest.mkdir(exist_ok=True)
        cached = dest / f"slides.{fmt}"
        if cached.is_file():
            return FileResponse(cached, filename=f'{r.meta["title"]}.{fmt}')
        kept = [
            dict(
                img=cv2.imread(str(r.path / "slides_out" / s["file"])),
                start=next(
                    i for i, x in enumerate(r.records) if x["time"] == s["time"]
                ),
                frame=s["frame"],
                builds=s["builds"],
                revisits=s["revisits"],
            )
            for s in slides
        ]
        names = [x["name"] for x in r.records]
        if not (dest / "index.json").exists():
            sd.export(kept, names, str(dest))
        index = read_json(dest / "index.json")
        for entry, s in zip(index, slides):
            entry["candidates"] = [names[i] for i in s["candidates"]]
        write_json(dest / "index.json", index)
        for entry in index:
            shutil.copy2(dest / entry["file"], r.path / "slides_out" / entry["file"])
        shutil.copy2(dest / "slides.pptx", r.path / "slides_out" / "slides.pptx")
        # Stable, canonical export files for external scripts and inspection.
        shutil.copy2(dest / "index.json", r.path / "slides_out" / "index.json")
        if fmt == "pdf":
            from reportlab.pdfgen import canvas

            c = canvas.Canvas(str(dest / "slides.pdf"), pagesize=(960, 540))
            for e in index:
                c.drawImage(str(dest / e["file"]), 0, 0, 960, 540)
                c.showPage()
            c.save()
        if fmt == "zip":
            with zipfile.ZipFile(dest / "slides.zip", "w", zipfile.ZIP_DEFLATED) as z:
                for e in index:
                    z.write(dest / e["file"], e["file"])
                z.write(dest / "index.json", "index.json")
        result = dest / f"slides.{fmt}"
        shutil.copy2(result, r.path / "slides_out" / result.name)
        return FileResponse(result, filename=f'{r.meta["title"]}.{fmt}')


@app.get("/api/health")
def health():
    return {"ok": True, "version": "1.0.0", "free_bytes": shutil.disk_usage(DATA).free}


DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
