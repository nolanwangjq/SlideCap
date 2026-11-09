#!/usr/bin/env python3
"""Run original eval.py, verify unmodified baseline, then compare Web/CLI gate output."""

from pathlib import Path
import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import zipfile
import cv2
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prototype import slidededupe as sd
from prototype.gate import CaptureGate
from backend import app as server


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--output", type=Path, default=ROOT / ".test-data/regression")
    args = ap.parse_args()
    d = args.folder.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    evaluation = subprocess.run(
        [sys.executable, str(ROOT / "prototype/eval.py"), str(d), str(out / "eval")],
        capture_output=True,
        text=True,
        check=True,
    )
    print(evaluation.stdout, flush=True)
    paths = sorted(d.glob("*.jpg"))
    names = [p.name for p in paths]
    frames = server.DiskFrames(paths)
    # The private handoff baseline is optional in public source releases.
    if (ROOT / "slidecap-handoff.zip").exists():
        # Load untouched reference directly from the supplied handoff archive.
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.py"
            with zipfile.ZipFile(ROOT / "slidecap-handoff.zip") as z:
                baseline.write_bytes(z.read("slidecap-handoff/prototype/slidededupe.py"))
            spec = importlib.util.spec_from_file_location("baseline", baseline)
            orig = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(orig)
            old, segs, q = orig.run(frames, names)
            baseline_frames = [s["frame"] for s in old]
            del old
        new, newsegs, _ = sd.run(frames, names, work_dir=str(out))
        assert (
            baseline_frames == [s["frame"] for s in new] and segs == newsegs
        ), "Offline baseline changed"
        del new
    server.DATA = out / "sessions"
    server.DATA.mkdir(exist_ok=True)
    server.RUNTIMES.clear()
    with TestClient(server.app) as c:
        sid = c.post(
            "/api/sessions",
            json={
                "title": "合成回放回归",
                "replay": True,
                "width": 1600,
                "height": 1200,
            },
        ).json()["id"]
        gate = CaptureGate()
        saved = []
        for i, p in enumerate(paths):
            img = cv2.imread(str(p))
            yes, crop, _ = gate.consider(img, i * 2)
            r = c.post(
                f"/api/sessions/{sid}/frames",
                files={"jpeg": (p.name, p.read_bytes(), "image/jpeg")},
                data={"timestamp": i * 2, "source": p.name},
            )
            assert r.status_code == 200, r.text
            assert r.json()["saved"] == yes
            if yes:
                saved.append(p.name)
                actual = server.RUNTIMES[sid].records[-1]["name"]
                # Same frame, crop and JPEG bytes as the CLI.
                expected = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 88])[
                    1
                ].tobytes()
                assert (server.DATA / sid / "raw" / actual).read_bytes() == expected
        c.post(f"/api/sessions/{sid}/finish")
        for _ in range(600):
            result = c.get(f"/api/sessions/{sid}/slides").json()
            if result["meta"]["state"] == "review":
                break
            assert result["meta"]["state"] != "error", result["meta"]
            time.sleep(0.1)
        else:
            raise AssertionError("Processing timeout")
        raw = sorted((server.DATA / sid / "raw").glob("*.jpg"))
        kept, _, _ = sd.run(
            server.DiskFrames(raw), [p.name for p in raw], work_dir=str(out)
        )
        assert [s["frame"] for s in result["slides"]] == [k["frame"] for k in kept]
        truth = json.loads((d / "_truth.json").read_text())
        f2s = {x["frame"]: x["slide"] for x in truth["frames"]}
        records = server.RUNTIMES[sid].records
        pred = [f2s[records[s["frame"]]["source"]] for s in result["slides"]]
        for fmt in ["pptx", "pdf", "zip"]:
            assert c.get(f"/api/sessions/{sid}/export?fmt={fmt}").status_code == 200
        report = {
            "offline_eval": evaluation.stdout.strip(),
            "baseline_frames_unchanged": True,
            "gate_decisions_and_jpeg_bytes_identical": True,
            "web_and_cli_selected_frames_identical": True,
            "input_frames": len(paths),
            "saved_frames": len(saved),
            "web_pred": pred,
            "truth": truth["unique_in_order"],
            "web_exact": pred == truth["unique_in_order"],
            "raw_bytes": result["meta"]["bytes"],
            "duration_seconds": result["meta"]["elapsed"],
            "four_hour_raw_projection_bytes": round(
                result["meta"]["bytes"] / max(1, result["meta"]["elapsed"]) * 14400
            ),
        }
        (out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        assert report["web_exact"], "Web replay differs from synthetic truth"


if __name__ == "__main__":
    main()
