import React, { createContext, useContext, useEffect, useReducer, useRef } from "react";
import { api } from "./api";
import type { GEdge, GEvent, GraphPayload, NodeSummary, User } from "./types";

export type View = "graph" | "map" | "globe" | "vessels" | "aircraft" | "markets" | "resolution" | "data" | "audit";

export interface State {
  user: User | null;
  view: View;
  nodes: Record<string, NodeSummary>;
  edges: Record<string, GEdge>;
  selected: string[];
  focus: string | null;
  events: GEvent[];
  range: [number, number] | null; // epoch ms
  highlight: { nodes: string[]; edges: string[]; label: string } | null;
  pending: { id: string; anchor?: string }[]; // newly added nodes awaiting placement
  graphRev: number;
  busy: number;
  flyto: { lon: number; lat: number; zoom: number; seq: number } | null; // camera request for the geo view
}

type Action =
  | { type: "user"; user: User }
  | { type: "view"; view: View }
  | { type: "add"; payload: GraphPayload; anchor?: string; select?: string[] }
  | { type: "remove"; ids: string[] }
  | { type: "clear" }
  | { type: "select"; ids: string[] }
  | { type: "focus"; id: string | null }
  | { type: "events"; events: GEvent[] }
  | { type: "range"; range: [number, number] | null }
  | { type: "highlight"; highlight: State["highlight"] }
  | { type: "placed" }
  | { type: "busy"; delta: number }
  | { type: "flyto"; lon: number; lat: number; zoom?: number };

const initial: State = {
  user: null,
  view: "graph",
  nodes: {},
  edges: {},
  selected: [],
  focus: null,
  events: [],
  range: null,
  highlight: null,
  pending: [],
  graphRev: 0,
  busy: 0,
  flyto: null,
};

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "user":
      return { ...s, user: a.user };
    case "view":
      return { ...s, view: a.view };
    case "add": {
      const nodes = { ...s.nodes };
      const pending = [...s.pending];
      for (const n of a.payload.nodes) {
        if (!nodes[n.id]) pending.push({ id: n.id, anchor: a.anchor });
        nodes[n.id] = { ...nodes[n.id], ...n };
      }
      const edges = { ...s.edges };
      for (const e of a.payload.edges) if (nodes[e.source] && nodes[e.target]) edges[e.id] = e;
      return {
        ...s,
        nodes,
        edges,
        pending,
        graphRev: s.graphRev + 1,
        selected: a.select ?? s.selected,
        focus: a.select?.length ? a.select[0] : s.focus,
      };
    }
    case "remove": {
      const drop = new Set(a.ids);
      const nodes = Object.fromEntries(Object.entries(s.nodes).filter(([k]) => !drop.has(k)));
      const edges = Object.fromEntries(
        Object.entries(s.edges).filter(([, e]) => !drop.has(e.source) && !drop.has(e.target)),
      );
      return {
        ...s,
        nodes,
        edges,
        selected: s.selected.filter((x) => !drop.has(x)),
        focus: s.focus && drop.has(s.focus) ? null : s.focus,
        graphRev: s.graphRev + 1,
      };
    }
    case "clear":
      return { ...s, nodes: {}, edges: {}, selected: [], focus: null, events: [], highlight: null, range: null, graphRev: s.graphRev + 1 };
    case "select":
      return { ...s, selected: a.ids, focus: a.ids.length ? a.ids[a.ids.length - 1] : s.focus };
    case "focus":
      return { ...s, focus: a.id };
    case "events":
      return { ...s, events: a.events };
    case "range":
      return { ...s, range: a.range };
    case "highlight":
      return { ...s, highlight: a.highlight };
    case "placed":
      return { ...s, pending: [] };
    case "busy":
      return { ...s, busy: Math.max(0, s.busy + a.delta) };
    case "flyto":
      return { ...s, flyto: { lon: a.lon, lat: a.lat, zoom: a.zoom ?? 6, seq: (s.flyto?.seq ?? 0) + 1 } };
  }
}

interface Ctx {
  state: State;
  dispatch: React.Dispatch<Action>;
  run: <T>(p: Promise<T>) => Promise<T>;
}

const StoreCtx = createContext<Ctx | null>(null);

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initial);
  const run = async <T,>(p: Promise<T>) => {
    dispatch({ type: "busy", delta: 1 });
    try {
      return await p;
    } finally {
      dispatch({ type: "busy", delta: -1 });
    }
  };

  // Keep the event set in sync with whatever is on the graph (debounced).
  const timer = useRef<number>();
  const nodeKey = Object.keys(state.nodes).sort().join("|");
  useEffect(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const ids = Object.keys(state.nodes).slice(0, 600);
      if (!ids.length) return dispatch({ type: "events", events: [] });
      api.events(ids).then((events) => dispatch({ type: "events", events })).catch(() => {});
    }, 250);
  }, [nodeKey, state.user?.username]);

  return <StoreCtx.Provider value={{ state, dispatch, run }}>{children}</StoreCtx.Provider>;
}

export function useStore() {
  const c = useContext(StoreCtx);
  if (!c) throw new Error("no store");
  return c;
}

export function eventsInRange(events: GEvent[], range: [number, number] | null) {
  if (!range) return events;
  return events.filter((e) => {
    const t = Date.parse(e.date);
    return t >= range[0] && t <= range[1];
  });
}
