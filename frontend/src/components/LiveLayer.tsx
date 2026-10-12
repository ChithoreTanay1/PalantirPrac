import { HTMLSelect, Icon, Switch, Tag, Tooltip } from "@blueprintjs/core";
import maplibregl, { type GeoJSONSource, type Map as MLMap } from "maplibre-gl";
import { type MutableRefObject, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";

export const LIVE_COLOR: Record<string, string> = {
  Fighting: "#E76A6E",
  "Mass violence": "#FF4D6A",
  Assault: "#F5498B",
  Coercion: "#EC9A3C",
  Protest: "#F0B726",
  "Military posture": "#BDADFF",
  "Sanctions / embargo": "#4C90F0",
  "Asset seizure": "#2D72D2",
  "Destruction of property": "#D1980B",
};
const newsColor: any = ["match", ["get", "kind"], ...Object.entries(LIVE_COLOR).flat(), "#ABB3BF"];
const STALE_MIN = 6 * 60; // an asset not heard from in 6 h is drawn as "possibly dark"
const BREAKDOWN_ZOOM = 5; // below this a cluster click zooms in; at or above it opens the breakdown

interface LiveAsset {
  id: string; feed: string; kind: string; ts: string; lat: number; lon: number; title: string;
  detail: any; url: string; record_id: string; cluster_id: string | null; track: [number, number, string][];
}

export const ago = (iso: string) => {
  const m = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
  return m < 60 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
};
const esc = (s: any) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

const empty = { type: "FeatureCollection" as const, features: [] as any[] };

function addLayers(map: MLMap) {
  if (map.getSource("live-news")) return;
  map.addSource("live-news", { type: "geojson", data: empty, cluster: true, clusterRadius: 38, clusterMaxZoom: 6 });
  map.addSource("live-assets", { type: "geojson", data: empty });
  map.addSource("live-tracks", { type: "geojson", data: empty });
  map.addSource("live-selection", { type: "geojson", data: empty });
  map.addLayer({ id: "live-tracks", type: "line", source: "live-tracks",
    paint: { "line-color": ["case", ["==", ["get", "feed"], "ais"], "#13C9BA", "#BDADFF"], "line-width": 1.6, "line-opacity": 0.7 } });
  map.addLayer({ id: "live-news-cluster", type: "circle", source: "live-news", filter: ["has", "point_count"],
    paint: {
      "circle-color": "rgba(231,106,110,0.25)", "circle-stroke-color": "#E76A6E", "circle-stroke-width": 1.2,
      "circle-radius": ["interpolate", ["linear"], ["get", "point_count"], 2, 10, 50, 18, 400, 30],
    } });
  map.addLayer({ id: "live-news-count", type: "symbol", source: "live-news", filter: ["has", "point_count"],
    layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": ["Open Sans Bold"], "text-size": 11, "text-allow-overlap": true },
    paint: { "text-color": "#FFD9DA" } });
  map.addLayer({ id: "live-news-pulse", type: "circle", source: "live-news",
    filter: ["all", ["!", ["has", "point_count"]], ["<", ["get", "age_min"], 45]],
    paint: { "circle-radius": 8, "circle-color": newsColor, "circle-opacity": 0.35, "circle-blur": 0.5 } });
  map.addLayer({ id: "live-news", type: "circle", source: "live-news", filter: ["!", ["has", "point_count"]],
    paint: {
      "circle-color": newsColor,
      "circle-radius": ["interpolate", ["linear"], ["get", "mentions"], 1, 3.5, 20, 6, 100, 9],
      "circle-opacity": ["interpolate", ["linear"], ["get", "age_min"], 0, 1, 720, 0.55, 1440, 0.3],
      "circle-stroke-color": "#111418", "circle-stroke-width": 0.8,
    } });
  map.addLayer({ id: "live-selection", type: "circle", source: "live-selection",
    paint: { "circle-radius": 9, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#FFFFFF", "circle-stroke-width": 1.5, "circle-stroke-opacity": 0.9 } });
  map.addLayer({ id: "live-assets-halo", type: "circle", source: "live-assets", filter: ["all", ["==", ["get", "stale"], 0], ["==", ["get", "linked"], 1]],
    paint: { "circle-radius": 12, "circle-color": ["case", ["==", ["get", "feed"], "ais"], "#13C9BA", "#BDADFF"], "circle-opacity": 0.25, "circle-blur": 0.4 } });
  map.addLayer({ id: "live-assets", type: "circle", source: "live-assets",
    paint: {
      "circle-radius": ["case", ["==", ["get", "linked"], 1], 6, 3],
      "circle-color": ["case", ["==", ["get", "stale"], 1], "rgba(0,0,0,0)", ["==", ["get", "linked"], 0],
        ["case", ["==", ["get", "feed"], "ais"], "#0E8F84", "#7E6FCC"], ["==", ["get", "feed"], "ais"], "#13C9BA", "#BDADFF"],
      "circle-stroke-color": ["case", ["==", ["get", "stale"], 1], "#8F99A8", "#FFFFFF"],
      "circle-stroke-width": ["case", ["==", ["get", "linked"], 1], 1.6, 0.6],
    } });
  map.addLayer({ id: "live-assets-label", type: "symbol", source: "live-assets",
    layout: { "text-field": ["get", "label"], "text-font": ["Open Sans Bold"], "text-size": 10.5, "text-offset": [0, 1.3], "text-anchor": "top", "text-optional": true },
    paint: { "text-color": ["case", ["==", ["get", "feed"], "ais"], "#7AE1D8", "#D7CCFF"], "text-halo-color": "#111418", "text-halo-width": 1.4 } });
}

export function useLive(mapRef: MutableRefObject<MLMap | null>, ready: boolean, visible: boolean) {
  const { dispatch } = useStore();
  const [on, setOn] = useState({ news: true, ships: true, aircraft: true });
  const [hours, setHours] = useState(24);
  const [data, setData] = useState<{ news: any[]; assets: LiveAsset[]; generated_at?: string }>({ news: [], assets: [] });
  const [status, setStatus] = useState<any>(null);
  const [kinds, setKinds] = useState<Record<string, boolean>>({});
  const [bulk, setBulk] = useState({ tankers: false, jurisdiction: false });
  const [breakdown, setBreakdown] = useState<{ events: any[]; zoomed: number } | null>(null);
  const timer = useRef<number>();

  // poll while the map/globe is visible
  useEffect(() => {
    if (!visible) return;
    const load = () => {
      const feeds = [on.news && "gdelt", on.ships && "ais", on.aircraft && "opensky"].filter(Boolean).join(",") || "none";
      const b = [bulk.tankers && "tankers", bulk.jurisdiction && "jurisdiction"].filter(Boolean).join(",");
      api.live(hours, feeds, b).then(setData).catch(() => {});
      api.liveStatus().then(setStatus).catch(() => {});
    };
    load();
    timer.current = window.setInterval(load, 60_000);
    return () => window.clearInterval(timer.current);
  }, [visible, hours, on.news, on.ships, on.aircraft, bulk.tankers, bulk.jurisdiction]);

  const news = useMemo(() => data.news.filter((n) => kinds[n.kind] !== false), [data.news, kinds]);

  // layers + interactions (once per map)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    addLayers(map);
    const popup = (lngLat: any, html: string) =>
      new maplibregl.Popup({ maxWidth: "320px", className: "gotham-popup" }).setLngLat(lngLat).setHTML(html).addTo(map);
    const onNews = (e: any) => {
      const p = e.features[0].properties;
      const d = JSON.parse(p.detail || "{}");
      popup(e.lngLat, `<div class="pop"><div class="pop-cls cls-0">LIVE · GDELT · ${esc(ago(p.ts))}</div>
        <div class="pop-type" style="color:${LIVE_COLOR[p.kind] ?? "#ABB3BF"}">${esc(p.kind)}</div>
        <div class="pop-title">${esc(d.place)}</div>
        <div class="pop-meta">${esc(d.actors || "unspecified actors")} · CAMEO ${esc(d.cameo)} · Goldstein ${esc(d.goldstein)}</div>
        <div class="pop-meta">${esc(d.mentions)} mentions · location precision: ${esc(p.geo_precision)}</div>
        <div class="pop-desc"><a href="${esc(p.url)}" target="_blank" rel="noreferrer">Open source article ↗</a></div>
        <div class="pop-src">Machine-coded from news. Unverified.</div></div>`);
    };
    const onAsset = (e: any) => {
      const p = e.features[0].properties;
      const d = JSON.parse(p.detail || "{}");
      const ship = p.feed === "ais";
      popup(e.lngLat, `<div class="pop"><div class="pop-cls cls-2">LIVE · ${ship ? "AIS" : "ADS-B"} · last seen ${esc(ago(p.ts))}${p.stale == 1 ? " · POSSIBLY DARK" : ""}</div>
        <div class="pop-type" style="color:${ship ? "#13C9BA" : "#BDADFF"}">${ship ? "Vessel" : "Aircraft"} · ${p.record_id ? "watchlist match" : esc(d.scope === "tanker" ? "tanker (not on a list)" : "jurisdiction-registered (not on a list)")}</div>
        <div class="pop-title">${esc(p.title)}</div>
        <div class="pop-meta">${p.record_id ? `Linked to: <b>${esc(d.linked)}</b> (${esc(d.match_basis)})` : esc(d.match_basis)}</div>
        <div class="pop-meta">${ship
          ? `MMSI ${esc(d.mmsi)} ${d.imo ? "· IMO " + esc(d.imo) : ""} · ${esc(d.sog_kn ?? "?")} kn · ${esc(d.nav_status ?? "")} ${d.destination ? "· → " + esc(d.destination) : ""}`
          : `ICAO24 ${esc(d.icao24)} · ${d.on_ground ? "on ground" : Math.round(d.altitude_m ?? 0) + " m"} · ${Math.round((d.velocity_ms ?? 0) * 1.944)} kn`}</div>
        <div class="pop-desc"><a href="${esc(p.url)}" target="_blank" rel="noreferrer">Open in public tracker ↗</a></div>
        <div class="pop-src">A match is a lead, not proof. Selected entity shown in the inspector.</div></div>`);
      if (p.cluster_id) dispatch({ type: "focus", id: p.cluster_id });
    };
    const onCluster = async (e: any) => {
      const f = e.features[0];
      const src = map.getSource("live-news") as any;
      const z = map.getZoom();
      if (z < BREAKDOWN_ZOOM) {
        // zoomed out (country / region view): zoom towards the cluster first
        const exp = await src.getClusterExpansionZoom(f.properties.cluster_id);
        map.easeTo({ center: f.geometry.coordinates, zoom: Math.min(Math.max(exp, z + 2), BREAKDOWN_ZOOM + 0.5), duration: 700 });
        return;
      }
      // zoomed in (city level): open the breakdown of everything inside this cluster
      const leaves = await src.getClusterLeaves(f.properties.cluster_id, 10000, 0);
      setBreakdown({
        events: leaves.map((l: any) => ({ ...l.properties, detail: JSON.parse(l.properties.detail || "{}"), lon: l.geometry.coordinates[0], lat: l.geometry.coordinates[1] })),
        zoomed: z,
      });
    };
    const cursor = (v: string) => () => (map.getCanvas().style.cursor = v);
    for (const l of ["live-news", "live-assets", "live-news-cluster"]) {
      map.on("mouseenter", l, cursor("pointer"));
      map.on("mouseleave", l, cursor(""));
    }
    map.on("click", "live-news", onNews);
    map.on("click", "live-assets", onAsset);
    map.on("click", "live-news-cluster", onCluster);
    return () => {
      map.off("click", "live-news", onNews);
      map.off("click", "live-assets", onAsset);
      map.off("click", "live-news-cluster", onCluster);
    };
  }, [ready]); // eslint-disable-line react-hooks/exhaustive-deps

  // data -> sources
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !map.getSource("live-news")) return;
    const t = Date.now();
    (map.getSource("live-news") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: (on.news ? news : []).map((n) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [n.lon, n.lat] },
        properties: { ...n, detail: JSON.stringify(n.detail), mentions: n.detail?.mentions ?? 1, age_min: (t - Date.parse(n.ts)) / 60000 },
      })),
    } as any);
    const assets = data.assets.filter((a) => (a.feed === "ais" ? on.ships : on.aircraft));
    (map.getSource("live-assets") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: assets.map((a) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [a.lon, a.lat] },
        properties: { ...a, detail: JSON.stringify(a.detail), track: undefined, label: a.record_id ? a.title : "", linked: a.record_id ? 1 : 0,
          stale: (t - Date.parse(a.ts)) / 60000 > STALE_MIN ? 1 : 0 },
      })),
    } as any);
    (map.getSource("live-tracks") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: assets.filter((a) => a.track.length > 1).map((a) => ({
        type: "Feature", geometry: { type: "LineString", coordinates: a.track.map((p) => [p[0], p[1]]) }, properties: { feed: a.feed },
      })),
    } as any);
  }, [news, data.assets, on, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !map.getSource("live-selection")) return;
    (map.getSource("live-selection") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: (breakdown?.events ?? []).map((e) => ({ type: "Feature", geometry: { type: "Point", coordinates: [e.lon, e.lat] }, properties: {} })),
    } as any);
  }, [breakdown, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  // pulse recent news
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !visible || !on.news) return;
    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      // ~10 fps is enough for a pulse and avoids repainting the whole map every frame
      if (now - last < 100) {
        raf = requestAnimationFrame(tick);
        return;
      }
      last = now;
      if (map.getLayer("live-news-pulse")) {
        const ph = (performance.now() % 1800) / 1800;
        map.setPaintProperty("live-news-pulse", "circle-radius", 6 + ph * 16);
        map.setPaintProperty("live-news-pulse", "circle-opacity", 0.45 * (1 - ph));
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [ready, visible, on.news]); // eslint-disable-line react-hooks/exhaustive-deps

  return { on, setOn, hours, setHours, data, news, status, kinds, setKinds, bulk, setBulk, breakdown, setBreakdown };
}

export function LivePanel({ live, flyTo }: { live: ReturnType<typeof useLive>; flyTo: (lon: number, lat: number) => void }) {
  const { on, setOn, hours, setHours, data, news, status, kinds, setKinds, bulk, setBulk } = live;
  const { dispatch } = useStore();
  const feed = (name: string) => status?.feeds?.find((f: any) => f.feed === name);
  const counts: Record<string, number> = {};
  for (const n of data.news) counts[n.kind] = (counts[n.kind] ?? 0) + 1;
  const ships = data.assets.filter((a) => a.feed === "ais" && a.record_id);
  const planes = data.assets.filter((a) => a.feed === "opensky" && a.record_id);
  const bulkShips = data.assets.filter((a) => a.feed === "ais" && !a.record_id).length;
  const bulkPlanes = data.assets.filter((a) => a.feed === "opensky" && !a.record_id).length;

  const Dot = ({ f }: { f: any }) => {
    const state = !f ? "off" : f.error ? "err" : f.last_ok ? "ok" : "wait";
    const tip = !f ? "not running" : f.error ? `Error: ${f.error}` : f.last_ok ? `OK · last update ${f.last_ok}${f.auth ? " · " + f.auth : ""}` : "starting…";
    return <Tooltip content={tip} placement="right"><span className={`live-dot ${state}`} /></Tooltip>;
  };

  return (
    <div className="live-panel">
      <div className="live-head">
        <span className="live-badge"><span className="live-blink" />LIVE</span>
        <HTMLSelect minimal value={hours} onChange={(e) => setHours(Number(e.target.value))}
          options={[{ value: 1, label: "last 1 h" }, { value: 6, label: "last 6 h" }, { value: 24, label: "last 24 h" }, { value: 48, label: "last 48 h" }]} />
      </div>
      {status && !status.enabled && <div className="small muted">Live feeds disabled (LIVE_ENABLED=0)</div>}
      <div className="live-row">
        <Dot f={feed("gdelt")} />
        <Switch checked={on.news} label={`News events · ${news.length}`} onChange={() => setOn({ ...on, news: !on.news })} />
      </div>
      {on.news && (
        <div className="live-kinds">
          {Object.entries(LIVE_COLOR).filter(([k]) => counts[k]).map(([k, c]) => (
            <label key={k} className={kinds[k] === false ? "off" : ""} onClick={() => setKinds({ ...kinds, [k]: kinds[k] === false })}>
              <i style={{ background: c }} />{k} <span>{counts[k]}</span>
            </label>
          ))}
        </div>
      )}
      <div className="live-row">
        <Dot f={feed("ais")} />
        <Switch checked={on.ships} label={`Sanctioned ships · ${ships.length}`} onChange={() => setOn({ ...on, ships: !on.ships })} />
      </div>
      {on.ships && (
        <div className="live-sub">
          <Switch checked={bulk.tankers} label={`All tankers${bulk.tankers ? ` · ${bulkShips}` : ""}`} onChange={() => setBulk({ ...bulk, tankers: !bulk.tankers })} />
        </div>
      )}
      <div className="live-row">
        <Dot f={feed("opensky")} />
        <Switch checked={on.aircraft} label={`Sanctioned aircraft · ${planes.length}`} onChange={() => setOn({ ...on, aircraft: !on.aircraft })} />
      </div>
      {on.aircraft && (
        <div className="live-sub">
          <Switch checked={bulk.jurisdiction} label={`Sanctioned-jurisdiction regs${bulk.jurisdiction ? ` · ${bulkPlanes}` : ""}`} onChange={() => setBulk({ ...bulk, jurisdiction: !bulk.jurisdiction })} />
        </div>
      )}
      {(ships.length > 0 || planes.length > 0) && (
        <div className="live-assets">
          {[...ships, ...planes].sort((a, b) => b.ts.localeCompare(a.ts)).slice(0, 12).map((a) => {
            const stale = (Date.now() - Date.parse(a.ts)) / 60000 > STALE_MIN;
            return (
              <button key={a.id} className={`live-asset ${stale ? "stale" : ""}`} onClick={() => { flyTo(a.lon, a.lat); if (a.cluster_id) dispatch({ type: "focus", id: a.cluster_id }); }}>
                <Icon icon={a.feed === "ais" ? "ship" : "airplane"} size={12} />
                <span className="ellipsis grow">{a.title}<span className="muted"> · {a.detail?.linked}</span></span>
                <span className="muted">{ago(a.ts)}</span>
              </button>
            );
          })}
        </div>
      )}
      {data.generated_at && <div className="small muted">Updated {ago(data.generated_at)} · refreshes every 60 s</div>}
      <Tag minimal className="live-note">News is machine-coded and unverified. Watchlist hits are leads.</Tag>
    </div>
  );
}
