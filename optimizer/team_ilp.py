"""ILP-optimizer for startlaget (PuLP/CBC).

Beslutningsvariabler: x_i (rytter valgt), y_t (SD-lag valgt).
Objektiv: sum forventede poeng over planhorisonten (default alle 21 etapper).
Constraints fra data/rules_2026.json — ALDRI hardkodet her:
  budsjett, posisjonskrav, 1 SD, maks per proffslag, kun tilgjengelige ryttere.

SD-lagkortet TELLER mot maks-3-per-proffslag (bekreftet av bruker 2026-07-03:
3 ryttere + SD fra samme lag = 4 = ulovlig).
"""
import json
import sys
from pathlib import Path

import pulp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from model.expected_points import compute
from scrapers import tourmanager as tm

RULES = json.loads((ROOT / "data" / "rules_2026.json").read_text())
assert RULES["verified"], "rules_2026.json ikke verifisert"


def solve(stage_from: int = 1, stage_to: int = 21, exclude: set[str] = frozenset(),
          locked: set[str] = frozenset()) -> dict:
    res = compute()
    riders, E, sd = res["riders"], res["E"], res["sd"]
    sds = tm.sport_directors()
    budget = RULES["budget"]["total"]
    positions = RULES["team"]["positions"]
    max_per_team = RULES["team"]["max_per_pro_team"]

    val = [sum(E[i][stage_from:stage_to + 1]) for i in range(len(riders))]
    sd_val = {p["name"]: sum(sd.get(p["name"], [0] * 22)[stage_from:stage_to + 1]) for p in sds}
    sd_price = {p["name"]: tm.price(p) for p in sds}

    prob = pulp.LpProblem("startlag", pulp.LpMaximize)
    x = {i: pulp.LpVariable(f"x_{i}", cat="Binary") for i in range(len(riders))}
    y = {n: pulp.LpVariable(f"y_{j}", cat="Binary") for j, n in enumerate(sd_val)}

    prob += (pulp.lpSum(val[i] * x[i] for i in x)
             + pulp.lpSum(sd_val[n] * y[n] for n in y))

    prob += (pulp.lpSum(riders[i]["price"] * x[i] for i in x)
             + pulp.lpSum(sd_price[n] * y[n] for n in y)) <= budget, "budsjett"

    for pos, count in positions.items():
        prob += pulp.lpSum(x[i] for i in x if riders[i]["position"] == pos) == count, f"pos_{pos}"

    prob += pulp.lpSum(y.values()) == RULES["team"]["sport_director_slots"], "sd_slots"

    teams = {r["team"] for r in riders}
    for t in teams:
        sd_term = [y[t]] if t in y else []  # SD-kortet teller mot lagkvoten
        prob += (pulp.lpSum(x[i] for i in x if riders[i]["team"] == t)
                 + pulp.lpSum(sd_term)) <= max_per_team, f"lag_{t[:20]}"

    for i in x:
        if not riders[i]["is_available"] or riders[i]["name"] in exclude:
            prob += x[i] == 0
        if riders[i]["name"] in locked:
            prob += x[i] == 1

    status = prob.solve(pulp.PULP_CBC_CMD(msg=0))
    assert pulp.LpStatus[status] == "Optimal", f"ILP-status: {pulp.LpStatus[status]}"

    chosen = [riders[i] | {"expected": round(val[i], 1)} for i in x if x[i].value() > 0.5]
    sd_chosen = [n for n in y if y[n].value() > 0.5]
    cost = sum(r["price"] for r in chosen) + sum(sd_price[n] for n in sd_chosen)
    assert cost <= budget, f"BUDSJETTBRUDD: {cost} > {budget}"  # guardrail

    return {
        "riders": sorted(chosen, key=lambda r: (r["position"], -r["expected"])),
        "sport_director": sd_chosen[0],
        "sd_expected": round(sd_val[sd_chosen[0]], 1),
        "sd_price": sd_price[sd_chosen[0]],
        "total_cost": cost,
        "budget_left": budget - cost,
        "total_expected": round(sum(r["expected"] for r in chosen) + sd_val[sd_chosen[0]], 1),
    }


if __name__ == "__main__":
    sol = solve()
    print(f"Forventet totalpoeng: {sol['total_expected']}")
    print(f"Kostnad: {sol['total_cost']:,} / {RULES['budget']['total']:,}  (rest: {sol['budget_left']:,})\n")
    for r in sol["riders"]:
        print(f"  {r['position']:13s} {r['name']:28s} {r['team_code']:4s} {r['price']:>10,}  E={r['expected']:>7}")
    print(f"\n  SD: {sol['sport_director']}  {sol['sd_price']:,}  E={sol['sd_expected']}")
