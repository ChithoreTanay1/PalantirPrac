"""Starts/stops the live feeds inside the FastAPI process, and serves their data.

Config (backend/.env or environment):
  LIVE_ENABLED=1            master switch (tests set 0)
  LIVE_HOURS=24             how long news events are kept
  GDELT_BACKFILL_HOURS=6    history fetched on first start
  OPENSKY_INTERVAL=120      seconds between OpenSky polls
  AISSTREAM_API_KEY=...     enables ships
  OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET (or OPENSKY_USERNAME / OPENSKY_PASSWORD)
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import timedelta

from ..db import get_engine, in_clause, loads, rows
from . import watchlist
from .ais import AisFeed
from .gdelt import GdeltFeed
from .opensky import OpenSkyFeed
from .store import env, health, init_live, iso, load_env, now, prune


class LiveService:
    def __init__(self):
        self.stop = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.watch: watchlist.Watchlist | None = None

    @property
    def enabled(self) -> bool:
        return env("LIVE_ENABLED", "1") == "1"

    async def start(self) -> None:
        load_env()
        init_live()
        if not self.enabled:
            health.set("live", enabled=False, error="LIVE_ENABLED=0")
            return
        self.watch = await asyncio.to_thread(watchlist.build)
        health.set("watchlist", **self.watch.summary())
        feeds = [
            GdeltFeed(backfill_hours=float(env("GDELT_BACKFILL_HOURS", "6")), interval=300),
            OpenSkyFeed(self.watch, interval=int(env("OPENSKY_INTERVAL", "120"))),
            AisFeed(self.watch),
        ]
        for f in feeds:
            self.tasks.append(asyncio.create_task(f.run(self.stop), name=f"live-{f.name}"))
        self.tasks.append(asyncio.create_task(self._housekeeping(), name="live-prune"))

    async def _housekeeping(self) -> None:
        hours = int(env("LIVE_HOURS", "24"))
        while not self.stop.is_set():
            try:
                await asyncio.to_thread(prune, hours)
            except Exception as e:
                health.fail("live", e)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=600)
            except asyncio.TimeoutError:
                pass

    async def shutdown(self) -> None:
        self.stop.set()
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


service = LiveService()


def snapshot(hours: float = 24, feeds: set[str] | None = None, max_news: int = 6000, bulk: set[str] | None = None) -> dict:
    """News + assets for the globe. `bulk` opts into non-watchlist assets ('tankers', 'jurisdiction')."""
    bulk = bulk or set()
    feeds = feeds or {"gdelt", "ais", "opensky"}
    since = iso(now() - timedelta(hours=hours))
    out = {"generated_at": iso(now()), "news": [], "assets": []}
    with get_engine().connect() as conn:
        if "gdelt" in feeds:
            out["news"] = rows(conn, "SELECT id, kind, ts, lat, lon, geo_precision, title, detail, url FROM live_events "
                                     "WHERE feed = 'gdelt' AND ts >= :s ORDER BY ts DESC LIMIT :n", s=since, n=max_news)
        asset_feeds = [f for f in ("ais", "opensky") if f in feeds]
        if asset_feeds:
            ph, params = in_clause("f", asset_feeds)
            allow_bulk = []
            if "tankers" in bulk:
                allow_bulk.append("ais")
            if "jurisdiction" in bulk:
                allow_bulk.append("opensky")
            bph, bparams = in_clause("b", allow_bulk)
            assets = rows(conn, f"SELECT l.*, res.cluster_id FROM live_events l LEFT JOIN resolution res ON res.record_id = l.record_id "
                                f"WHERE l.feed IN ({ph}) AND (l.record_id IS NOT NULL OR l.feed IN ({bph})) "
                                f"ORDER BY l.ts DESC LIMIT 5000", **params, **bparams)
            tracks = defaultdict(list)
            if assets:
                ph2, p2 = in_clause("a", [a["id"] for a in assets])
                for t in rows(conn, f"SELECT asset_id, ts, lat, lon FROM live_track WHERE asset_id IN ({ph2}) ORDER BY ts", **p2):
                    tracks[t["asset_id"]].append([t["lon"], t["lat"], t["ts"]])
            for a in assets:
                a["track"] = tracks.get(a["id"], [])[-60:]
            out["assets"] = assets
    for x in out["news"] + out["assets"]:
        x["detail"] = loads(x.get("detail"), {})
    return out


def status() -> dict:
    with get_engine().connect() as conn:
        counts = {r["feed"]: r["n"] for r in rows(conn, "SELECT feed, COUNT(*) AS n FROM live_events GROUP BY feed")}
    return {"enabled": service.enabled, "feeds": health.snapshot(), "stored": counts}


def _status_of(ts: str) -> str:
    from datetime import datetime
    age_min = (now() - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 60
    return "live" if age_min <= 60 else ("recent" if age_min <= 360 else "dark")


def assets(kind: str, scope: str = "watchlist", q: str | None = None, limit: int = 2000) -> dict:
    """Tracked vessels or aircraft with status, track length and STS-area flags."""
    from .hotspots import near_sts

    feed = "ais" if kind == "vessel" else "opensky"
    where = "l.feed = :f"
    if scope == "watchlist":
        where += " AND l.record_id IS NOT NULL"
    elif scope == "bulk":
        where += " AND l.record_id IS NULL"
    params = {"f": feed, "lim": limit}
    if q:
        where += " AND (LOWER(l.title) LIKE :q OR LOWER(l.detail) LIKE :q)"
        params["q"] = f"%{q.lower()}%"
    with get_engine().connect() as conn:
        items = rows(conn, f"SELECT l.id, l.ts, l.lat, l.lon, l.title, l.detail, l.url, l.record_id, res.cluster_id, "
                           f"(SELECT COUNT(*) FROM live_track t WHERE t.asset_id = l.id) AS track_points "
                           f"FROM live_events l LEFT JOIN resolution res ON res.record_id = l.record_id "
                           f"WHERE {where} ORDER BY l.ts DESC LIMIT :lim", **params)
        totals = {r["scope"]: r["n"] for r in rows(conn, "SELECT CASE WHEN record_id IS NULL THEN 'bulk' ELSE 'watchlist' END AS scope, "
                                                          "COUNT(*) AS n FROM live_events WHERE feed = :f GROUP BY 1", f=feed)}
    counts = {"live": 0, "recent": 0, "dark": 0, "sts": 0}
    for it in items:
        it["detail"] = loads(it["detail"], {})
        it["status"] = _status_of(it["ts"])
        counts[it["status"]] += 1
        it["sts"] = None
        if kind == "vessel":
            sog = it["detail"].get("sog_kn")
            hit = near_sts(it["lat"], it["lon"])
            if hit and (sog is None or sog < 1.0):
                it["sts"] = {"area": hit[0], "km": hit[1]}
                counts["sts"] += 1
    return {"kind": kind, "scope": scope, "counts": counts, "totals": totals, "items": items}


def asset_detail(asset_id: str) -> dict | None:
    from .hotspots import near_sts

    with get_engine().connect() as conn:
        found = rows(conn, "SELECT l.*, res.cluster_id FROM live_events l LEFT JOIN resolution res ON res.record_id = l.record_id "
                           "WHERE l.id = :i", i=asset_id)
        if not found:
            return None
        a = found[0]
        a["detail"] = loads(a["detail"], {})
        a["track"] = [[t["lon"], t["lat"], t["ts"]] for t in rows(conn, "SELECT lon, lat, ts FROM live_track WHERE asset_id = :i ORDER BY ts", i=asset_id)]
    a["status"] = _status_of(a["ts"])
    # derived: STS-area visits along the stored track, gaps in reporting
    visits, gaps = [], []
    from datetime import datetime
    prev = None
    for lon, lat, ts in a["track"]:
        hit = near_sts(lat, lon)
        if hit and (not visits or visits[-1]["area"] != hit[0]):
            visits.append({"area": hit[0], "first_seen": ts})
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if prev and (t - prev).total_seconds() > 6 * 3600:
            gaps.append({"from": iso(prev), "to": ts, "hours": round((t - prev).total_seconds() / 3600, 1)})
        prev = t
    a["sts_visits"] = visits
    a["reporting_gaps"] = gaps
    return a
