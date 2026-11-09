#!/usr/bin/env python3
"""Generate a fake 'lecture' captured by a cheap static camera every 2 s, with ground truth."""
import os, json, random, sys
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont

rng = random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
np.random.seed(rng.randint(0, 10**6))
OUT = sys.argv[1] if len(sys.argv) > 1 else "synthetic"
os.makedirs(OUT, exist_ok=True)
F = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf")
FB = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans-Bold.ttf")
WORDS = ("robot gripper force torque policy sim2real benchmark latency actuator encoder "
         "controller impedance MuJoCo dataset teleop reward contact friction compliance").split()


def slide(n, bullets_shown=None, theme=0):
    W, H = 1280, 720
    bg = [(255, 255, 255), (245, 245, 235), (25, 35, 60)][theme]
    fg = (20, 20, 20) if theme < 2 else (240, 240, 240)
    im = Image.new("RGB", (W, H), bg); d = ImageDraw.Draw(im)
    r = random.Random(n)
    d.rectangle([0, 0, W, 90], fill=[(40, 90, 170), (150, 40, 40), (90, 160, 90)][n % 3])
    d.text((40, 18), f"{n}. " + " ".join(r.choice(WORDS).title() for _ in range(3)), font=ImageFont.truetype(FB, 46), fill="white")
    bullets = [" ".join(r.choice(WORDS) for _ in range(r.randint(4, 7))) for _ in range(5)]
    k = len(bullets) if bullets_shown is None else bullets_shown
    for i, b in enumerate(bullets[:k]):
        d.text((70, 130 + i * 70), "• " + b, font=ImageFont.truetype(F, 38), fill=fg)
    if n % 4 == 0:  # a chart
        for j in range(6):
            h = r.randint(40, 200); d.rectangle([900 + j * 50, 650 - h, 935 + j * 50, 650], fill=(200, 120, 30))
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


# --- lecture script: (slide_id, build_steps or None). slide 4 is an animated build; presenter goes back 8 -> 6.
script = [(1, None), (2, None), (3, None), (4, 1), (4, 2), (4, 3), (4, 5), (5, None), (6, None),
          (7, None), (8, None), (6, None), (8, None), (9, None), (10, None), (11, None), (12, None)]
truth = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

CW, CH = 1600, 1200
quad = np.float32([[430, 260], [1230, 300], [1215, 760], [445, 780]]) + np.random.randn(4, 2).astype(np.float32) * 8
M = cv2.getPerspectiveTransform(np.float32([[0, 0], [1280, 0], [1280, 720], [0, 720]]), quad)

frames, gt, prev = [], [], None
fi = 0
for si, (sid, b) in enumerate(script):
    theme = 2 if sid in (9, 10) else (1 if sid % 5 == 0 else 0)
    s = slide(sid, b, theme)
    dur = rng.randint(3, 8) if b is not None and b < 5 else rng.randint(10, 40)  # frames @ 2 s
    for t in range(dur):
        src = s
        if prev is not None and t == 0 and rng.random() < 0.7:        # fade transition caught mid-way
            src = cv2.addWeighted(prev, 0.5, s, 0.5, 0)
        room = np.full((CH, CW, 3), 35, np.uint8)
        scr = cv2.warpPerspective(src, M, (CW, CH))
        mask = cv2.warpPerspective(np.ones((720, 1280), np.uint8), M, (CW, CH)).astype(bool)
        gain = rng.uniform(0.75, 1.0)                                  # auto-exposure drift
        room[mask] = np.clip(scr[mask] * gain, 0, 255).astype(np.uint8)
        if rng.random() < 0.12:                                        # someone walks past
            cx = rng.randint(300, 1300)
            cv2.ellipse(room, (cx, 1100), (180, 420), 0, 0, 360, (20, 20, 25), -1)
            cv2.circle(room, (cx, 640), 110, (30, 28, 30), -1)
        k = rng.choice([1, 1, 3, 5])                                   # focus / hand-shake blur
        if k > 1: room = cv2.GaussianBlur(room, (k, k), 0)
        room = np.clip(room + np.random.randn(*room.shape) * 4, 0, 255).astype(np.uint8)
        name = f"f{fi:05d}.jpg"
        cv2.imwrite(os.path.join(OUT, name), room, [cv2.IMWRITE_JPEG_QUALITY, 80])
        gt.append(dict(frame=name, slide=sid, build=b)); fi += 1
    prev = s

json.dump(dict(unique_in_order=truth, frames=gt), open(os.path.join(OUT, "_truth.json"), "w"), indent=1)
print(f"wrote {fi} frames ({fi*2/60:.1f} min of lecture at 1 photo / 2 s) to {OUT}/")
