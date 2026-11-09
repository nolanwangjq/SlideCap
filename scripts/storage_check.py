#!/usr/bin/env python3
"""Accelerated four-hour fixture, using the shared gate at 1-second ticks."""

import argparse
import json
from pathlib import Path
import sys
import time
import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prototype.gate import CaptureGate
from prototype import slidededupe as sd
from backend.app import DiskFrames

ap = argparse.ArgumentParser()
ap.add_argument("folder", type=Path)
ap.add_argument("--output", type=Path, default=Path(".test-data/four-hour"))
a = ap.parse_args()
raw = a.output / "raw"
raw.mkdir(parents=True, exist_ok=True)
paths = sorted(a.folder.glob("*.jpg"))
if not paths:
    sys.exit("No JPG frames")
gate = CaptureGate()
start = time.monotonic()
saved = 0
# One source photo covers two seconds, matching make_synthetic's lecture timing.
for tick in range(14400):
    frame = cv2.imread(str(paths[(tick // 2) % len(paths)]))
    yes, crop, _ = gate.consider(frame, tick)
    if yes:
        cv2.imwrite(str(raw / f"t{tick:08}.jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
        saved += 1
    if tick and tick % 1800 == 0:
        print(f"{tick/3600:.1f} hours simulated; {saved} saved", flush=True)
files = sorted(raw.glob("*.jpg"))
names = [p.name for p in files]
kept, segs, _ = sd.run(DiskFrames(files), names, work_dir=str(a.output))
sd.export(kept, names, str(a.output / "slides_out"))
raw_bytes = sum(p.stat().st_size for p in files)
total = sum(p.stat().st_size for p in a.output.rglob("*") if p.is_file())
report = {
    "kind": "accelerated four-hour synthetic fixture, not real-time camera",
    "ticks": 14400,
    "interval_seconds": 1,
    "saved_frames": saved,
    "unique_slides": len(kept),
    "raw_bytes": raw_bytes,
    "total_bytes": total,
    "under_2GB": total < 2_000_000_000,
    "execution_seconds": round(time.monotonic() - start, 2),
}
(a.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
assert report["under_2GB"]
