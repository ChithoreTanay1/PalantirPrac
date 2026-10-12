"""OpenSky Network live ADS-B state vectors.

Auth: OAuth2 client credentials (OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET, the API-client
flow for accounts created since 2025), falling back to basic auth (OPENSKY_USERNAME /
OPENSKY_PASSWORD) or anonymous. A global /states/all call costs 4 API credits; the default
interval (120 s) uses ~2,900 of the 4,000 daily credits a standard account gets.

Tracking scopes:
  watchlist     sanctioned airframes (ICAO24) and sanctioned airlines (callsign prefix)
  jurisdiction  any aircraft whose ICAO24 block is registered in a sanctioned jurisdiction
                (OPENSKY_JURISDICTIONS, default Iran, Russia, Syria, North Korea, Belarus, Venezuela)
On demand (per aircraft, uses credits, cached): flight history and the current flight's track.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx

from .store import add_track, env, health, iso, upsert_events
from .watchlist import Watchlist

API = "https://opensky-network.org/api"
STATES_URL = f"{API}/states/all"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
# OpenSky's own origin_country spellings; '|'-separated because names may contain commas
DEFAULT_JURISDICTIONS = ("Islamic Republic of Iran|Russian Federation|Syrian Arab Republic|"
                         "Democratic People's Republic of Korea|Belarus|Venezuela")
JURISDICTION_TRACK_EVERY = 600  # seconds between stored track points for non-watchlist aircraft


class _Auth:
    """Shared OpenSky credentials (token cached across the feed and on-demand calls)."""

    def __init__(self):
        self.token: str | None = None
        self.exp = 0.0

    def mode(self) -> str:
        if env("OPENSKY_CLIENT_ID") and env("OPENSKY_CLIENT_SECRET"):
            return "oauth2 client"
        if env("OPENSKY_USERNAME"):
            return "basic auth"
        return "anonymous (low limits)"

    async def kwargs(self, client: httpx.AsyncClient) -> dict:
        cid, secret = env("OPENSKY_CLIENT_ID"), env("OPENSKY_CLIENT_SECRET")
        if cid and secret:
            if not self.token or time.time() > self.exp - 60:
                r = await client.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret})
                r.raise_for_status()
                tok = r.json()
                self.token, self.exp = tok["access_token"], time.time() + tok.get("expires_in", 1800)
            return {"headers": {"Authorization": f"Bearer {self.token}"}}
        user, pw = env("OPENSKY_USERNAME"), env("OPENSKY_PASSWORD")
        if user and pw:
            return {"auth": (user, pw)}
        return {}


auth = _Auth()


def jurisdictions() -> set[str]:
    return {j.strip() for j in (env("OPENSKY_JURISDICTIONS", DEFAULT_JURISDICTIONS) or "").split("|") if j.strip()}


def match_states(states: list[list], watch: Watchlist, include_jurisdiction: bool = False) -> list[dict]:
    """Turn raw state vectors into live asset rows (watchlist hits, optionally jurisdiction hits)."""
    juris = jurisdictions() if include_jurisdiction else set()
    out = []
    for s in states or []:
        icao24, callsign = (s[0] or "").lower(), (s[1] or "").strip()
        lon, lat = s[5], s[6]
        if lat is None or lon is None:
            continue
        hit = watch.icao24.get(icao24)
        record_id = None
        if hit:
            record_id, basis, scope = hit["record_id"], f"airframe ({hit['basis']})", "watchlist"
        elif len(callsign) > 3 and callsign[:3] in watch.operators and callsign[3:4].isdigit():
            record_id, basis, scope = watch.operators[callsign[:3]]["record_id"], f"operator {callsign[:3]}", "watchlist"
        elif s[2] in juris:
            basis, scope = f"registered in {s[2]}", "jurisdiction"
        else:
            continue
        seen = s[4] or s[3] or int(time.time())
        out.append({
            "id": f"adsb:{icao24}",
            "feed": "opensky",
            "kind": "Aircraft",
            "ts": iso(datetime.fromtimestamp(seen, timezone.utc)),
            "lat": lat,
            "lon": lon,
            "geo_precision": "exact",
            "title": callsign or icao24,
            "detail": {
                "icao24": icao24, "callsign": callsign, "origin_country": s[2], "altitude_m": s[13] or s[7],
                "velocity_ms": s[9], "heading": s[10], "on_ground": s[8], "squawk": s[14],
                "match_basis": basis, "scope": scope, "linked": watch.captions.get(record_id) if record_id else None,
                "registration": (hit or {}).get("registration"),
            },
            "url": f"https://globe.adsbexchange.com/?icao={icao24}",
            "record_id": record_id,
        })
    return out


class OpenSkyFeed:
    name = "opensky"

    def __init__(self, watch: Watchlist, interval: int = 120):
        self.watch = watch
        self.interval = interval
        self.last_track: dict[str, float] = {}
        self.include_jurisdiction = env("OPENSKY_TRACK_JURISDICTIONS", "1") == "1"

    async def tick(self, client: httpx.AsyncClient) -> None:
        kw = await auth.kwargs(client)
        r = await client.get(STATES_URL, **kw)
        if r.status_code == 429:
            raise RuntimeError(f"rate limited (429); retry after {r.headers.get('X-Rate-Limit-Retry-After-Seconds', '?')} s")
        r.raise_for_status()
        data = r.json()
        rows = match_states(data.get("states"), self.watch, self.include_jurisdiction)
        await asyncio.to_thread(upsert_events, rows)
        t = time.time()
        track = []
        for x in rows:
            every = 0 if x["record_id"] else JURISDICTION_TRACK_EVERY
            if t - self.last_track.get(x["id"], 0) >= every:
                self.last_track[x["id"]] = t
                track.append({"asset_id": x["id"], "ts": x["ts"], "lat": x["lat"], "lon": x["lon"]})
        await asyncio.to_thread(add_track, track)
        health.ok(self.name, aircraft_seen=len(data.get("states") or []),
                  watchlist_hits=sum(1 for x in rows if x["record_id"]),
                  jurisdiction_hits=sum(1 for x in rows if not x["record_id"]),
                  credits_left=r.headers.get("X-Rate-Limit-Remaining"))

    async def run(self, stop) -> None:
        health.set(self.name, enabled=True, label="OpenSky ADS-B", interval_s=self.interval, auth=auth.mode(),
                   watch_airframes=len(self.watch.icao24), watch_operators=sorted(self.watch.operators),
                   jurisdictions=sorted(jurisdictions()) if self.include_jurisdiction else [])
        async with httpx.AsyncClient(timeout=60) as client:
            while not stop.is_set():
                try:
                    await self.tick(client)
                except Exception as e:
                    health.fail(self.name, e)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.interval)
                except asyncio.TimeoutError:
                    pass


# --------------------------------------------------------------------------- on demand

_cache: dict[str, tuple[float, object]] = {}


async def _get(path: str, params: dict, ttl: int) -> object:
    key = path + repr(sorted(params.items()))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.get(f"{API}{path}", params=params, **(await auth.kwargs(client)))
    if r.status_code == 404:
        data: object = []
    elif r.status_code in (401, 403):
        raise PermissionError("OpenSky rejected the request: flight history and tracks need an OpenSky account (set keys in backend/.env)")
    elif r.status_code == 429:
        raise RuntimeError("OpenSky rate limit reached; try again later")
    else:
        r.raise_for_status()
        data = r.json()
    _cache[key] = (time.time(), data)
    return data


async def flights(icao24: str, days: int = 2) -> list[dict]:
    """Departure/arrival history (OpenSky caps one request at 2 days; airports are estimated)."""
    end = int(time.time())
    begin = end - days * 86400
    data = await _get("/flights/aircraft", {"icao24": icao24.lower(), "begin": begin, "end": end}, ttl=900)
    return sorted(data or [], key=lambda f: -(f.get("firstSeen") or 0))  # type: ignore[union-attr]


async def track(icao24: str) -> dict:
    """Waypoints of the aircraft's current (or most recent) flight."""
    data = await _get("/tracks/all", {"icao24": icao24.lower(), "time": 0}, ttl=120)
    return data or {}  # type: ignore[return-value]
