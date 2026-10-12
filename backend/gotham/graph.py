"""Graph and object queries over the resolved (cluster-level) graph.

Every function takes the caller's clearance and filters records, edges and events whose
classification is above it. Nothing above clearance is ever returned, not even as a count.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import text

from .db import in_clause, loads, rows

SOURCE_PRIORITY = ["os_us_ofac_sdn", "os_eu_fsf", "synth_sanctions", "icij", "synth_icij", "ch", "synth_ch"]


def _priority(source: str) -> int:
    return SOURCE_PRIORITY.index(source) if source in SOURCE_PRIORITY else len(SOURCE_PRIORITY)


def _is_sanctioned(props: dict) -> bool:
    return bool(props.get("sanctions")) or "sanction" in (props.get("topics") or [])


def summarize(conn, cluster_ids, clearance: int) -> dict[str, dict]:
    """Compact node descriptors for a set of clusters (used for graph nodes and search hits)."""
    ids = list(dict.fromkeys(cluster_ids))
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 500):
        ph, params = in_clause("c", ids[i:i + 500])
        members = defaultdict(list)
        for r in rows(conn, f"SELECT res.cluster_id, r.id, r.source, r.schema, r.caption, r.countries, r.props, "
                            f"r.synthetic, r.classification FROM resolution res JOIN records r ON r.id = res.record_id "
                            f"WHERE res.cluster_id IN ({ph}) AND r.classification <= :clr", clr=clearance, **params):
            members[r["cluster_id"]].append(r)
        for cid, recs in members.items():
            recs.sort(key=lambda r: (_priority(r["source"]), r["id"]))
            props = [loads(r["props"], {}) for r in recs]
            countries = Counter(c for r in recs for c in loads(r["countries"], []))
            out[cid] = {
                "id": cid,
                "schema": recs[0]["schema"],
                "caption": recs[0]["caption"],
                "countries": [c for c, _ in countries.most_common(4)],
                "sources": sorted({r["source"] for r in recs}, key=_priority),
                "n_records": len(recs),
                "sanctioned": any(_is_sanctioned(p) for p in props),
                "synthetic": any(r["synthetic"] for r in recs),
                "classification": max(r["classification"] for r in recs),
            }
    return out


def search(conn, q: str, clearance: int, schema: str | None = None, limit: int = 25) -> list[dict]:
    q = (q or "").strip().lower()
    if len(q) < 2:
        return []
    params = {"q": f"%{q}%", "pre": f"{q}%", "clr": clearance, "lim": limit * 4}
    where = "LOWER(r.names) LIKE :q AND r.classification <= :clr AND r.schema <> 'Address'"
    if schema:
        where += " AND r.schema = :schema"
        params["schema"] = schema
    hits = rows(conn, f"SELECT DISTINCT res.cluster_id, CASE WHEN LOWER(r.caption) LIKE :pre THEN 0 ELSE 1 END AS rank "
                      f"FROM records r JOIN resolution res ON res.record_id = r.id WHERE {where} "
                      f"ORDER BY rank LIMIT :lim", **params)
    order = list(dict.fromkeys(h["cluster_id"] for h in hits))[:limit]
    summ = summarize(conn, order, clearance)
    return [summ[c] for c in order if c in summ]


def cluster_of(conn, record_id: str) -> str | None:
    return conn.execute(text("SELECT cluster_id FROM resolution WHERE record_id = :r"), {"r": record_id}).scalar()


def entity(conn, cluster_id: str, clearance: int) -> dict | None:
    recs = rows(conn, "SELECT r.* FROM resolution res JOIN records r ON r.id = res.record_id "
                      "WHERE res.cluster_id = :c AND r.classification <= :clr", c=cluster_id, clr=clearance)
    if not recs:
        return None
    recs.sort(key=lambda r: (_priority(r["source"]), r["id"]))
    for r in recs:
        for k in ("names", "countries", "identifiers", "props"):
            r[k] = loads(r[k])
        r.pop("gt_id", None)  # ground truth is for evaluation only; never shown to analysts
        r.pop("content_hash", None)
    # Merged property view with per-value provenance ("which source said this?").
    merged: dict[str, list[dict]] = defaultdict(list)

    def add(prop, value, rec):
        if value in (None, "", [], {}):
            return
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, dict):
                continue
            existing = next((m for m in merged[prop] if m["value"] == v), None)
            if existing:
                if rec["id"] not in existing["records"]:
                    existing["records"].append(rec["id"])
            else:
                merged[prop].append({"value": v, "records": [rec["id"]]})

    sanctions = []
    for r in recs:
        add("name", r["names"], r)
        add("country", r["countries"], r)
        add("identifier", r["identifiers"], r)
        add("birthDate", r["birth_date"], r)
        for k, v in r["props"].items():
            if k == "sanctions":
                sanctions += [dict(s, record=r["id"]) for s in v]
            else:
                add(k, v, r)
    # Why are these records in one cluster? The scored pairs that connect them.
    ids = [r["id"] for r in recs]
    ph, params = in_clause("m", ids)
    evidence = rows(conn, f"SELECT a, b, score, features FROM er_candidates WHERE a IN ({ph}) AND b IN ({ph}) "
                          f"ORDER BY score DESC LIMIT 50", **params) if len(ids) > 1 else []
    for e in evidence:
        e["features"] = loads(e["features"])
    judgements = rows(conn, f"SELECT * FROM judgements WHERE a IN ({ph}) OR b IN ({ph}) ORDER BY id DESC LIMIT 50", **params)
    # Near-misses: candidates outside this cluster that an analyst might want to merge.
    near = rows(conn, f"SELECT c.a, c.b, c.score, c.features, ra.cluster_id AS ca, rb.cluster_id AS cb FROM er_candidates c "
                      f"JOIN resolution ra ON ra.record_id = c.a JOIN resolution rb ON rb.record_id = c.b "
                      f"WHERE (c.a IN ({ph}) OR c.b IN ({ph})) AND ra.cluster_id <> rb.cluster_id "
                      f"ORDER BY c.score DESC LIMIT 10", **params)
    other_clusters = [n["cb"] if n["ca"] == cluster_id else n["ca"] for n in near]
    summ = summarize(conn, other_clusters + [cluster_id], clearance)
    suggestions = []
    for n, oc in zip(near, other_clusters):
        if oc in summ:
            suggestions.append({"cluster": summ[oc], "score": n["score"], "features": loads(n["features"]),
                                "a": n["a"], "b": n["b"]})
    degree = conn.execute(text("SELECT COUNT(*) FROM gedges WHERE src = :c AND classification <= :clr"),
                          {"c": cluster_id, "clr": clearance}).scalar()
    return {"summary": summ[cluster_id], "records": recs, "properties": merged, "sanctions": sanctions,
            "evidence": evidence, "judgements": judgements, "suggestions": suggestions, "degree": degree}


def _edges_between(conn, nodes: set[str], clearance: int) -> list[dict]:
    if not nodes:
        return []
    out = []
    node_list = list(nodes)
    for i in range(0, len(node_list), 400):
        ph, params = in_clause("n", node_list[i:i + 400])
        out += rows(conn, f"SELECT g.src, g.dst, g.edge_id, g.type, g.share, e.role, e.source, e.start_date, e.end_date "
                          f"FROM gedges g JOIN edges e ON e.id = g.edge_id "
                          f"WHERE g.dir = 1 AND g.src IN ({ph}) AND g.classification <= :clr", clr=clearance, **params)
    return [e for e in out if e["dst"] in nodes]


def subgraph(conn, nodes, clearance: int, extra_edges: list[dict] | None = None) -> dict:
    node_set = set(nodes)
    summ = summarize(conn, node_set, clearance)
    node_set &= set(summ)
    edges = _edges_between(conn, node_set, clearance)
    seen = {e["edge_id"] for e in edges}
    for e in extra_edges or []:
        if e["edge_id"] not in seen and e["src"] in node_set and e["dst"] in node_set:
            edges.append(e)
    return {"nodes": [summ[n] for n in node_set], "edges": _collapse(edges)}


def _collapse(edges: list[dict]) -> list[dict]:
    """Merge parallel edges of the same type (e.g. the same OWNS fact reported by 3 sources)."""
    grouped: dict[tuple, dict] = {}
    for e in edges:
        key = (e["src"], e["dst"], e["type"])
        g = grouped.get(key)
        if g is None:
            grouped[key] = g = {"id": f"{e['src']}>{e['type']}>{e['dst']}", "source": e["src"], "target": e["dst"],
                                "type": e["type"], "roles": [], "share": e.get("share"), "edge_ids": [], "sources": []}
        g["edge_ids"].append(e["edge_id"])
        if e.get("role") and e["role"] not in g["roles"]:
            g["roles"].append(e["role"])
        if e.get("source") and e["source"] not in g["sources"]:
            g["sources"].append(e["source"])
        if e.get("share") is not None:
            g["share"] = e["share"]
    return list(grouped.values())


def expand(conn, cluster_ids: list[str], clearance: int, types: list[str] | None = None, limit: int = 60) -> dict:
    """One hop around the given nodes (the Gotham 'Search Around' action)."""
    ph, params = in_clause("n", cluster_ids)
    where = f"g.src IN ({ph}) AND g.classification <= :clr"
    if types:
        tph, tparams = in_clause("t", types)
        where += f" AND g.type IN ({tph})"
        params.update(tparams)
    neigh = rows(conn, f"SELECT g.dst, COUNT(*) AS n FROM gedges g WHERE {where} GROUP BY g.dst ORDER BY n DESC LIMIT :lim",
                 clr=clearance, lim=limit, **params)
    total = conn.execute(text(f"SELECT COUNT(DISTINCT g.dst) FROM gedges g WHERE {where}"), dict(clr=clearance, **params)).scalar()
    g = subgraph(conn, set(cluster_ids) | {n["dst"] for n in neigh}, clearance)
    g["truncated"] = max(0, total - len(neigh))
    return g


def k_hop(conn, start: str, k: int, clearance: int, limit: int = 300) -> dict:
    """All nodes within k hops, via a recursive CTE (benchmarked in bench.py)."""
    k = max(1, min(k, 4))
    found = rows(conn, """
        WITH RECURSIVE walk(node, depth) AS (
            SELECT CAST(:start AS TEXT), 0
            UNION
            SELECT g.dst, w.depth + 1 FROM walk w
            JOIN gedges g ON g.src = w.node
            WHERE w.depth < :k AND g.classification <= :clr
        )
        SELECT node, MIN(depth) AS depth FROM walk GROUP BY node ORDER BY MIN(depth), node LIMIT :lim
    """, start=start, k=k, clr=clearance, lim=limit)
    g = subgraph(conn, {f["node"] for f in found}, clearance)
    depth = {f["node"]: f["depth"] for f in found}
    for n in g["nodes"]:
        n["depth"] = depth.get(n["id"])
    g["truncated"] = len(found) >= limit
    return g


def shortest_path(conn, a: str, b: str, clearance: int, max_depth: int = 8, max_frontier: int = 20000) -> dict:
    """Bidirectional BFS, one indexed SQL query per level. Returns the first shortest path."""
    if a == b:
        return {"found": True, "path": [a], **subgraph(conn, {a}, clearance)}
    parents = {a: {a: None}, b: {b: None}}  # side -> node -> (prev, edge)
    frontier = {a: {a}, b: {b}}
    meet = None
    for _ in range(max_depth):
        side = a if len(frontier[a]) <= len(frontier[b]) else b
        other = b if side == a else a
        nxt: set[str] = set()
        cur = list(frontier[side])
        for i in range(0, len(cur), 500):
            ph, params = in_clause("n", cur[i:i + 500])
            for r in rows(conn, f"SELECT src, dst, edge_id, type, share FROM gedges "
                                f"WHERE src IN ({ph}) AND classification <= :clr", clr=clearance, **params):
                if r["dst"] not in parents[side]:
                    parents[side][r["dst"]] = (r["src"], r)
                    nxt.add(r["dst"])
                    if r["dst"] in parents[other]:
                        meet = r["dst"]
                        break
            if meet:
                break
        if meet or not nxt or len(nxt) > max_frontier:
            break
        frontier[side] = nxt
    if not meet:
        return {"found": False, "path": [], "nodes": [], "edges": []}

    def walk(side, node):
        chain, edges = [], []
        while parents[side][node] is not None:
            prev, edge = parents[side][node]
            chain.append(prev)
            edges.append(edge)
            node = prev
        return chain, edges

    left, le = walk(a, meet)
    right, re_ = walk(b, meet)
    path = list(reversed(left)) + [meet] + right
    # gedges rows may be reversed (dir=-1); fetch the real direction for display.
    edge_ids = [e["edge_id"] for e in le + re_]
    ph, params = in_clause("e", edge_ids)
    real = rows(conn, f"SELECT g.src, g.dst, g.edge_id, g.type, g.share, e.role, e.source FROM gedges g "
                      f"JOIN edges e ON e.id = g.edge_id WHERE g.dir = 1 AND g.edge_id IN ({ph})", **params)
    g = subgraph(conn, set(path), clearance, extra_edges=real)
    path_edges = {e["edge_id"] for e in real}
    g["edges"] = [e for e in g["edges"] if set(e["edge_ids"]) & path_edges]
    return {"found": True, "path": path, "hops": len(path) - 1, **g}


def ownership_chain(conn, start: str, clearance: int, direction: str = "up", max_depth: int = 10) -> dict:
    """Unwind ownership with a path-tracking recursive CTE.

    up   = who ultimately owns `start` (towards beneficial owners)
    down = everything `start` owns, directly or indirectly
    Effective share multiplies along the chain (unknown shares count as 1.0 and are flagged).
    Cycles (circular ownership, a classic shell-company pattern) are detected and reported.
    """
    dir_ = -1 if direction == "up" else 1
    chain = rows(conn, """
        WITH RECURSIVE own AS (
            -- one row per (owner, owned) pair even when several sources report the same fact
            SELECT src, dst, MIN(edge_id) AS edge_id, MAX(share) AS share FROM gedges
            WHERE type = 'OWNS' AND dir = :dir AND classification <= :clr GROUP BY src, dst
        ),
        chain(node, via_edge, parent, depth, eff_share, known, path, cycle) AS (
            SELECT CAST(:start AS TEXT), CAST(NULL AS TEXT), CAST(NULL AS TEXT), 0,
                   CAST(1.0 AS REAL), 1, '/' || CAST(:start AS TEXT) || '/', 0
            UNION ALL
            SELECT g.dst, g.edge_id, c.node, c.depth + 1,
                   CAST(c.eff_share * COALESCE(g.share, 1.0) AS REAL),
                   CASE WHEN g.share IS NULL THEN 0 ELSE c.known END,
                   c.path || g.dst || '/',
                   CASE WHEN c.path LIKE '%/' || g.dst || '/%' THEN 1 ELSE 0 END
            FROM chain c JOIN own g ON g.src = c.node
            WHERE c.depth < :maxd AND c.cycle = 0
        )
        SELECT node, via_edge, parent, depth, eff_share, known, path, cycle FROM chain ORDER BY depth
    """, start=start, dir=dir_, maxd=max_depth, clr=clearance)
    nodes = {c["node"] for c in chain}
    has_child = {c["parent"] for c in chain if c["parent"]}
    edge_ids = [c["via_edge"] for c in chain if c["via_edge"]]
    real = []
    if edge_ids:
        ph, params = in_clause("e", edge_ids)
        real = rows(conn, f"SELECT g.src, g.dst, g.edge_id, g.type, g.share, e.role, e.source FROM gedges g "
                          f"JOIN edges e ON e.id = g.edge_id WHERE g.dir = 1 AND g.edge_id IN ({ph})", **params)
    g = subgraph(conn, nodes, clearance, extra_edges=real)
    own_edges = set(edge_ids)
    g["edges"] = [e for e in g["edges"] if set(e["edge_ids"]) & own_edges]
    terminals = {}
    for c in chain:
        if c["node"] != start and c["node"] not in has_child and not c["cycle"]:
            t = terminals.setdefault(c["node"], {"node": c["node"], "eff_share": 0.0, "paths": 0, "exact": True, "depth": c["depth"]})
            t["eff_share"] += c["eff_share"]
            t["paths"] += 1
            t["exact"] = t["exact"] and bool(c["known"])
    for t in terminals.values():
        t["eff_share"] = min(1.0, round(t["eff_share"], 4))
    cycles = [c["path"].strip("/").split("/") for c in chain if c["cycle"]]
    return {"direction": direction, "chain": chain, "terminals": sorted(terminals.values(), key=lambda t: -t["eff_share"]),
            "cycles": cycles, **g}


def events(conn, cluster_ids: list[str], clearance: int, start: str | None = None, end: str | None = None) -> list[dict]:
    if not cluster_ids:
        return []
    ph, params = in_clause("c", cluster_ids)
    where = f"res.cluster_id IN ({ph}) AND e.classification <= :clr"
    if start:
        where += " AND e.date >= :start"
        params["start"] = start
    if end:
        where += " AND e.date <= :end"
        params["end"] = end
    return rows(conn, f"SELECT e.*, res.cluster_id FROM events e JOIN resolution res ON res.record_id = e.record_id "
                      f"WHERE {where} ORDER BY e.date LIMIT 5000", clr=clearance, **params)


def co_movement(conn, cluster_id: str, clearance: int, km: float = 25, days: int = 1) -> list[dict]:
    """Other entities repeatedly at the same place on the same day(s) as `cluster_id`."""
    deg = km / 111.0
    evs = events(conn, [cluster_id], clearance)
    evs = [e for e in evs if e["geo_precision"] == "exact"]
    hits: dict[str, list] = defaultdict(list)
    for e in evs:
        from datetime import date, timedelta
        d = date.fromisoformat(e["date"][:10])
        for r in rows(conn, "SELECT e.date, e.place, e.lat, e.lon, res.cluster_id FROM events e "
                            "JOIN resolution res ON res.record_id = e.record_id "
                            "WHERE e.date BETWEEN :d0 AND :d1 AND e.geo_precision = 'exact' AND e.classification <= :clr "
                            "AND e.lat BETWEEN :la0 AND :la1 AND e.lon BETWEEN :lo0 AND :lo1 AND res.cluster_id <> :c",
                      d0=(d - timedelta(days=days)).isoformat(), d1=(d + timedelta(days=days)).isoformat(),
                      la0=e["lat"] - deg, la1=e["lat"] + deg, lo0=e["lon"] - deg, lo1=e["lon"] + deg,
                      clr=clearance, c=cluster_id):
            hits[r["cluster_id"]].append({"date": r["date"], "place": r["place"]})
    summ = summarize(conn, list(hits), clearance)
    out = [{"cluster": summ[c], "count": len(v), "meetings": v} for c, v in hits.items() if c in summ and len(v) >= 2]
    return sorted(out, key=lambda x: -x["count"])


def ownership_cycles(conn, clearance: int, limit: int = 50) -> list[list[str]]:
    """Find short circular-ownership loops (length 2-4) anywhere in the graph."""
    found = rows(conn, """
        WITH RECURSIVE walk(start, node, depth, path) AS (
            SELECT g.src, g.dst, 1, '/' || g.src || '/' || g.dst || '/'
            FROM gedges g WHERE g.type = 'OWNS' AND g.dir = 1 AND g.classification <= :clr
            UNION ALL
            SELECT w.start, g.dst, w.depth + 1, w.path || g.dst || '/'
            FROM walk w JOIN gedges g ON g.src = w.node AND g.type = 'OWNS' AND g.dir = 1
            WHERE w.depth < 4 AND w.node <> w.start AND g.classification <= :clr
              AND (g.dst = w.start OR w.path NOT LIKE '%/' || g.dst || '/%')
        )
        SELECT path FROM walk WHERE node = start AND depth > 1 LIMIT :lim
    """, clr=clearance, lim=limit * 4)
    seen, out = set(), []
    for f in found:
        cyc = f["path"].strip("/").split("/")[:-1]
        key = frozenset(cyc)
        if key not in seen:
            seen.add(key)
            out.append(cyc)
    return out[:limit]
