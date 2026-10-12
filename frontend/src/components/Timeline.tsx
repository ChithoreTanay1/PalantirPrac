import { Button, Tag } from "@blueprintjs/core";
import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../store";
import { C, eventColor } from "../theme";

const H = 92; // plot height
const PAD_L = 36;
const PAD_R = 14;

const fmt = (t: number) => new Date(t).toISOString().slice(0, 10);

/** Histogram of event activity with a brushable time filter (drives graph dimming + map). */
export default function Timeline() {
  const { state, dispatch } = useStore();
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [drag, setDrag] = useState<{ x0: number; x1: number } | null>(null);
  const [hover, setHover] = useState<{ x: number; label: string } | null>(null);

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
    if (ref.current) ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  const data = useMemo(() => {
    const evs = state.events.filter((e) => e.date);
    if (!evs.length) return null;
    const times = evs.map((e) => Date.parse(e.date)).filter((t) => !isNaN(t));
    let t0 = Math.min(...times);
    let t1 = Math.max(...times);
    if (t1 - t0 < 1000 * 86400 * 60) {
      t0 -= 1000 * 86400 * 45;
      t1 += 1000 * 86400 * 45;
    }
    const span = t1 - t0;
    // bucket size: month if < 12y, else quarter/year
    const years = span / (365 * 86400e3);
    const bucketMonths = years <= 8 ? 1 : years <= 25 ? 3 : 12;
    const start = new Date(t0);
    start.setUTCDate(1);
    start.setUTCMonth(start.getUTCMonth() - (start.getUTCMonth() % bucketMonths));
    const buckets: { t: number; end: number; byType: Record<string, number>; n: number }[] = [];
    const d = new Date(start);
    while (d.getTime() <= t1) {
      const s = d.getTime();
      d.setUTCMonth(d.getUTCMonth() + bucketMonths);
      buckets.push({ t: s, end: d.getTime(), byType: {}, n: 0 });
    }
    for (const e of evs) {
      const t = Date.parse(e.date);
      const i = buckets.findIndex((b) => t >= b.t && t < b.end);
      if (i >= 0) {
        buckets[i].n++;
        buckets[i].byType[e.type] = (buckets[i].byType[e.type] ?? 0) + 1;
      }
    }
    const max = Math.max(...buckets.map((b) => b.n), 1);
    return { buckets, t0: buckets[0].t, t1: buckets[buckets.length - 1].end, max, bucketMonths };
  }, [state.events]);

  const plotW = Math.max(100, width - PAD_L - PAD_R);
  const x = (t: number) => (data ? PAD_L + ((t - data.t0) / (data.t1 - data.t0)) * plotW : 0);
  const inv = (px: number) => (data ? data.t0 + ((px - PAD_L) / plotW) * (data.t1 - data.t0) : 0);

  const ticks = useMemo(() => {
    if (!data) return [];
    const y0 = new Date(data.t0).getUTCFullYear();
    const y1 = new Date(data.t1).getUTCFullYear();
    const step = Math.max(1, Math.ceil((y1 - y0) / Math.max(2, plotW / 70)));
    const out = [];
    for (let y = y0; y <= y1; y += step) out.push(Date.UTC(y, 0, 1));
    return out.filter((t) => t >= data.t0 && t <= data.t1);
  }, [data, plotW]);

  const onDown = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const px = e.clientX - r.left;
    setDrag({ x0: px, x1: px });
  };
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const px = e.clientX - r.left;
    if (drag) setDrag({ ...drag, x1: px });
    if (data && px >= PAD_L) setHover({ x: px, label: fmt(inv(px)) });
  };
  const onUp = () => {
    if (!drag || !data) return setDrag(null);
    const a = Math.min(drag.x0, drag.x1);
    const b = Math.max(drag.x0, drag.x1);
    setDrag(null);
    if (b - a < 4) return dispatch({ type: "range", range: null });
    dispatch({ type: "range", range: [inv(Math.max(a, PAD_L)), inv(Math.min(b, PAD_L + plotW))] });
  };

  const total = state.events.length;
  const inRange = state.range ? state.events.filter((e) => { const t = Date.parse(e.date); return t >= state.range![0] && t <= state.range![1]; }).length : total;
  const types = [...new Set(state.events.map((e) => e.type))];

  return (
    <div className="timeline">
      <div className="timeline-head">
        <span className="panel-title">TIMELINE</span>
        <span className="muted small">
          {total ? `${inRange} of ${total} events` : "No events for objects in this investigation"}
          {data && ` · ${data.bucketMonths === 1 ? "monthly" : data.bucketMonths === 3 ? "quarterly" : "yearly"} buckets · drag to filter`}
        </span>
        {state.range && (
          <Tag intent="primary" minimal onRemove={() => dispatch({ type: "range", range: null })}>
            {fmt(state.range[0])} → {fmt(state.range[1])}
          </Tag>
        )}
        <span className="timeline-legend">
          {types.map((t) => <span key={t}><i style={{ background: eventColor(t) }} />{t}</span>)}
        </span>
        {state.range && <Button minimal small icon="cross" text="Reset" onClick={() => dispatch({ type: "range", range: null })} />}
      </div>
      <div ref={ref} className="timeline-plot">
        {data && (
          <svg width={width} height={H + 22} onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp}
            onMouseLeave={() => { setHover(null); if (drag) onUp(); }}>
            <line x1={PAD_L} x2={PAD_L + plotW} y1={H} y2={H} stroke={C.line} />
            {[0.5, 1].map((f) => (
              <g key={f}>
                <line x1={PAD_L} x2={PAD_L + plotW} y1={H - f * (H - 8)} y2={H - f * (H - 8)} stroke={C.bg3} strokeDasharray="2 4" />
                <text x={PAD_L - 6} y={H - f * (H - 8) + 3} fill={C.faint} fontSize={9} textAnchor="end">{Math.round(f * data.max)}</text>
              </g>
            ))}
            {data.buckets.map((b) => {
              const bx = x(b.t);
              const bw = Math.max(1, x(b.end) - bx - 1);
              let y = H;
              const dim = state.range && (b.end < state.range[0] || b.t > state.range[1]);
              return (
                <g key={b.t} opacity={dim ? 0.25 : 1}>
                  {Object.entries(b.byType).map(([t, n]) => {
                    const h = (n / data.max) * (H - 8);
                    y -= h;
                    return <rect key={t} x={bx} y={y} width={bw} height={h} fill={eventColor(t)} rx={1} />;
                  })}
                </g>
              );
            })}
            {ticks.map((t) => (
              <g key={t}>
                <line x1={x(t)} x2={x(t)} y1={H} y2={H + 4} stroke={C.faint} />
                <text x={x(t)} y={H + 15} fill={C.faint} fontSize={10} textAnchor="middle">{new Date(t).getUTCFullYear()}</text>
              </g>
            ))}
            {state.range && !drag && (
              <rect x={x(state.range[0])} y={0} width={Math.max(2, x(state.range[1]) - x(state.range[0]))} height={H}
                fill={C.blue} opacity={0.12} stroke={C.blue} strokeOpacity={0.7} />
            )}
            {drag && (
              <rect x={Math.min(drag.x0, drag.x1)} y={0} width={Math.abs(drag.x1 - drag.x0)} height={H} fill={C.blue} opacity={0.2} />
            )}
            {hover && !drag && (
              <g pointerEvents="none">
                <line x1={hover.x} x2={hover.x} y1={0} y2={H} stroke={C.muted} strokeDasharray="2 2" />
                <text x={hover.x + 4} y={10} fill={C.muted} fontSize={10}>{hover.label}</text>
              </g>
            )}
          </svg>
        )}
      </div>
    </div>
  );
}
