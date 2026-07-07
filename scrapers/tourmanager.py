"""tourmanager.no — spilldata via det åpne API-et.

Backend: https://vm-fantasyapi-production.up.railway.app (funnet via devtools
2026-07-03). Alle lese-endepunktene svarer 200 UTEN auth, så ingen token trengs
for polling. Brukerens token ligger i data/raw/tourmanager/.token (chmod 600)
i tilfelle vi senere trenger kontospesifikke endepunkter (eget lag).

Endepunkter under /tournaments/tourmanager26:
  (rot)          turnering + fullt regelsett (rulesets[0].scoringJson)
  /players       rytterpool: 184 ryttere + 23 SPORT_DIRECTOR-lag, priser i prices[]
  /rounds        alle 21 etapper med stageType og deadlineAt
  /active-round  gjeldende runde + effectiveDeadline + secondsRemaining + isLocked
  /player-points poeng per rytter (tom før touren starter)

Alt caches datostemplet til data/raw/tourmanager/. noPriceChanges=true i
regelsettet, men vi re-fetcher daglig likevel (isAvailable kan endres ved DNS).
"""
import json
import time
from datetime import date
from pathlib import Path

import requests

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "tourmanager"
BASE = "https://vm-fantasyapi-production.up.railway.app/tournaments/tourmanager26"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
    "Accept": "application/json",
    "Origin": "https://www.tourmanager.no",
    "Referer": "https://www.tourmanager.no/",
}

ENDPOINTS = {
    "tournament": "",
    "players": "/players",
    "rounds": "/rounds",
    "active-round": "/active-round",
    "player-points": "/player-points",
}

_last_request = 0.0


def _fetch(name: str, force: bool = False):
    """Hent endepunkt, cachet per dag. force=True hopper over cache
    (brukes for active-round som endres i løpet av dagen)."""
    global _last_request
    path = RAW_DIR / f"{name}_{date.today():%Y%m%d}.json"
    if path.exists() and not force:
        return json.loads(path.read_text())

    wait = 1.0 - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()

    resp = requests.get(BASE + ENDPOINTS[name], headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return data


def tournament() -> dict:
    return _fetch("tournament")


def ruleset() -> dict:
    return tournament()["rulesets"][0]["scoringJson"]


def riders(include_sport_directors: bool = False) -> list[dict]:
    """Rytterpoolen. Hver rytter: name, position, teamId, team.name, prices[],
    isAvailable. SPORT_DIRECTOR-oppføringene er lag, ikke ryttere."""
    pool = _fetch("players")
    if include_sport_directors:
        return pool
    return [p for p in pool if p["position"] != "SPORT_DIRECTOR"]


def sport_directors() -> list[dict]:
    return [p for p in _fetch("players") if p["position"] == "SPORT_DIRECTOR"]


def price(player: dict) -> int:
    """Gjeldende pris i cents (siste innslag i prices-listen)."""
    return player["prices"][-1]["priceCents"]


def rounds() -> list[dict]:
    return _fetch("rounds")


def active_round() -> dict:
    """Live rundestatus — alltid fersk, aldri fra cache."""
    return _fetch("active-round", force=True)


def player_points() -> dict:
    return _fetch("player-points", force=True)
