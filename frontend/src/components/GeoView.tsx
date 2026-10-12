import { Button, ButtonGroup, Switch, Tag } from "@blueprintjs/core";
import maplibregl, { type GeoJSONSource, type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { eventsInRange, useStore } from "../store";
import { EVENT_COLOR, SCHEMA_COLOR, eventColor } from "../theme";
import type { GEvent } from "../types";
import { LivePanel, useLive } from "./LiveLayer";
import NewsBreakdown from "./NewsBreakdown";

const STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

const colorExpr: any = ["match", ["get", "type"], ...Object.entries(EVENT_COLOR).flat(), "#ABB3BF"];

function toGeoJSON(events: GEvent[], focus: string | null, captions: Record<string, string>) {
  return {
    type: "FeatureCollection" as const,
    features: events
      .filter((e) => e.lat != null && e.lon != null)
      .map((e) => ({
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [e.lon!, e.lat!] },
        properties: {
          id: e.id,
          cluster: e.cluster_id,
          caption: captions[e.cluster_id] ?? e.cluster_id,
          type: e.type,
          date: e.date,
          place: e.place ?? "",
          description: e.description ?? "",
          exact: e.geo_precision === "exact" ? 1 : 0,
          focus: e.cluster_id === focus ? 1 : 0,
          classification: e.classification,
          source: e.source,
        },
      })),
  };
}

function tracks(events: GEvent[], schemas: Record<string, string>) {
  const by: Record<string, GEvent[]> = {};
  for (const e of events) if (e.geo_precision === "exact" && e.lat != null) (by[e.cluster_id] ??= []).push(e);
  return {
    type: "FeatureCollection" as const,
    features: Object.entries(by)
      .filter(([, evs]) => evs.length > 1)
      .map(([cid, evs]) => ({
        type: "Feature" as const,
        geometry: {
          type: "LineString" as const,
          coordinates: evs.sort((a, b) => a.date.localeCompare(b.date)).map((e) => [e.lon!, e.lat!]),
        },
        properties: { cluster: cid, color: (SCHEMA_COLOR as any)[schemas[cid]] ?? "#ABB3BF" },
      })),
  };
}

export default function GeoView({ globe, visible }: { globe: boolean; visible: boolean }) {
  const { state, dispatch } = useStore();
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const [ready, setReady] = useState(false);
  const [showTracks, setShowTracks] = useState(true);
  const [spin, setSpin] = useState(true);
  const interacting = useRef(false);

  const live = useLive(mapRef, ready, visible);
  const flyTo = (lon: number, lat: number, zoom?: number) => {
    interacting.current = true;
    mapRef.current?.flyTo({ center: [lon, lat], zoom: zoom ?? Math.max(mapRef.current.getZoom(), 5), duration: 1200 });
  };
  const filtered = useMemo(() => eventsInRange(state.events, state.range), [state.events, state.range]);
  const captions = useMemo(() => Object.fromEntries(Object.values(state.nodes).map((n) => [n.id, n.caption])), [state.nodes]);
  const schemas = useMemo(() => Object.fromEntries(Object.values(state.nodes).map((n) => [n.id, n.schema])), [state.nodes]);

  useEffect(() => {
    const map = new maplibregl.Map({
      container: ref.current!,
      style: STYLE,
      center: [30, 35],
      zoom: 2.2,
      attributionControl: { compact: true },
    });
    mapRef.current = map;
    (window as any).__map = map; // debugging / automated UI checks
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
    map.on("load", () => {
      map.addSource("events", { type: "geojson", data: toGeoJSON([], null, {}) });
      map.addSource("tracks", { type: "geojson", data: tracks([], {}) });
      map.addLayer({
        id: "tracks",
        type: "line",
        source: "tracks",
        paint: { "line-color": ["get", "color"], "line-width": 1.6, "line-opacity": 0.55, "line-dasharray": [2, 1.5] },
      });
      map.addLayer({
        id: "events-halo",
        type: "circle",
        source: "events",
        filter: ["==", ["get", "focus"], 1],
        paint: { "circle-radius": 13, "circle-color": "#4C90F0", "circle-opacity": 0.18, "circle-blur": 0.4 },
      });
      map.addLayer({
        id: "events-country",
        type: "circle",
        source: "events",
        filter: ["==", ["get", "exact"], 0],
        paint: {
          "circle-radius": 5,
          "circle-color": "rgba(0,0,0,0)",
          "circle-stroke-color": colorExpr,
          "circle-stroke-width": 1.5,
          "circle-stroke-opacity": 0.8,
        },
      });
      map.addLayer({
        id: "events-exact",
        type: "circle",
        source: "events",
        filter: ["==", ["get", "exact"], 1],
        paint: {
          "circle-radius": ["case", ["==", ["get", "focus"], 1], 7, 5.5],
          "circle-color": colorExpr,
          "circle-stroke-color": ["case", ["==", ["get", "focus"], 1], "#FFFFFF", "#111418"],
          "circle-stroke-width": 1.2,
        },
      });
      for (const layer of ["events-exact", "events-country"]) {
        map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
        map.on("click", layer, (e) => {
          const f = e.features?.[0];
          if (!f) return;
          const p = f.properties as any;
          const cls = ["UNCLASSIFIED", "RESTRICTED", "SECRET"][p.classification] ?? "";
          const html = `<div class="pop">
              <div class="pop-cls cls-${p.classification}">${cls}</div>
              <div class="pop-type" style="color:${eventColor(p.type)}">${p.type}</div>
              <div class="pop-title">${escapeHtml(p.caption)}</div>
              <div class="pop-meta">${p.date} · ${escapeHtml(p.place || "")} ${p.exact ? "" : "(country-level location)"}</div>
              <div class="pop-desc">${escapeHtml(p.description)}</div>
              <div class="pop-src">Source: ${escapeHtml(p.source)}</div></div>`;
          new maplibregl.Popup({ closeButton: true, maxWidth: "300px", className: "gotham-popup" })
            .setLngLat((f.geometry as any).coordinates)
            .setHTML(html)
            .addTo(map);
          dispatch({ type: "select", ids: [p.cluster] });
        });
      }
      setReady(true);
    });
    const stop = () => (interacting.current = true);
    map.on("mousedown", stop);
    map.on("touchstart", stop);
    map.on("wheel", stop);
    return () => map.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // projection switch (MapLibre v5 globe)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.setProjection({ type: globe ? "globe" : "mercator" });
    if (globe) {
      try {
        (map as any).setSky?.({
          "sky-color": "#0B1020",
          "horizon-color": "#1C2A4A",
          "fog-color": "#111418",
          "sky-horizon-blend": 0.6,
          "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 1, 7, 0],
        });
      } catch {
        /* older style spec */
      }
      if (map.getZoom() > 3) map.easeTo({ zoom: 1.8, duration: 800 });
      interacting.current = false;
    }
  }, [globe, ready]);

  // slow rotation on the globe until the user grabs it
  useEffect(() => {
    if (!globe || !visible || !spin) return;
    let raf = 0;
    const tick = () => {
      const map = mapRef.current;
      if (map && ready && !interacting.current && map.getZoom() < 3.5) {
        const c = map.getCenter();
        map.setCenter([c.lng + 0.06, c.lat]);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [globe, visible, spin, ready]);

  useEffect(() => {
    if (visible) window.setTimeout(() => mapRef.current?.resize(), 30);
  }, [visible]);

  // camera requests from other views ("show on globe")
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !state.flyto) return;
    interacting.current = true;
    window.setTimeout(() => map.flyTo({ center: [state.flyto!.lon, state.flyto!.lat], zoom: state.flyto!.zoom, duration: 1400 }), 60);
  }, [state.flyto?.seq, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  // data
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    (map.getSource("events") as GeoJSONSource).setData(toGeoJSON(filtered, state.focus, captions) as any);
    (map.getSource("tracks") as GeoJSONSource).setData((showTracks ? tracks(filtered, schemas) : tracks([], {})) as any);
  }, [filtered, state.focus, captions, schemas, ready, showTracks]);

  // fly to focused entity
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !visible || !state.focus) return;
    const pts = filtered.filter((e) => e.cluster_id === state.focus && e.lat != null);
    if (!pts.length) return;
    interacting.current = true;
    const b = new maplibregl.LngLatBounds();
    pts.forEach((e) => b.extend([e.lon!, e.lat!]));
    map.fitBounds(b, { padding: 120, maxZoom: globe ? 4 : 6, duration: 900 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.focus, visible, ready]);

  const located = new Set(filtered.filter((e) => e.lat != null).map((e) => e.cluster_id)).size;
  const types = [...new Set(filtered.map((e) => e.type))];

  return (
    <div className="geo-wrap">
      <div ref={ref} className="map" />
      <div className="map-side">
      <div className="map-panel">
        <div className="panel-title">{globe ? "GLOBE" : "MAP"} · EVENTS</div>
        <div className="map-stats">
          <b>{filtered.length}</b> events · <b>{located}</b> located objects
          {state.range && <Tag minimal intent="primary" className="ml6">time-filtered</Tag>}
        </div>
        <div className="map-legend">
          {types.map((t) => (
            <span key={t}><i style={{ background: eventColor(t) }} />{t}</span>
          ))}
          <span><i className="ring" />Country-level location</span>
        </div>
        <Switch checked={showTracks} label="Movement tracks" onChange={() => setShowTracks(!showTracks)} />
        {globe && <Switch checked={spin} label="Auto-rotate" onChange={() => { setSpin(!spin); interacting.current = false; }} />}
        <ButtonGroup minimal fill>
          <Button icon="map" active={!globe} text="2D" onClick={() => dispatch({ type: "view", view: "map" })} />
          <Button icon="globe" active={globe} text="Globe" onClick={() => dispatch({ type: "view", view: "globe" })} />
        </ButtonGroup>
        {!state.events.length && <p className="muted small">Add objects to the investigation to see their events here.</p>}
      </div>
      <LivePanel live={live} flyTo={flyTo} />
      </div>
      <NewsBreakdown live={live} flyTo={flyTo} />
    </div>
  );
}

function escapeHtml(s: string) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
