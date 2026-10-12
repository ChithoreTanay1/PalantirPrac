export type Schema = "Person" | "Company" | "Vessel" | "Aircraft" | "Address";

export interface User {
  username: string;
  clearance: number;
  display?: string;
  clearance_label: string;
}

export interface NodeSummary {
  id: string;
  schema: Schema;
  caption: string;
  countries: string[];
  sources: string[];
  n_records: number;
  sanctioned: boolean;
  synthetic: boolean;
  classification: number;
  depth?: number;
}

export interface GEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  roles: string[];
  share: number | null;
  edge_ids: string[];
  sources: string[];
}

export interface GraphPayload {
  nodes: NodeSummary[];
  edges: GEdge[];
  truncated?: number | boolean;
}

export interface PathPayload extends GraphPayload {
  found: boolean;
  path: string[];
  hops?: number;
}

export interface OwnershipPayload extends GraphPayload {
  direction: "up" | "down";
  chain: { node: string; parent: string | null; depth: number; eff_share: number; known: number; cycle: number }[];
  terminals: { node: string; eff_share: number; paths: number; exact: boolean; depth: number }[];
  cycles: string[][];
}

export interface GEvent {
  id: string;
  source: string;
  record_id: string;
  cluster_id: string;
  type: string;
  date: string;
  lat: number | null;
  lon: number | null;
  geo_precision: string | null;
  place: string | null;
  country: string | null;
  description: string | null;
  classification: number;
  source_url: string | null;
  source_doc: string | null;
}

export interface RecordRow {
  id: string;
  source: string;
  source_id: string;
  schema: string;
  caption: string;
  names: string[];
  countries: string[];
  identifiers: string[];
  birth_date: string | null;
  props: Record<string, any>;
  classification: number;
  synthetic: number;
  source_url: string | null;
  source_doc: string | null;
  run_id: string;
  ingested_at: string;
}

export interface EntityDetail {
  summary: NodeSummary;
  records: RecordRow[];
  properties: Record<string, { value: string; records: string[] }[]>;
  sanctions: { program: string; authority: string; reason?: string; start_date?: string; record: string; source_url?: string }[];
  evidence: { a: string; b: string; score: number; features: any }[];
  judgements: { id: number; a: string; b: string; judgement: string; username: string; reason: string; created_at: string }[];
  suggestions: { cluster: NodeSummary; score: number; features: any; a: string; b: string }[];
  degree: number;
}

export interface Quote {
  symbol: string;
  name: string;
  group: string;
  price: number | null;
  change: number | null;
  change_pct: number | null;
  spark: (number | null)[];
  ts: string | null;
}

export interface MarketHistory {
  symbol: string;
  range: string;
  interval: string;
  points: { t: string; o: number | null; h: number | null; l: number | null; c: number; v: number | null }[];
  meta: { currency?: string; exchange?: string; year_high?: number; year_low?: number; market_cap?: number; prev_close?: number };
}
