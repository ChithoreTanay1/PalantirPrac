# Mini-Gotham: Illicit Finance & Sanctions Graph

> A personal learning project: a Gotham-style analyst workbench for following sanctioned people, companies, ships and aircraft through offshore leaks, company registries and live tracking feeds.

![stack](https://img.shields.io/badge/stack-FastAPI%20%7C%20SQLite%2FPostgres%20%7C%20DuckDB%20%7C%20React%20%7C%20Cytoscape%20%7C%20MapLibre-2d72d2)
![data](https://img.shields.io/badge/data-public%20%2B%20synthetic-2e7d32)

It uses **public data** (sanctions lists, ICIJ Offshore Leaks, Companies House, GLEIF) plus clearly flagged **synthetic** data. The design notes are in [`gotham.md`](gotham.md).

![Map view with live news events, a sanctioned aircraft track and the entity inspector](docs/screenshot-map.png)

---

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Data sources](#data-sources)
- [CLI reference](#cli-reference)
- [Live layer](#live-layer)
- [Architecture](#architecture)
- [API](#api)
- [Project layout](#project-layout)
- [Testing & benchmarks](#testing--benchmarks)
- [Deployment](#deployment)
- [Caveats](#caveats)

---

## Features

| Area | What it does | Where |
|---|---|---|
| **Ingest & normalise** | Six source families go into one schema with full provenance. Pipeline: raw → Parquet staging (queried with DuckDB) → DB. Deterministic ids and content hashes make re-runs idempotent. Each run reports insert/update/unchanged/delete counts. | `backend/gotham/ingest/` |
| **Focused bulk ingest** | ICIJ, Companies House and GLEIF dumps are trimmed to the subgraph within *N* hops of a sanctions match. Registered-agent hubs are never expanded through. | `ingest/focus.py` |
| **Entity resolution** | Blocking on rare identifiers and phonetic token pairs, then explainable pairwise scoring and union-find clustering. Scoring handles typos and transliteration, strips legal forms, applies a digit veto, and weighs DOB/country/identifier evidence. Merge/unmerge decisions go to an append-only `judgements` audit trail. | `gotham/resolve.py` |
| **Graph explorer** | Search-around, k-hop (recursive CTE), bidirectional-BFS shortest path, ownership unwinding up/down with effective share, ownership-cycle finder, co-movement. | `gotham/graph.py` |
| **Map, globe & timeline** | MapLibre 2D map and globe projection with movement tracks. A brushable timeline filters the map and dims the graph. | `frontend/src/components/` |
| **Live layer** | GDELT news events, OpenSky aircraft and aisstream.io ships, linked to sanctioned records by identifier. | `gotham/live/` |
| **Vessels & aircraft** | Tracked-asset views with mini-maps, flight history, and known ship-to-ship transfer hotspots. | `AssetsView.tsx`, `live/hotspots.py` |
| **Markets** | Delayed quotes and history for indices, commodities, FX, crypto, rates and defence/shipping stocks. Data comes from yfinance. | `gotham/markets.py` |
| **Access control & audit** | Clearance levels (`analyst` → `investigator` → `admin`). Records above your clearance are never returned, and every API access is logged to `audit_log`. | `gotham/api.py` |

---

## Quick start

**Requirements:** Python 3.12, Node 18+.

### 1. Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip

.venv/Scripts/python -m gotham.cli download        # OFAC SDN + EU FSF from OpenSanctions (~70 MB)
.venv/Scripts/python -m gotham.cli bootstrap       # synthetic data + ingest everything + entity resolution
.venv/Scripts/python -m uvicorn gotham.api:app --port 8000
```

### 2. Frontend

```bash
cd frontend
npm install
npm run build      # the backend serves it at http://127.0.0.1:8000
# or, for development:
npm run dev        # http://localhost:5173 (proxies /api to :8000)
```

### 3. Optional: Postgres instead of SQLite

SQLite (`data/gotham.db`) is the default. To use Postgres:

```bash
docker compose up -d
set DATABASE_URL=postgresql+psycopg2://gotham:gotham@localhost:5432/gotham   # PowerShell: $env:DATABASE_URL="..."
```

All SQL is written to run unchanged on SQLite ≥ 3.35 and Postgres ≥ 12.

---

## Data sources

### Real (public)

| Source | Contents | Records loaded* |
|---|---|---|
| **OpenSanctions** (18 lists) | US OFAC SDN & Consolidated, US Trade CSL & BIS Denied, EU FSF, UK HMT & FCDO, UN SC, Switzerland, Canada, Australia, New Zealand, Japan, France, Belgium, Ukraine NSDC & War Sanctions, Israel MoD | ~166,600 |
| **ICIJ Offshore Leaks** | Offshore Leaks, Panama, Paradise, Pandora Papers, Bahamas Leaks | 283,734 of 1.6M nodes |
| **Companies House** | UK basic company data + persons with significant control (PSC) | 276,975 of 5.7M companies |
| **GLEIF** | Legal Entity Identifiers + parent/child relationships | 56,182 of 3.46M LEIs |
| **OpenSky aircraft DB** | ICAO24 ↔ tail number mapping for the aircraft watchlist | reference only |

<sub>*Counts are for focused mode (`--hops 2`) on this machine. In total: **786,658 records**, which entity resolution groups into **55,075 merged clusters**. The SQLite DB is about 1.8 GB.</sub>

OpenSanctions has already merged entities across these lists. We ingest each record under its list's **native** id and keep the canonical id only as hidden ground truth. Our ER therefore has to re-discover the cross-list merges, and `cli eval` measures how well it does.

### Synthetic (`gotham/synth.py`)

This is fictional data written in the real ICIJ, Companies House and FtM formats, so the production adapters run on it. It contains planted patterns that the tests check for:

| Pattern | What's planted | Search for |
|---|---|---|
| **P1** Duplicate identity | One sanctioned person spelled four ways across three sources | `Viktor Arkadyevich Morozan` |
| **P2** Hidden ownership | Sanctioned owner → BVI shell → Panama shell → UK company → UK company | `Kestrel Bay Holdings`, `Northgate Marine` |
| **P3** Shell ring | Circular ownership sharing an address and an intermediary | `Halcyon Ridge`, `Corvid Point`, `Basalt Crest` |
| **P4** Synchronised vessels | Two vessels at the same anchorage on the same day, repeatedly | vessels `Velmora`, `Ostrel` |
| **P5** Classified data | RESTRICTED/SECRET events and links for permission tests | `Rustam Belashev` |

### What you can search

People, companies, vessels, aircraft and addresses. For example: `Deripaska`, `Abramovich`, `Prigozhin`, `Wagner`, `Rosneft`, `Sberbank`, `Huawei`, `Mahan Air`, `Mossack Fonseca`, `Roldugin`.

> **Note:** in focused mode only leak and registry entries **linked to a sanctioned party** are loaded. For example, Panama Papers entries with no sanctions link (such as Messi or Gunnlaugsson/Wintris) are in the raw dump but not in the DB. Use `--full` to load everything.

---

## CLI reference

Run from `backend/`: `.venv/Scripts/python -m gotham.cli <command>`

| Command | Description |
|---|---|
| `download [targets…]` | Fetch raw data into `data/raw/`. Targets: any OpenSanctions dataset name, or the groups `sanctions`, `icij`, `ch`, `gleif`, `all`. Existing files are skipped unless `--force` is given. |
| `synth [--seed N] [--n N]` | Generate the synthetic dataset. |
| `ingest <source> [path]` | `opensanctions` · `icij` · `ch` · `gleif` · `synth` |
| `resolve [--threshold X]` | Run entity resolution (blocking → scoring → clustering). |
| `eval` | ER precision / recall / F1 against ground truth. |
| `bootstrap [--no-real] [--full] [--hops N]` | One-shot setup: synth + ingest everything present in `data/raw/` + resolve. |
| `reset` | Delete the local SQLite DB and staging files. |

**Ingest flags** for `icij`, `ch` and `gleif`:

| Flag | Effect |
|---|---|
| *(default)* | **Focused**: keep only nodes within `--hops` relationships of a sanctions match |
| `--hops N` | Size of the focused neighbourhood (default `2`) |
| `--full` | Ingest the whole dump. ER is in-memory, so expect it to be slow and memory-hungry. |
| `--limit N` | Only the first N rows per file (smoke tests) |

**Typical full run** (downloads about 3 GB; the ingest took around 75 minutes here, mostly the Companies House scan):

```bash
python -m gotham.cli download all
python -m gotham.cli bootstrap
```

Re-running any ingest is safe. Unchanged rows are detected by content hash and skipped.

---

## Live layer

These free, near-real-time feeds run inside the backend process. They are kept **separate from the investigation graph**: short-lived (24–48 h), not entity-resolved, and linked to graph objects only when an identifier (IMO / MMSI / ICAO24 / airline code) matches.

| Feed | Refresh | Needs | Shows |
|---|---|---|---|
| **GDELT 2.0** events | 15 min (polled every 5 min) | nothing | Fighting, assault, coercion, protest, sanctions/embargo… from world news |
| **OpenSky** ADS-B | every 120 s | `OPENSKY_CLIENT_ID` / `SECRET` (optional, much higher limits) | Sanctioned airframes (tail/serial → ICAO24) and sanctioned airlines (callsign prefix) |
| **aisstream.io** AIS | streaming | `AISSTREAM_API_KEY` | Ships whose IMO/MMSI is on a sanctions list. Hollow marker = silent for 6 h+ ("possibly dark"). |

### Configuration

Copy `backend/.env.example` to `backend/.env` (it's git-ignored) and restart the backend:

```ini
LIVE_ENABLED=1              # master switch (tests set 0)
LIVE_HOURS=24               # how long news events are kept
GDELT_BACKFILL_HOURS=6      # history fetched on first start
AISSTREAM_API_KEY=          # https://aisstream.io/apikeys
OPENSKY_CLIENT_ID=          # OpenSky → Account → API client
OPENSKY_CLIENT_SECRET=
OPENSKY_INTERVAL=120        # seconds between OpenSky polls
```

Feed health shows as coloured dots in the **LIVE** panel and at `/api/live/status`.

---

## Architecture

```
 data/raw/                 data/staging/            data/gotham.db              frontend/
 ─────────                 ─────────────            ──────────────              ─────────
 OpenSanctions FtM ─┐                               records  (entities)
 ICIJ CSV ──────────┤  ingest   Parquet  ──DuckDB──▶ edges    (relationships)     React + Blueprint
 Companies House ───┼────────▶  per source          events   (dated / located)  ◀─▶ Cytoscape graph
 GLEIF ─────────────┤                               resolution (clusters)          MapLibre map/globe
 synthetic ─────────┘                               gedges   (graph index)         Timeline · Markets
                                    resolve.py ───▶ er_candidates, judgements
 GDELT / OpenSky / AIS ──── live/runner.py ───────▶ live_events, live_track
                                                         ▲
                                              FastAPI (gotham/api.py)
                                     clearance filter + audit_log on every call
```

---

## API

The FastAPI app is served at `http://127.0.0.1:8000`. Interactive docs are at `/docs`.

| Group | Endpoints |
|---|---|
| **Session** | `GET /api/users` · `/api/me` · `/api/stats` · `/api/analytics/sources` |
| **Search & entities** | `GET /api/search` · `/api/entity` |
| **Graph** | `POST /api/graph/expand` · `/api/graph/nodes` · `GET /api/graph/khop` · `/api/graph/path` · `/api/graph/ownership` |
| **Analysis** | `POST /api/events` · `GET /api/analysis/co-movement` · `/api/analysis/cycles` |
| **Entity resolution** | `GET /api/er/queue` · `POST /api/er/judge` · `/api/er/unmerge` · `GET /api/er/eval` |
| **Audit** | `GET /api/audit` |
| **Live** | `GET /api/live` · `/api/live/assets` · `/api/live/asset` · `/api/live/aircraft/flights` · `/api/live/aircraft/track` · `/api/live/status` |
| **Markets** | `GET /api/markets/quotes` · `/api/markets/history` · `/api/markets/news` |

---

## Project layout

```
PalantirPrac/
├── backend/
│   ├── gotham/
│   │   ├── api.py            # FastAPI app, access control, audit log
│   │   ├── cli.py            # download / ingest / resolve / bootstrap
│   │   ├── db.py             # SQLite ⇄ Postgres engine
│   │   ├── resolve.py        # entity resolution + evaluation
│   │   ├── graph.py          # k-hop, paths, ownership, cycles
│   │   ├── geo.py            # country normalisation + centroid geocoding
│   │   ├── markets.py        # yfinance quotes / history / news
│   │   ├── synth.py          # synthetic dataset generator
│   │   ├── bench.py          # graph query benchmarks
│   │   ├── ingest/           # opensanctions, icij, companies_house, gleif, focus, common
│   │   └── live/             # gdelt, opensky, ais, watchlist, hotspots, runner, store
│   ├── tests/                # ER + access, planted patterns, live layer
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   └── src/
│       ├── App.tsx, api.ts, store.tsx, actions.ts
│       └── components/       # GraphView, GeoView, Timeline, Inspector, LiveLayer,
│                             # AssetsView, MarketsView, NewsBreakdown, Panels, TopBar
├── data/
│   ├── raw/                  # downloaded dumps (git-ignored)
│   ├── staging/              # Parquet per source (git-ignored)
│   ├── synthetic/            # generated data + ground_truth.json (git-ignored)
│   └── ref/                  # countries.csv, live watchlists
├── docs/                     # screenshots
├── docker-compose.yml        # optional Postgres
└── gotham.md                 # project concept & design notes
```

---

## Testing & benchmarks

```bash
cd backend
.venv/Scripts/python -m pytest                              # synthetic data only, temp DB, live feeds off
.venv/Scripts/python -m gotham.cli eval                     # ER quality vs ground truth
.venv/Scripts/python -m gotham.bench --edges 100000 1000000 # graph query timings
```

**Results on this machine (SQLite)**, measured on the OFAC + EU FSF + synthetic dataset before the bulk sources were added:

- **Entity resolution:** precision **0.81**, recall **0.89**, F1 **0.85** across 28k records with ground truth. Most remaining "false positives" are same-name OFAC branches that OpenSanctions keeps separate.
- **Graph queries:** k-hop at k ≤ 2 takes under 1 ms. At k = 3, a recursive CTE (~4–5 ms) is already slower than a Python level-by-level BFS (~3 ms), because CTE `UNION` deduplicates on (node, depth) rather than on node. That is the first sign of where CTEs break down. Push to 10M+ edges to find the cliff.

---

## Deployment

The frontend is a static Vite build. The backend is a FastAPI app that needs WebSockets and enough disk for the DB.

| Part | Free options |
|---|---|
| **Frontend** | Cloudflare Pages · Vercel · Netlify · GitHub Pages (build `npm run build`, output `dist`) |
| **Backend** | Oracle Cloud Always Free VM · Hugging Face Spaces (Docker) · Render · Koyeb |
| **Database** | Neon · Supabase (Postgres via `DATABASE_URL`) |

> With the bulk sources loaded, the DB is about **1.8 GB**, which is too big for most free Postgres tiers and ephemeral disks. Use a VM with persistent disk, or build a smaller demo DB (`--hops 1`, or sanctions + synthetic only).

Remember to point the frontend at the deployed API and to enable CORS for the frontend's domain.

---

## Caveats

- Stay on entities that appear in sanctions lists, indictments or leaks. Names are messy.
- **A match is a lead, not proof.** The UI shows scores, evidence and per-value sources, not verdicts.
- Appearing in the ICIJ Offshore Leaks does not imply wrongdoing. Many offshore entities are legal.
- Live tracks are leads too: registrations get re-used, transponders get swapped, and AIS coverage is thin mid-ocean.
- Market data is delayed and unofficial (yfinance) and is not for trading.
