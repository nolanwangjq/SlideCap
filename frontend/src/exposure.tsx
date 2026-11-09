import React, { useEffect, useRef, useState } from 'react';
export type Range = { min: number; max: number; step?: number };
export const keys = ['exposureMode', 'exposureCompensation', 'exposureTime', 'brightness'];
export function quantize(value: number, r: Range, flicker = false) {
  const step = r.step || 0.01;
  if (flicker) {
    // Image Capture exposureTime units are 100 microseconds: 100 = 10 ms.
    const low = Math.ceil(r.min / 100), high = Math.floor(r.max / 100);
    if (low <= high) value = Math.max(low, Math.min(high, Math.round(value / 100))) * 100;
  }
  return Math.max(r.min, Math.min(r.max, Number((r.min + Math.round((value - r.min) / step) * step).toFixed(6))));
}
export function measure(data: Uint8ClampedArray, w: number, h: number, quad: number[][]) {
  const bins = new Array<number>(256).fill(0), zebra = new Uint8ClampedArray(data.length);
  let count = 0;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let inside = false;
    for (let i = 0, j = 3; i < 4; j = i++) {
      const a = quad[i], b = quad[j];
      if ((a[1] > y + .5) !== (b[1] > y + .5) && x + .5 < (b[0]-a[0]) * (y+.5-a[1]) / (b[1]-a[1]) + a[0]) inside = !inside;
    }
    if (!inside) continue;
    const p = (y*w+x)*4, l = Math.round(.2126*data[p]+.7152*data[p+1]+.0722*data[p+2]);
    bins[l]++; count++;
    if (l >= 250 && (x+y)%10 < 5) { zebra[p]=255; zebra[p+3]=210; }
  }
  let sum = 0, p99 = 0;
  for (; p99 < 255; p99++) { sum += bins[p99]; if (sum >= Math.ceil(count*.99)) break; }
  return { bins, p99, count, zebra };
}
export function useExposure(stream: MediaStream | null, video: React.RefObject<HTMLVideoElement | null>, quad: number[][] | null, enabled: boolean, change: (values: Record<string, unknown>) => Promise<void>) {
  const track = stream?.getVideoTracks()[0];
  const caps: any = track?.getCapabilities?.() || {};
  const numeric = keys.slice(1).filter(k => Number.isFinite(caps[k]?.min) && caps[k].max > caps[k].min);
  const control = numeric.find(k => k !== 'brightness');
  const [mode, setMode] = useState('screen'), [values, setValues] = useState<any>({}), [status, setStatus] = useState('连接相机后可用。');
  const [bins, setBins] = useState<number[]>([]), [confirmed, setConfirmed] = useState(false);
  const zebra = useRef<HTMLCanvasElement>(null), busy = useRef(false), stable = useRef(0), locked = useRef(false), failed = useRef(false);
  const current = useRef({ change, quad, confirmed, mode, enabled });
  current.current = { change, quad, confirmed, mode, enabled };
  const save = (m: string, v: any) => { try { localStorage.setItem('slidecap-exposure:'+track?.label, JSON.stringify({ mode:m, values:v })); } catch {} };
  async function apply(v: any) {
    if (!track || busy.current) return false;
    busy.current = true;
    try {
      await current.current.change(v);
      if (track.readyState !== 'live') return false;
      const actual: any = track.getSettings();
      for (const [k,value] of Object.entries(v)) {
        if (actual[k] === undefined || (typeof value === 'number' ? Math.abs(actual[k]-value) > (caps[k]?.step || .01) : actual[k] !== value)) throw new Error(`${k} 未被相机采用`);
      }
      setValues(actual); save(current.current.mode, actual); return true;
    } catch (e) { failed.current = true; setStatus(`曝光设置失败：${(e as Error).message}`); return false; }
    finally { busy.current = false; }
  }
  async function choose(m: string, restored?: any) {
    current.current.mode = m; setMode(m); stable.current=0; locked.current=false; failed.current=false;
    const v: any = {};
    if (m === 'auto' || (m === 'screen' && control === 'exposureCompensation')) {
      if (caps.exposureMode?.includes('continuous')) v.exposureMode='continuous';
    } else if (caps.exposureMode?.includes('manual')) v.exposureMode='manual';
    if (restored) for (const k of numeric) if (Number.isFinite(restored[k])) v[k]=quantize(restored[k], caps[k], m==='manual' && k==='exposureTime');
    save(m, restored || track?.getSettings());
    if (Object.keys(v).length) await apply(v);
  }
  useEffect(() => {
    setConfirmed(false); setBins([]); setValues(track?.getSettings() || {}); failed.current=false;
    if (!track) return;
    let saved: any;
    try { saved=JSON.parse(localStorage.getItem('slidecap-exposure:'+track.label) || 'null'); } catch {}
    void choose(['screen','auto','manual'].includes(saved?.mode) ? saved.mode : 'screen', saved?.values);
  }, [track]);
  useEffect(() => { setConfirmed(false); stable.current=0; locked.current=false; }, [quad]);
  useEffect(() => {
    if (!track) return;
    const canvas=document.createElement('canvas');
    let previous = JSON.stringify(keys.map(k => (track.getSettings() as any)[k]));
    const timer=window.setInterval(async () => {
      const observed = JSON.stringify(keys.map(k => (track.getSettings() as any)[k]));
      if (observed !== previous && !busy.current) {
        previous = observed;
        await apply({});
      }
      const c=current.current, v=video.current, out=zebra.current;
      if (!c.enabled || !c.confirmed || !c.quad || !v || v.readyState<2) {
        out?.getContext('2d')?.clearRect(0,0,out.width,out.height); setBins([]);
        if (!failed.current) setStatus('请确认屏幕区域。'); return;
      }
      canvas.width=480; canvas.height=Math.max(1,Math.round(480*v.videoHeight/v.videoWidth));
      const ctx=canvas.getContext('2d', {willReadFrequently:true})!;
      ctx.drawImage(v,0,0,canvas.width,canvas.height);
      const q=c.quad.map(([x,y]) => [x*canvas.width/v.videoWidth,y*canvas.height/v.videoHeight]);
      const m=measure(ctx.getImageData(0,0,canvas.width,canvas.height).data,canvas.width,canvas.height,q);
      setBins(m.bins);
      if (out) { out.width=canvas.width; out.height=canvas.height; out.getContext('2d')!.putImageData(new ImageData(m.zebra,canvas.width,canvas.height),0,0); }
      if (failed.current || busy.current) return;
      if (!m.count) { setStatus('屏幕区域无有效像素，请重新选择。'); return; }
      const prefix=`屏幕 P99 ${m.p99} · `;
      if (c.mode!=='screen') { setStatus(prefix+(c.mode==='auto'?'自动曝光':'手动曝光')); return; }
      if (!control) { setStatus(prefix+'未开放曝光补偿或曝光时间，无法闭环调节。'); return; }
      if (locked.current) { setStatus(prefix+'曝光已 manual 锁定'); return; }
      if (m.p99>=230 && m.p99<=240) {
        stable.current++;
        setStatus(prefix+'亮度稳定中');
        if (stable.current>=3) {
          if (caps.exposureMode?.includes('manual')) {
            const ok=await apply({exposureMode:'manual'}); locked.current=ok;
          } else setStatus(prefix+'已稳定；相机不支持 manual 锁定。');
        }
        return;
      }
      stable.current=0;
      const r=caps[control], actual:any=track.getSettings(), old=actual[control] ?? r.min;
      const delta=control==='exposureTime' ? old*(235/Math.max(1,m.p99)-1)*.5 : Math.sign(235-m.p99)*Math.max(r.step||.1, Math.min(.5,Math.abs(235-m.p99)/80));
      const next=quantize(old+delta,r);
      setStatus(prefix+(next===old?'已到相机调节边界，未达到目标。':'正在调节至 230–240'));
      if (next!==old) await apply({[control]:next});
    },500);
    return () => clearInterval(timer);
  }, [track]);
  const supported = numeric.length>0 || caps.exposureMode?.length>0;
  const panel = <section className="exposure-panel"><h4>曝光调节</h4>
    {!track ? <p className="setting-note">连接相机后可用。</p> : <>
      <details><summary>设备能力</summary>{keys.map(k=><p key={k}>{k}: {caps[k]===undefined ? '不支持（浏览器未开放）' : JSON.stringify(caps[k])}</p>)}</details>
      {!supported ? <p className="setting-note">相机未开放曝光控制。</p> : <>
        <label>曝光模式<select aria-label="曝光模式" value={mode} disabled={busy.current} onChange={e=>void choose(e.target.value)}>
          <option value="screen">屏幕优先（默认）</option><option value="auto" disabled={!caps.exposureMode?.includes('continuous')}>自动</option><option value="manual" disabled={!numeric.length}>手动</option>
        </select></label>
        {mode==='manual' && numeric.map(k=><label key={k}>{({exposureCompensation:'曝光补偿',exposureTime:'曝光时间',brightness:'亮度'} as any)[k]} · {k==='exposureTime' ? `${(values[k] ?? caps[k].min)/10} ms` : values[k] ?? caps[k].min}
          <input aria-label={k} type="range" min={caps[k].min} max={caps[k].max} step={caps[k].step||.01} value={values[k] ?? caps[k].min} onChange={e=>void apply({
            ...(k==='exposureTime' && caps.exposureMode?.includes('manual') ? {exposureMode:'manual'} : {}),
            ...(k==='exposureCompensation' && caps.exposureMode?.includes('continuous') ? {exposureMode:'continuous'} : {}),
            [k]:quantize(+e.target.value,caps[k],k==='exposureTime')
          })}/>
        </label>)}
      </>}
      <button disabled={!quad} onClick={()=>{setConfirmed(true); stable.current=0; locked.current=false; void choose(mode);}}> {confirmed?'重新测光':'确认区域'}</button>
      <svg className="histogram" viewBox="0 0 256 64" role="img" aria-label="屏幕区域亮度直方图">{bins.map((n,i)=><rect key={i} x={i} y={64-60*n/Math.max(1,...bins)} width="1" height={60*n/Math.max(1,...bins)} fill={i>=250?'#f45b69':'currentColor'}/>)}</svg>
      <p className="setting-note" role="status">{status}</p>
    </>}
  </section>;
  return { panel, zebra, changing: busy, confirmed };
}
