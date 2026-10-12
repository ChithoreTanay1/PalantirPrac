"""FastAPI backend. Run: uvicorn gotham.api:app --reload (from backend/)."""
from __future__ import annotations

from contextlib import asynccontextmanager

import duckdb
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text

from . import graph, resolve
from .live import runner as live
from .db import CLEARANCES, ROOT, dumps, get_engine, init_db, loads, rows
from .ingest.common import STAGING, now_iso


@asynccontextmanager
async def lifespan(_app):
    init_db()
    await live.service.start()
    yield
    await live.service.shutdown()


app = FastAPI(title="Mini-Gotham", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class User(BaseModel):
    username: str
    clearance: int
    display: str | None = None


def current_user(request: Request) -> User:
    name = request.headers.get("x-user") or request.query_params.get("as") or "analyst"
    with get_engine().connect() as conn:
        r = rows(conn, "SELECT * FROM users WHERE username = :u", u=name)
    if not r:
        raise HTTPException(401, f"unknown user {name!r}")
    return User(**r[0])


def audit(user: User, action: str, object_id: str | None = None, detail: dict | None = None) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("INSERT INTO audit_log (ts, username, action, object_id, detail) VALUES (:t, :u, :a, :o, :d)"),
                     {"t": now_iso(), "u": user.username, "a": action, "o": object_id, "d": dumps(detail or {})})


# ----------------------------------------------------------------------------- meta


@app.get("/api/users")
def users():
    with get_engine().connect() as conn:
        out = rows(conn, "SELECT * FROM users ORDER BY clearance")
    for u in out:
        u["clearance_label"] = CLEARANCES[u["clearance"]]
    return out


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {**user.model_dump(), "clearance_label": CLEARANCES[user.clearance]}


@app.get("/api/stats")
def stats(user: User = Depends(current_user)):
    with get_engine().connect() as conn:
        counts = rows(conn, "SELECT source, schema, COUNT(*) AS n FROM records WHERE classification <= :c "
                            "GROUP BY source, schema ORDER BY source, schema", c=user.clearance)
        out = {
            "records": counts,
            "edges": conn.execute(text("SELECT COUNT(*) FROM edges WHERE classification <= :c"), {"c": user.clearance}).scalar(),
            "events": conn.execute(text("SELECT COUNT(*) FROM events WHERE classification <= :c"), {"c": user.clearance}).scalar(),
            "clusters": conn.execute(text("SELECT COUNT(DISTINCT cluster_id) FROM resolution")).scalar(),
            "runs": rows(conn, "SELECT * FROM ingest_runs ORDER BY started_at DESC LIMIT 20"),
        }
    return out


@app.get("/api/analytics/sources")
def analytics(user: User = Depends(current_user)):
    """Bulk analytics straight off the Parquet staging layer with DuckDB (no DB round trip)."""
    files = list(STAGING.glob("*/records.parquet"))
    if not files:
        return []
    paths = ", ".join(f"'{p.as_posix()}'" for p in files)
    con = duckdb.connect()
    try:
        res = con.execute(
            f"SELECT source, schema, COUNT(*) AS n, COUNT(DISTINCT caption) AS distinct_captions, "
            f"SUM(CASE WHEN identifiers <> '[]' THEN 1 ELSE 0 END) AS with_identifiers "
            f"FROM read_parquet([{paths}], union_by_name=true) WHERE classification <= {int(user.clearance)} "
            f"GROUP BY 1, 2 ORDER BY 1, 3 DESC"
        ).fetchall()
    finally:
        con.close()
    return [dict(zip(["source", "schema", "n", "distinct_captions", "with_identifiers"], r)) for r in res]


# ----------------------------------------------------------------------------- objects


@app.get("/api/search")
def search(q: str, schema: str | None = None, limit: int = 25, user: User = Depends(current_user)):
    with get_engine().connect() as conn:
        res = graph.search(conn, q, user.clearance, schema, min(limit, 100))
    audit(user, "search", None, {"q": q, "schema": schema, "hits": len(res)})
    return res


@app.get("/api/entity")
def entity(id: str, user: User = Depends(current_user)):
    with get_engine().connect() as conn:
        cid = graph.cluster_of(conn, id) or id
        ent = graph.entity(conn, cid, user.clearance)
    if not ent:
        audit(user, "view_denied_or_missing", id)
        raise HTTPException(404, "not found")
    audit(user, "view_entity", cid)
    return ent


class IdsBody(BaseModel):
    ids: list[str]
    types: list[str] | None = None
    start: str | None = None
    end: str | None = None
    limit: int = 60


@app.post("/api/graph/expand")
def expand(body: IdsBody, user: User = Depends(current_user)):
    with get_engine().connect() as conn:
        g = graph.expand(conn, body.ids, user.clearance, body.types, min(body.limit, 300))
    audit(user, "expand", ",".join(body.ids[:20]), {"types": body.types, "nodes": len(g["nodes"])})
    return g


@app.post("/api/graph/nodes")
def nodes(body: IdsBody, user: User = Depends(current_user)):
    """Re-fetch a set of nodes and the edges among them (e.g. after a merge changes clusters)."""
    with get_engine().connect() as conn:
        ids = [graph.cluster_of(conn, i) or i for i in body.ids]
        return graph.subgraph(conn, set(ids), user.clearance)


@app.get("/api/graph/khop")
def khop(id: str, k: int = 2, limit: int = 300, user: User = Depends(current_user)):
    with get_engine().connect() as conn:
        g = graph.k_hop(conn, id, k, user.clearance, min(limit, 2000))
    audit(user, "k_hop", id, {"k": k, "nodes": len(g["nodes"])})
    return g


@app.get("/api/graph/path")
def path(a: str, b: str, max_depth: int = 8, user: User = Depends(current_user)):
    with get_engine().connect() as conn:
        g = graph.shortest_path(conn, a, b, user.clearance, min(max_depth, 10))
    audit(user, "shortest_path", f"{a} -> {b}", {"found": g["found"], "hops": g.get("hops")})
    return g


@app.get("/api/graph/ownership")
def ownership(id: str, direction: str = "up", user: User = Depends(current_user)):
    if direction not in ("up", "down"):
        raise HTTPException(400, "direction must be up or down")
    with get_engine().connect() as conn:
        g = graph.ownership_chain(conn, id, user.clearance, direction)
    audit(user, "ownership_chain", id, {"direction": direction, "nodes": len(g["nodes"]), "cycles": len(g["cycles"])})
    return g


@app.post("/api/events")
def events(body: IdsBody, user: User = Depends(current_user)):
    with get_engine().connect() as conn:
        evs = graph.events(conn, body.ids, user.clearance, body.start, body.end)
    audit(user, "events", ",".join(body.ids[:20]), {"n": len(evs)})
    return evs


@app.get("/api/analysis/co-movement")
def co_movement(id: str, km: float = 25, days: int = 1, user: User = Depends(current_user)):
    with get_engine().connect() as conn:
        res = graph.co_movement(conn, id, user.clearance, km, days)
    audit(user, "co_movement", id, {"hits": len(res)})
    return res


@app.get("/api/analysis/cycles")
def cycles(user: User = Depends(current_user)):
    with get_engine().connect() as conn:
        cyc = graph.ownership_cycles(conn, user.clearance)
        summ = graph.summarize(conn, {n for c in cyc for n in c}, user.clearance)
    audit(user, "ownership_cycles", None, {"n": len(cyc)})
    return [{"nodes": [summ[n] for n in c if n in summ]} for c in cyc]


# ----------------------------------------------------------------------------- entity resolution


@app.get("/api/er/queue")
def er_queue(min_score: float = resolve.REVIEW_FLOOR, max_score: float = resolve.AUTO_MERGE, limit: int = 50,
             schema: str | None = None, user: User = Depends(current_user)):
    """Pairs in the grey zone: not auto-merged, not yet judged by a human."""
    sql = ("SELECT c.a, c.b, c.score, c.features FROM er_candidates c "
           "JOIN resolution ra ON ra.record_id = c.a JOIN resolution rb ON rb.record_id = c.b "
           "JOIN records xa ON xa.id = c.a JOIN records xb ON xb.id = c.b "
           "WHERE c.score >= :lo AND c.score < :hi AND ra.cluster_id <> rb.cluster_id "
           "AND xa.classification <= :clr AND xb.classification <= :clr "
           "AND NOT EXISTS (SELECT 1 FROM judgements j WHERE (j.a = c.a AND j.b = c.b) OR (j.a = c.b AND j.b = c.a)) ")
    params = {"lo": min_score, "hi": max_score, "clr": user.clearance, "lim": limit}
    if schema:
        sql += "AND xa.schema = :schema "
        params["schema"] = schema
    sql += "ORDER BY c.score DESC LIMIT :lim"
    with get_engine().connect() as conn:
        pairs = rows(conn, sql, **params)
        ids = {p["a"] for p in pairs} | {p["b"] for p in pairs}
        recs = {}
        if ids:
            from .db import in_clause
            ph, ps = in_clause("r", ids)
            for r in rows(conn, f"SELECT id, source, schema, caption, names, countries, identifiers, birth_date "
                                f"FROM records WHERE id IN ({ph})", **ps):
                for k in ("names", "countries", "identifiers"):
                    r[k] = loads(r[k])
                recs[r["id"]] = r
    for p in pairs:
        p["features"] = loads(p["features"])
        p["record_a"], p["record_b"] = recs.get(p["a"]), recs.get(p["b"])
    return pairs


class JudgeBody(BaseModel):
    a: str
    b: str
    judgement: str
    reason: str | None = None


@app.post("/api/er/judge")
def er_judge(body: JudgeBody, user: User = Depends(current_user)):
    try:
        res = resolve.judge(body.a, body.b, body.judgement, user.username, body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(user, f"er_{body.judgement}", f"{body.a} | {body.b}", {"reason": body.reason})
    with get_engine().connect() as conn:
        res["cluster"] = graph.cluster_of(conn, body.a)
    return res


class UnmergeBody(BaseModel):
    record_id: str
    reason: str | None = None


@app.post("/api/er/unmerge")
def er_unmerge(body: UnmergeBody, user: User = Depends(current_user)):
    res = resolve.unmerge(body.record_id, user.username, body.reason)
    audit(user, "er_unmerge", body.record_id, {"reason": body.reason})
    return res


@app.get("/api/er/eval")
def er_eval(user: User = Depends(current_user)):
    return resolve.evaluate()


@app.get("/api/audit")
def audit_log(limit: int = 200, user: User = Depends(current_user)):
    with get_engine().connect() as conn:
        if user.clearance >= 2:
            return rows(conn, "SELECT * FROM audit_log ORDER BY id DESC LIMIT :l", l=limit)
        return rows(conn, "SELECT * FROM audit_log WHERE username = :u ORDER BY id DESC LIMIT :l", u=user.username, l=limit)


# ----------------------------------------------------------------------------- live layer

_last_live_audit: dict[str, float] = {}


@app.get("/api/live")
def live_data(hours: float = 24, feeds: str = "gdelt,ais,opensky", bulk: str = "", user: User = Depends(current_user)):
    """Live news events + tracked ships/aircraft. The globe polls this, so the audit entry is
    throttled to one per user per 10 minutes."""
    import time

    if time.time() - _last_live_audit.get(user.username, 0) > 600:
        _last_live_audit[user.username] = time.time()
        audit(user, "live_view", None, {"feeds": feeds, "hours": hours})
    return live.snapshot(min(hours, 48), set(feeds.split(",")), bulk=set(filter(None, bulk.split(","))))


@app.get("/api/live/assets")
def live_assets(kind: str = "vessel", scope: str = "watchlist", q: str | None = None, user: User = Depends(current_user)):
    if kind not in ("vessel", "aircraft") or scope not in ("watchlist", "bulk", "all"):
        raise HTTPException(400, "kind must be vessel|aircraft, scope watchlist|bulk|all")
    return live.assets(kind, scope, q)


@app.get("/api/live/asset")
def live_asset(id: str, user: User = Depends(current_user)):
    a = live.asset_detail(id)
    if not a:
        raise HTTPException(404, "asset not tracked")
    audit(user, "live_asset", id)
    return a


@app.get("/api/live/aircraft/flights")
async def aircraft_flights(icao24: str, user: User = Depends(current_user)):
    from .live import opensky
    try:
        return await opensky.flights(icao24)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except Exception as e:
        raise HTTPException(502, f"OpenSky: {e}")


@app.get("/api/live/aircraft/track")
async def aircraft_track(icao24: str, user: User = Depends(current_user)):
    from .live import opensky
    try:
        return await opensky.track(icao24)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except Exception as e:
        raise HTTPException(502, f"OpenSky: {e}")


@app.get("/api/live/status")
def live_status(user: User = Depends(current_user)):
    return live.status()


# ----------------------------------------------------------------------------- markets (yfinance, delayed)


@app.get("/api/markets/quotes")
def market_quotes(tab: str | None = None, symbols: str | None = None, user: User = Depends(current_user)):
    from . import markets
    try:
        if symbols is not None:
            return markets.symbol_quotes(symbols.split(","))
        return markets.tab_quotes(tab or "indices")
    except KeyError:
        raise HTTPException(400, f"unknown tab {tab!r}")
    except Exception as e:
        raise HTTPException(502, f"Yahoo Finance: {e}")


@app.get("/api/markets/history")
def market_history(symbol: str, range: str = "1mo", user: User = Depends(current_user)):
    from . import markets
    try:
        return markets.history(symbol, range)
    except Exception as e:
        raise HTTPException(502, f"Yahoo Finance: {e}")


@app.get("/api/markets/news")
def market_news(symbol: str, user: User = Depends(current_user)):
    from . import markets
    try:
        return markets.news(symbol)
    except Exception as e:
        raise HTTPException(502, f"Yahoo Finance: {e}")


# ----------------------------------------------------------------------------- frontend

DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404, f"no API route /{full_path}")
        f = DIST / full_path
        if full_path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
