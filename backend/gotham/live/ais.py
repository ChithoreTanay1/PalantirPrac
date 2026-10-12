"""aisstream.io live AIS (ship positions) over a websocket. Free API key: AISSTREAM_API_KEY.

We subscribe to the whole world (the MMSI filter allows only 50 ships, and the watchlist
has ~800) and filter locally. ShipStaticData messages carry the IMO number, which lets us
learn the current MMSI of a watchlisted ship even when the sanctions list only has its IMO.

Coverage is terrestrial-receiver based (good near coasts, thin mid-ocean), and sanctioned
"dark fleet" tankers often switch AIS off, so *silence* is itself a signal.
"""
from __future__ import annotations

import asyncio
import json
import time

import websockets

from .store import add_track, env, health, iso, now, upsert_events
from .watchlist import Watchlist

WS_URL = "wss://stream.aisstream.io/v0/stream"
NAV_STATUS = {0: "under way (engine)", 1: "at anchor", 2: "not under command", 3: "restricted manoeuvrability",
              5: "moored", 7: "fishing", 8: "under way (sail)"}


class AisFeed:
    name = "ais"

    def __init__(self, watch: Watchlist, flush_every: float = 15.0, track_every: float = 600.0,
                 track_tankers: bool | None = None):
        self.watch = watch
        self.flush_every = flush_every
        self.track_every = track_every            # watchlisted ships
        self.tanker_track_every = 1800.0          # other tankers (keeps storage bounded)
        self.track_tankers = env("AIS_TRACK_ALL_TANKERS", "1") == "1" if track_tankers is None else track_tankers
        self.pending: dict[str, dict] = {}
        self.last_track: dict[str, float] = {}
        self.static: dict[str, dict] = {}  # mmsi -> static info (type, IMO, destination, ...)
        self.msgs = 0

    def handle(self, msg: dict) -> None:
        """Process one aisstream message (public for tests)."""
        self.msgs += 1
        mtype = msg.get("MessageType")
        meta = msg.get("MetaData", {})
        mmsi = str(meta.get("MMSI") or "")
        body = msg.get("Message", {}).get(mtype, {}) if mtype else {}
        if mtype == "ShipStaticData":
            imo = str(body.get("ImoNumber") or "").lstrip("0")
            if imo and imo in self.watch.imo and mmsi and mmsi not in self.watch.mmsi:
                self.watch.mmsi[mmsi] = self.watch.imo[imo]  # learned IMO -> current MMSI
            stype = body.get("Type")
            if mmsi in self.watch.mmsi or (self.track_tankers and isinstance(stype, int) and 80 <= stype <= 89):
                dim = body.get("Dimension") or {}
                self.static[mmsi] = {"imo": imo, "destination": (body.get("Destination") or "").strip(),
                                     "ais_name": (body.get("Name") or "").strip(), "type": stype,
                                     "call_sign": (body.get("CallSign") or "").strip(),
                                     "length_m": (dim.get("A") or 0) + (dim.get("B") or 0) or None,
                                     "draught_m": body.get("MaximumStaticDraught")}
            return
        if mtype not in ("PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"):
            return
        rid = self.watch.mmsi.get(mmsi)
        st = self.static.get(mmsi, {})
        is_tanker = isinstance(st.get("type"), int) and 80 <= st["type"] <= 89
        if not rid and not is_tanker:
            return
        lat, lon = meta.get("latitude"), meta.get("longitude")
        if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
            return
        self.pending[mmsi] = {
            "id": f"ais:{mmsi}",
            "feed": "ais",
            "kind": "Vessel",
            "ts": iso(now()),
            "lat": lat,
            "lon": lon,
            "geo_precision": "exact",
            "title": (meta.get("ShipName") or st.get("ais_name") or mmsi).strip(),
            "detail": {"mmsi": mmsi, "imo": st.get("imo"), "sog_kn": body.get("Sog"), "cog": body.get("Cog"),
                       "heading": body.get("TrueHeading"), "nav_status": NAV_STATUS.get(body.get("NavigationalStatus"), body.get("NavigationalStatus")),
                       "destination": st.get("destination"), "ship_type": st.get("type"), "call_sign": st.get("call_sign"),
                       "length_m": st.get("length_m"), "draught_m": st.get("draught_m"),
                       "linked": self.watch.captions.get(rid) if rid else None,
                       "match_basis": "MMSI/IMO on sanctions list" if rid else "tanker (AIS type 80-89)",
                       "scope": "watchlist" if rid else "tanker"},
            "url": f"https://www.marinetraffic.com/en/ais/details/ships/mmsi:{mmsi}",
            "record_id": rid,
        }

    async def flush(self) -> None:
        if not self.pending:
            return
        batch = list(self.pending.values())
        self.pending.clear()
        await asyncio.to_thread(upsert_events, batch)
        t = time.time()
        track = []
        for b in batch:
            every = self.track_every if b["record_id"] else self.tanker_track_every
            if t - self.last_track.get(b["id"], 0) >= every:
                self.last_track[b["id"]] = t
                track.append({"asset_id": b["id"], "ts": b["ts"], "lat": b["lat"], "lon": b["lon"]})
        await asyncio.to_thread(add_track, track)

    async def run(self, stop) -> None:
        key = env("AISSTREAM_API_KEY")
        health.set(self.name, enabled=bool(key), label="aisstream.io AIS", watch_imo=len(self.watch.imo),
                   watch_mmsi=len(self.watch.mmsi))
        if not key:
            health.fail(self.name, "AISSTREAM_API_KEY not set in backend/.env")
            return
        backoff = 5
        while not stop.is_set():
            try:
                async with websockets.connect(WS_URL, max_size=2**22, ping_interval=30) as ws:
                    await ws.send(json.dumps({
                        "APIKey": key,
                        "BoundingBoxes": [[[-90, -180], [90, 180]]],
                        "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport", "ShipStaticData"],
                    }))
                    backoff = 5
                    last_flush = time.time()
                    while not stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            raise RuntimeError("no AIS messages for 60 s")
                        msg = json.loads(raw)
                        if "error" in msg:
                            raise RuntimeError(f"aisstream: {msg['error']}")
                        self.handle(msg)
                        if time.time() - last_flush >= self.flush_every:
                            await self.flush()
                            last_flush = time.time()
                            health.ok(self.name, messages=self.msgs, watch_mmsi=len(self.watch.mmsi),
                                      tankers_known=sum(1 for v in self.static.values() if isinstance(v.get("type"), int) and 80 <= v["type"] <= 89),
                                      tracked=len(self.last_track))
            except Exception as e:
                health.fail(self.name, e)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 300)
