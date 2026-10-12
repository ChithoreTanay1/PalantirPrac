import { HTMLSelect, Icon, Tag, Tooltip } from "@blueprintjs/core";
import { useEffect, useState } from "react";
import { LIVE_COLOR, ago, type useLive } from "./LiveLayer";

const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "source";
  }
};

/** Side panel for a clicked news cluster: composition by kind, expandable per-kind event lists. */
export default function NewsBreakdown({ live, flyTo }: {
  live: ReturnType<typeof useLive>;
  flyTo: (lon: number, lat: number, zoom?: number) => void;
}) {
  const { breakdown, setBreakdown } = live;
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [sort, setSort] = useState<"mentions" | "recent">("mentions");
  useEffect(() => setOpen({}), [breakdown]);
  if (!breakdown) return null;

  const evs = breakdown.events;
  const byKind: Record<string, any[]> = {};
  for (const e of evs) (byKind[e.kind] ??= []).push(e);
  const kinds = Object.entries(byKind).sort((a, b) => b[1].length - a[1].length);
  const places: Record<string, number> = {};
  for (const e of evs) places[e.detail.place] = (places[e.detail.place] ?? 0) + 1;
  const topPlaces = Object.entries(places).sort((a, b) => b[1] - a[1]).slice(0, 6);
  const times = evs.map((e) => Date.parse(e.ts));
  const mentions = evs.reduce((n, e) => n + (e.detail.mentions ?? 0), 0);
  const sorter = (a: any, b: any) =>
    sort === "mentions"
      ? (b.detail.mentions ?? 0) - (a.detail.mentions ?? 0) || b.ts.localeCompare(a.ts)
      : b.ts.localeCompare(a.ts);

  return (
    <div className="breakdown">
      <div className="bd-head">
        <div>
          <div className="panel-title">LIVE · NEWS CLUSTER</div>
          <div className="bd-title">{evs.length} events{topPlaces[0] ? ` around ${topPlaces[0][0]}` : ""}</div>
          <div className="small muted">
            newest {ago(new Date(Math.max(...times)).toISOString())} · oldest {ago(new Date(Math.min(...times)).toISOString())} ·{" "}
            {mentions.toLocaleString()} mentions
          </div>
        </div>
        <button className="bd-close" onClick={() => setBreakdown(null)} title="Close">
          <Icon icon="cross" />
        </button>
      </div>

      <div className="bd-bar">
        {kinds.map(([k, list]) => (
          <Tooltip key={k} content={`${k}: ${list.length}`}>
            <span style={{ width: `${(list.length / evs.length) * 100}%`, background: LIVE_COLOR[k] ?? "#ABB3BF" }} />
          </Tooltip>
        ))}
      </div>

      {topPlaces.length > 1 && (
        <div className="bd-places">
          {topPlaces.map(([p, n]) => (
            <Tag key={p} minimal>{p} · {n}</Tag>
          ))}
        </div>
      )}

      <div className="bd-sort small muted">
        Sort events by
        <HTMLSelect minimal value={sort} onChange={(e) => setSort(e.target.value as "mentions" | "recent")}
          options={[{ value: "mentions", label: "most mentioned" }, { value: "recent", label: "most recent" }]} />
      </div>

      <div className="bd-list">
        {kinds.map(([k, list]) => {
          const isOpen = !!open[k];
          return (
            <div key={k} className="bd-kind">
              <button className="bd-kind-head" onClick={() => setOpen({ ...open, [k]: !isOpen })}>
                <Icon icon={isOpen ? "chevron-down" : "chevron-right"} size={12} />
                <i style={{ background: LIVE_COLOR[k] ?? "#ABB3BF" }} />
                <span className="grow">{k}</span>
                <span className="bd-count">{list.length}</span>
                <span className="bd-pct">{Math.round((list.length / evs.length) * 100)}%</span>
              </button>
              {isOpen && (
                <div className="bd-events">
                  {[...list].sort(sorter).slice(0, 200).map((e) => (
                    <div key={e.id} className="bd-event" onClick={() => flyTo(e.lon, e.lat, 9)}>
                      <div className="bd-ev-top">
                        <span className="ellipsis grow">{e.detail.actors || "Unspecified actors"}</span>
                        <span className="muted">{ago(e.ts)}</span>
                      </div>
                      <div className="small muted">
                        {e.detail.place} · {e.detail.mentions} mention{e.detail.mentions === 1 ? "" : "s"} · CAMEO {e.detail.cameo} · Goldstein{" "}
                        {Number(e.detail.goldstein).toFixed(1)}
                        {e.geo_precision === "country" && " · country-level location"}
                      </div>
                      {e.url && (
                        <a className="small" href={e.url} target="_blank" rel="noreferrer" onClick={(ev) => ev.stopPropagation()}>
                          {host(e.url)} ↗
                        </a>
                      )}
                    </div>
                  ))}
                  {list.length > 200 && <div className="small muted">+{list.length - 200} more</div>}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="small muted bd-foot">Machine-coded from news by GDELT. Locations are often city or country centroids. Unverified.</div>
    </div>
  );
}
