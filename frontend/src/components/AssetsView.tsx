import { AnchorButton, Button, Callout, HTMLSelect, HTMLTable, Icon, InputGroup, NonIdealState, SegmentedControl, Spinner, Tag } from "@blueprintjs/core";
import maplibregl, { type GeoJSONSource, type Map as MLMap } from "maplibre-gl";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useActions } from "../actions";
import { api } from "../api";
import { useStore } from "../store";
import { ago } from "./LiveLayer";

type Kind = "vessel" | "aircraft";
const STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
const STS = [
  ["Laconian Gulf", 36.55, 22.75], ["Kalamata", 36.95, 22.05], ["Ceuta", 35.95, -5.3], ["Fujairah", 25.2, 56.45],
  ["Sohar", 24.45, 56.8], ["Eastern OPL (Johor)", 1.35, 104.45], ["Lomé", 6.05, 1.3], ["Malta (Hurd Bank)", 35.8, 14.65],
  ["Kerch / Taman", 45.15, 36.65], ["Port Said", 31.35, 32.35],
] as const;

const SHIP_TYPE = (t?: number) => (t == null ? "—" : t >= 80 && t <= 89 ? `Tanker (${t})` : t >= 70 && t <= 79 ? `Cargo (${t})` : String(t));
const fmtTime = (unix?: number) => (unix ? new Date(unix * 1000).toISOString().replace("T", " ").slice(0, 16) : "—");

function MiniMap({ asset, extraTrack, color }: { asset: any; extraTrack: number[][] | null; color: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const map = useRef<MLMap | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const m = new maplibregl.Map({ container: ref.current!, style: STYLE, center: [20, 30], zoom: 2, attributionControl: { compact: true } });
    map.current = m;
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.on("load", () => {
      m.addSource("sts", {
        type: "geojson",
        data: { type: "FeatureCollection", features: STS.map(([n, la, lo]) => ({ type: "Feature", geometry: { type: "Point", coordinates: [lo, la] }, properties: { n } })) } as any,
      });
      m.addSource("track", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addSource("extra", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addSource("pos", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "sts", type: "circle", source: "sts", paint: { "circle-radius": 10, "circle-color": "rgba(236,154,60,.12)", "circle-stroke-color": "#EC9A3C", "circle-stroke-width": 1, "circle-stroke-opacity": 0.6 } });
      m.addLayer({ id: "extra", type: "line", source: "extra", paint: { "line-color": "#F0B726", "line-width": 2, "line-dasharray": [2, 1] } });
      m.addLayer({ id: "track", type: "line", source: "track", paint: { "line-color": color, "line-width": 2, "line-opacity": 0.85 } });
      m.addLayer({ id: "track-pts", type: "circle", source: "track", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": 2.2, "circle-color": color } });
      m.addLayer({ id: "pos", type: "circle", source: "pos", paint: { "circle-radius": 7, "circle-color": color, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
      setReady(true);
    });
    return () => m.remove();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const m = map.current;
    if (!m || !ready || !asset) return;
    const pts: number[][] = asset.track?.map((p: any[]) => [p[0], p[1]]) ?? [];
    (m.getSource("track") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: [
        ...(pts.length > 1 ? [{ type: "Feature", geometry: { type: "LineString", coordinates: pts }, properties: {} }] : []),
        ...pts.map((c) => ({ type: "Feature", geometry: { type: "Point", coordinates: c }, properties: {} })),
      ],
    } as any);
    (m.getSource("pos") as GeoJSONSource).setData({ type: "Feature", geometry: { type: "Point", coordinates: [asset.lon, asset.lat] }, properties: {} } as any);
    (m.getSource("extra") as GeoJSONSource).setData(
      extraTrack && extraTrack.length > 1
        ? ({ type: "Feature", geometry: { type: "LineString", coordinates: extraTrack }, properties: {} } as any)
        : ({ type: "FeatureCollection", features: [] } as any),
    );
    const all = [...pts, ...(extraTrack ?? []), [asset.lon, asset.lat]];
    const b = new maplibregl.LngLatBounds();
    all.forEach((c) => b.extend(c as [number, number]));
    m.fitBounds(b, { padding: 40, maxZoom: 8, duration: 600 });
  }, [asset, extraTrack, ready]);

  return <div ref={ref} className="map-el" />;
}

export default function AssetsView({ kind }: { kind: Kind }) {
  const { state, dispatch } = useStore();
  const actions = useActions();
  const ship = kind === "vessel";
  const [scope, setScope] = useState("watchlist");
  const [text, setText] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [data, setData] = useState<any>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [flights, setFlights] = useState<any[] | null>(null);
  const [osTrack, setOsTrack] = useState<number[][] | null>(null);
  const [osError, setOsError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<string>("ts");
  const [loadingExtra, setLoadingExtra] = useState(false);
  const visible = state.view === (ship ? "vessels" : "aircraft");

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    const load = () => api.liveAssets(kind, scope, text).then((d) => alive && setData(d)).catch(() => {});
    load();
    const t = window.setInterval(load, 60_000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [kind, scope, text, visible]);

  useEffect(() => {
    setFlights(null);
    setOsTrack(null);
    setOsError(null);
    if (!sel) return setDetail(null);
    api.liveAsset(sel).then(setDetail).catch(() => setDetail(null));
  }, [sel]);

  const items = useMemo(() => {
    let list = data?.items ?? [];
    if (statusFilter === "sts") list = list.filter((i: any) => i.sts);
    else if (statusFilter !== "all") list = list.filter((i: any) => i.status === statusFilter);
    const val = (i: any) =>
      sortKey === "ts" ? i.ts : sortKey === "name" ? i.title : sortKey === "speed" ? (ship ? i.detail.sog_kn : i.detail.velocity_ms) ?? -1
        : sortKey === "track" ? i.track_points : i.detail.linked ?? "";
    return [...list].sort((a, b) => {
      const x = val(a), y = val(b);
      return typeof x === "number" ? y - x : String(y).localeCompare(String(x)) * (sortKey === "name" || sortKey === "linked" ? -1 : 1);
    });
  }, [data, statusFilter, sortKey, ship]);

  const loadFlights = async (icao24: string) => {
    setLoadingExtra(true);
    setOsError(null);
    try {
      const [f, t] = await Promise.all([api.aircraftFlights(icao24), api.aircraftTrack(icao24).catch(() => null)]);
      setFlights(f);
      setOsTrack(t?.path ? t.path.map((p: any[]) => [p[2], p[1]]) : null);
    } catch (e: any) {
      setOsError(String(e.message).replace(/^\d+ /, ""));
    } finally {
      setLoadingExtra(false);
    }
  };

  const c = data?.counts ?? {};
  const color = ship ? "#13C9BA" : "#BDADFF";
  const th = (key: string, label: string) => (
    <th onClick={() => setSortKey(key)}>{label}{sortKey === key && <Icon icon="caret-down" size={11} />}</th>
  );

  return (
    <div className="assets-page">
      <div className="page-head">
        <div>
          <h2><Icon icon={ship ? "ship" : "airplane"} size={18} /> {ship ? "Vessel tracking" : "Aircraft tracking"}</h2>
          <p className="muted">
            {ship
              ? "Live AIS from aisstream.io: ships on sanctions lists (by IMO/MMSI) and, optionally, every tanker broadcasting worldwide."
              : "Live ADS-B from OpenSky: sanctioned airframes, sanctioned airlines (callsign) and aircraft registered in sanctioned jurisdictions."}
          </p>
        </div>
        <div className="metric-row">
          <div className="metric"><div className="metric-v">{(data?.items?.length ?? 0).toLocaleString()}</div><div className="metric-l">Tracked</div></div>
          <div className="metric"><div className="metric-v" style={{ color: "#72ca9b" }}>{c.live ?? 0}</div><div className="metric-l">Live &lt;1 h</div></div>
          <div className="metric"><div className="metric-v" style={{ color: "#F0B726" }}>{c.recent ?? 0}</div><div className="metric-l">1–6 h</div></div>
          <div className="metric"><div className="metric-v">{c.dark ?? 0}</div><div className="metric-l">Silent 6 h+</div></div>
          {ship && <div className="metric"><div className="metric-v" style={{ color: "#EC9A3C" }}>{c.sts ?? 0}</div><div className="metric-l">Loitering at STS area</div></div>}
        </div>
      </div>

      <div className="assets-toolbar">
        <SegmentedControl small value={scope} onValueChange={setScope}
          options={[
            { value: "watchlist", label: ship ? "Sanctions list matches" : "Sanctioned airframes & airlines" },
            { value: "bulk", label: ship ? "All tankers" : "Sanctioned-jurisdiction registrations" },
            { value: "all", label: "Everything" },
          ]} />
        <HTMLSelect value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
          options={[{ value: "all", label: "Any status" }, { value: "live", label: "Live (<1 h)" }, { value: "recent", label: "Recent (1–6 h)" },
            { value: "dark", label: "Silent 6 h+" }, ...(ship ? [{ value: "sts", label: "At STS area" }] : [])]} />
        <InputGroup leftIcon="search" placeholder={ship ? "Name, MMSI, IMO, destination…" : "Callsign, ICAO24, registration…"}
          value={text} onChange={(e) => setText(e.target.value)} className="grow" />
        <span className="small muted">{data?.totals ? `${data.totals.watchlist ?? 0} list matches · ${data.totals.bulk ?? 0} other tracked` : ""} · refreshes every 60 s</span>
      </div>

      <div className="assets-body">
        <div className="assets-table">
          {!data ? <Spinner className="mt20" /> : !items.length ? (
            <NonIdealState icon={ship ? "ship" : "airplane"} title="Nothing tracked yet"
              description={ship ? "Needs AISSTREAM_API_KEY in backend/.env. Tankers appear once their static data has been received (a few minutes)."
                : "Watchlisted aircraft appear when airborne and in OpenSky receiver coverage."} />
          ) : (
            <HTMLTable compact interactive striped>
              <thead>
                <tr>
                  {th("name", ship ? "Vessel" : "Callsign")}
                  <th>Status</th>
                  {th("ts", "Last seen")}
                  {th("linked", "Linked entity / basis")}
                  {ship ? <><th>MMSI</th><th>IMO</th><th>Type</th>{th("speed", "kn")}<th>Nav status</th><th>Destination</th><th>STS area</th></>
                    : <><th>ICAO24</th><th>Reg.</th><th>Country</th><th>Alt (m)</th>{th("speed", "kn")}</>}
                  {th("track", "Track pts")}
                </tr>
              </thead>
              <tbody>
                {items.map((i: any) => (
                  <tr key={i.id} className={sel === i.id ? "sel" : ""} onClick={() => setSel(i.id)}>
                    <td><b>{i.title}</b></td>
                    <td><span className={`status-pill ${i.status}`}>{i.status === "dark" ? "SILENT" : i.status.toUpperCase()}</span></td>
                    <td>{ago(i.ts)}</td>
                    <td title={i.detail.match_basis}>{i.detail.linked ? <span style={{ color: "#FFB3B5" }}>{i.detail.linked}</span> : <span className="muted">{i.detail.match_basis}</span>}</td>
                    {ship ? (
                      <>
                        <td className="mono">{i.detail.mmsi}</td>
                        <td className="mono">{i.detail.imo || "—"}</td>
                        <td>{SHIP_TYPE(i.detail.ship_type)}</td>
                        <td>{i.detail.sog_kn ?? "—"}</td>
                        <td>{i.detail.nav_status ?? "—"}</td>
                        <td>{i.detail.destination || "—"}</td>
                        <td>{i.sts ? <Tag minimal intent="warning">{i.sts.area}</Tag> : ""}</td>
                      </>
                    ) : (
                      <>
                        <td className="mono">{i.detail.icao24}</td>
                        <td>{i.detail.registration || "—"}</td>
                        <td>{i.detail.origin_country}</td>
                        <td>{i.detail.on_ground ? "ground" : Math.round(i.detail.altitude_m ?? 0)}</td>
                        <td>{i.detail.velocity_ms != null ? Math.round(i.detail.velocity_ms * 1.944) : "—"}</td>
                      </>
                    )}
                    <td>{i.track_points}</td>
                  </tr>
                ))}
              </tbody>
            </HTMLTable>
          )}
        </div>

        <div className="asset-detail">
          <div className="mini-map">
            <MiniMap asset={detail} extraTrack={osTrack} color={color} />
          </div>
          {!detail ? (
            <NonIdealState icon="select" title={`Select a ${ship ? "vessel" : "aircraft"}`} description="Track, history and links appear here." />
          ) : (
            <div className="asset-detail-body">
              <h3>{detail.title}</h3>
              <div className="insp-tags">
                <span className={`status-pill ${detail.status}`}>{detail.status === "dark" ? "SILENT 6 H+" : detail.status.toUpperCase()}</span>
                <Tag minimal>last seen {ago(detail.ts)}</Tag>
                {detail.record_id ? <Tag intent="danger" icon="ban-circle">ON SANCTIONS LIST</Tag> : <Tag minimal>not listed</Tag>}
              </div>
              {detail.record_id && (
                <Callout intent="danger" className="mt10 small" icon="link">
                  Linked to <b>{detail.detail.linked}</b> · basis: {detail.detail.match_basis}
                  <div className="row-actions">
                    <Button small intent="primary" icon="graph" text="Open in graph"
                      onClick={() => { if (detail.cluster_id) { actions.addEntity(detail.cluster_id); dispatch({ type: "view", view: "graph" }); } }} />
                  </div>
                </Callout>
              )}
              <div className="row-actions mb10">
                <Button small icon="globe" text="Show on globe" onClick={() => { dispatch({ type: "view", view: "globe" }); dispatch({ type: "flyto", lon: detail.lon, lat: detail.lat, zoom: 6 }); }} />
                <AnchorButton small minimal icon="share" text={ship ? "MarineTraffic" : "ADS-B Exchange"} href={detail.url} target="_blank" />
              </div>
              <div className="kv-grid small">
                <span>Position</span><span className="mono">{detail.lat.toFixed(4)}, {detail.lon.toFixed(4)}</span>
                {Object.entries(detail.detail).filter(([k, v]) => v != null && v !== "" && !["linked", "match_basis", "scope"].includes(k)).map(([k, v]) => (
                  <Fragment key={k}><span>{k.replace(/_/g, " ")}</span><span className="mono">{String(v)}</span></Fragment>
                ))}
                <span>Stored track</span><span>{detail.track.length} points{detail.track.length ? ` since ${ago(detail.track[0][2])}` : ""}</span>
              </div>
              {ship && (
                <>
                  <div className="section-title">STS-area visits (stored track)</div>
                  {detail.sts_visits.length ? detail.sts_visits.map((v: any) => (
                    <div key={v.area + v.first_seen} className="small">{v.area} · {ago(v.first_seen)}</div>
                  )) : <div className="small muted">None recorded.</div>}
                </>
              )}
              <div className="section-title">Reporting gaps &gt; 6 h</div>
              {detail.reporting_gaps.length ? detail.reporting_gaps.map((g: any) => (
                <div key={g.from} className="small">{g.hours} h silent · from {g.from.replace("T", " ").slice(0, 16)} to {g.to.replace("T", " ").slice(0, 16)}</div>
              )) : <div className="small muted">None in stored track.</div>}
              {!ship && (
                <>
                  <div className="section-title">OpenSky flight history (last 2 days)</div>
                  {!flights && !osError && (
                    <Button small icon="history" loading={loadingExtra} text="Load flights & current track" onClick={() => loadFlights(detail.detail.icao24)} />
                  )}
                  {osError && <Callout intent="warning" className="small">{osError}</Callout>}
                  {flights && (flights.length ? (
                    <HTMLTable compact className="flights-table">
                      <thead><tr><th>Departed (UTC)</th><th>From</th><th>To</th><th>Callsign</th></tr></thead>
                      <tbody>
                        {flights.map((f: any) => (
                          <tr key={f.firstSeen}><td>{fmtTime(f.firstSeen)}</td><td>{f.estDepartureAirport ?? "?"}</td><td>{f.estArrivalAirport ?? "?"}</td><td>{f.callsign?.trim()}</td></tr>
                        ))}
                      </tbody>
                    </HTMLTable>
                  ) : <div className="small muted">No flights recorded by OpenSky in the last 2 days.</div>)}
                  {osTrack && <div className="small muted mt10">Dashed gold line: current flight path from OpenSky ({osTrack.length} waypoints).</div>}
                </>
              )}
              <div className="small muted mt10">A match is a lead, not proof. Transponders can be spoofed, swapped or switched off.</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
