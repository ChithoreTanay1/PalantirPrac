# Mini-Gotham: Illicit Finance & Sanctions Graph

Personal project. Public, historical, non-live data only. Goal: learn system design while exploring the criminal/illicit side of geopolitics.

## Concept

One graph of **people, companies, vessels, aircraft, addresses, and events**, with a map, a timeline, and a link-analysis view.

## Datasets (all free, all public)

| Domain | Source |
|---|---|
| Offshore shells, officers | ICIJ Offshore Leaks |
| Sanctions, PEPs, crime-linked entities | OpenSanctions (consolidates OFAC, EU, UN, and others) |
| Corporate ownership | UK Companies House PSC, GLEIF, OpenCorporates (limited) |
| Sanctioned vessels, shipping | OpenSanctions vessel lists plus historical AIS (NOAA, Danish Maritime Authority) |
| Aircraft | OpenSky historical, plus sanctioned aircraft lists |
| Conflict/violence events | ACLED, UCDP, GDELT |
| Terrorism history | Global Terrorism Database |
| Trade flows | UN Comtrade (arms, dual-use goods) |
| Court records/indictments | DOJ press releases, OFAC enforcement actions, CourtListener |
| Ground truth | OCCRP Aleph, plus published investigations to test against |

## Stack (~€0)

- **Postgres:** metadata, entities, audit log
- **Parquet + DuckDB:** bulk datasets, analytics
- **Graph layer:** Postgres edge tables first
- **Python:** ingestion and entity resolution
- **FastAPI** backend
- **React + Cytoscape.js + MapLibre** frontend

## Scope: only these 4 things

1. **Ingest and normalize** 3 sources into one entity schema, with provenance on every record
2. **Entity resolution:** merge "same company, different spelling" across sources (hardest and most educational part)
3. **Graph explorer:** k-hop traversal, shortest path between two entities, ownership-chain unwinding
4. **Map and timeline view** of events around a selected entity,add in a globe too make *the UI only* as close to the palantir gotham as possible if no sources try looking up some on the internet and use that

## Why it teaches system design

- Entity resolution: blocking, similarity scoring, merge/unmerge with an audit trail
- Multi-source ingestion: schema mapping and idempotent re-runs
- Graph queries: decide when Postgres CTEs break down and benchmark that point
- Provenance and lineage: every claim traces to its source document
- Scale tests: load 10M+ edges and measure

## Testing and validation

1. **Planted-pattern tests:** inject known structures (shell-company ring, synchronized-movement pair, duplicate identity) into synthetic data and check the tool surfaces them. VAST Challenge datasets (IEEE VIS) provide synthetic scenarios with published ground truth.
2. **Entity resolution:** take OpenSanctions plus Companies House, create fuzzy duplicates (typos, transliterations, reordered names), measure precision/recall of merge logic.
3. **Scale tests:** load 1M, 10M, 100M edges and time k-hop traversals, geo-box queries, and full-text search.
4. **Permission/audit tests:** user A can never see objects above their clearance; every access is logged.
5. **Compare to peers:** OCCRP Aleph on the same data; Neo4j Bloom and Gephi as graph UI baselines.
6. **Replicate a known investigation:** pick a published case (a Panama Papers network or a sanctioned oligarch's ownership chain) and see whether the tool rediscovers it from raw data.

## Caveats

- Stay on entities that appear in sanctions lists, indictments, or leaks. Do not build profiles of private individuals (GDPR, and unnecessary for testing).
- Names are messy (transliteration, aliases); entity resolution will eat about half the time.
- A match is a lead, not proof. Show confidence and sources, not verdicts.
