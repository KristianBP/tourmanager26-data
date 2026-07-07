"""Henter TourManager-API-data + cyclingstage-favoritter til data/*.json.

Kjøres av GitHub Actions hver morgen. Kun stdlib.
"""
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://vm-fantasyapi-production.up.railway.app/tournaments/tourmanager26"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
    "Accept": "application/json",
    "Origin": "https://www.tourmanager.no",
    "Referer": "https://www.tourmanager.no/",
}
OUT = Path(__file__).parent / "data"


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def main():
    OUT.mkdir(exist_ok=True)
    status = {"fetched_at_utc": datetime.now(timezone.utc).isoformat(), "errors": []}

    active = None
    for name, ep in [("active_round", "/active-round"), ("players", "/players"),
                     ("player_points", "/player-points")]:
        try:
            raw = get(BASE + ep)
            (OUT / f"{name}.json").write_bytes(raw)
            if name == "active_round":
                active = json.loads(raw)
        except Exception as e:
            status["errors"].append(f"{name}: {e}")

    # favoritter for aktiv etappe fra cyclingstage
    try:
        n = active["round"]["number"]
        html = get(f"https://www.cyclingstage.com/tour-de-france-2026-favourites/"
                   f"stage-{n}-contenders-tdf-2026/").decode("utf-8", "replace")
        text = re.sub(r"<[^>]+>", "\n", html)
        favs = []
        for line in text.splitlines():
            m = re.match(r"^\s*(\*{1,4})\s+(.+)$", line.strip())
            if m:
                for nm in m.group(2).split(","):
                    favs.append({"name": nm.strip(), "stars": len(m.group(1))})
        (OUT / "favourites.json").write_text(json.dumps(
            {"stage": n, "favorites": favs}, ensure_ascii=False, indent=1))
    except Exception as e:
        status["errors"].append(f"favourites: {e}")

    (OUT / "status.json").write_text(json.dumps(status, indent=1))
    print(json.dumps(status, indent=1))


if __name__ == "__main__":
    main()
