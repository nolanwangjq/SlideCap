#!/usr/bin/env python3
"""Run the pipeline on a synthetic set and score it against ground truth."""
import sys, os, json, glob, time, cv2
sys.path.insert(0, os.path.dirname(__file__))
import slidededupe as sd

d = sys.argv[1]; out = sys.argv[2]
truth = json.load(open(os.path.join(d, "_truth.json")))
f2s = {f["frame"]: f["slide"] for f in truth["frames"]}
f2b = {f["frame"]: f["build"] for f in truth["frames"]}
paths = sorted(glob.glob(os.path.join(d, "*.jpg")))
frames = [cv2.imread(p) for p in paths]; names = [os.path.basename(p) for p in paths]
t0 = time.time()
kept, segs, quad = sd.run(frames, names)
dt = time.time() - t0
idx = sd.export(kept, names, out)
pred = [f2s[e["best_frame"]] for e in idx]
final_build = [f2b[e["best_frame"]] in (None, 5) for e in idx]
print("truth :", truth["unique_in_order"])
print("pred  :", pred)
print("exact order match:", pred == truth["unique_in_order"],
      "| animated slide kept at final state:", all(final_build),
      f"| {len(frames)} frames in {dt:.1f}s ({1000*dt/len(frames):.0f} ms/frame)")
