#!/usr/bin/env python3
"""
slidededupe.py — offline slide extraction from a stream of interval photos.

Input : a folder of photos taken every N seconds by any camera (the device, a phone,
        an action cam in time-lapse mode). Ordered by filename (or EXIF/mtime).
Output: one clean, perspective-corrected image per unique slide, in order of first
        appearance, plus slides.pptx and index.json.

Pipeline
  1. Screen localisation  – find the bright projected quad, take a robust median over
                            frames (the camera is static), warp every frame to 16:9.
  2. Change segmentation  – compare each frame to the running segment reference with
                            SSIM on a small normalised thumbnail; a change only counts
                            when it persists for >= `persist` frames (kills people
                            walking past, fade transitions, flicker).
  3. Animation merge      – if segment k+1 contains all the "ink" of segment k plus more,
                            k was a build step (bullet appearing) -> keep only the final.
  4. Global de-dupe       – presenter jumps back to an earlier slide -> matched against
                            every kept slide with SSIM and dropped.
  5. Best-frame pick      – per segment, maximise sharpness and minimise occlusion
                            (distance to the segment's pixel-wise median).
"""
import argparse, json, os, glob
import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim

OUT_W, OUT_H = 1280, 720
THUMB = (160, 90)


# ---------------------------------------------------------------- 1. screen
def find_screen_quad(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (7, 7), 0)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.05 * g.size:
        return None
    hull = cv2.convexHull(c)
    for eps in (0.02, 0.03, 0.05, 0.08):
        approx = cv2.approxPolyDP(hull, eps * cv2.arcLength(hull, True), True)
        if len(approx) == 4:
            return order_quad(approx.reshape(4, 2).astype(np.float32))
    x, y, w, h = cv2.boundingRect(c)
    return np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])


def order_quad(p):
    s, d = p.sum(1), np.diff(p, axis=1).ravel()
    return np.float32([p[np.argmin(s)], p[np.argmin(d)], p[np.argmax(s)], p[np.argmax(d)]])


def robust_quad(imgs, sample=40):
    idx = np.linspace(0, len(imgs) - 1, min(sample, len(imgs))).astype(int)
    quads = [q for q in (find_screen_quad(imgs[i]) for i in idx) if q is not None]
    if not quads:
        return None
    return np.median(np.stack(quads), axis=0).astype(np.float32)


def warp(img, quad):
    if quad is None:
        return cv2.resize(img, (OUT_W, OUT_H), interpolation=cv2.INTER_AREA)
    dst = np.float32([[0, 0], [OUT_W, 0], [OUT_W, OUT_H], [0, OUT_H]])
    M = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(img, M, (OUT_W, OUT_H), flags=cv2.INTER_CUBIC)


# ---------------------------------------------------------------- features
def thumb(w):
    g = cv2.cvtColor(w, cv2.COLOR_BGR2GRAY)
    t = cv2.resize(g, THUMB, interpolation=cv2.INTER_AREA).astype(np.float32)
    return (t - t.mean()) / (t.std() + 1e-6)          # exposure-invariant


def sim(a, b):
    data_range = float(max(a.max(), b.max()) - min(a.min(), b.min()))
    return 1.0 if data_range == 0 else ssim(a, b, data_range=data_range)


def robust_sim(a, b, trim=0.25):
    """SSIM averaged over the best (1-trim) of the map: ignores a person blocking part of it."""
    data_range = float(max(a.max(), b.max()) - min(a.min(), b.min()))
    if data_range == 0:
        return 1.0
    _, m = ssim(a, b, data_range=data_range, full=True)
    v = np.sort(m.ravel())
    return float(v[int(trim * v.size):].mean())


def ink(w):
    """binary mask of 'content' (text/graphics) relative to local background."""
    g = cv2.cvtColor(cv2.resize(w, (320, 180), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    m = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 21, 12)
    return cv2.dilate(m, np.ones((3, 3), np.uint8)) > 0


def sharpness(w):
    return cv2.Laplacian(cv2.cvtColor(w, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()


# ---------------------------------------------------------------- 2. segment
def segment(thumbs, t_new=0.80, persist=2, min_len=2):
    """returns list of (start, end_exclusive) frame-index ranges"""
    segs, start = [], 0
    ref, pending = thumbs[0], 0
    for i in range(1, len(thumbs)):
        if sim(thumbs[i], ref) < t_new:
            pending += 1
            # the change must also be self-consistent (the new frames agree with each other)
            if pending >= persist and sim(thumbs[i], thumbs[i - 1]) > t_new:
                cut = i - pending + 1
                segs.append((start, cut))
                start, ref, pending = cut, thumbs[i], 0
        else:
            pending = 0
            ref = 0.8 * ref + 0.2 * thumbs[i]          # slow drift (exposure, laser pointer)
    segs.append((start, len(thumbs)))
    # absorb blips shorter than min_len frames (occluded first frame, flash, etc.)
    out = []
    for a, b in segs:
        if out and (b - a < min_len or out[-1][1] - out[-1][0] < min_len):
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


# ---------------------------------------------------------------- 5. best frame
def best_frame(warped, a, b):
    stack = np.stack([cv2.resize(w, (320, 180)) for w in (warped[i] for i in range(a, b))]).astype(np.float32)
    med = np.median(stack, axis=0)
    occl = np.array([np.mean(np.abs(s - med) > 40) for s in stack])        # 0 = clean
    dist = np.array([np.mean(np.abs(s - med)) / 255.0 for s in stack])     # fade/blend frames deviate
    sharp = np.array([sharpness(w) for w in (warped[i] for i in range(a, b))])
    sharp = sharp / (sharp.max() + 1e-6)
    score = sharp - 3.0 * occl - 10.0 * dist
    if b - a >= 4:                                   # boundary frames are where transitions live
        score[0] -= 10; score[-1] -= 10
    return a + int(np.argmax(score)), float(occl.min())


# ---------------------------------------------------------------- 3. builds
def _g(w):
    g = cv2.cvtColor(cv2.resize(w, (960, 540), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32)
    return (g - g.mean()) / (g.std() + 1e-6)


def is_build_step(prev, cur):
    """prev is an earlier build state of cur iff everything prev shows is unchanged in cur
    and cur only ADDS content. Same-template different slides fail the first test."""
    pi, ci = prev["ink"], cur["ink"]
    if ci.sum() < 1.05 * pi.sum():
        return False
    gp, gc = _g(prev["img"]), _g(cur["img"])
    # compare at 960x540 so different words on the same text rows do NOT correlate
    region = cv2.resize(pi.astype(np.uint8), (960, 540), interpolation=cv2.INTER_NEAREST) > 0
    # local correlation of the prev content inside cur
    a, b = gp[region], gc[region]
    corr = float(np.corrcoef(a, b)[0, 1])
    cover = (pi & ci).sum() / max(pi.sum(), 1)
    return corr > 0.85 and cover > 0.9


# ---------------------------------------------------------------- main
def run(frames_bgr, names, keep_builds=False, t_dup=0.85, quad=None, forced_cuts=None, work_dir=None):
    if not len(frames_bgr):
        return [], [], quad
    quad = robust_quad(frames_bgr) if quad is None else np.asarray(quad, dtype=np.float32)
    if work_dir:
        # Lazy warps bound RAM and avoid multi-gigabyte intermediate image files.
        from functools import lru_cache
        class WarpedFrames:
            def __len__(self): return len(frames_bgr)
            @lru_cache(maxsize=8)
            def __getitem__(self, i): return warp(frames_bgr[i], quad)
        warped = WarpedFrames()
    else:
        warped = [warp(f, quad) for f in frames_bgr]
    thumbs = [thumb(warped[i]) for i in range(len(warped))]
    segs = segment(thumbs)

    forced_cuts = set(forced_cuts or [])
    if forced_cuts:
        bounds = sorted({0, len(frames_bgr), *forced_cuts, *(a for a, b in segs)})
        segs = list(zip(bounds, bounds[1:]))
    cands = []
    for a, b in segs:
        k, occ = best_frame(warped, a, b)
        med = np.median(np.stack(thumbs[a:b]), axis=0)              # transient occluders vanish
        cands.append(dict(frame=k, start=a, end=b, img=warped[k].copy(), thumb=med, ink=ink(warped[k]),
                          candidates=list(range(a, b)), forced=a in forced_cuts))

    # adjacent segments that are the same slide (split by a long occlusion) -> merge
    m = []
    for c in cands:
        if m and not c["forced"] and sim(m[-1]["thumb"], c["thumb"]) > t_dup:
            p = m[-1]
            if sharpness(c["img"]) > sharpness(p["img"]):
                p["img"], p["frame"], p["ink"] = c["img"], c["frame"], c["ink"]
            p["candidates"] += c["candidates"]
            p["end"] = c["end"]
            continue
        m.append(c)
    cands = m

    # 3. animation merge: k is a build-step of k+1 if k's ink is (almost) contained in k+1
    if not keep_builds:
        merged = []
        for c in cands:
            if merged:
                p = merged[-1]
                if not c["forced"] and not p["forced"] and is_build_step(p, c):
                    c["candidates"] = p["candidates"] + c["candidates"]
                    c["start"] = p["start"]; c["builds"] = p.get("builds", 0) + 1
                    merged[-1] = c
                    continue
            merged.append(c)
        cands = merged

    # 4. global de-dupe (revisited slides) – keep first appearance, upgrade to sharper copy
    kept = []
    for c in cands:
        dup = None if c["forced"] else next((k for k in kept if sim(k["thumb"], c["thumb"]) > t_dup), None)
        if dup is None:
            kept.append(c)
        else:
            dup["candidates"] += c["candidates"]
            dup.setdefault("revisits", []).append(names[c["start"]])
            if sharpness(c["img"]) > sharpness(dup["img"]):
                dup["img"], dup["frame"] = c["img"], c["frame"]
    return kept, segs, quad


def export(kept, names, out):
    os.makedirs(out, exist_ok=True)
    index = []
    for i, k in enumerate(kept, 1):
        p = os.path.join(out, f"slide_{i:03d}.png")
        cv2.imwrite(p, k["img"])
        index.append(dict(slide=i, file=os.path.basename(p), first_seen=names[k["start"]],
                          best_frame=names[k["frame"]], build_steps=k.get("builds", 0),
                          revisited_at=k.get("revisits", [])))
    with open(os.path.join(out, "index.json"), "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    try:
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation(); prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
        for e in index:
            s = prs.slides.add_slide(prs.slide_layouts[6])
            s.shapes.add_picture(os.path.join(out, e["file"]), 0, 0, prs.slide_width, prs.slide_height)
        prs.save(os.path.join(out, "slides.pptx"))
    except ImportError:
        pass
    return index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("frames_dir"); ap.add_argument("-o", "--out", default="slides_out")
    ap.add_argument("--keep-builds", action="store_true")
    a = ap.parse_args()
    paths = sorted(glob.glob(os.path.join(a.frames_dir, "*.jp*g")) + glob.glob(os.path.join(a.frames_dir, "*.png")))
    frames = [cv2.imread(p) for p in paths]
    names = [os.path.basename(p) for p in paths]
    kept, segs, _ = run(frames, names, keep_builds=a.keep_builds)
    idx = export(kept, names, a.out)
    print(f"{len(paths)} frames -> {len(segs)} segments -> {len(idx)} unique slides -> {a.out}/")


if __name__ == "__main__":
    main()
