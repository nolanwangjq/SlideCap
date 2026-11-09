import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Camera,
  ChevronRight,
  Sun,
  Moon,
  SlidersHorizontal,
  ArrowRight,
  ScanLine,
  History,
  X,
  Check,
  Pause,
  Play,
  Square,
  EyeOff,
  Eye,
  Download,
  Trash2,
  Images,
  GripVertical,
  Layers,
  ChevronLeft,
  AlertCircle,
  RefreshCw,
  Video,
  Plus,
} from "lucide-react";
import "./style.css";
import { useExposure } from "./exposure";

type Quad = [number, number][];
type Slide = {
  id: string;
  image: string;
  time: number;
  file?: string;
  frame?: number;
  candidates?: number[];
  builds?: number;
  revisits?: string[];
  possible_duplicate?: boolean;
  forced?: boolean;
};
type Meta = {
  id: string;
  title: string;
  state: string;
  created_at: string;
  elapsed: number;
  bytes: number;
  saved: number;
  checked: number;
  count: number;
  progress: number;
  processing_label?: string;
  error?: string;
  revision: number;
  last_detected?: string;
  cover?: string;
  gaps?: { at: number; seconds: number }[];
};
type Snapshot = {
  meta: Meta;
  slides: Slide[];
  status: { warning?: string; gap?: number };
};
const clock = (s: number) =>
  [Math.floor(s / 3600), Math.floor((s % 3600) / 60), Math.floor(s % 60)]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
async function api(path: string, options: RequestInit = {}) {
  const r = await fetch("/api" + path, options);
  if (!r.ok) {
    let message;
    try {
      message = (await r.json()).detail;
    } catch {
      message = r.statusText;
    }
    throw new Error(
      typeof message === "string" ? message : JSON.stringify(message),
    );
  }
  return r.json();
}
const json = (value: unknown, method = "POST"): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});

function App() {
  const [phase, setPhase] = useState<"setup" | "capture" | "review">("setup");
  const [theme, setTheme] = useState(
    localStorage.getItem("slidecap-theme") || "dark",
  );
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]),
    [device, setDevice] = useState("");
  const [stream, setStream] = useState<MediaStream | null>(null),
    [settings, setSettings] = useState<MediaTrackSettings>({});
  const [caps, setCaps] = useState<Record<string, any>>({});
  const [quad, setQuad] = useState<Quad | null>(null),
    [manual, setManual] = useState(false);
  const [advanced, setAdvanced] = useState(true),
    [title, setTitle] = useState(""),
    [interval, setIntervalValue] = useState(1);
  const [meta, setMeta] = useState<Meta | null>(null),
    [slides, setSlides] = useState<Slide[]>([]);
  const [error, setError] = useState(""),
    [warning, setWarning] = useState(""),
    [hidden, setHidden] = useState(document.hidden);
  const [history, setHistory] = useState<Meta[]>([]),
    [historyOpen, setHistoryOpen] = useState(false);
  const [focus, setFocus] = useState(false),
    [paused, setPaused] = useState(false),
    [finishAsk, setFinishAsk] = useState(false);
  const [selection, setSelection] = useState<string[]>([]),
    [candidate, setCandidate] = useState<Slide | null>(null),
    [viewer, setViewer] = useState<number | null>(null);
  const [busy, setBusy] = useState(false),
    [cameraBusy, setCameraBusy] = useState(false),
    [replayImage, setReplayImage] = useState("");
  const [replaying, setReplaying] = useState(false),
    [replayProgress, setReplayProgress] = useState({ done: 0, total: 0 });
  const [now, setNow] = useState(Date.now()),
    [framesInfo, setFramesInfo] = useState<{ time: number; source: string }[]>(
      [],
    );
  const [lockModes, setLockModes] = useState<Record<string, boolean>>({});
  const video = useRef<HTMLVideoElement>(null),
    overlay = useRef<SVGSVGElement>(null);
  const streamRef = useRef<MediaStream | null>(null),
    quadRef = useRef<Quad | null>(null),
    manualRef = useRef(false),
    phaseRef = useRef(phase),
    metaRef = useRef<Meta | null>(null);
  const worker = useRef<Worker | null>(null),
    socket = useRef<WebSocket | null>(null),
    wake = useRef<any>(null),
    started = useRef(0);
  const active = useRef(false),
    uploadBusy = useRef(false),
    replayActive = useRef(false),
    replayPause = useRef(false),
    replayAbort = useRef(false),
    replayForce = useRef(false);
  const drag = useRef<number | null>(null),
    dragCard = useRef<string | null>(null),
    loadingReview = useRef(false),
    mounted = useRef(true);
  const exposureAck = useRef<{ id: string; resolve: () => void } | null>(null);
  async function changeExposure(values: Record<string, unknown>) {
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track) throw new Error("相机未连接");
    await drain();
    await track.applyConstraints({ advanced: [values] } as any);
    setSettings(track.getSettings());
    if (active.current) {
      const id = crypto.randomUUID();
      await new Promise<void>((resolve, reject) => {
        const timeout = window.setTimeout(() => {
          reject(new Error("曝光已改变，但后端未确认；请恢复实时连接后重新设置曝光。"));
        }, 5000);
        exposureAck.current = { id, resolve: () => { clearTimeout(timeout); resolve(); } };
        if (socket.current?.readyState === WebSocket.OPEN)
          socket.current.send(JSON.stringify({ type: "exposure_changed", id, values }));
      });
    }
  }
  const exposure = useExposure(stream, video, quad, phase !== "review" && !replaying, changeExposure);
  const width = settings.width || 1600,
    height = settings.height || 900;
  const is4K =
    Math.min(width, height) >= 2160 && Math.max(width, height) >= 3840;
  const screenWidth = quad
    ? Math.max(
        Math.hypot(quad[1][0] - quad[0][0], quad[1][1] - quad[0][1]),
        Math.hypot(quad[2][0] - quad[3][0], quad[2][1] - quad[3][1]),
      )
    : 0;
  const textPx = screenWidth * 0.027;
  const readability = textPx >= 16 ? "清晰" : textPx >= 10 ? "勉强" : "看不清";
  const advice = !quad
    ? "把镜头对准投影屏，或拖动四角手动选择。"
    : textPx >= 16
      ? "屏幕文字大小合适，可以开始捕捉。"
      : textPx >= 10
        ? "放大一点，或往前坐几排。"
        : "往前坐几排；远距离拍摄建议换 4K 相机。";
  const guide: Quad = quad || [
    [width * 0.15, height * 0.18],
    [width * 0.85, height * 0.18],
    [width * 0.85, height * 0.82],
    [width * 0.15, height * 0.82],
  ];
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("slidecap-theme", theme);
  }, [theme]);
  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);
  useEffect(() => {
    quadRef.current = quad;
    manualRef.current = manual;
  }, [quad, manual]);
  useEffect(() => {
    metaRef.current = meta;
  }, [meta]);
  useEffect(() => {
    if (video.current && stream) {
      video.current.srcObject = stream;
      video.current.play().catch(() => {});
    }
  }, [stream, phase]);
  async function listDevices() {
    if (navigator.mediaDevices) {
      const d = await navigator.mediaDevices.enumerateDevices();
      setDevices(d.filter((x) => x.kind === "videoinput"));
    }
  }
  async function releaseWake() {
    if (wake.current) {
      await wake.current.release().catch(() => {});
      wake.current = null;
    }
  }
  async function acquireWake() {
    if ("wakeLock" in navigator && !document.hidden) {
      try {
        wake.current = await (navigator as any).wakeLock.request("screen");
      } catch {
        setWarning("未能保持屏幕唤醒，请检查系统休眠设置。");
      }
    }
  }
  useEffect(() => {
    listDevices().catch(() => {});
    api("/sessions")
      .then(setHistory)
      .catch((e) => setError(e.message));
    const visibility = () => {
      setHidden(document.hidden);
      if (!document.hidden && active.current) acquireWake();
    };
    const before = (e: BeforeUnloadEvent) => {
      if (active.current || replayActive.current) {
        e.preventDefault();
        e.returnValue = "正在捕捉";
      }
    };
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("beforeunload", before);
    navigator.mediaDevices?.addEventListener("devicechange", listDevices);
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      mounted.current = false;
      clearInterval(t);
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("beforeunload", before);
      navigator.mediaDevices?.removeEventListener("devicechange", listDevices);
      streamRef.current?.getTracks().forEach((t) => t.stop());
      worker.current?.terminate();
      socket.current?.close();
      releaseWake();
    };
  }, []);
  async function openCamera(id = "") {
    setCameraBusy(true);
    setError("");
    try {
      if (!navigator.mediaDevices)
        throw new Error("请使用 localhost 地址与支持摄像头的浏览器。");
      streamRef.current?.getTracks().forEach((t) => t.stop());
      const constraints: any = {
        width: { ideal: 3840 },
        height: { ideal: 2160 },
        ...(id ? { deviceId: { exact: id } } : {}),
        pan: true,
        tilt: true,
        zoom: true,
      };
      let s: MediaStream;
      try {
        s = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: constraints,
        });
      } catch (e) {
        if ((e as DOMException).name === "OverconstrainedError") {
          delete constraints.pan;
          delete constraints.tilt;
          delete constraints.zoom;
          s = await navigator.mediaDevices.getUserMedia({
            audio: false,
            video: constraints,
          });
        } else throw e;
      }
      streamRef.current = s;
      setStream(s);
      const track = s.getVideoTracks()[0];
      setSettings(track.getSettings());
      setDevice(track.getSettings().deviceId || id);
      setCaps(track.getCapabilities?.() || {});
      setLockModes({});
      track.addEventListener("ended", () =>
        setError("相机已断开。请重新连接相机，或结束并整理已保存的照片。"),
      );
      track.addEventListener("mute", () =>
        setWarning("相机暂时没有画面，请检查连接。"),
      );
      track.addEventListener("unmute", () => setWarning(""));
      setQuad(null);
      setManual(false);
      quadRef.current = null;
      manualRef.current = false;
      await listDevices();
    } catch (e) {
      setStream(null);
      streamRef.current = null;
      setError(
        (e as DOMException).name === "NotAllowedError"
          ? "相机权限未开启。请在浏览器地址栏允许访问摄像头，然后重试。"
          : `无法打开相机：${(e as Error).message}`,
      );
    } finally {
      setCameraBusy(false);
    }
  }
  async function grab(): Promise<ImageBitmap> {
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track || track.readyState !== "live") throw new Error("相机未连接");
    if ((window as any).ImageCapture) {
      return new (window as any).ImageCapture(track).grabFrame();
    }
    if (!video.current || video.current.readyState < 2)
      throw new Error("相机画面尚未就绪");
    return createImageBitmap(video.current);
  }
  async function jpeg() {
    const bitmap = await grab();
    try {
      const c = new OffscreenCanvas(bitmap.width, bitmap.height);
      c.getContext("2d")!.drawImage(bitmap, 0, 0);
      return await c.convertToBlob({ type: "image/jpeg", quality: 0.94 });
    } finally {
      bitmap.close();
    }
  }
  useEffect(() => {
    if (!stream || phase !== "setup" || exposure.confirmed) return;
    let cancelled = false,
      inFlight = false;
    const detect = async () => {
      if (manualRef.current || inFlight) return;
      inFlight = true;
      try {
        const body = new FormData();
        body.append("jpeg", await jpeg());
        const d = await api("/detect-screen", { method: "POST", body });
        if (!cancelled && !manualRef.current) setQuad(d.quad);
      } catch {
      } finally {
        inFlight = false;
      }
    };
    const t = window.setInterval(detect, 2500);
    detect();
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [stream, phase, exposure.confirmed]);
  async function constrain(key: string, value: unknown) {
    try {
      await streamRef.current
        ?.getVideoTracks()[0]
        .applyConstraints({ advanced: [{ [key]: value }] } as any);
      setSettings(streamRef.current?.getVideoTracks()[0].getSettings() || {});
      if (key.endsWith("Mode"))
        setLockModes((p) => ({ ...p, [key]: value === "manual" }));
    } catch (e) {
      setError(`相机未接受此设置：${(e as Error).message}`);
    }
  }
  function moveCorner(e: React.PointerEvent<SVGSVGElement>) {
    if (drag.current === null || !overlay.current) return;
    const rect = overlay.current.getBoundingClientRect();
    const points = (quadRef.current || guide).map(
      (p) => [...p] as [number, number],
    );
    points[drag.current] = [
      Math.max(
        0,
        Math.min(width, ((e.clientX - rect.left) / rect.width) * width),
      ),
      Math.max(
        0,
        Math.min(height, ((e.clientY - rect.top) / rect.height) * height),
      ),
    ];
    quadRef.current = points;
    manualRef.current = true;
    setQuad(points);
    setManual(true);
  }
  async function loadReview(sid: string) {
    if (loadingReview.current) return;
    loadingReview.current = true;
    try {
      const d = await api(`/sessions/${sid}/slides`);
      setMeta(d.meta);
      metaRef.current = d.meta;
      setSlides(d.slides);
      setFramesInfo(d.frames);
      setPhase("review");
      setSelection([]);
      setPaused(false);
      setHistoryOpen(false);
      api("/sessions").then(setHistory);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      loadingReview.current = false;
    }
  }
  function applyState(data: Snapshot) {
    setMeta(data.meta);
    metaRef.current = data.meta;
    if (data.meta.state === "review") {
      if (phaseRef.current !== "review") loadReview(data.meta.id);
    } else {
      setSlides(data.slides);
      if (data.meta.error) setError(data.meta.error);
    }
    setWarning(data.status.warning || "");
  }
  function connect(sid: string) {
    socket.current?.close();
    let reconnect: ReturnType<typeof setTimeout>;
    const attach = () => {
      if (metaRef.current?.id !== sid || !mounted.current) return;
      const ws = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/sessions/${sid}/events`,
      );
      socket.current = ws;
      ws.onmessage = (e) => {
        const message = JSON.parse(e.data);
        if (message.type === "exposure_ack") {
          const pending = exposureAck.current;
          if (pending && message.id === pending.id) {
            pending.resolve(); exposureAck.current = null;
          }
        } else applyState(message);
      };
      ws.onopen = () => {
        setError((e) => (e.startsWith("实时连接") ? "" : e));
        ws.send(JSON.stringify({ type: "exposure_changed", id: exposureAck.current?.id || crypto.randomUUID() }));
      };
      ws.onclose = () => {
        if (
          socket.current === ws &&
          mounted.current &&
          metaRef.current?.id === sid &&
          phaseRef.current !== "setup"
        ) {
          reconnect = setTimeout(attach, 2000);
        }
      };
      ws.onerror = () =>
        setError("实时连接暂时中断，正在重连；已保存的照片仍在本机。");
    };
    clearTimeout(reconnect!);
    attach();
  }
  async function create(replay = false, w = width, h = height) {
    const m = await api(
      "/sessions",
      json({
        title,
        camera: replay
          ? "照片回放"
          : streamRef.current?.getVideoTracks()[0].label,
        width: w,
        height: h,
        interval,
        quad: replay ? null : quadRef.current,
        replay,
      }),
    );
    setMeta(m);
    metaRef.current = m;
    setSlides([]);
    setPhase("capture");
    phaseRef.current = "capture";
    setPaused(false);
    setSelection([]);
    setError("");
    setWarning("");
    started.current = Date.now();
    connect(m.id);
    return m;
  }
  function startWorker(sid: string) {
    worker.current?.terminate();
    const w = new Worker(new URL("./capture.worker.ts", import.meta.url), {
      type: "module",
    });
    worker.current = w;
    w.onmessage = async ({ data }) => {
      if (data.type === "grab") {
        if (exposure.changing.current || exposureAck.current) {
          w.postMessage({ type: "failed" }); return;
        }
        uploadBusy.current = true;
        try {
          const bitmap = await grab();
          w.postMessage({ type: "frame", bitmap, force: data.force }, [bitmap]);
        } catch (e) {
          uploadBusy.current = false;
          w.postMessage({ type: "failed" });
          setError((e as Error).message);
        }
      } else if (data.type === "uploaded") {
        applyState(data.state);
        setError((e) => (e.startsWith("照片上传失败") ? "" : e));
      } else if (data.type === "error") {
        setError(`照片上传失败：${data.message}`);
      } else if (data.type === "idle") {
        uploadBusy.current = false;
      }
    };
    w.postMessage({ type: "start", sid, interval, start: started.current });
  }
  async function start() {
    if (!stream || busy) return;
    setBusy(true);
    try {
      const m = await create();
      active.current = true;
      await acquireWake();
      startWorker(m.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function drain() {
    while (uploadBusy.current) await new Promise((r) => setTimeout(r, 50));
  }
  async function togglePause() {
    if (!meta || busy) return;
    setBusy(true);
    try {
      const value = !paused;
      if (replayActive.current) {
        replayPause.current = value;
        await drain();
      } else {
        worker.current?.postMessage({ type: "stop" });
        await drain();
      }
      await api(`/sessions/${meta.id}/pause`, json({ paused: value }));
      setPaused(value);
      if (!value && !replayActive.current) startWorker(meta.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function finish() {
    if (!meta) return;
    setFinishAsk(false);
    setBusy(true);
    try {
      replayAbort.current = true;
      worker.current?.postMessage({ type: "stop" });
      await drain();
      active.current = false;
      await releaseWake();
      const m = await api(`/sessions/${meta.id}/finish`, json({}));
      setMeta(m);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function manualShot() {
    if (paused || meta?.state !== "capturing") return;
    if (replayActive.current) replayForce.current = true;
    else worker.current?.postMessage({ type: "force" });
  }

  async function edit(action: string, ids: string[], frame?: number) {
    if (!meta || busy) return;
    setBusy(true);
    try {
      const d = await api(
        `/sessions/${meta.id}/slides`,
        json({ action, ids, frame, revision: meta.revision }, "PATCH"),
      );
      setSlides(d.slides);
      setMeta(d.meta);
      setSelection([]);
      setCandidate(null);
      setViewer(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function reorder(from: string, to: string) {
    const ids = slides.map((s) => s.id);
    const a = ids.indexOf(from),
      b = ids.indexOf(to);
    if (a < 0 || b < 0 || a === b) return;
    ids.splice(b, 0, ids.splice(a, 1)[0]);
    edit("reorder", ids);
  }
  async function download(fmt: string) {
    if (!meta) return;
    setBusy(true);
    try {
      const r = await fetch(`/api/sessions/${meta.id}/export?fmt=${fmt}`);
      if (!r.ok) throw new Error((await r.json()).detail);
      const blob = await r.blob(),
        url = URL.createObjectURL(blob),
        a = document.createElement("a");
      a.href = url;
      a.download = `${meta.title}.${fmt}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function reset() {
    socket.current?.close();
    socket.current = null;
    metaRef.current = null;
    setMeta(null);
    setPhase("setup");
    phaseRef.current = "setup";
    setSlides([]);
    setError("");
    setWarning("");
    setReplaying(false);
    setReplayImage("");
    setFocus(false);
    setSettings(streamRef.current?.getVideoTracks()[0].getSettings() || {});
    api("/sessions").then(setHistory);
  }
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (
        (e.target as HTMLElement).matches("input,select,textarea,button") ||
        e.altKey ||
        e.metaKey ||
        e.ctrlKey
      )
        return;
      if (e.code === "Escape") {
        setViewer(null);
        setCandidate(null);
        setFinishAsk(false);
        setHistoryOpen(false);
      }
      if (viewer !== null) {
        if (e.code === "ArrowRight")
          setViewer(Math.min(slides.length - 1, viewer + 1));
        if (e.code === "ArrowLeft") setViewer(Math.max(0, viewer - 1));
        return;
      }
      if (e.code === "Space") {
        e.preventDefault();
        if (phase === "setup") start();
        else if (
          phase === "capture" &&
          ["capturing", "paused"].includes(meta?.state || "")
        )
          togglePause();
      }
      if (e.code === "KeyS" && phase === "capture") manualShot();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });
  const processing = meta?.state === "processing";
  const recording =
    phase === "capture" && !processing && meta?.state !== "error";
  const elapsed =
    recording && !replaying && !paused && started.current
      ? Math.max(meta?.elapsed || 0, (now - started.current) / 1000)
      : meta?.elapsed || 0;
  const preview = (
    <div
      className={`preview ${!stream && !replayImage ? "empty-preview" : ""}`}
      style={{
        aspectRatio: `${width}/${height}`,
        maxWidth:
          stream || replayImage
            ? `${((phase === "capture" ? 300 : 560) * width) / height}px`
            : undefined,
        marginInline: "auto",
      }}
    >
      <video
        ref={video}
        autoPlay
        muted
        playsInline
        className={replayImage ? "off" : ""}
      />
      {stream && !replayImage && <canvas ref={exposure.zebra} className="zebra-overlay" aria-hidden="true" />}
      {replayImage && (
        <img className="replay-image" src={replayImage} alt="正在回放的照片" />
      )}
      {!stream && !replayImage && (
        <div className="camera-empty">
          <div className="camera-symbol">
            <Camera size={32} strokeWidth={1.3} />
          </div>
          <button
            className="primary"
            onClick={() => openCamera()}
            disabled={cameraBusy}
          >
            {cameraBusy ? "正在连接…" : "连接相机"}
            <ArrowRight size={16} />
          </button>
        </div>
      )}
      {stream && phase === "setup" && (
        <svg
          ref={overlay}
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          className="quad-overlay"
          onPointerMove={moveCorner}
          onPointerUp={() => (drag.current = null)}
          onPointerCancel={() => (drag.current = null)}
        >
          <polygon
            points={guide.map((p) => p.join(",")).join(" ")}
            className={quad ? "detected" : "guide"}
          />
          {guide.map(([x, y], i) => (
            <circle
              key={i}
              cx={x}
              cy={y}
              r={width * 0.009}
              tabIndex={0}
              role="slider"
              aria-label={`屏幕角点 ${i + 1}`}
              aria-valuetext={`${Math.round(x)}, ${Math.round(y)}`}
              onKeyDown={(e) => {
                const dx =
                    e.key === "ArrowRight" ? 5 : e.key === "ArrowLeft" ? -5 : 0,
                  dy = e.key === "ArrowDown" ? 5 : e.key === "ArrowUp" ? -5 : 0;
                if (dx || dy) {
                  e.preventDefault();
                  const q = guide.map((p) => [...p] as [number, number]);
                  q[i] = [
                    Math.max(0, Math.min(width, x + dx)),
                    Math.max(0, Math.min(height, y + dy)),
                  ];
                  setQuad(q);
                  setManual(true);
                }
              }}
              onPointerDown={(e) => {
                drag.current = i;
                overlay.current?.setPointerCapture(e.pointerId);
                e.preventDefault();
              }}
            />
          ))}
        </svg>
      )}
      {stream && phase === "setup" && (
        <div className="preview-label">
          <ScanLine size={14} />
          {manual
            ? "已手动校正 · 拖动四角微调"
            : quad
              ? "已找到投影屏 · 拖动四角微调"
              : "把镜头对准投影屏"}
        </div>
      )}
      {phase === "capture" && (
        <div className="recording-label">
          <i className={paused ? "dot paused" : "dot"} />
          {processing
            ? "正在整理"
            : paused
              ? "已暂停"
              : replaying
                ? "正在回放"
                : "正在捕捉"}
          <b>{clock(elapsed)}</b>
        </div>
      )}
    </div>
  );
  return (
    <div className="app">
      <header className="app-header">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            if (phase === "review") reset();
          }}
        >
          <span className="brand-icon">
            <ScanLine size={21} />
          </span>
          SlideCap<span className="local-badge">本地</span>
        </a>
        <nav className="steps" aria-label="工作流程">
          {["对准", "捕捉", "整理"].map((s, i) => (
            <React.Fragment key={s}>
              {i > 0 && <span className="step-line" />}
              <span
                className={`step ${["setup", "capture", "review"].indexOf(phase) === i ? "current" : ""} ${["setup", "capture", "review"].indexOf(phase) > i ? "done" : ""}`}
              >
                <b>
                  {["setup", "capture", "review"].indexOf(phase) > i ? (
                    <Check size={12} />
                  ) : (
                    i + 1
                  )}
                </b>
                {s}
              </span>
            </React.Fragment>
          ))}
        </nav>
        <div className="header-actions">
          <button
            className="quiet"
            onClick={() => {
              api("/sessions").then(setHistory);
              setHistoryOpen(true);
            }}
          >
            <History size={17} />
            <span>历史会话</span>
          </button>
          <button
            className="icon"
            aria-label="切换深浅色主题"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </div>
      </header>
      {(error || warning || (hidden && phase === "capture")) && (
        <div className="banner" role="status">
          <AlertCircle size={17} />
          <span>
            {hidden && phase === "capture"
              ? "请保持此标签页打开；后台采集可能受到浏览器或系统限制。"
              : error || warning}
          </span>
          <button
            aria-label="关闭提示"
            className="icon"
            onClick={() => {
              setError("");
              setWarning("");
            }}
          >
            <X size={15} />
          </button>
        </div>
      )}
      <main>
        {phase === "setup" && (
          <>
            <div className={`setup-layout ${advanced ? "" : "collapsed"}`}>
              <section className="preview-panel">
                <div className="panel-toolbar">
                  <div className="camera-select">
                    <Video size={17} />
                    <select
                      aria-label="选择相机"
                      value={device}
                      onChange={(e) => openCamera(e.target.value)}
                      disabled={cameraBusy}
                    >
                      <option value="">
                        {devices.length ? "选择相机" : "尚未连接相机"}
                      </option>
                      {devices.map((d, i) => (
                        <option key={d.deviceId || i} value={d.deviceId}>
                          {d.label || `相机 ${i + 1}（连接后显示名称）`}
                        </option>
                      ))}
                    </select>
                  </div>
                  {stream && (
                    <span
                      title={!is4K ? "坐后排时文字可能看不清" : ""}
                      className={`resolution ${is4K ? "good" : "fair"}`}
                    >
                      {is4K ? "4K ✓" : `${Math.min(width, height)}p`}
                      <small>
                        {settings.width} × {settings.height}
                      </small>
                    </span>
                  )}
                  <button
                    aria-label="高级设置"
                    className={`icon ${advanced ? "active" : ""}`}
                    onClick={() => setAdvanced(!advanced)}
                  >
                    <SlidersHorizontal size={18} />
                  </button>
                </div>
                {preview}
                <div className="readability">
                  <span
                    className={`readability-icon ${quad ? (textPx >= 16 ? "good" : "fair") : ""}`}
                  >
                    <ScanLine size={21} />
                  </span>
                  <div>
                    <strong>
                      {quad ? `文字可读性：${readability}` : "等待对准投影屏"}
                    </strong>
                    <p>{advice}</p>
                  </div>
                  {quad && (
                    <span className="text-estimate">
                      ≈ {Math.round(textPx)} px<span>估算正文字高</span>
                    </span>
                  )}
                </div>
              </section>
              {advanced && (
                <aside className="settings-panel">
                  <div className="aside-heading">
                    <h3>捕捉设置</h3>
                    <SlidersHorizontal size={16} />
                  </div>
                  <label>
                    课程名称<span>可选</span>
                    <input
                      placeholder="例如：设计思维 · 第三讲"
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      maxLength={100}
                    />
                  </label>
                  <label>
                    检测间隔
                    <div className="interval-control">
                      <input
                        type="range"
                        min="0.5"
                        max="5"
                        step="0.5"
                        value={interval}
                        onChange={(e) => setIntervalValue(+e.target.value)}
                      />
                      <output>{interval} 秒</output>
                    </div>
                  </label>
                  <p className="setting-note">
                    只在画面变化时保存照片，静止时每 15 秒保留一张备选。
                  </p>
                  <div className="divider" />
                  {exposure.panel}
                  <h4>相机控制</h4>
                  {["pan", "tilt", "zoom"]
                    .filter((k) => caps[k])
                    .map((k) => (
                      <label key={k}>
                        {
                          (
                            {
                              pan: "水平转动",
                              tilt: "垂直转动",
                              zoom: "变焦",
                            } as Record<string, string>
                          )[k]
                        }
                        <input
                          type="range"
                          min={caps[k].min}
                          max={caps[k].max}
                          step={caps[k].step || 0.1}
                          defaultValue={(settings as any)[k] || caps[k].min}
                          onChange={(e) => constrain(k, +e.target.value)}
                        />
                      </label>
                    ))}
                  {["focusMode"]
                    .filter((k) => caps[k]?.includes("manual"))
                    .map((k) => (
                      <label className="toggle-row" key={k}>
                        {k === "exposureMode" ? "锁定曝光" : "锁定对焦"}
                        <input
                          type="checkbox"
                          checked={!!lockModes[k]}
                          onChange={(e) =>
                            constrain(
                              k,
                              e.target.checked
                                ? "manual"
                                : caps[k].includes("continuous")
                                  ? "continuous"
                                  : caps[k].find((m: string) => m !== "manual"),
                            )
                          }
                        />
                      </label>
                    ))}
                  <button
                    className="quiet reset-quad"
                    disabled={!stream}
                    onClick={() => {
                      setManual(false);
                      manualRef.current = false;
                      setQuad(null);
                    }}
                  >
                    <RefreshCw size={14} />
                    重新自动检测屏幕
                  </button>
                </aside>
              )}
            </div>
            <div className="setup-bottom">
              <div className="start-wrap">
                <button
                  className="primary large"
                  onClick={start}
                  disabled={!stream || busy}
                >
                  开始捕捉
                  <ArrowRight size={18} />
                </button>
                <span>
                  或按 <kbd>空格</kbd> 开始
                </span>
              </div>
            </div>
          </>
        )}
        {phase === "capture" && (
          <>
            <div className="page-heading">
              <div>
                <div className="eyebrow">
                  {processing ? "正在整理" : "捕捉中"}
                </div>
                <h1>{meta?.title}</h1>
                <p>
                  {processing
                    ? "校正、合并动画、去除回翻重复。原始照片会保留在本机。"
                    : paused
                      ? "捕捉已暂停。准备好后，继续就可以。"
                      : "每次换页，都会在这里留下一个瞬间。"}
                </p>
              </div>
              <span className="capture-indicator">
                <i className={paused || processing ? "dot paused" : "dot"} />
                {processing
                  ? "正在整理"
                  : paused
                    ? "已暂停"
                    : replaying
                      ? "照片回放中"
                      : "正在捕捉"}
              </span>
            </div>
            {processing ? (
              <section className="processing">
                <div className="spinner" />
                <h2>{meta?.processing_label || "正在分析照片"}</h2>
                <p>{meta?.saved} 张原片，正在寻找每一页的最佳状态。</p>
                <progress max="100" value={meta?.progress} />
                <span>{meta?.progress}%</span>
              </section>
            ) : (
              <div className={`capture-layout ${focus ? "focused" : ""}`}>
                <aside className="capture-sidebar">
                  {!replaying && exposure.panel}
                  <div style={{ display: focus ? "none" : undefined }}>
                    {preview}
                  </div>
                  <button className="quiet" onClick={() => setFocus(!focus)}>
                    {focus ? <Eye size={16} /> : <EyeOff size={16} />}
                    {focus ? "显示预览" : "专注模式 · 隐藏预览"}
                  </button>
                  {!focus && (
                    <div className="capture-stats">
                      <span>
                        捕捉时长<strong>{clock(elapsed)}</strong>
                      </span>
                      <span>
                        临时幻灯片
                        <strong>
                          {slides.length}
                          <small> 页</small>
                        </strong>
                      </span>
                      <p>临时页面会在结束后进一步去重、合并与校正。</p>
                    </div>
                  )}
                </aside>
                <section className="film-panel">
                  <div className="section-heading">
                    <h3>
                      已捕捉的画面 <span>{slides.length}</span>
                    </h3>
                    <span>
                      {replaying
                        ? `回放 ${replayProgress.done} / ${replayProgress.total}`
                        : "实时更新"}
                    </span>
                  </div>
                  {slides.length ? (
                    <div className="slide-grid live-grid">
                      {slides.map((s, i) => (
                        <article className="slide-card" key={s.id}>
                          <img src={s.image} alt={`临时幻灯片 ${i + 1}`} />
                          <div className="card-info">
                            <strong>{String(i + 1).padStart(2, "0")}</strong>
                            <span>{clock(s.time)}</span>
                            {s.forced && <span className="tag">手动</span>}
                          </div>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <div className="empty-grid">
                      <Images size={37} strokeWidth={1} />
                      <h3>第一张幻灯片，即将出现</h3>
                      <p>画面稳定后自动捕捉。你也可以按 S 手动拍一张。</p>
                    </div>
                  )}
                </section>
              </div>
            )}
            <div className="capture-bottom">
              <div className="status-line">
                <span className="tiny-dot" />
                {meta?.saved || 0} 张照片 ·{" "}
                {((meta?.bytes || 0) / 1024 / 1024).toFixed(1)} MB
                <span className="status-detail">
                  {settings.width} × {settings.height} ·{" "}
                  {Math.round(settings.frameRate || 0)} fps
                </span>
                <span className="status-detail">
                  最近检测{" "}
                  {meta?.last_detected
                    ? new Date(meta.last_detected).toLocaleTimeString()
                    : "等待中"}
                </span>
              </div>
              {!processing && (
                <div className="capture-actions">
                  <button
                    disabled={busy || meta?.state === "error"}
                    onClick={togglePause}
                  >
                    {paused ? <Play size={15} /> : <Pause size={15} />}
                    {paused ? "继续" : "暂停"}
                  </button>
                  <button
                    onClick={manualShot}
                    disabled={paused || busy || meta?.state === "error"}
                  >
                    <Camera size={16} />
                    手动拍一张<kbd>S</kbd>
                  </button>
                  <button
                    className="end-button"
                    disabled={busy}
                    onClick={() => setFinishAsk(true)}
                  >
                    <Square size={13} />
                    {meta?.state === "error" ? "重新整理" : "结束"}
                  </button>
                </div>
              )}
            </div>
          </>
        )}
        {phase === "review" && (
          <>
            <div className="page-heading">
              <div>
                <div className="eyebrow">每一页，都已就位</div>
                <h1>{meta?.title}</h1>
                <p>
                  {slides.length} 页幻灯片 · {clock(meta?.elapsed || 0)} ·
                  点击查看大图，拖动调整顺序。
                </p>
              </div>
              <div className="export-actions">
                <button className="quiet" onClick={reset}>
                  <Plus size={16} />
                  新建捕捉
                </button>
                <div className="export-group">
                  <button
                    className="primary"
                    onClick={() => download("pptx")}
                    disabled={busy || !slides.length}
                  >
                    <Download size={16} />
                    导出 PPTX
                  </button>
                  <details>
                    <summary aria-label="更多导出格式">⌄</summary>
                    <div className="dropdown">
                      <button
                        onClick={() => download("pdf")}
                        disabled={busy || !slides.length}
                      >
                        导出 PDF
                      </button>
                      <button
                        onClick={() => download("zip")}
                        disabled={busy || !slides.length}
                      >
                        PNG 压缩包
                      </button>
                    </div>
                  </details>
                </div>
              </div>
            </div>
            <div className="review-toolbar">
              <span>
                <Check size={14} />
                已保存到本机
                <span className="muted"> · 原始照片保留，可随时换帧</span>
              </span>
              <div>
                <button
                  className="quiet"
                  onClick={() =>
                    setSelection(
                      selection.length === slides.length
                        ? []
                        : slides.map((s) => s.id),
                    )
                  }
                >
                  {selection.length === slides.length && slides.length
                    ? "取消全选"
                    : "全选"}
                </button>
                {selection.length > 0 && (
                  <>
                    <span>已选 {selection.length} 页</span>
                    <button
                      disabled={selection.length < 2 || busy}
                      onClick={() => edit("merge", selection)}
                    >
                      <Layers size={14} />
                      合并
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => edit("delete", selection)}
                    >
                      <Trash2 size={14} />
                      删除
                    </button>
                  </>
                )}
              </div>
            </div>
            {slides.length ? (
              <div className="slide-grid review-grid">
                {slides.map((s, i) => (
                  <article
                    key={s.id}
                    className={`slide-card review-card ${selection.includes(s.id) ? "selected" : ""}`}
                    draggable={!busy}
                    onDragStart={() => (dragCard.current = s.id)}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={() => {
                      if (dragCard.current) reorder(dragCard.current, s.id);
                      dragCard.current = null;
                    }}
                  >
                    <div className="slide-image">
                      <button
                        className="image-button"
                        onClick={() => setViewer(i)}
                        aria-label={`查看第 ${i + 1} 页`}
                      >
                        <img
                          loading="lazy"
                          src={s.image}
                          alt={`幻灯片 ${i + 1}`}
                        />
                      </button>
                      <input
                        className="select-slide"
                        type="checkbox"
                        aria-label={`选择第 ${i + 1} 页`}
                        checked={selection.includes(s.id)}
                        onChange={(e) =>
                          setSelection((p) =>
                            e.target.checked
                              ? [...p, s.id]
                              : p.filter((x) => x !== s.id),
                          )
                        }
                      />
                      <div className="card-tools">
                        <button
                          title="换一张"
                          aria-label={`第 ${i + 1} 页换一张`}
                          onClick={() => setCandidate(s)}
                        >
                          <Images size={16} />
                        </button>
                        <button
                          title="删除"
                          aria-label={`删除第 ${i + 1} 页`}
                          onClick={() => edit("delete", [s.id])}
                        >
                          <Trash2 size={15} />
                        </button>
                      </div>
                    </div>
                    <div className="card-info">
                      <GripVertical size={13} />
                      <strong>{String(i + 1).padStart(2, "0")}</strong>
                      <span>{clock(s.time)}</span>
                      <div className="reorder-buttons">
                        <button
                          disabled={i === 0 || busy}
                          aria-label={`第 ${i + 1} 页前移`}
                          onClick={() => reorder(s.id, slides[i - 1].id)}
                        >
                          <ChevronLeft size={14} />
                        </button>
                        <button
                          disabled={i === slides.length - 1 || busy}
                          aria-label={`第 ${i + 1} 页后移`}
                          onClick={() => reorder(s.id, slides[i + 1].id)}
                        >
                          <ChevronRight size={14} />
                        </button>
                      </div>
                    </div>
                    <div className="tags">
                      {!!s.builds && <span className="tag">动画合并</span>}
                      {!!s.revisits?.length && (
                        <span className="tag">回翻去重</span>
                      )}
                      {s.possible_duplicate && (
                        <span className="tag amber">可能重复</span>
                      )}
                      {s.forced && <span className="tag">手动拍摄</span>}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="empty-grid">
                <Images size={36} />
                <h3>还没有可导出的幻灯片</h3>
                <p>可以新建捕捉，或从历史会话重新打开已有课程。</p>
                <button onClick={reset}>返回对准</button>
              </div>
            )}
            <p className="review-footnote">
              {meta?.gaps?.length
                ? `本次记录到 ${meta.gaps.length} 次采集间隔异常，请对照课堂内容检查。`
                : "整理完成。所有编辑会同步到下一次导出。"}
            </p>
          </>
        )}
      </main>
      {historyOpen && (
        <div className="modal-backdrop" onClick={() => setHistoryOpen(false)}>
          <section
            className="history-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="历史会话"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-heading">
              <h2>历史会话</h2>
              <button
                className="icon"
                onClick={() => setHistoryOpen(false)}
                aria-label="关闭历史"
              >
                <X size={20} />
              </button>
            </div>
            <p>留在本机的每一次收获。</p>
            {!history.length ? (
              <div className="empty-grid">
                <History size={30} />
                <h3>从第一堂课开始</h3>
                <p>完成捕捉后，会话会自动出现在这里。</p>
              </div>
            ) : (
              history.map((h) => (
                <button
                  className="history-item"
                  key={h.id}
                  disabled={phase === "capture"}
                  onClick={async () => {
                    if (h.state === "review") {
                      await loadReview(h.id);
                      socket.current?.close();
                      socket.current = null;
                    } else {
                      setMeta(h);
                      metaRef.current = h;
                      setPhase("capture");
                      phaseRef.current = "capture";
                      setHistoryOpen(false);
                      connect(h.id);
                      await api(`/sessions/${h.id}/finish`, json({}));
                    }
                  }}
                >
                  {h.cover ? (
                    <img src={h.cover} alt="会话封面" />
                  ) : (
                    <span className="history-placeholder">
                      <Images size={24} />
                    </span>
                  )}
                  <span>
                    <strong>{h.title}</strong>
                    <small>
                      {new Date(h.created_at).toLocaleString("zh-CN")} ·{" "}
                      {h.count} 页
                    </small>
                    <small>
                      {h.state === "review" ? "打开整理" : "恢复已保存的照片"}
                    </small>
                  </span>
                  <ChevronRight size={16} />
                </button>
              ))
            )}
            {phase === "capture" && <p>结束当前捕捉后，即可打开历史会话。</p>}
          </section>
        </div>
      )}
      {finishAsk && (
        <div className="modal-backdrop">
          <section
            className="confirm-modal"
            role="dialog"
            aria-modal="true"
            aria-label="结束捕捉"
          >
            <span className="modal-symbol">
              <Square size={22} />
            </span>
            <h2>结束这次捕捉？</h2>
            <p>
              已保存 {meta?.saved || 0}{" "}
              张照片。接下来会自动校正屏幕、合并动画，并挑选清晰的页面。
            </p>
            <div className="dialog-actions">
              <button onClick={() => setFinishAsk(false)}>继续捕捉</button>
              <button className="primary" onClick={finish}>
                结束并整理
                <ArrowRight size={16} />
              </button>
            </div>
          </section>
        </div>
      )}
      {candidate && (
        <div className="modal-backdrop" onClick={() => setCandidate(null)}>
          <section
            className="candidate-modal"
            role="dialog"
            aria-modal="true"
            aria-label="选择候选帧"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-heading">
              <div>
                <h2>挑一张更好的</h2>
                <p>
                  这一页的 {candidate.candidates?.length}{" "}
                  张候选照片，点击即可替换。
                </p>
              </div>
              <button
                className="icon"
                onClick={() => setCandidate(null)}
                aria-label="关闭候选帧"
              >
                <X size={20} />
              </button>
            </div>
            <div className="candidate-strip">
              {candidate.candidates?.map((idx) => (
                <button
                  key={idx}
                  className={candidate.frame === idx ? "chosen" : ""}
                  disabled={busy}
                  onClick={() => edit("replace", [candidate.id], idx)}
                >
                  <img
                    loading="lazy"
                    src={`/api/sessions/${meta?.id}/candidates/${idx}`}
                    alt={`候选帧 ${idx + 1}`}
                  />
                  <span>
                    {clock(framesInfo[idx]?.time || 0)}
                    {candidate.frame === idx && <Check size={14} />}
                  </span>
                </button>
              ))}
            </div>
          </section>
        </div>
      )}
      {viewer !== null && slides[viewer] && (
        <div
          className="modal-backdrop lightbox"
          role="dialog"
          aria-modal="true"
          aria-label="幻灯片大图"
          onClick={() => setViewer(null)}
        >
          <button
            className="lightbox-close icon"
            aria-label="关闭大图"
            onClick={() => setViewer(null)}
          >
            <X size={24} />
          </button>
          <button
            className="icon"
            disabled={viewer === 0}
            onClick={(e) => {
              e.stopPropagation();
              setViewer(viewer - 1);
            }}
            aria-label="上一页"
          >
            <ChevronLeft size={28} />
          </button>
          <figure onClick={(e) => e.stopPropagation()}>
            <img src={slides[viewer].image} alt={`第 ${viewer + 1} 页大图`} />
            <figcaption>
              {viewer + 1} / {slides.length}
              <span>{clock(slides[viewer].time)} · 使用 ← → 翻页</span>
            </figcaption>
          </figure>
          <button
            className="icon"
            disabled={viewer === slides.length - 1}
            onClick={(e) => {
              e.stopPropagation();
              setViewer(viewer + 1);
            }}
            aria-label="下一页"
          >
            <ChevronRight size={28} />
          </button>
        </div>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
