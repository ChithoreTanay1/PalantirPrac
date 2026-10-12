"""GDELT 2.0 event feed: geolocated news events, a new file every 15 minutes. Free, no key.

http://data.gdeltproject.org/gdeltv2/lastupdate.txt points at the newest *.export.CSV.zip
(61 tab-separated columns, CAMEO-coded). We keep only conflict/coercion event types and
sanctions/embargo actions, deduplicated per article and location.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import zipfile
from datetime import datetime, timedelta, timezone

import httpx

from .store import health, iso, now, upsert_events

LAST_UPDATE = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
FILE_URL = "http://data.gdeltproject.org/gdeltv2/{stamp}.export.CSV.zip"

# CAMEO root codes worth showing on an illicit-finance / conflict globe
ROOTS = {
    "14": "Protest",
    "15": "Military posture",
    "17": "Coercion",
    "18": "Assault",
    "19": "Fighting",
    "20": "Mass violence",
}
# Specific codes from otherwise-filtered roots
CODES = {
    "163": "Sanctions / embargo",
    "1631": "Sanctions / embargo",
    "1632": "Sanctions / embargo",
    "1633": "Sanctions / embargo",
    "1634": "Sanctions / embargo",
    "1711": "Asset seizure",
    "1712": "Destruction of property",
}
GEO_PRECISION = {"1": "country", "2": "region", "3": "city", "4": "city", "5": "region"}

# column indices in the 2.0 export file
C_ACTOR1, C_ACTOR2 = 6, 16
C_CODE, C_ROOT, C_GOLDSTEIN, C_MENTIONS, C_SOURCES = 26, 28, 30, 31, 32
C_GEO_TYPE, C_GEO_NAME, C_GEO_CC, C_LAT, C_LON, C_ADDED, C_URL = 51, 52, 53, 56, 57, 59, 60


def classify(code: str, root: str) -> str | None:
    if code in CODES:
        return CODES[code]
    if code[:3] in CODES:
        return CODES[code[:3]]
    return ROOTS.get(root)


def parse_export(raw_zip: bytes) -> list[dict]:
    z = zipfile.ZipFile(io.BytesIO(raw_zip))
    reader = csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8", errors="replace"), delimiter="\t")
    out: dict[str, dict] = {}
    for row in reader:
        if len(row) < 61:
            continue
        category = classify(row[C_CODE], row[C_ROOT])
        if not category or not row[C_LAT] or not row[C_LON]:
            continue
        try:
            lat, lon = float(row[C_LAT]), float(row[C_LON])
        except ValueError:
            continue
        url = row[C_URL]
        # one point per (article, category, place): GDELT emits many near-duplicate rows per story
        key = hashlib.sha1(f"{url}|{category}|{row[C_GEO_NAME]}".encode()).hexdigest()[:20]
        actors = " → ".join(a.title() for a in (row[C_ACTOR1], row[C_ACTOR2]) if a)
        mentions = int(row[C_MENTIONS] or 0)
        if key in out:
            out[key]["detail"]["mentions"] += mentions
            continue
        added = datetime.strptime(row[C_ADDED], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        out[key] = {
            "id": f"gdelt:{key}",
            "feed": "gdelt",
            "kind": category,
            "ts": iso(added),
            "lat": lat,
            "lon": lon,
            "geo_precision": GEO_PRECISION.get(row[C_GEO_TYPE], "unknown"),
            "title": f"{category} · {row[C_GEO_NAME]}",
            "detail": {
                "actors": actors,
                "cameo": row[C_CODE],
                "goldstein": float(row[C_GOLDSTEIN] or 0),
                "mentions": mentions,
                "sources": int(row[C_SOURCES] or 0),
                "place": row[C_GEO_NAME],
                "country_fips": row[C_GEO_CC],
            },
            "url": url,
            "seen_at": iso(added),
        }
    return list(out.values())


def stamps_back(hours: float, latest: datetime) -> list[str]:
    """15-minute file stamps going back `hours` from the latest published file."""
    n = int(hours * 4)
    return [(latest - timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S") for i in range(n)]


class GdeltFeed:
    name = "gdelt"

    def __init__(self, backfill_hours: float = 6, interval: int = 300):
        self.backfill_hours = backfill_hours
        self.interval = interval
        self.done: set[str] = set()

    async def _fetch(self, client: httpx.AsyncClient, stamp: str) -> int:
        r = await client.get(FILE_URL.format(stamp=stamp))
        if r.status_code == 404:
            self.done.add(stamp)  # GDELT occasionally skips a slot
            return 0
        r.raise_for_status()
        rows = await asyncio.to_thread(parse_export, r.content)
        await asyncio.to_thread(upsert_events, rows)
        self.done.add(stamp)
        return len(rows)

    async def tick(self, client: httpx.AsyncClient) -> None:
        r = await client.get(LAST_UPDATE)
        r.raise_for_status()
        url = next(line.split()[-1] for line in r.text.splitlines() if ".export." in line)
        latest = datetime.strptime(url.rsplit("/", 1)[1][:14], "%Y%m%d%H%M%S")
        todo = [s for s in stamps_back(self.backfill_hours if not self.done else 1, latest) if s not in self.done]
        added = 0
        for stamp in todo:
            added += await self._fetch(client, stamp)
        health.ok(self.name, latest_file=latest.strftime("%Y-%m-%d %H:%M UTC"), files=len(self.done),
                  last_batch=added, next_in_s=self.interval)

    async def run(self, stop) -> None:
        health.set(self.name, enabled=True, label="GDELT news events", interval_s=self.interval)
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            while not stop.is_set():
                try:
                    await self.tick(client)
                except Exception as e:  # keep the loop alive; surface the error in the UI
                    health.fail(self.name, e)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.interval)
                except asyncio.TimeoutError:
                    pass
