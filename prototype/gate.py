"""The original live_capture gate, shared by CLI and HTTP ingestion."""
import cv2
try:
    from . import slidededupe as sd
except ImportError:
    import slidededupe as sd


class CaptureGate:
    def __init__(self, heartbeat=15.0, burst=3, quad=None):
        self.heartbeat, self.burst = heartbeat, burst
        self.quad = quad
        self.detected_quad = quad
        self.last_thumb, self.last_save_t = None, -1e9
        self.burst_left, self.tick, self.crop = 0, 0, None

    def consider(self, frame, now, force=False, exposure_settling=False):
        if self.crop is None and self.tick % 5 == 0:
            q = self.quad if self.quad is not None else sd.find_screen_quad(frame)
            if q is not None:
                self.detected_quad = q
                h, w = frame.shape[:2]
                x0, y0 = q.min(0); x1, y1 = q.max(0)
                dx, dy = (x1 - x0) * .06, (y1 - y0) * .06
                self.crop = (int(max(0, y0-dy)), int(min(h, y1+dy)),
                             int(max(0, x0-dx)), int(min(w, x1+dx)))
        c = self.crop
        cropped = frame[c[0]:c[1], c[2]:c[3]] if c else frame
        th = sd.thumb(cv2.resize(cropped, (640, 360)))
        if exposure_settling:
            self.last_thumb = th
            self.burst_left = 0
            self.tick += 1
            if force:
                self.last_save_t = now
            return bool(force), cropped, False
        changed = self.last_thumb is None or sd.sim(th, self.last_thumb) < .90
        if changed or force:
            self.burst_left = self.burst
        saved = self.burst_left > 0 or now-self.last_save_t >= self.heartbeat
        if saved:
            self.last_thumb, self.last_save_t = th, now
            self.burst_left = max(0, self.burst_left-1)
        self.tick += 1
        return saved, cropped, changed
