import { AnchorButton, Button, Callout, HTMLTable, Icon, InputGroup, NonIdealState, SegmentedControl, Spinner, Tab, Tabs, Tag } from "@blueprintjs/core";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import type { MarketHistory, Quote } from "../types";
import { ago } from "./LiveLayer";

const TABS = [
  { id: "indices", label: "Indices", icon: "globe" },
  { id: "commodities", label: "Commodities", icon: "oil-field" },
  { id: "fx", label: "FX", icon: "exchange" },
  { id: "crypto", label: "Crypto", icon: "dollar" },
  { id: "rates", label: "Rates", icon: "percentage" },
  { id: "geopolitical", label: "Defense & Shipping", icon: "shield" },
  { id: "watchlist", label: "Watchlist", icon: "star" },
] as const;

const RANGES = ["1d", "5d", "1mo", "6mo", "1y", "5y", "max"];
const UP = "#32A467";
const DOWN = "#E76A6E";
const WL_KEY = "mg.market.watchlist";
const DEFAULT_WL = ["AAPL", "NVDA", "MSFT", "TSLA", "PLTR"];

function loadWatchlist(): string[] {
  try {
    const v = JSON.parse(window.localStorage.getItem(WL_KEY) ?? "null");
    return Array.isArray(v) ? v : DEFAULT_WL;
  } catch {
    return DEFAULT_WL;
  }
}
function saveWatchlist(list: string[]) {
  try {
    window.localStorage.setItem(WL_KEY, JSON.stringify(list));
  } catch {
    /* private mode */
  }
}

export const fmtPrice = (v: number | null | undefined) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  const d = a >= 1000 ? 2 : a >= 10 ? 2 : a >= 1 ? 4 : 5;
  return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
};
const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`);
const fmtChg = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${fmtPrice(v)}`);
const tone = (v: number | null | undefined) => (v == null ? undefined : v >= 0 ? UP : DOWN);
const fmtBig = (v?: number) =>
  v == null ? "—" : v >= 1e12 ? `${(v / 1e12).toFixed(2)}T` : v >= 1e9 ? `${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v.toLocaleString();

function Sparkline({ data, width = 110, height = 26 }: { data: (number | null)[]; width?: number; height?: number }) {
  const pts = data.filter((v): v is number => v != null);
  if (pts.length < 2) return <span className="muted">—</span>;
  const min = Math.min(...pts), max = Math.max(...pts), span = max - min || 1;
  const d = pts.map((v, i) => `${(i / (pts.length - 1)) * width},${height - 2 - ((v - min) / span) * (height - 4)}`).join(" ");
  return (
    <svg width={width} height={height} className="spark">
      <polyline points={d} fill="none" stroke={pts[pts.length - 1] >= pts[0] ? UP : DOWN} strokeWidth={1.4} />
    </svg>
  );
}

function PriceChart({ hist }: { hist: MarketHistory }) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(400);
  const [hover, setHover] = useState<number | null>(null);
  const h = 230, padR = 58, padB = 20, padT = 8;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const pts = hist.points;
  if (pts.length < 2) return <div className="small muted">Not enough data for this range.</div>;
  const closes = pts.map((p) => p.c);
  const min = Math.min(...closes), max = Math.max(...closes), span = max - min || 1;
  const iw = w - padR, ih = h - padB - padT;
  const x = (i: number) => (i / (pts.length - 1)) * iw;
  const y = (v: number) => padT + ih - ((v - min) / span) * ih;
  const line = closes.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const color = closes[closes.length - 1] >= closes[0] ? UP : DOWN;
  const intraday = hist.range === "1d" || hist.range === "5d";
  const fmtT = (t: string) => {
    const d = new Date(t);
    return intraday ? d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  };
  const ticks = [0, 1, 2, 3].map((k) => min + (span * k) / 3);
  const hp = hover != null ? pts[hover] : null;

  return (
    <div ref={ref} className="price-chart">
      <svg width={w} height={h}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const i = Math.round(((e.clientX - r.left) / iw) * (pts.length - 1));
          setHover(i >= 0 && i < pts.length ? i : null);
        }}
        onMouseLeave={() => setHover(null)}>
        <defs>
          <linearGradient id="mkt-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.28} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={0} x2={iw} y1={y(t)} y2={y(t)} stroke="var(--bg3)" strokeDasharray="2 3" />
            <text x={iw + 6} y={y(t) + 3} className="chart-axis">{fmtPrice(t)}</text>
          </g>
        ))}
        <path d={`${line}L${iw},${padT + ih}L0,${padT + ih}Z`} fill="url(#mkt-fill)" />
        <path d={line} fill="none" stroke={color} strokeWidth={1.6} />
        <text x={0} y={h - 5} className="chart-axis">{fmtT(pts[0].t)}</text>
        <text x={iw} y={h - 5} className="chart-axis" textAnchor="end">{fmtT(pts[pts.length - 1].t)}</text>
        {hp && hover != null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={padT} y2={padT + ih} stroke="var(--muted)" strokeWidth={0.8} />
            <circle cx={x(hover)} cy={y(hp.c)} r={3.5} fill={color} stroke="#fff" strokeWidth={1.2} />
          </g>
        )}
      </svg>
      <div className="chart-readout mono">
        {hp ? <>{fmtT(hp.t)} · O {fmtPrice(hp.o)} · H {fmtPrice(hp.h)} · L {fmtPrice(hp.l)} · C <b>{fmtPrice(hp.c)}</b>{hp.v ? ` · Vol ${fmtBig(hp.v)}` : ""}</>
          : <span className="muted">Hover the chart for OHLC</span>}
      </div>
    </div>
  );
}

function Detail({ quote, inWatchlist, onToggleWatch }: { quote: Quote; inWatchlist: boolean; onToggleWatch: () => void }) {
  const [range, setRange] = useState("1mo");
  const [hist, setHist] = useState<MarketHistory | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [news, setNews] = useState<any[] | null>(null);

  useEffect(() => {
    let alive = true;
    setHist(null);
    setErr(null);
    api.marketHistory(quote.symbol, range).then((h) => alive && setHist(h)).catch((e) => alive && setErr(String(e.message)));
    return () => {
      alive = false;
    };
  }, [quote.symbol, range]);

  useEffect(() => {
    setNews(null);
    api.marketNews(quote.symbol).then(setNews).catch(() => setNews([]));
  }, [quote.symbol]);

  const pts = hist?.points ?? [];
  const first = pts[0]?.c, last = pts[pts.length - 1]?.c;
  const periodChg = first && last ? ((last - first) / first) * 100 : null;
  const hi = pts.length ? Math.max(...pts.map((p) => p.h ?? p.c)) : null;
  const lo = pts.length ? Math.min(...pts.map((p) => p.l ?? p.c)) : null;
  const m = hist?.meta ?? {};

  return (
    <div className="asset-detail-body">
      <div className="mkt-detail-head">
        <div>
          <h3>{quote.name}</h3>
          <div className="small muted mono">{quote.symbol}{m.exchange ? ` · ${m.exchange}` : ""}{m.currency ? ` · ${m.currency}` : ""}</div>
        </div>
        <Button small minimal icon={inWatchlist ? "star" : "star-empty"} intent={inWatchlist ? "warning" : "none"}
          title={inWatchlist ? "Remove from watchlist" : "Add to watchlist"} onClick={onToggleWatch} />
      </div>
      <div className="mkt-price">
        <span className="mkt-price-v">{fmtPrice(quote.price)}</span>
        <span style={{ color: tone(quote.change) }}>{fmtChg(quote.change)} ({fmtPct(quote.change_pct)})</span>
      </div>
      <SegmentedControl small className="mb10" value={range} onValueChange={setRange}
        options={RANGES.map((r) => ({ value: r, label: r.toUpperCase() }))} />
      {err ? <Callout intent="warning" className="small">{err}</Callout> : !hist ? <Spinner className="mt20" size={24} /> : <PriceChart hist={hist} />}
      <div className="section-title">Statistics</div>
      <div className="kv-grid small">
        <span>Range change</span><span className="mono" style={{ color: tone(periodChg) }}>{fmtPct(periodChg)}</span>
        <span>Range high / low</span><span className="mono">{fmtPrice(hi)} / {fmtPrice(lo)}</span>
        <span>Previous close</span><span className="mono">{fmtPrice(m.prev_close)}</span>
        <span>52-week high / low</span><span className="mono">{fmtPrice(m.year_high)} / {fmtPrice(m.year_low)}</span>
        {m.market_cap ? <><span>Market cap</span><span className="mono">{fmtBig(m.market_cap)}</span></> : null}
        <span>Interval</span><span className="mono">{hist?.interval ?? "—"}</span>
      </div>
      <div className="section-title">News</div>
      {!news ? <Spinner size={16} /> : !news.length ? <div className="small muted">No recent headlines from Yahoo Finance.</div> : (
        news.map((n) => (
          <div key={n.url ?? n.title} className="mkt-news">
            <a href={n.url} target="_blank" rel="noreferrer">{n.title}</a>
            <div className="small muted">{n.publisher ?? ""}{n.ts ? ` · ${ago(n.ts)}` : ""}</div>
          </div>
        ))
      )}
      <div className="row-actions mt10">
        <AnchorButton small minimal icon="share" text="Yahoo Finance" href={`https://finance.yahoo.com/quote/${encodeURIComponent(quote.symbol)}`} target="_blank" />
      </div>
      <div className="small muted mt10">Delayed data from Yahoo Finance via yfinance (unofficial). Not for trading decisions.</div>
    </div>
  );
}

export default function MarketsView() {
  const { state } = useStore();
  const [tab, setTab] = useState<string>(() => {
    try {
      return window.localStorage.getItem("mg.market.tab") ?? "indices";
    } catch {
      return "indices";
    }
  });
  const [watchlist, setWatchlist] = useState<string[]>(loadWatchlist);
  const [quotes, setQuotes] = useState<Quote[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [updated, setUpdated] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [addSym, setAddSym] = useState("");
  const [sel, setSel] = useState<Quote | null>(null);
  const [sortKey, setSortKey] = useState<"group" | "name" | "change_pct">("group");
  const visible = state.view === "markets";

  useEffect(() => {
    try {
      window.localStorage.setItem("mg.market.tab", tab);
    } catch {
      /* private mode */
    }
  }, [tab]);

  const wlKey = watchlist.join(",");
  useEffect(() => {
    if (!visible) return;
    let alive = true;
    setQuotes(null);
    setErr(null);
    const load = () =>
      (tab === "watchlist" ? api.marketSymbols(watchlist) : api.marketQuotes(tab))
        .then((q) => {
          if (!alive) return;
          setQuotes(q);
          setErr(null);
          setUpdated(new Date().toISOString());
        })
        .catch((e) => alive && setErr(String(e.message)));
    load();
    const t = window.setInterval(load, 120_000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [tab, wlKey, visible]); // eslint-disable-line react-hooks/exhaustive-deps

  // keep the selected row's quote fresh across refreshes
  useEffect(() => {
    if (sel && quotes) {
      const q = quotes.find((x) => x.symbol === sel.symbol);
      if (q) setSel(q);
    }
  }, [quotes]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleWatch = (sym: string) => {
    const next = watchlist.includes(sym) ? watchlist.filter((s) => s !== sym) : [...watchlist, sym];
    setWatchlist(next);
    saveWatchlist(next);
  };
  const addToWatchlist = () => {
    const s = addSym.trim().toUpperCase();
    if (s && !watchlist.includes(s)) {
      const next = [...watchlist, s];
      setWatchlist(next);
      saveWatchlist(next);
    }
    setAddSym("");
  };

  const rows = useMemo(() => {
    const f = filter.trim().toLowerCase();
    let list = (quotes ?? []).filter((q) => !f || q.name.toLowerCase().includes(f) || q.symbol.toLowerCase().includes(f));
    if (sortKey === "name") list = [...list].sort((a, b) => a.name.localeCompare(b.name));
    if (sortKey === "change_pct") list = [...list].sort((a, b) => (b.change_pct ?? -Infinity) - (a.change_pct ?? -Infinity));
    return list;
  }, [quotes, filter, sortKey]);

  const priced = (quotes ?? []).filter((q) => q.change_pct != null);
  const up = priced.filter((q) => q.change_pct! > 0).length;
  const down = priced.filter((q) => q.change_pct! < 0).length;
  const best = priced.reduce<Quote | null>((b, q) => (!b || q.change_pct! > b.change_pct! ? q : b), null);
  const worst = priced.reduce<Quote | null>((b, q) => (!b || q.change_pct! < b.change_pct! ? q : b), null);

  const th = (key: typeof sortKey, label: string) => (
    <th onClick={() => setSortKey(key)}>{label}{sortKey === key && <Icon icon="caret-down" size={11} />}</th>
  );

  return (
    <div className="assets-page">
      <div className="page-head">
        <div>
          <h2><Icon icon="timeline-line-chart" size={18} /> Markets</h2>
          <p className="muted">Delayed quotes from Yahoo Finance (yfinance). Refreshes every 2 min; prices may lag by 15 min or more.</p>
        </div>
        <div className="metric-row">
          <div className="metric"><div className="metric-v" style={{ color: UP }}>{up}</div><div className="metric-l">Advancing</div></div>
          <div className="metric"><div className="metric-v" style={{ color: DOWN }}>{down}</div><div className="metric-l">Declining</div></div>
          <div className="metric"><div className="metric-v small-v" style={{ color: tone(best?.change_pct) }}>{best ? `${best.name} ${fmtPct(best.change_pct)}` : "—"}</div><div className="metric-l">Best</div></div>
          <div className="metric"><div className="metric-v small-v" style={{ color: tone(worst?.change_pct) }}>{worst ? `${worst.name} ${fmtPct(worst.change_pct)}` : "—"}</div><div className="metric-l">Worst</div></div>
        </div>
      </div>

      <Tabs id="markets-tabs" className="mkt-tabs" selectedTabId={tab} onChange={(id) => { setTab(String(id)); setSel(null); setFilter(""); }}>
        {TABS.map((t) => (
          <Tab key={t.id} id={t.id} title={<span><Icon icon={t.icon as any} size={12} /> {t.label}</span>} />
        ))}
      </Tabs>

      <div className="assets-toolbar">
        <InputGroup leftIcon="filter" placeholder="Filter by name or symbol…" value={filter} onChange={(e) => setFilter(e.target.value)} className="grow" />
        {tab === "watchlist" && (
          <>
            <InputGroup leftIcon="add" placeholder="Add Yahoo symbol (e.g. AAPL, RHM.DE, ^N225)" value={addSym}
              onChange={(e) => setAddSym(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addToWatchlist()} />
            <Button icon="plus" text="Add" onClick={addToWatchlist} disabled={!addSym.trim()} />
          </>
        )}
        <span className="small muted">{updated ? `updated ${ago(updated)}` : ""}</span>
      </div>

      <div className="assets-body">
        <div className="assets-table">
          {err && !quotes ? (
            <NonIdealState icon="error" title="Could not load quotes" description={err} />
          ) : !quotes ? <Spinner className="mt20" /> : !rows.length ? (
            <NonIdealState icon="timeline-line-chart" title={tab === "watchlist" ? "Watchlist is empty" : "No matches"}
              description={tab === "watchlist" ? "Add a Yahoo Finance symbol above." : undefined} />
          ) : (
            <HTMLTable compact interactive striped>
              <thead>
                <tr>
                  {th("name", "Name")}
                  <th>Symbol</th>
                  <th className="num">Last</th>
                  <th className="num">Change</th>
                  {th("change_pct", "Chg %")}
                  <th>1 month</th>
                  <th>As of</th>
                  {tab === "watchlist" && <th />}
                </tr>
              </thead>
              <tbody>
                {rows.map((q, i) => (
                  <Fragment key={q.symbol}>
                    {sortKey === "group" && tab !== "watchlist" && (i === 0 || rows[i - 1].group !== q.group) && (
                      <tr className="mkt-group"><td colSpan={7}>{q.group}</td></tr>
                    )}
                    <tr className={sel?.symbol === q.symbol ? "sel" : ""} onClick={() => setSel(q)}>
                      <td><b>{q.name}</b></td>
                      <td className="mono">{q.symbol}</td>
                      <td className="num mono">{fmtPrice(q.price)}</td>
                      <td className="num mono" style={{ color: tone(q.change) }}>{fmtChg(q.change)}</td>
                      <td>{q.change_pct == null ? "—" : (
                        <Tag minimal style={{ color: tone(q.change_pct), background: q.change_pct >= 0 ? "rgba(50,164,103,.15)" : "rgba(231,106,110,.15)" }}>
                          {fmtPct(q.change_pct)}
                        </Tag>
                      )}</td>
                      <td><Sparkline data={q.spark} /></td>
                      <td className="small muted">{q.ts ? new Date(q.ts).toLocaleDateString() : "no data"}</td>
                      {tab === "watchlist" && (
                        <td><Button small minimal icon="cross" title="Remove" onClick={(e) => { e.stopPropagation(); toggleWatch(q.symbol); }} /></td>
                      )}
                    </tr>
                  </Fragment>
                ))}
              </tbody>
            </HTMLTable>
          )}
        </div>

        <div className="asset-detail">
          {!sel ? (
            <NonIdealState icon="select" title="Select an instrument" description="Chart, statistics and headlines appear here." />
          ) : (
            <Detail key={sel.symbol} quote={sel} inWatchlist={watchlist.includes(sel.symbol)} onToggleWatch={() => toggleWatch(sel.symbol)} />
          )}
        </div>
      </div>
    </div>
  );
}
