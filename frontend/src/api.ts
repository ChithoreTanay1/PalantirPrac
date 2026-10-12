import type { EntityDetail, GEvent, GraphPayload, NodeSummary, OwnershipPayload, PathPayload, User, Quote, MarketHistory } from "./types";

let currentUser = localStorageGet("mg.user") ?? "analyst";

function localStorageGet(k: string): string | null {
  try {
    return window.localStorage.getItem(k);
  } catch {
    return null;
  }
}

export function setApiUser(u: string) {
  currentUser = u;
  try {
    window.localStorage.setItem("mg.user", u);
  } catch {
    /* private mode */
  }
}
export const getApiUser = () => currentUser;

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", "x-user": currentUser, ...(init?.headers || {}) },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${body.slice(0, 200)}`);
  }
  return res.json();
}

const q = (o: Record<string, string | number | undefined>) =>
  new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined) as [string, string][]).toString();

export const api = {
  users: () => req<User[]>("/api/users"),
  me: () => req<User>("/api/me"),
  stats: () => req<any>("/api/stats"),
  analytics: () => req<any[]>("/api/analytics/sources"),
  search: (text: string, schema?: string) => req<NodeSummary[]>(`/api/search?${q({ q: text, schema, limit: 30 })}`),
  entity: (id: string) => req<EntityDetail>(`/api/entity?${q({ id })}`),
  expand: (ids: string[], types?: string[], limit = 60) =>
    req<GraphPayload>("/api/graph/expand", { method: "POST", body: JSON.stringify({ ids, types, limit }) }),
  nodes: (ids: string[]) => req<GraphPayload>("/api/graph/nodes", { method: "POST", body: JSON.stringify({ ids }) }),
  khop: (id: string, k: number) => req<GraphPayload>(`/api/graph/khop?${q({ id, k, limit: 400 })}`),
  path: (a: string, b: string) => req<PathPayload>(`/api/graph/path?${q({ a, b })}`),
  ownership: (id: string, direction: "up" | "down") =>
    req<OwnershipPayload>(`/api/graph/ownership?${q({ id, direction })}`),
  events: (ids: string[]) => req<GEvent[]>("/api/events", { method: "POST", body: JSON.stringify({ ids }) }),
  coMovement: (id: string) => req<{ cluster: NodeSummary; count: number; meetings: { date: string; place: string }[] }[]>(
    `/api/analysis/co-movement?${q({ id })}`),
  cycles: () => req<{ nodes: NodeSummary[] }[]>("/api/analysis/cycles"),
  erQueue: (schema?: string) => req<any[]>(`/api/er/queue?${q({ schema, limit: 40 })}`),
  erEval: () => req<any>("/api/er/eval"),
  judge: (a: string, b: string, judgement: "positive" | "negative", reason?: string) =>
    req<any>("/api/er/judge", { method: "POST", body: JSON.stringify({ a, b, judgement, reason }) }),
  unmerge: (record_id: string, reason?: string) =>
    req<any>("/api/er/unmerge", { method: "POST", body: JSON.stringify({ record_id, reason }) }),
  audit: () => req<any[]>("/api/audit"),
  live: (hours: number, feeds: string, bulk = "") => req<any>(`/api/live?${q({ hours, feeds, bulk })}`),
  liveAssets: (kind: "vessel" | "aircraft", scope: string, text?: string) =>
    req<any>(`/api/live/assets?${q({ kind, scope, q: text || undefined })}`),
  liveAsset: (id: string) => req<any>(`/api/live/asset?${q({ id })}`),
  aircraftFlights: (icao24: string) => req<any[]>(`/api/live/aircraft/flights?${q({ icao24 })}`),
  aircraftTrack: (icao24: string) => req<any>(`/api/live/aircraft/track?${q({ icao24 })}`),
  liveStatus: () => req<any>("/api/live/status"),
  marketQuotes: (tab: string) => req<Quote[]>(`/api/markets/quotes?${q({ tab })}`),
  marketSymbols: (symbols: string[]) => req<Quote[]>(`/api/markets/quotes?${q({ symbols: symbols.join(",") })}`),
  marketHistory: (symbol: string, range: string) => req<MarketHistory>(`/api/markets/history?${q({ symbol, range })}`),
  marketNews: (symbol: string) => req<{ title: string; url?: string; ts?: string; publisher?: string }[]>(`/api/markets/news?${q({ symbol })}`),
};
