"""expected_points[rytter][etappe] — spillets poengskala, ETTER multiplikator.

Modell per etappe: poeng-pooler fordeles på ryttere etter styrkescorer.
  1. Målgang:  favoritt-prior (cyclingstage-stjerner, 60 %) blandet med
               kapabilitetsmodell (PCS-spesialitet mot etappetype, 40 %).
  2. Spurterklassifisering: andel av målgangsforventning (etappetypeavhengig).
  3. Passeringspunkt B: liten pool til spurtere (flatt) / brudd (ellers).
  4. Klatrepoeng: pool fra klatreinventaret; deles brudd/favorittgruppe etter
     klatrenes plassering i etappen. Brudd fordeles på bruddkandidater.
  5. Multiplikator per spillposisjon til slutt (fra rules_2026.json).
SD-forventning per lag beregnes separat (multipliseres IKKE).

Output: dict med matrise og metadata; brukes av optimizer/team_ilp.py.
"""
import json
import re
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RULES = json.loads((ROOT / "data" / "rules_2026.json").read_text())
W = yaml.safe_load((ROOT / "model" / "weights.yaml").read_text())

assert RULES["verified"], "rules_2026.json er ikke verifisert — kjør Fase 0"

STAGE_TYPE_MAP = {  # API stageType -> modellens etappetype
    "FLAT": "flat", "HILLY": "hilly", "MOUNTAIN": "mountain",
    "INDIVIDUAL_TIME_TRIAL": "ITT", "TEAM_TIME_TRIAL": "TTT",
    "MOUNTAIN_TIME_TRIAL": "ITT",
}

CLIMB_POINTS = RULES["points"]["climbs"]  # HC/cat1/... -> plasspoeng
CAT_KEY = {"HC": "HC", "1": "cat1", "2": "cat2", "3": "cat3", "4": "cat4"}


def _norm(name: str) -> frozenset:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return frozenset(s.lower().replace("-", " ").split())


def load_inputs():
    riders = json.loads((ROOT / "data" / "processed" / "riders_2026.json").read_text())
    rounds_file = sorted((ROOT / "data" / "raw" / "tourmanager").glob("rounds_*.json"))[-1]
    rounds = json.loads(rounds_file.read_text())
    climbs_path = ROOT / "data" / "processed" / "stage_climbs_2026.json"
    climbs = json.loads(climbs_path.read_text()) if climbs_path.exists() else None
    favs = {}
    for n in range(1, 22):
        files = sorted((ROOT / "data" / "raw" / "cyclingstage").glob(f"cyclingstage_stage{n:02d}_*.json"))
        if files:
            favs[n] = json.loads(files[-1].read_text())
    return riders, rounds, climbs, favs


def _match_favorites(fav_entries: list[dict], riders: list[dict]) -> dict[int, int]:
    """-> {rider_index: stars}. Håndterer 'Lag (Kaptein1, Kaptein2)' på TTT."""
    idx_by_tokens = {_norm(r["name"]): i for i, r in enumerate(riders)}
    out = {}
    for f in fav_entries:
        name = f["name"]
        paren = re.findall(r"\(([^)]+)\)", name)
        names = []
        if paren:  # TTT-format: lagnavn (leder1, leder2)
            names = [x.strip() for x in paren[0].split(",")]
        else:
            names = [name]
        for nm in names:
            toks = _norm(nm)
            hit = idx_by_tokens.get(toks)
            if hit is None:
                cands = [i for t, i in idx_by_tokens.items() if toks <= t or t <= toks]
                hit = cands[0] if len(cands) == 1 else None
            if hit is None and len(toks) == 1:
                # bare etternavn (TTT-parenteser): unik match på etternavn
                cands = [i for t, i in idx_by_tokens.items() if toks & t]
                hit = cands[0] if len(cands) == 1 else None
            if hit is not None:
                out[hit] = max(out.get(hit, 0), f["stars"])
    return out


def _form_factors(riders: list[dict]) -> list[float]:
    """Karrieretall × formfaktor: (form_2026/median)^exp, klippet.
    Demper veteraner med stor karriere men svak 2026-sesong."""
    cw = W["capability"]
    forms = sorted(r["form_2026"] for r in riders if r["form_2026"] > 0)
    med = forms[len(forms) // 2] if forms else 1.0
    out = []
    for r in riders:
        f = (max(r["form_2026"], 0.01) / med) ** cw["form_exponent"]
        out.append(min(max(f, cw["form_factor_min"]), cw["form_factor_max"]))
    return out


def _capability(r: dict, stype: str) -> float:
    spec = W["capability"]["stage_specialities"][stype]
    return sum(r["speciality"][k] * w for k, w in spec.items())


def _distribute(pool: float, scores: dict[int, float], gamma: float) -> dict[int, float]:
    powered = {i: s ** gamma for i, s in scores.items() if s > 0}
    total = sum(powered.values())
    if total <= 0:
        return {}
    return {i: pool * v / total for i, v in powered.items()}


def _climb_pool(stage_no: int, stype: str, climbs) -> tuple[float, dict[str, float]]:
    """-> (total pool, {posisjon: poeng}). Fallback ved manglende inventar."""
    if climbs and str(stage_no) in climbs["stages"]:
        by_pos = {}
        for c in climbs["stages"][str(stage_no)]["climbs"]:
            pts = sum(CLIMB_POINTS[CAT_KEY[c["category"]]])
            by_pos[c["position"]] = by_pos.get(c["position"], 0) + pts
        return sum(by_pos.values()), by_pos
    fb = W["kom"]["fallback_pool"].get(stype, 0)
    return fb, ({"mid": fb * 0.6, "finish": fb * 0.4} if fb else {})


def compute() -> dict:
    riders, rounds, climbs, favs = load_inputs()
    mult = RULES["multipliers"]
    finish_scale = RULES["points"]["stage_finish"]
    fav_pts = {int(k): v for k, v in W["favorites"]["star_finish_points"].items()}
    blend = W["favorites"]["blend_weight"]
    n_r = len(riders)

    E = [[0.0] * 22 for _ in range(n_r)]          # E[i][stage] etter multiplikator
    E_finish_raw = [[0.0] * 22 for _ in range(n_r)]  # før multiplikator, til SD-modellen
    ff = _form_factors(riders)

    for rnd in rounds:
        s = rnd["number"]
        stype = STAGE_TYPE_MAP[rnd["stageType"]]
        stars = _match_favorites(favs[s]["favorites"], riders) if s in favs and favs[s]["published"] else {}

        # 1) målgang
        cap = {i: _capability(riders[i], stype) * ff[i] for i in range(n_r)}
        cap_dist = _distribute(W["capability"]["finish_pool"], cap, W["capability"]["gamma"])
        fin = {}
        for i in range(n_r):
            fav_part = fav_pts.get(stars.get(i, 0), 0)
            cap_part = cap_dist.get(i, 0)
            fin[i] = blend * fav_part + (1 - blend) * cap_part if stars else cap_part
        # 2) spurterklassifisering
        frac = W["sprint_class"]["finish_fraction"][stype]
        # 3) passeringspunkt
        pp_pool = W["passing_point"]["pool"] if stype not in ("ITT", "TTT") else 0
        # 4) klatring
        total_kom, kom_by_pos = _climb_pool(s, stype, climbs)
        brk_score = {}
        for i, r in enumerate(riders):
            bs = sum(r["speciality"].get(k, 0) * w for k, w in W["kom"]["breakaway_speciality"].items())
            brk_score[i] = (bs * W["kom"]["breakaway_role_factor"][r["position"]] * ff[i]
                            * (1 + W["kom"]["favorite_break_boost"] * stars.get(i, 0)))
        kom = {i: 0.0 for i in range(n_r)}
        for pos, pool in kom_by_pos.items():
            b_share = W["kom"]["break_share"].get(pos, 0.5)
            for i, v in _distribute(pool * b_share, brk_score, W["kom"]["gamma"]).items():
                kom[i] += v
            # favorittgruppe-andel: fordeles etter målgangsforventning på fjell
            for i, v in _distribute(pool * (1 - b_share), fin, 2.0).items():
                kom[i] += v
        # passeringspunkt-fordeling
        pp = {}
        if pp_pool:
            if stype == "flat":
                sp_share = W["passing_point"]["sprinter_share_flat"]
                pp = _distribute(pp_pool * sp_share, {i: fin[i] for i in range(n_r) if riders[i]["position"] == "SPRINTER"}, 1.5)
                for i, v in _distribute(pp_pool * (1 - sp_share), brk_score, 2.0).items():
                    pp[i] = pp.get(i, 0) + v
            else:
                pp = _distribute(pp_pool, brk_score, 2.0)

        finish_baseline = finish_scale["other"]  # alle fullførende får dette
        for i, r in enumerate(riders):
            base = fin[i] * (1 + frac) + kom.get(i, 0) + pp.get(i, 0) + finish_baseline
            E_finish_raw[i][s] = fin[i]
            E[i][s] = base * mult[r["position"]]

    # overlevelsesdiskontering: senere etapper tynges av DNF-risiko
    surv = W["completion_risk"]["survival_per_stage"]
    for i in range(n_r):
        for s in range(1, 22):
            E[i][s] *= surv ** (s - 1)

    # Giro-Tour-dobbel: slitasjestraff, verst i uke 3
    gf = W["giro_fatigue"]
    for i, r in enumerate(riders):
        g = r.get("giro_2026_stages", 0)
        if g < 10:
            continue
        for s in range(1, 22):
            w = gf["week1"] if s <= 9 else gf["week2"] if s <= 15 else gf["week3"]
            if g < gf["full_threshold"]:
                w = 1 - (1 - w) * gf["partial_scale"]
            E[i][s] *= w
            E_finish_raw[i][s] *= w  # slår også inn i SD-lagstyrken

    # SD-forventning per lag per etappe (ikke multiplisert).
    # SD-regelen: de 5 første ULIKE lagene i mål får 160/100/60/40/20 — ett lag
    # kan maks få 160. E[SD] = sum over plasseringer av P(plass k) * premie_k,
    # estimert med Gumbel-trick Monte Carlo over lagstyrker (Plackett-Luce).
    # Lagstyrke = beste rytters målgangsforventning + 0.25 x nest beste.
    import math
    import random

    teams = sorted({r["team"] for r in riders})
    prizes = RULES["points"]["sport_director"]["stage_top_five"]
    gamma_sd = W["sport_director"]["gamma"]
    rng = random.Random(42)
    N_MC = 1500
    sd = {t: [0.0] * 22 for t in teams}
    for s in range(1, 22):
        per_team: dict[str, list[float]] = {t: [] for t in teams}
        for i, r in enumerate(riders):
            per_team[r["team"]].append(E_finish_raw[i][s])
        logw = {}
        for t, vals in per_team.items():
            vals.sort(reverse=True)
            w = (vals[0] + 0.25 * (vals[1] if len(vals) > 1 else 0)) ** gamma_sd
            logw[t] = math.log(max(w, 1e-6))
        totals = {t: 0.0 for t in teams}
        for _ in range(N_MC):
            noisy = sorted(teams, key=lambda t: logw[t] - math.log(-math.log(rng.random())), reverse=True)
            for k, t in enumerate(noisy[:len(prizes)]):
                totals[t] += prizes[k]
        for t in teams:
            sd[t][s] = totals[t] / N_MC

    return {"riders": riders, "E": E, "sd": sd,
            "stage_types": {r["number"]: STAGE_TYPE_MAP[r["stageType"]] for r in rounds},
            "used_climb_inventory": climbs is not None}


if __name__ == "__main__":
    res = compute()
    riders, E = res["riders"], res["E"]
    tot = sorted(((sum(E[i][1:]), i) for i in range(len(riders))), reverse=True)
    print(f"klatreinventar brukt: {res['used_climb_inventory']}")
    print("\ntopp 20 forventede totalpoeng (etter multiplikator):")
    for v, i in tot[:20]:
        r = riders[i]
        print(f"  {r['name']:28s} {r['position']:13s} {r['price']:>10,}  E={v:7.1f}  E/pris={v/r['price']*1e6:.2f}")
    print("\ntopp 5 SD-lag:")
    sd_tot = sorted(((sum(v[1:]), t) for t, v in res["sd"].items()), reverse=True)
    for v, t in sd_tot[:5]:
        print(f"  {t:35s} E={v:7.1f}")
