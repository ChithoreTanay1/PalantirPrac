"""Shared ingestion machinery: the normalised schema, Parquet staging and idempotent loading.

Pipeline per source:   raw file --adapter--> Record/Edge/Event --stage--> Parquet --load--> DB

* Every object carries provenance (source, source_url, source_doc, run_id).
* Every object has a deterministic id ("<source>:<native id>") and a content hash, so a
  re-run inserts new rows, updates changed rows and leaves identical rows untouched.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text

from ..db import DATA, dumps, get_engine, init_db

STAGING = DATA / "staging"

# Normalised object types (a deliberately small subset of FollowTheMoney).
ENTITY_SCHEMAS = {"Person", "Company", "Vessel", "Aircraft", "Address"}
# Normalised relationship types.
EDGE_TYPES = {
    "OWNS", "DIRECTS", "REPRESENTS", "FAMILY", "ASSOCIATE", "MEMBER_OF",
    "INTERMEDIARY_OF", "REGISTERED_AT", "LINKED",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _hash(obj: dict) -> str:
    return hashlib.sha1(dumps(obj).encode()).hexdigest()


@dataclass
class Record:
    source: str
    source_id: str
    schema: str
    caption: str
    names: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    birth_date: str | None = None
    props: dict = field(default_factory=dict)
    classification: int = 0
    synthetic: bool = False
    gt_id: str | None = None  # ground-truth identity, used only by ER evaluation
    source_url: str | None = None
    source_doc: str | None = None

    @property
    def id(self) -> str:
        return f"{self.source}:{self.source_id}"


@dataclass
class Edge:
    source: str
    src: str  # record id
    dst: str  # record id
    type: str
    role: str | None = None
    share: float | None = None
    start_date: str | None = None
    end_date: str | None = None
    props: dict = field(default_factory=dict)
    classification: int = 0
    source_url: str | None = None
    source_doc: str | None = None
    native_id: str | None = None

    @property
    def id(self) -> str:
        key = self.native_id or f"{self.src}|{self.type}|{self.dst}|{self.role or ''}"
        return f"{self.source}:e:{hashlib.sha1(key.encode()).hexdigest()[:20]}"


@dataclass
class Event:
    source: str
    record_id: str
    type: str
    date: str
    lat: float | None = None
    lon: float | None = None
    geo_precision: str | None = None
    place: str | None = None
    country: str | None = None
    description: str | None = None
    classification: int = 0
    source_url: str | None = None
    source_doc: str | None = None
    native_id: str | None = None

    @property
    def id(self) -> str:
        key = self.native_id or f"{self.record_id}|{self.type}|{self.date}|{self.place or ''}"
        return f"{self.source}:ev:{hashlib.sha1(key.encode()).hexdigest()[:20]}"


_DATE_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%b %d, %Y", "%Y-%m", "%Y")


def norm_date(value: str | None) -> str | None:
    """Parse the zoo of source date formats into ISO (keeping partial precision)."""
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", v):
        return v
    if re.match(r"\d{4}-\d{2}-\d{2}T", v):
        return v[:10]
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(v.title() if "%b" in fmt else v, fmt)
            return d.strftime("%Y-%m-%d" if "%d" in fmt else ("%Y-%m" if "%m" in fmt or "%b" in fmt else "%Y"))
        except ValueError:
            continue
    return None


def uniq(values: Iterable) -> list:
    seen, out = set(), []
    for v in values:
        if v is None:
            continue
        v = str(v).strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


# --------------------------------------------------------------------------- staging


def _record_row(r: Record, run_id: str) -> dict:
    row = asdict(r)
    row["id"] = r.id
    row["synthetic"] = int(r.synthetic)
    row["names"] = dumps(uniq(r.names or [r.caption]))
    row["countries"] = dumps(uniq(r.countries))
    row["identifiers"] = dumps(uniq(r.identifiers))
    row["props"] = dumps(r.props)
    row["content_hash"] = _hash({k: v for k, v in row.items() if k != "id"})
    row["run_id"] = run_id
    return row


def _edge_row(e: Edge, run_id: str) -> dict:
    row = asdict(e)
    row.pop("native_id")
    row["id"] = e.id
    row["props"] = dumps(e.props)
    row["content_hash"] = _hash({k: v for k, v in row.items() if k != "id"})
    row["run_id"] = run_id
    return row


def _event_row(ev: Event, run_id: str) -> dict:
    row = asdict(ev)
    row.pop("native_id")
    row["id"] = ev.id
    row["content_hash"] = _hash({k: v for k, v in row.items() if k != "id"})
    row["run_id"] = run_id
    return row


_NUMERIC = {"classification": pa.int64(), "synthetic": pa.int64(), "share": pa.float64(),
            "lat": pa.float64(), "lon": pa.float64()}
STAGE_CHUNK = 50_000


def _arrow_schema(kind: str) -> pa.Schema:
    return pa.schema([(c, _NUMERIC.get(c, pa.string())) for c in _COLUMNS[kind] if c != "ingested_at"])


def stage(source: str, objects: Iterator, run_id: str, progress_every: int = 250_000) -> dict[str, Path]:
    """Stream adapter output to Parquet (data/staging/<source>/*.parquet) in fixed-size chunks.

    Memory stays flat regardless of source size: only the set of ids seen so far is kept
    (first occurrence of an id wins). The Parquet files are the bulk/analytics copy (queried
    with DuckDB); the DB load reads them back, so a load can be repeated without re-parsing.
    """
    out_dir = STAGING / source
    out_dir.mkdir(parents=True, exist_ok=True)
    row_fn = {"records": _record_row, "edges": _edge_row, "events": _event_row}
    schemas = {k: _arrow_schema(k) for k in row_fn}
    paths = {k: out_dir / f"{k}.parquet" for k in row_fn}
    writers = {k: pq.ParquetWriter(paths[k], schemas[k]) for k in row_fn}
    buffers: dict[str, list[dict]] = {k: [] for k in row_fn}
    seen: dict[str, set[str]] = {k: set() for k in row_fn}

    def flush(kind: str) -> None:
        buf = buffers[kind]
        if not buf:
            return
        cols = {}
        for f in schemas[kind]:
            vals = [r.get(f.name) for r in buf]
            if f.type == pa.string():
                vals = [None if v is None else str(v) for v in vals]
            cols[f.name] = vals
        writers[kind].write_table(pa.table(cols, schema=schemas[kind]))
        buf.clear()

    n = 0
    try:
        for obj in objects:
            kind = "records" if isinstance(obj, Record) else "edges" if isinstance(obj, Edge) else                 "events" if isinstance(obj, Event) else None
            if kind is None:
                continue
            row = row_fn[kind](obj, run_id)
            if row["id"] in seen[kind]:
                continue
            seen[kind].add(row["id"])
            buffers[kind].append(row)
            if len(buffers[kind]) >= STAGE_CHUNK:
                flush(kind)
            n += 1
            if progress_every and n % progress_every == 0:
                print(f"  [{source}] staged {n:,} objects "
                      f"({len(seen['records']):,} records, {len(seen['edges']):,} edges, {len(seen['events']):,} events)",
                      flush=True)
        for kind in row_fn:
            flush(kind)
    finally:
        for w in writers.values():
            w.close()
    return paths


# --------------------------------------------------------------------------- loading

_COLUMNS = {
    "records": [
        "id", "source", "source_id", "schema", "caption", "names", "countries", "identifiers",
        "birth_date", "props", "classification", "synthetic", "gt_id", "source_url", "source_doc",
        "run_id", "content_hash", "ingested_at",
    ],
    "edges": [
        "id", "source", "src", "dst", "type", "role", "share", "start_date", "end_date", "props",
        "classification", "source_url", "source_doc", "run_id", "content_hash",
    ],
    "events": [
        "id", "source", "record_id", "type", "date", "lat", "lon", "geo_precision", "place",
        "country", "description", "classification", "source_url", "source_doc", "run_id", "content_hash",
    ],
}


def load(source: str, paths: dict[str, Path], run_id: str, file: str | None = None, file_hash: str | None = None) -> dict:
    """Idempotent load of staged Parquet into the DB. Returns insert/update/unchanged counts."""
    engine = get_engine()
    init_db(engine)
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    started = now_iso()
    with engine.begin() as conn:
        for kind, cols in _COLUMNS.items():
            pf = pq.ParquetFile(paths[kind])
            if pf.metadata.num_rows == 0:
                continue
            existing = {
                r[0]: r[1]
                for r in conn.execute(text(f"SELECT id, content_hash FROM {kind} WHERE source = :s"), {"s": source})
            }
            seen = set()
            placeholders = ", ".join(f":{c}" for c in cols)
            insert_sql = text(f"INSERT INTO {kind} ({', '.join(cols)}) VALUES ({placeholders})")
            update_sql = text(f"UPDATE {kind} SET {', '.join(f'{c} = :{c}' for c in cols if c != 'id')} WHERE id = :id")
            for batch in pf.iter_batches(batch_size=STAGE_CHUNK):
                new, changed = [], []
                for row in batch.to_pylist():
                    row.setdefault("ingested_at", started)
                    row = {c: row.get(c) for c in cols}
                    seen.add(row["id"])
                    old = existing.get(row["id"])
                    if old is None:
                        new.append(row)
                    elif old != row["content_hash"]:
                        changed.append(row)
                    else:
                        stats["unchanged"] += 1
                if new:
                    conn.execute(insert_sql, new)
                if changed:
                    conn.execute(update_sql, changed)
                stats["inserted"] += len(new)
                stats["updated"] += len(changed)
            # Rows that disappeared from the source are removed (the source is the authority).
            gone = [i for i in existing if i not in seen]
            if gone:
                conn.execute(text(f"DELETE FROM {kind} WHERE id = :id"), [{"id": g} for g in gone])
            stats["deleted"] += len(gone)
        conn.execute(
            text(
                "INSERT INTO ingest_runs (id, source, file, file_hash, started_at, finished_at, inserted, updated, unchanged) "
                "VALUES (:id, :s, :f, :h, :st, :fi, :i, :u, :n)"
            ),
            {"id": run_id, "s": source, "f": file, "h": file_hash, "st": started, "fi": now_iso(),
             "i": stats["inserted"], "u": stats["updated"], "n": stats["unchanged"]},
        )
    return stats


def file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(source: str, objects: Iterator, file: Path | None = None) -> dict:
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    paths = stage(source, objects, run_id)
    stats = load(source, paths, run_id, str(file) if file else None, file_sha1(file) if file and file.is_file() else None)
    stats["run_id"] = run_id
    return stats
