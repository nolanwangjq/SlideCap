#!/usr/bin/env python3
"""
live_capture.py — SlideCap v1: capture slides live from any camera, then de-dupe.

Sources (--source):
  0, 1, ...                 a UVC webcam index (Insta360 Link 2 Pro / 2C Pro, any USB webcam)
  http://... / rtsp://...   a phone streaming its camera (e.g. an "IP camera" app on the same WiFi)
  path/to/folder            replay a folder of photos (testing; each file = one tick)

What it does
  every --interval seconds: grab a frame, compare a tiny thumbnail with the last saved one.
    - changed  -> save a short burst (so the de-dupe stage can pick the sharpest, unoccluded one)
    - unchanged-> save only a heartbeat every --heartbeat seconds
  once the screen is located, frames are cropped to the screen (+margin) -> ~5-10x less disk.
  --autoframe (PTZ cameras such as Link 2 Pro): pan/tilt/zoom so the screen fills the frame.
  on Ctrl+C or --duration: run slidededupe -> slides_out/{slide_###.png, slides.pptx, index.json}

Disk: 4h at 1 s ticks typically saves ~600-1500 cropped JPEGs (~0.3-1 GB) instead of 14,400 frames.
"""
import argparse, glob, os, time, datetime
import cv2, numpy as np
import slidededupe as sd
from gate import CaptureGate


# ------------------------------------------------------------------ sources
class FolderSource:
    def __init__(self, d):
        self.paths = sorted(glob.glob(os.path.join(d, "*.jp*g")) + glob.glob(os.path.join(d, "*.png")))
        self.i = 0
    def read(self):
        if self.i >= len(self.paths):
            return None
        f = cv2.imread(self.paths[self.i]); self.i += 1
        return f
    def ptz(self, **kw): return False
    def release(self): pass


class CamSource:
    def __init__(self, src, w=3840, h=2160):
        self.cap = cv2.VideoCapture(int(src) if str(src).isdigit() else src)
        if not self.cap.isOpened():
            raise SystemExit(f"cannot open camera source {src}")
        if str(src).isdigit():                       # UVC: ask for 4K MJPEG (Link 2 Pro supports it over USB 2.0)
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w); self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        for _ in range(5): self.cap.read()            # let auto-exposure settle
    def read(self):
        for _ in range(2): self.cap.grab()            # drop stale buffered frames
        ok, f = self.cap.read()
        return f if ok else None
    def ptz(self, pan=None, tilt=None, zoom=None):
        """standard UVC PTZ; returns False if the camera ignores it (most fixed webcams)."""
        ok = False
        for prop, v in ((cv2.CAP_PROP_PAN, pan), (cv2.CAP_PROP_TILT, tilt), (cv2.CAP_PROP_ZOOM, zoom)):
            if v is not None:
                ok |= bool(self.cap.set(prop, v))
        return ok
    def get(self, prop): return self.cap.get(prop)
    def release(self): self.cap.release()


# ------------------------------------------------------------------ auto-framing (PTZ cameras)
def autoframe(src, steps=8, fill=0.85):
    """closed loop: find the screen, pan/tilt toward its centre, zoom until it fills `fill` of width.
    Units of UVC pan/tilt/zoom differ per camera; we step relatively and watch the result."""
    if not isinstance(src, CamSource):
        return False
    pan, tilt, zoom = (src.get(p) for p in (cv2.CAP_PROP_PAN, cv2.CAP_PROP_TILT, cv2.CAP_PROP_ZOOM))
    step_pt, moved = 3600.0, False                    # Link-style units: 3600 = 1 degree
    for _ in range(steps):
        f = src.read()
        q = sd.find_screen_quad(f) if f is not None else None
        if q is None:
            break
        H, W = f.shape[:2]
        cx, cy = q[:, 0].mean() / W - 0.5, q[:, 1].mean() / H - 0.5
        width_frac = (q[:, 0].max() - q[:, 0].min()) / W
        if abs(cx) < 0.04 and abs(cy) < 0.04 and width_frac > fill * 0.9:
            break
        pan += cx * 30 * step_pt; tilt -= cy * 20 * step_pt
        if abs(cx) < 0.1 and abs(cy) < 0.1 and width_frac < fill:
            zoom = zoom * min(fill / max(width_frac, 1e-3), 1.5)
        moved |= src.ptz(pan=pan, tilt=tilt, zoom=zoom)
        time.sleep(0.6)
    return moved


# ------------------------------------------------------------------ main loop
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="0")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between checks")
    ap.add_argument("--heartbeat", type=float, default=15.0, help="save one frame every N s even if unchanged")
    ap.add_argument("--burst", type=int, default=3, help="frames saved after a change")
    ap.add_argument("--duration", type=float, default=0, help="stop after N s (0 = until Ctrl+C)")
    ap.add_argument("--autoframe", action="store_true", help="aim + zoom a PTZ camera (Link 2 Pro) at the screen")
    ap.add_argument("--session", default=None)
    ap.add_argument("--replay-tick", type=float, default=2.0, help="seconds represented by one file in folder replay")
    a = ap.parse_args()

    replay = os.path.isdir(a.source)
    src = FolderSource(a.source) if replay else CamSource(a.source)
    session = a.session or "session_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    raw = os.path.join(session, "raw"); os.makedirs(raw, exist_ok=True)

    if a.autoframe:
        print("autoframe:", "PTZ applied" if autoframe(src) else "camera has no PTZ / screen not found; skipping")

    gate = CaptureGate(a.heartbeat, a.burst)
    n_saved = 0
    t0, tick = time.time(), 0
    try:
        while True:
            f = src.read()
            if f is None:
                break
            now = tick * a.replay_tick if replay else time.time() - t0
            if a.duration and now > a.duration:
                break
            saved, fc, changed = gate.consider(f, now)
            if saved:
                ts = f"{now:08.1f}".replace(".", "_")
                cv2.imwrite(os.path.join(raw, f"t{ts}.jpg"), fc, [cv2.IMWRITE_JPEG_QUALITY, 88])
                n_saved += 1
            tick += 1
            if not replay:
                time.sleep(max(0, a.interval - (time.time() - t0 - now)))
    except KeyboardInterrupt:
        pass
    finally:
        src.release()

    print(f"checked {tick} frames, saved {n_saved} ({100*n_saved/max(tick,1):.0f}%) -> {raw}")
    paths = sorted(glob.glob(os.path.join(raw, "*.jpg")))
    frames = [cv2.imread(p) for p in paths]; names = [os.path.basename(p) for p in paths]
    kept, segs, _ = sd.run(frames, names)
    idx = sd.export(kept, names, os.path.join(session, "slides_out"))
    print(f"{len(idx)} unique slides -> {session}/slides_out/ (slides.pptx, index.json)")


if __name__ == "__main__":
    main()
