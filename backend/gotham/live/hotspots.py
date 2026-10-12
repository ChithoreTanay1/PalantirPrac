"""Known ship-to-ship (STS) transfer areas associated with sanctions evasion in public reporting.

Approximate centre points; a vessel that is slow (< 1 kn) within RADIUS_KM is flagged.
Being there is not evidence of anything: many legitimate transfers happen at the same spots.
"""
from __future__ import annotations

import math

RADIUS_KM = 45

STS_AREAS = [
    ("Laconian Gulf (GR)", 36.55, 22.75),
    ("Kalamata / Messinian Gulf (GR)", 36.95, 22.05),
    ("Ceuta / Gibraltar anchorage", 35.95, -5.30),
    ("Fujairah anchorage (AE)", 25.20, 56.45),
    ("Sohar offshore (OM)", 24.45, 56.80),
    ("Eastern OPL off Johor (MY)", 1.35, 104.45),
    ("Lomé offshore (TG)", 6.05, 1.30),
    ("Malta offshore (Hurd Bank)", 35.80, 14.65),
    ("Kerch Strait / Taman (RU)", 45.15, 36.65),
    ("Port Said anchorage (EG)", 31.35, 32.35),
]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742 * math.asin(math.sqrt(a))


def near_sts(lat: float, lon: float) -> tuple[str, float] | None:
    best = None
    for name, la, lo in STS_AREAS:
        d = haversine_km(lat, lon, la, lo)
        if d <= RADIUS_KM and (best is None or d < best[1]):
            best = (name, round(d, 1))
    return best
