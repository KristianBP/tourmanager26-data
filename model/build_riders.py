"""Bygg data/processed/riders_2026.json: TM-pool + PCS-profil + formscore.

Kjør: .venv/bin/python model/build_riders.py
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from model.form import form_score
from model.match_names import match
from scrapers import pcs, tourmanager as tm

PROCESSED = ROOT / "data" / "processed"


def _load_profile(rider_url: str) -> dict | None:
    slug = rider_url.rstrip("/").split("/")[-1]
    files = sorted((ROOT / "data" / "raw" / "pcs").glob(f"rider_{slug}_*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text())


def _age(birthdate: str | None) -> int | None:
    if not birthdate:
        return None
    try:
        y, m, d = (int(x) for x in str(birthdate).split("-"))
        t = date.today()
        return t.year - y - ((t.month, t.day) < (m, d))
    except Exception:
        return None


def build() -> list[dict]:
    riders = tm.riders()
    sl = pcs.startlist()
    mapping, unmatched = match(riders, sl)
    assert not unmatched, f"umatchede ryttere: {[p['name'] for p in unmatched]}"

    out = []
    for p in riders:
        pcs_entry = mapping[p["id"]]
        prof = _load_profile(pcs_entry["rider_url"]) or {}
        spec = prof.get("points_per_speciality") or {}
        seasons = {s["season"]: s for s in prof.get("points_per_season_history") or []}
        out.append({
            "tm_id": p["id"],
            "name": p["name"],
            "position": p["position"],
            "price": tm.price(p),
            "team": p["team"]["name"],
            "team_code": p["team"]["code"],
            "is_available": p["isAvailable"],
            "pcs_url": pcs_entry["rider_url"],
            "age": _age(prof.get("birthdate")),
            "speciality": {
                "climber": spec.get("climber", 0),
                "gc": spec.get("gc", 0),
                "sprint": spec.get("sprint", 0),
                "time_trial": spec.get("time_trial", 0),
                "one_day": spec.get("one_day_races", 0),
                "hills": spec.get("hills", 0),
            },
            "form_2026": form_score(prof.get("season_results")),
            "giro_2026_stages": sum(1 for x in prof.get("season_results") or []
                                    if "giro-d-italia/2026" in (x.get("stage_url") or "") and x.get("date")),
            "pcs_points_2026": (seasons.get(2026) or {}).get("points", 0),
            "pcs_points_2025": (seasons.get(2025) or {}).get("points", 0),
        })
    return out


if __name__ == "__main__":
    rows = build()
    PROCESSED.mkdir(parents=True, exist_ok=True)
    path = PROCESSED / "riders_2026.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(f"skrev {len(rows)} ryttere til {path}")
    top = sorted(rows, key=lambda r: r["form_2026"], reverse=True)[:12]
    print("\ntopp 12 form_2026:")
    for r in top:
        print(f"  {r['name']:28s} {r['position']:13s} {r['price']:>10,}  form={r['form_2026']:>7}")
