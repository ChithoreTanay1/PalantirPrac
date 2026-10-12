"""Country normalisation and centroid geocoding.

Most public sources only give a country (or a free-text address), so events placed from
them carry `geo_precision = "country"` and the UI renders them differently from exact points.
"""
from __future__ import annotations

import csv
import hashlib
from functools import lru_cache

import pycountry

from .db import DATA

_ALIASES = {
    "england": "GB", "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "british": "GB",
    "russia": "RU", "iran": "IR", "syria": "SY", "north korea": "KP", "south korea": "KR",
    "british virgin islands": "VG", "bvi": "VG", "virgin islands, british": "VG",
    "usa": "US", "united states": "US", "uae": "AE", "turkey": "TR", "venezuela": "VE",
    "bolivia": "BO", "tanzania": "TZ", "vietnam": "VN", "laos": "LA", "moldova": "MD",
    "macau": "MO", "hong kong": "HK", "taiwan": "TW", "czech republic": "CZ",
    "isle of man": "IM", "jersey": "JE", "guernsey": "GG", "cayman islands": "KY",
    "crimea": "UA", "kosovo": "XK", "palestine": "PS",
}


@lru_cache(maxsize=1)
def _centroids() -> dict[str, tuple[float, float, str]]:
    out = {}
    with open(DATA / "ref" / "countries.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["latitude"]:
                out[row["country"].upper()] = (float(row["latitude"]), float(row["longitude"]), row["name"])
    out.setdefault("XK", (42.6, 20.9, "Kosovo"))
    return out


@lru_cache(maxsize=4096)
def country_code(value: str | None) -> str | None:
    """Map 'gb', 'GBR', 'England', 'British Virgin Islands' ... to ISO-3166 alpha-2."""
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    low = v.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    if len(v) == 2 and v.upper() in _centroids():
        return v.upper()
    if len(v) == 3:
        c = pycountry.countries.get(alpha_3=v.upper())
        if c:
            return c.alpha_2
    if "-" in v and len(v) <= 6:  # OpenSanctions sub-national codes such as "ua-cri"
        return country_code(v.split("-")[0])
    try:
        return pycountry.countries.lookup(v).alpha_2
    except LookupError:
        pass
    try:
        hits = pycountry.countries.search_fuzzy(v)
        return hits[0].alpha_2 if hits else None
    except LookupError:
        return None


def country_name(code: str | None) -> str | None:
    if not code:
        return None
    c = _centroids().get(code.upper())
    return c[2] if c else code.upper()


def centroid(code: str | None, jitter_key: str | None = None) -> tuple[float, float] | None:
    """Country centroid, deterministically jittered so stacked events stay clickable."""
    if not code:
        return None
    c = _centroids().get(code.upper())
    if not c:
        return None
    lat, lon = c[0], c[1]
    if jitter_key:
        h = hashlib.sha1(jitter_key.encode()).digest()
        lat += (h[0] / 255 - 0.5) * 1.6
        lon += (h[1] / 255 - 0.5) * 1.6
    return round(lat, 5), round(lon, 5)
