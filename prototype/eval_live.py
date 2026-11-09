import sys, json, os, subprocess
d, sess = sys.argv[1], sys.argv[2]
subprocess.run([sys.executable, "live_capture.py", "--source", d, "--session", sess, "--heartbeat", "10"], check=True)
t = json.load(open(os.path.join(d, "_truth.json")))
idx = json.load(open(os.path.join(sess, "slides_out", "index.json")))
fr = t["frames"]
def slide_of(name):  # t00000123_0.jpg -> seconds -> tick
    sec = float(name[1:-4].replace("_", ".")); return fr[int(round(sec / 2))]["slide"]
pred = [slide_of(e["best_frame"]) for e in idx]
print("pred:", pred, "| exact:", pred == t["unique_in_order"])
