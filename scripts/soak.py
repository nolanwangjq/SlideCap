#!/usr/bin/env python3
"""HTTP endurance test; does not substitute for a real browser/camera soak."""

import argparse
import json
from pathlib import Path
import sys
import time
import httpx

ap = argparse.ArgumentParser()
ap.add_argument("folder", type=Path)
ap.add_argument("--seconds", type=float, default=1860)
ap.add_argument("--interval", type=float, default=1)
ap.add_argument("--url", default="http://127.0.0.1:8765")
ap.add_argument("--report", type=Path, default=Path("soak-report.json"))
a = ap.parse_args()
paths = sorted(a.folder.glob("*.jpg"))
if not paths:
    sys.exit("No JPG frames")
with httpx.Client(base_url=a.url, timeout=30, trust_env=False) as c:
    r = c.post(
        "/api/sessions", json={"title": "HTTP 持续采集测试", "interval": a.interval}
    )
    r.raise_for_status()
    sid = r.json()["id"]
    start = time.monotonic()
    count = 0
    errors = []
    latencies = []
    gaps = []
    previous = start
    while time.monotonic() - start < a.seconds:
        due = start + count * a.interval
        time.sleep(max(0, due - time.monotonic()))
        now = time.monotonic()
        gaps.append(now - previous)
        previous = now
        # Repeat each source frame twice: the fixture represents a 2-second cadence.
        p = paths[(count // 2) % len(paths)]
        try:
            r = c.post(
                f"/api/sessions/{sid}/frames",
                files={"jpeg": (p.name, p.read_bytes(), "image/jpeg")},
                data={"timestamp": now - start, "source": p.name},
            )
            r.raise_for_status()
            latencies.append(time.monotonic() - now)
        except Exception as e:
            errors.append({"tick": count, "error": str(e)})
        count += 1
        if count % 60 == 0:
            print(
                f"{count} frames / {time.monotonic()-start:.0f}s / errors={len(errors)}",
                flush=True,
            )
    c.post(f"/api/sessions/{sid}/finish").raise_for_status()
    for _ in range(1200):
        result = c.get(f"/api/sessions/{sid}/slides").json()
        if result["meta"]["state"] in ("review", "error"):
            break
        time.sleep(0.1)
    report = {
        "kind": "HTTP only; no browser or camera",
        "session": sid,
        "seconds": time.monotonic() - start,
        "sent": count,
        "errors": errors,
        "max_upload_seconds": max(latencies, default=0),
        "max_tick_gap": max(gaps, default=0),
        "meta": result["meta"],
    }
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors or result["meta"]["state"] != "review":
        sys.exit(1)
