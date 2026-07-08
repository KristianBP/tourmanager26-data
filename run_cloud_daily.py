"""Daglig cloud-reoptimalisering (kjøres av GitHub Actions, full nettverkstilgang).

1. Henter ferske tourmanager-data + favoritter/orakel for aktiv etappe.
2. Bygger rytterdatabasen (PCS fra cachede profiler i repoet).
3. Kjører blokk-ILP fra det FAKTISKE laget i team.json.
4. Skriver data/recommendation.json som morgenbriefen leser.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

BOUNDARIES = [5, 7, 9, 14, 16, 22]  # regimeskifter; 22 = slutt


def make_blocks(active: int) -> dict:
    edges = [active] + [b for b in BOUNDARIES if b > active]
    blocks, letter = {}, ord("A")
    for lo, hi in zip(edges, edges[1:]):
        blocks[chr(letter)] = (lo, hi - 1)
        letter += 1
    return blocks


def main():
    from scrapers import tourmanager as tm
    from scrapers.cyclingstage import fetch_cyclingstage_favorites, fetch_cyclingoracle_prediction

    rec = {"generated_utc": datetime.now(timezone.utc).isoformat(), "errors": []}

    ar = tm.active_round()
    active = ar["round"]["number"]
    rec.update(active_stage=active, stage_type=ar["round"]["stageType"],
               route=f"{ar['round']['startCity']} -> {ar['round']['finishCity']}",
               deadline_utc=ar["effectiveDeadline"], is_locked=ar["isLocked"])

    for n in range(active, min(active + 3, 22)):  # dagens + neste par etapper
        try:
            fetch_cyclingstage_favorites(n)
        except Exception as e:
            rec["errors"].append(f"favoritter et.{n}: {e}")
    try:
        oracle = fetch_cyclingoracle_prediction(active)
        rec["oracle_xw_top"] = (oracle.get("predictions_xw") or [])[:8]
    except Exception as e:
        rec["errors"].append(f"orakel: {e}")

    import subprocess
    subprocess.run([sys.executable, str(ROOT / "model" / "build_riders.py")],
                   check=True, capture_output=True)

    team = json.loads((ROOT / "team.json").read_text())
    cur, cur_sd = set(team["riders"]), team["sport_director"]
    pool = tm.riders()
    unavailable = {p["name"] for p in pool if not p["isAvailable"]}
    dnf_own = sorted(cur & unavailable)
    rec["dnf_in_team"] = dnf_own

    pot_left = 25 - team["transfers_used"]
    usable = max(0, pot_left - team["transfer_reserve"])
    rec["pot"] = {"left": pot_left, "reserve": team["transfer_reserve"], "usable": usable}

    import optimizer.blocks_ilp as bl
    bl.BLOCKS = make_blocks(active)
    rec["blocks"] = {k: list(v) for k, v in bl.BLOCKS.items()}

    # Guardrail mot modell-myopi: egne ryttere i UTVETYDIG storform (>=500
    # faktiske poeng) selges ikke i dagens bølge. 300-500 noteres kun som
    # formsignal (brukerønske 8/7: "ikke fredet fredet").
    pts = tm.player_points()
    by_name = {p["name"]: pts.get(p["id"], 0) for p in pool}
    in_form = {n for n in cur if by_name.get(n, 0) >= 500 and n not in unavailable}
    rec["locked_in_form_today"] = sorted(in_form)
    rec["noted_form"] = sorted(n for n in cur if 300 <= by_name.get(n, 0) < 500)

    first_letter = list(bl.BLOCKS)[0]
    common = dict(exclude=unavailable, initial_team=cur - unavailable, initial_sd=cur_sd,
                  locked_block={first_letter: in_form})
    plan = bl.solve_blocks(max_transfers_per_switch=6,
                           total_planned=min(usable, 14), transfer_cost=35, **common)
    hold = None
    if not dnf_own:
        try:
            hold = bl.solve_blocks(max_transfers_per_switch=0, total_planned=0, **common)
        except Exception:
            pass

    first = list(bl.BLOCKS)[0]
    a_team = {r["name"] for r in plan["blocks"][first]["riders"]}
    rec["today"] = {
        "transfers_in": sorted(a_team - cur),
        "transfers_out": sorted(cur - a_team),
        "sd": plan["blocks"][first]["sd"],
        "sd_change": plan["blocks"][first]["sd"] != cur_sd,
        "team_after": sorted(a_team),
        "cost": plan["blocks"][first]["cost"],
    }
    rec["plan_E"] = plan["total_E"]
    rec["hold_E"] = hold["total_E"] if hold else None
    rec["delta_vs_hold"] = round(plan["total_E"] - hold["total_E"], 1) if hold else None
    rec["future_waves"] = {b: plan["transfers"].get(b, []) for b in list(bl.BLOCKS)[1:]}
    n_today = len(rec["today"]["transfers_in"]) + (1 if rec["today"]["sd_change"] else 0)
    rec["transfers_needed_today"] = n_today

    (ROOT / "data" / "recommendation.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1))
    print(json.dumps(rec, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
