"""Formscore fra PCS season_results (2026-sesongen).

Score = sum over resultater av (pcs_points × rittvekt × recency-vekt).
Rittvekter fra model/weights.yaml (dauphine/suisse/NM tyngst).
Normaliseres ikke her — brukes relativt mellom ryttere.
"""
from datetime import date, datetime
from pathlib import Path

import yaml

_W = yaml.safe_load((Path(__file__).parent / "weights.yaml").read_text())

# stage_url-prefiks -> vektnøkkel i weights.yaml
RACE_KEYS = {
    "race/criterium-du-dauphine/2026": "dauphine_2026",
    "race/tour-de-suisse/2026": "tour_de_suisse_2026",
    "race/nc-": "national_championships_2026",   # nasjonale mesterskap
    "race/tour-de-romandie/2026": "romandie_2026",
    "race/itzulia-basque-country/2026": "itzulia_2026",
    "race/paris-nice/2026": "paris_nice_2026",
    "race/tirreno-adriatico/2026": "tirreno_adriatico_2026",
}
DEFAULT_RACE_WEIGHT = 0.4  # andre 2026-ritt


def _race_weight(stage_url: str) -> float:
    for prefix, key in RACE_KEYS.items():
        if stage_url.startswith(prefix) or (prefix == "race/nc-" and "/nc-" in stage_url):
            return _W["form"]["race_weights"].get(key, DEFAULT_RACE_WEIGHT)
    return DEFAULT_RACE_WEIGHT


def _recency_weight(d: str | None, today: date) -> float:
    if not d:
        return 0.6  # klassementsposter uten dato: middels
    try:
        days = (today - datetime.strptime(d, "%Y-%m-%d").date()).days
    except ValueError:
        return 0.6
    if days <= 30:
        return 1.0
    if days <= 60:
        return 0.7
    return 0.4


def form_score(season_results: list[dict], today: date | None = None) -> float:
    today = today or date.today()
    score = 0.0
    for r in season_results or []:
        pts = r.get("pcs_points") or 0
        if not pts:
            continue
        url = r.get("stage_url") or ""
        score += pts * _race_weight(url) * _recency_weight(r.get("date"), today)
    return round(score, 1)
