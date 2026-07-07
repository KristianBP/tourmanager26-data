"""ProCyclingStats via `procyclingstats`-pakken (v0.2.8, krever cloudscraper).

Rate-limit: maks 1 request/sek. Alt caches til data/raw/pcs/ med datostempel;
cached fil for dagens dato brukes i stedet for ny request.
"""
import json
import time
from datetime import date
from pathlib import Path

from procyclingstats import Race, RaceStartlist, Rider, Stage

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "pcs"
_last_request = 0.0


def _throttle():
    global _last_request
    wait = 1.0 - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _cached(name: str, fetch, allow_stale: bool = False):
    """Hent fra dagens cache hvis den finnes, ellers fetch() og lagre.
    allow_stale=True: bruk nyeste daterte fil uansett alder uten ny fetch
    (rytterprofiler er statiske under touren; brukes også i cloud-kjøring
    der PCS kan være utilgjengelig)."""
    path = RAW_DIR / f"{name}_{date.today():%Y%m%d}.json"
    if path.exists():
        return json.loads(path.read_text())
    if allow_stale:
        older = sorted(RAW_DIR.glob(f"{name}_*.json"))
        if older:
            return json.loads(older[-1].read_text())
    _throttle()
    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return data


def startlist(race_url: str = "race/tour-de-france/2026/startlist") -> list[dict]:
    return _cached("startlist", lambda: RaceStartlist(race_url).startlist(), allow_stale=True)


def rider(rider_url: str) -> dict:
    """Full rytterprofil inkl. points_per_speciality (sprint/climber/tt/gc/one_day)."""
    slug = rider_url.rstrip("/").split("/")[-1]
    return _cached(f"rider_{slug}", lambda: Rider(rider_url).parse(), allow_stale=True)


def stage_results(stage_url: str) -> dict:
    """Resultater for en kjørt etappe, f.eks. race/tour-de-france/2026/stage-1."""
    slug = stage_url.rstrip("/").split("/")[-1]
    return _cached(f"results_{slug}", lambda: Stage(stage_url).parse())
