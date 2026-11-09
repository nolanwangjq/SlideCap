// A dedicated worker owns cadence, JPEG encoding and upload. The main thread only
// obtains a fresh ImageBitmap from the camera; no video is ever recorded.
let timer: ReturnType<typeof setInterval> | undefined;
let busy = false;
let pendingForce = false;
let config: { sid: string; interval: number; start: number } | null = null;
self.onmessage = async ({ data }) => {
  if (data.type === "start") {
    if (timer) clearInterval(timer);
    config = data;
    busy = false;
    timer = setInterval(() => {
      if (!busy && config) {
        busy = true;
        self.postMessage({ type: "grab", force: pendingForce });
        pendingForce = false;
      }
    }, data.interval * 1000);
  } else if (data.type === "stop") {
    if (timer) clearInterval(timer);
    timer = undefined;
    config = null;
    pendingForce = false;
  } else if (data.type === "force") {
    if (!config) return;
    if (busy) pendingForce = true;
    else {
      busy = true;
      self.postMessage({ type: "grab", force: true });
    }
  } else if (data.type === "failed") {
    busy = false;
  } else if (data.type === "frame") {
    const bitmap: ImageBitmap = data.bitmap;
    const active = config;
    try {
      if (!active) return;
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
      canvas.getContext("2d")!.drawImage(bitmap, 0, 0);
      const blob = await canvas.convertToBlob({
        type: "image/jpeg",
        quality: 0.94,
      });
      const form = new FormData();
      form.append("jpeg", blob, "frame.jpg");
      form.append("timestamp", String((Date.now() - active.start) / 1000));
      form.append("force", String(data.force));
      const response = await fetch(`/api/sessions/${active.sid}/frames`, {
        method: "POST",
        body: form,
        signal: AbortSignal.timeout(20000),
      });
      if (!response.ok)
        throw new Error((await response.json()).detail || "上传失败");
      self.postMessage({ type: "uploaded", state: await response.json() });
    } catch (e) {
      self.postMessage({ type: "error", message: String(e) });
    } finally {
      bitmap.close();
      busy = false;
      self.postMessage({ type: "idle" });
    }
  }
};
