"""Storage, config and health for the live layer.

Live data is deliberately kept *outside* the investigation graph: it is short-lived
(pruned after LIVE_HOURS), not entity-resolved, and only *linked* to graph objects when an
identifier matches (IMO / MMSI / ICAO24 / airline ICAO code).
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from ..db import ROOT, dumps, get_engine

LIVE_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS live_events (
        id TEXT PRIMARY KEY,
        feed TEXT NOT NULL,
        kind TEXT NOT NULL,
        ts TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        geo_precision TEXT,
        title TEXT,
        detail TEXT,
        url TEXT,
        record_id TEXT,
        seen_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_live_events_feed_seen ON live_events(feed, seen_at)",
    "CREATE INDEX IF NOT EXISTS ix_live_track_ts ON live_track(ts)",
    """CREATE TABLE IF NOT EXISTS live_track (
        asset_id TEXT NOT NULL,
        ts TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        PRIMARY KEY (asset_id, ts)
    )""",
]


def load_env() -> None:
    """Minimal .env loader (backend/.env), so API keys never live in code or git."""
    path = ROOT / "backend" / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def init_live() -> None:
    with get_engine().begin() as conn:
        for stmt in LIVE_SCHEMA:
            conn.execute(text(stmt))


class Health:
    """Per-feed status shown in the UI (enabled, last success, last error, counters)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.feeds: dict[str, dict] = {}

    def set(self, feed: str, **kw) -> None:
        with self._lock:
            self.feeds.setdefault(feed, {"feed": feed}).update(kw)

    def ok(self, feed: str, **kw) -> None:
        self.set(feed, last_ok=iso(now()), error=None, **kw)

    def fail(self, feed: str, err: Exception | str) -> None:
        self.set(feed, last_error_at=iso(now()), error=str(err)[:300])

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(v) for v in self.feeds.values()]


health = Health()


def upsert_events(rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = ["id", "feed", "kind", "ts", "lat", "lon", "geo_precision", "title", "detail", "url", "record_id", "seen_at"]
    for r in rows:
        r.setdefault("seen_at", iso(now()))
        r.setdefault("geo_precision", None)
        r.setdefault("url", None)
        r.setdefault("record_id", None)
        if isinstance(r.get("detail"), (dict, list)):
            r["detail"] = dumps(r["detail"])
    sets = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
    with get_engine().begin() as conn:
        conn.execute(text(f"INSERT INTO live_events ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
                          f"ON CONFLICT (id) DO UPDATE SET {sets}"), rows)
    return len(rows)


def add_track(points: list[dict]) -> None:
    if not points:
        return
    with get_engine().begin() as conn:
        conn.execute(text("INSERT INTO live_track (asset_id, ts, lat, lon) VALUES (:asset_id, :ts, :lat, :lon) "
                          "ON CONFLICT (asset_id, ts) DO NOTHING"), points)


def prune(news_hours: int, asset_days: int = 7) -> None:
    cut_news = iso(now() - timedelta(hours=news_hours))
    cut_asset = iso(now() - timedelta(days=asset_days))
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM live_events WHERE feed = 'gdelt' AND seen_at < :c"), {"c": cut_news})
        conn.execute(text("DELETE FROM live_events WHERE feed <> 'gdelt' AND seen_at < :c"), {"c": cut_asset})
        conn.execute(text("DELETE FROM live_track WHERE ts < :c"), {"c": cut_asset})
        # bulk-tracked assets (all tankers, jurisdiction aircraft): keep 48 h of history only
        cut_bulk = iso(now() - timedelta(hours=48))
        conn.execute(text("DELETE FROM live_track WHERE ts < :c AND asset_id IN "
                          "(SELECT id FROM live_events WHERE record_id IS NULL AND feed <> 'gdelt')"), {"c": cut_bulk})
        conn.execute(text("DELETE FROM live_events WHERE feed <> 'gdelt' AND record_id IS NULL AND seen_at < :c"), {"c": cut_bulk})
