"""Etappefavoritter fra cyclingstage.com og cyclingoracle.com.

Begge er server-rendret HTML. Favorittlistene publiseres typisk kvelden før
etappen (cyclingoracle av og til litt tidligere). Caches per etappe per dag —
re-fetch samme dag gir cache-treff, ny dag gir ny fetch (listene kan oppdateres).
"""
import json
import re
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "cyclingstage"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}

CYCLINGSTAGE_URL = "https://www.cyclingstage.com/tour-de-france-2026-route/stage-{n}-tdf-2026/"
CYCLINGSTAGE_FAV_URL = "https://www.cyclingstage.com/tour-de-france-2026-favourites/stage-{n}-contenders-tdf-2026/"
CYCLINGORACLE_URL = "https://www.cyclingoracle.com/nl/blog/tour-de-france-2026-voorspelling-etappe-{n}"

_last_request = 0.0


def _get(url: str) -> str:
    global _last_request
    wait = 1.0 - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def _cache_path(source: str, stage: int) -> Path:
    return RAW_DIR / f"{source}_stage{stage:02d}_{date.today():%Y%m%d}.json"


def fetch_cyclingstage_favorites(stage: int) -> dict:
    """Favoritter fra cyclingstage sin egen favorittartikkel per etappe.

    Format i artikkelteksten (verifisert 2026-07-03):
        **** Tim Merlier, Jasper Philipsen, Olav Kooij
        *** Biniam Girmay, ...
    Flere stjerner = sterkere favoritt. På TTT (etappe 1) er navnene lag med
    kapteiner i parentes. Artiklene oppdateres frem mot etappestart —
    cachen er per dag, så re-kjøring en ny dag gir fersk liste.
    """
    path = _cache_path("cyclingstage", stage)
    if path.exists():
        return json.loads(path.read_text())

    html = _get(CYCLINGSTAGE_FAV_URL.format(n=stage))
    article = BeautifulSoup(html, "html.parser").find("article")
    favorites = []
    for line in article.get_text("\n").splitlines():
        m = re.match(r"^\s*(\*{1,4})\s+(.+)$", line.strip())
        if m:
            stars = len(m.group(1))
            for name in m.group(2).split(","):
                favorites.append({"name": name.strip(), "stars": stars})

    data = {"source": "cyclingstage", "stage": stage, "fetched": str(date.today()),
            "url": CYCLINGSTAGE_FAV_URL.format(n=stage),
            "favorites": favorites, "published": bool(favorites)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return data


def fetch_cyclingoracle_prediction(stage: int) -> dict:
    """Modellprediksjon fra cyclingoracle (WielerOrakel).

    xW-tabellen (vinnersannsynlighet per rytter) ligger som dobbelt
    HTML-entity-escaped JSON i et data-attributt (verifisert 2026-07-07).
    """
    import html as htmllib

    path = _cache_path("cyclingoracle", stage)
    if path.exists():
        return json.loads(path.read_text())

    raw = _get(CYCLINGORACLE_URL.format(n=stage))
    unescaped = htmllib.unescape(htmllib.unescape(raw))
    preds = []
    m = re.search(r'"predictions":\[(.*?)\]', unescaped, re.S)
    if m:
        for obj in re.finditer(r"\{[^{}]*\}", m.group(1)):
            try:
                d = json.loads(obj.group(0).replace("\\/", "/"))
                preds.append({"name": d.get("col1") or d.get("searchQuery"),
                              "win_pct": float(str(d.get("winPercentage", "0")).replace(",", "."))})
            except (ValueError, KeyError):
                pass
    text = BeautifulSoup(raw, "html.parser").get_text("\n", strip=True)

    data = {"source": "cyclingoracle", "stage": stage, "fetched": str(date.today()),
            "predictions_xw": preds, "raw_text": text[:6000]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return data
