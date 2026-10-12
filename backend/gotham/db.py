"""Database access. Postgres in production (docker-compose), SQLite for zero-setup dev.

All SQL here is written to run unchanged on both: recursive CTEs, ON CONFLICT upserts
and JSON stored as TEXT are supported by SQLite >= 3.35 and Postgres >= 12.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DEFAULT_URL = f"sqlite:///{(DATA / 'gotham.db').as_posix()}"

SCHEMA = [
    # One row per source record. `id` is "<source>:<source_id>" and never changes,
    # which is what makes re-ingestion idempotent.
    """CREATE TABLE IF NOT EXISTS records (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        schema TEXT NOT NULL,
        caption TEXT NOT NULL,
        names TEXT NOT NULL DEFAULT '[]',
        countries TEXT NOT NULL DEFAULT '[]',
        identifiers TEXT NOT NULL DEFAULT '[]',
        birth_date TEXT,
        props TEXT NOT NULL DEFAULT '{}',
        classification INTEGER NOT NULL DEFAULT 0,
        synthetic INTEGER NOT NULL DEFAULT 0,
        gt_id TEXT,
        source_url TEXT,
        source_doc TEXT,
        run_id TEXT,
        content_hash TEXT NOT NULL,
        ingested_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_records_schema ON records(schema)",
    "CREATE INDEX IF NOT EXISTS ix_records_caption ON records(caption)",
    """CREATE TABLE IF NOT EXISTS edges (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        src TEXT NOT NULL,
        dst TEXT NOT NULL,
        type TEXT NOT NULL,
        role TEXT,
        share REAL,
        start_date TEXT,
        end_date TEXT,
        props TEXT NOT NULL DEFAULT '{}',
        classification INTEGER NOT NULL DEFAULT 0,
        source_url TEXT,
        source_doc TEXT,
        run_id TEXT,
        content_hash TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_edges_src ON edges(src)",
    "CREATE INDEX IF NOT EXISTS ix_edges_dst ON edges(dst)",
    """CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        record_id TEXT NOT NULL,
        type TEXT NOT NULL,
        date TEXT NOT NULL,
        lat REAL,
        lon REAL,
        geo_precision TEXT,
        place TEXT,
        country TEXT,
        description TEXT,
        classification INTEGER NOT NULL DEFAULT 0,
        source_url TEXT,
        source_doc TEXT,
        run_id TEXT,
        content_hash TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_events_record ON events(record_id)",
    "CREATE INDEX IF NOT EXISTS ix_events_date ON events(date)",
    """CREATE TABLE IF NOT EXISTS ingest_runs (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        file TEXT,
        file_hash TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        inserted INTEGER DEFAULT 0,
        updated INTEGER DEFAULT 0,
        unchanged INTEGER DEFAULT 0
    )""",
    # Entity resolution state. `resolution` maps every record to its cluster;
    # `judgements` is the append-only audit trail of merge / unmerge decisions.
    """CREATE TABLE IF NOT EXISTS resolution (
        record_id TEXT PRIMARY KEY,
        cluster_id TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_resolution_cluster ON resolution(cluster_id)",
    """CREATE TABLE IF NOT EXISTS er_candidates (
        a TEXT NOT NULL,
        b TEXT NOT NULL,
        score REAL NOT NULL,
        features TEXT NOT NULL,
        PRIMARY KEY (a, b)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_er_candidates_score ON er_candidates(score)",
    """CREATE TABLE IF NOT EXISTS judgements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        a TEXT NOT NULL,
        b TEXT NOT NULL,
        judgement TEXT NOT NULL,
        username TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL
    )""",
    # Cluster-level edge table the graph queries run on, rebuilt after each resolution.
    """CREATE TABLE IF NOT EXISTS gedges (
        src TEXT NOT NULL,
        dst TEXT NOT NULL,
        edge_id TEXT NOT NULL,
        type TEXT NOT NULL,
        share REAL,
        classification INTEGER NOT NULL DEFAULT 0,
        dir INTEGER NOT NULL
    )""",
    # Each edge is stored twice (dir=1 as recorded, dir=-1 reversed) so every traversal,
    # in either direction, is a single indexed lookup on `src`.
    "CREATE INDEX IF NOT EXISTS ix_gedges_src ON gedges(src, type)",
    """CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        clearance INTEGER NOT NULL,
        display TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        username TEXT NOT NULL,
        action TEXT NOT NULL,
        object_id TEXT,
        detail TEXT
    )""",
]

CLEARANCES = {0: "UNCLASSIFIED", 1: "RESTRICTED", 2: "SECRET"}
DEFAULT_USERS = [
    ("analyst", 0, "Analyst (UNCLASSIFIED)"),
    ("investigator", 1, "Investigator (RESTRICTED)"),
    ("admin", 2, "Admin (SECRET)"),
]

_engine: Engine | None = None


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def is_postgres(conn_or_engine) -> bool:
    return conn_or_engine.dialect.name == "postgresql"


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = database_url()
        if url.startswith("sqlite"):
            DATA.mkdir(parents=True, exist_ok=True)
            _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

            @event.listens_for(_engine, "connect")
            def _pragmas(dbapi_conn, _):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA cache_size=-200000")
                cur.close()
        else:
            _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def reset_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def init_db(engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    with engine.begin() as conn:
        for stmt in SCHEMA:
            if is_postgres(conn):
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
            conn.execute(text(stmt))
        for username, clearance, display in DEFAULT_USERS:
            conn.execute(
                text(
                    "INSERT INTO users (username, clearance, display) VALUES (:u, :c, :d) "
                    "ON CONFLICT (username) DO NOTHING"
                ),
                {"u": username, "c": clearance, "d": display},
            )


@contextmanager
def tx():
    with get_engine().begin() as conn:
        yield conn


def loads(value, default=None):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def rows(conn: Connection, sql: str, **params) -> list[dict]:
    return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def in_clause(prefix: str, values) -> tuple[str, dict]:
    """Expand a list into `:p0, :p1, ...` placeholders (portable across dialects)."""
    values = list(values)
    if not values:
        return "NULL", {}
    names = [f"{prefix}{i}" for i in range(len(values))]
    return ", ".join(f":{n}" for n in names), dict(zip(names, values))
