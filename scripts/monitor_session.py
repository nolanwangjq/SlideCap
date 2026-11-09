#!/usr/bin/env python3
"""Observe an actual browser-owned capture without sending frames or changing state."""

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import time

ap = argparse.ArgumentParser()
ap.add_argument("session", type=Path)
ap.add_argument("--seconds", type=float, default=1860)
ap.add_argument("--output", type=Path, default=Path(".test-data/camera-soak.json"))
a = ap.parse_args()
a.output.parent.mkdir(parents=True, exist_ok=True)
observations = []
last_checked = -1
report_at = 0
while True:
    m = json.loads((a.session / "meta.json").read_text())
    if m["checked"] != last_checked:
        observations.append(
            {
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "checked": m["checked"],
                "elapsed": m["elapsed"],
                "saved": m["saved"],
                "bytes": m["bytes"],
            }
        )
        last_checked = m["checked"]
    if m["elapsed"] >= report_at:
        print(
            f"{m['elapsed']:.1f}s | {m['checked']} checks | {m['saved']} saved | {m['bytes']/1e6:.2f} MB | gaps: {len(m['gaps'])}",
            flush=True,
        )
        report_at = m["elapsed"] + 60
        a.output.write_text(
            json.dumps(
                {"meta": m, "observations": observations}, ensure_ascii=False, indent=2
            )
        )
    if m["elapsed"] >= a.seconds or m["state"] != "capturing":
        break
    time.sleep(1)
a.output.write_text(
    json.dumps(
        {
            "meta": m,
            "observations": observations,
            "duration_reached": m["elapsed"] >= a.seconds,
        },
        ensure_ascii=False,
        indent=2,
    )
)
print("Monitoring complete; camera is still controlled by the browser.", flush=True)
