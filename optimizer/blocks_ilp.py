"""3-blokk-ILP: startlag + planlagte bytter ved regimeskifter.

Blokker: A = et. 1-6 (TTT + Pyreneene), B = et. 7-13 (flat midtdel),
C = et. 14-21 (Vogesene + Alpene). Lag velges per blokk; endringer mellom
blokker koster bytter fra potten. SD kan byttes mellom blokker (koster 1 bytte).
ANTAGELSE: spillet tillater SD-bytte underveis — verifiseres av bruker.

Alle constraints fra rules_2026.json gjelder i HVER blokk (budsjett,
posisjoner, maks per proffslag inkl. SD).
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
assert RULES["verified"]

BLOCKS = {"A": (1, 6), "B": (7, 13), "C": (14, 21)}


def solve_blocks(max_transfers_per_switch: int = 4, total_planned: int = 8,
                 exclude: set[str] = frozenset(), locked: set[str] = frozenset(),
                 exclude_block: dict[str, set[str]] | None = None,
                 locked_block: dict[str, set[str]] | None = None,
                 initial_team: set[str] | None = None,
                 initial_sd: str | None = None,
                 transfer_cost: float = 0.0) -> dict:
    """exclude/locked gjelder alle blokker (locked = rytter holdes hele touren).
    exclude_block/locked_block: {'A': {navn, ...}} — gjelder kun gitt blokk.
    initial_team/initial_sd: dagens faktiske lag — bytter INN i første blokk
    relativt til dette koster fra potten (brukes i daglig reoptimalisering)."""
    exclude_block = exclude_block or {}
    locked_block = locked_block or {}
    res = compute()
    riders, E, sd = res["riders"], res["E"], res["sd"]
    sds = tm.sport_directors()
    budget = RULES["budget"]["total"]
    positions = RULES["team"]["positions"]
    max_per_team = RULES["team"]["max_per_pro_team"]
    n = len(riders)

    val = {b: [sum(E[i][lo:hi + 1]) for i in range(n)] for b, (lo, hi) in BLOCKS.items()}
    sd_val = {p["name"]: {b: sum(sd.get(p["name"], [0] * 22)[lo:hi + 1])
                          for b, (lo, hi) in BLOCKS.items()} for p in sds}
    sd_price = {p["name"]: tm.price(p) for p in sds}

    blocks = list(BLOCKS)
    prob = pulp.LpProblem("blokker", pulp.LpMaximize)
    x = {(b, i): pulp.LpVariable(f"x_{b}_{i}", cat="Binary") for b in blocks for i in range(n)}
    y = {(b, nm): pulp.LpVariable(f"y_{b}_{j}", cat="Binary") for b in blocks for j, nm in enumerate(sd_val)}
    later = blocks[1:] if initial_team is None else blocks  # med initial_team koster også første blokk
    # buy = kjøp inn ved inngangen til blokk b (rytter eller SD)
    buy = {(b, i): pulp.LpVariable(f"buy_{b}_{i}", cat="Binary") for b in later for i in range(n)}
    sd_buy = {(b, j): pulp.LpVariable(f"sdbuy_{b}_{j}", cat="Binary") for b in later for j in range(len(sd_val))}

    # transfer_cost = "skatt" per bytte (opsjonverdien av å beholde et bytte
    # i potten til DNF/formjakt) — hindrer churn for marginale gevinster
    prob += (pulp.lpSum(val[b][i] * x[b, i] for b in blocks for i in range(n))
             + pulp.lpSum(sd_val[nm][b] * y[b, nm] for b, nm in y)
             - transfer_cost * (pulp.lpSum(buy.values()) + pulp.lpSum(sd_buy.values())))

    for b in blocks:
        prob += (pulp.lpSum(riders[i]["price"] * x[b, i] for i in range(n))
                 + pulp.lpSum(sd_price[nm] * y[b, nm] for nm in sd_val)) <= budget, f"budsjett_{b}"
        prob += pulp.lpSum(y[b, nm] for nm in sd_val) == 1, f"en_sd_{b}"
        for pos, cnt in positions.items():
            prob += pulp.lpSum(x[b, i] for i in range(n) if riders[i]["position"] == pos) == cnt, f"pos_{b}_{pos}"
        for t in {r["team"] for r in riders}:
            sd_term = [y[b, t]] if t in sd_val else []
            prob += (pulp.lpSum(x[b, i] for i in range(n) if riders[i]["team"] == t)
                     + pulp.lpSum(sd_term)) <= max_per_team, f"kvote_{b}_{t[:18]}"
        for i in range(n):
            if (not riders[i]["is_available"] or riders[i]["name"] in exclude
                    or riders[i]["name"] in exclude_block.get(b, ())):
                prob += x[b, i] == 0
            elif riders[i]["name"] in locked or riders[i]["name"] in locked_block.get(b, ()):
                prob += x[b, i] == 1

    prev = dict(zip(later, blocks)) if initial_team is None else dict(zip(blocks[1:], blocks))
    sd_names = list(sd_val)
    for b in later:
        for i in range(n):
            if b in prev:
                prob += buy[b, i] >= x[b, i] - x[prev[b], i]
            else:  # første blokk mot dagens faktiske lag
                base = 1 if riders[i]["name"] in initial_team else 0
                prob += buy[b, i] >= x[b, i] - base
        for j, nm in enumerate(sd_names):
            if b in prev:
                prob += sd_buy[b, j] >= y[b, nm] - y[prev[b], nm]
            else:
                base = 1 if nm == initial_sd else 0
                prob += sd_buy[b, j] >= y[b, nm] - base
        prob += (pulp.lpSum(buy[b, i] for i in range(n))
                 + pulp.lpSum(sd_buy[b, j] for j in range(len(sd_val)))) <= max_transfers_per_switch, f"maxbytte_{b}"
    prob += pulp.lpSum(buy.values()) + pulp.lpSum(sd_buy.values()) <= total_planned, "totalbytte"

    status = prob.solve(pulp.PULP_CBC_CMD(msg=0))
    assert pulp.LpStatus[status] == "Optimal", pulp.LpStatus[status]

    out = {"blocks": {}}
    for b in blocks:
        chosen = [i for i in range(n) if x[b, i].value() > 0.5]
        sd_b = next(nm for bb, nm in y if bb == b and y[bb, nm].value() > 0.5)
        cost = sum(riders[i]["price"] for i in chosen) + sd_price[sd_b]
        assert cost <= budget
        out["blocks"][b] = {
            "riders": sorted((riders[i] | {"E_block": round(val[b][i], 1)} for i in chosen),
                             key=lambda r: (r["position"], -r["E_block"])),
            "cost": cost,
            "sd": sd_b,
            "sd_E": round(sd_val[sd_b][b], 1),
        }
    out["transfers"] = {b: sorted(riders[i]["name"] for i in range(n) if buy[b, i].value() > 0.5)
                        + [f"SD:{nm}" for j, nm in enumerate(sd_val) if sd_buy[b, j].value() > 0.5]
                        for b in later}
    out["total_E"] = round(pulp.value(prob.objective), 1)
    return out


if __name__ == "__main__":
    sol = solve_blocks()
    print(f"Total E (3 blokker): {sol['total_E']}\n")
    for b, (lo, hi) in BLOCKS.items():
        blk = sol["blocks"][b]
        print(f"BLOKK {b} (etappe {lo}-{hi}), kost {blk['cost']:,}, SD={blk['sd']} (E={blk['sd_E']}):")
        for r in blk["riders"]:
            print(f"  {r['position']:13s} {r['name']:26s} {r['price']:>10,}  E={r['E_block']:>7}")
        print()
    for b, moves in sol["transfers"].items():
        print(f"Planlagte bytter INN ved blokk {b}:", moves)