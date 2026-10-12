import type { Schema } from "./types";

// Gotham-like palette (Blueprint dark scale + saturated object colours)
export const C = {
  bg0: "#111418",
  bg1: "#1C2127",
  bg2: "#252A31",
  bg3: "#2F343C",
  bg4: "#383E47",
  line: "#404854",
  text: "#F6F7F9",
  muted: "#ABB3BF",
  faint: "#738091",
  blue: "#4C90F0",
  blueDim: "#2D72D2",
  red: "#E76A6E",
  redDim: "#CD4246",
  gold: "#F0B726",
  green: "#32A467",
  orange: "#EC9A3C",
  teal: "#13C9BA",
  violet: "#BDADFF",
};

export const SCHEMA_COLOR: Record<Schema, string> = {
  Person: "#4C90F0",
  Company: "#D1980B",
  Vessel: "#13C9BA",
  Aircraft: "#BDADFF",
  Address: "#8F99A8",
};

export const SCHEMA_ICON: Record<Schema, string> = {
  Person: "person",
  Company: "office",
  Vessel: "ship",
  Aircraft: "airplane",
  Address: "map-marker",
};

export const EDGE_COLOR: Record<string, string> = {
  OWNS: "#F0B726",
  DIRECTS: "#4C90F0",
  REPRESENTS: "#72CA9B",
  FAMILY: "#F5498B",
  ASSOCIATE: "#C87619",
  MEMBER_OF: "#9D3F9D",
  INTERMEDIARY_OF: "#738091",
  REGISTERED_AT: "#5F6B7C",
  LINKED: "#ABB3BF",
};

export const EVENT_COLOR: Record<string, string> = {
  "Sanction designation": "#E76A6E",
  Incorporation: "#D1980B",
  "Struck off": "#8F99A8",
  "Port call / AIS position": "#13C9BA",
  Meeting: "#4C90F0",
  "Source report": "#F5498B",
};
export const eventColor = (t: string) => EVENT_COLOR[t] ?? "#ABB3BF";

export const CLASSIFICATION = [
  { label: "UNCLASSIFIED", color: "#238551" },
  { label: "RESTRICTED", color: "#C87619" },
  { label: "SECRET", color: "#CD4246" },
];

const PATHS: Record<Schema, string> = {
  Person:
    '<circle cx="12" cy="8" r="4"/><path d="M4 20c0-4.2 3.6-7 8-7s8 2.8 8 7v1H4z"/>',
  Company:
    '<path d="M4 21V5l8-3v19zm9 0V8l7 3v10zM6 7h2v2H6zm0 4h2v2H6zm0 4h2v2H6zm9-1h2v2h-2zm0 4h2v2h-2z" fill-rule="evenodd"/>',
  Vessel:
    '<path d="M11 2h2v3h4v6l3 1-3 8H7l-3-8 3-1V5h4zm-2 5v3l3-1 3 1V7z" fill-rule="evenodd"/><path d="M3 21c2 1 4 1 6 0 2 1 4 1 6 0 2 1 4 1 6 0v1.5c-2 1-4 1-6 0-2 1-4 1-6 0-2 1-4 1-6 0z"/>',
  Aircraft:
    '<path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5z"/>',
  Address:
    '<path d="M12 2a7 7 0 0 0-7 7c0 5.2 7 13 7 13s7-7.8 7-13a7 7 0 0 0-7-7zm0 9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5z"/>',
};

const cache = new Map<string, string>();
export function nodeIcon(schema: Schema, color = "#F6F7F9"): string {
  const key = schema + color;
  if (!cache.has(key)) {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="48" height="48" fill="${color}">${PATHS[schema] ?? PATHS.Company}</svg>`;
    cache.set(key, "data:image/svg+xml;utf8," + encodeURIComponent(svg));
  }
  return cache.get(key)!;
}

export const SOURCE_LABEL: Record<string, string> = {
  os_us_ofac_sdn: "OFAC SDN",
  os_eu_fsf: "EU FSF",
  synth_sanctions: "Synthetic sanctions",
  synth_icij: "Synthetic ICIJ",
  synth_ch: "Synthetic Companies House",
  synth_events: "Synthetic events",
  icij: "ICIJ Offshore Leaks",
  ch: "Companies House",
};
export const sourceLabel = (s: string) => SOURCE_LABEL[s] ?? s;
