"""Parser en bytte-issue og oppdaterer team.json.

Issue-body-format (linjer, rekkefølge likegyldig, norsk):
    inn: Jasper Philipsen, Tim Merlier
    ut: Anthony Turgis, Clément Russo
    sd: Alpecin - Premier Tech          <- valgfri (bytte av sportsdirektør)

Navn matches tolerant (aksent-ufølsomt, delnavn ok hvis unikt) mot
rytterpoolen i data/players.json. Alt valideres mot data/rules_2026.json:
posisjonskrav, budsjett, maks per proffslag (inkl. SD). Ved feil endres
INGENTING og feilmeldingen skrives til result.md.

Bruk: python apply_transfer_issue.py <issue_body_fil>
Exit 0 = anvendt, 1 = avvist (result.md forklarer).
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULT = ROOT / "result.md"


def norm(s: str) -> frozenset:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return frozenset(s.lower().replace("-", " ").replace("'", " ").split())


def match_name(query: str, candidates: list[str]) -> str | None:
    q = norm(query)
    exact = [c for c in candidates if norm(c) == q]
    if len(exact) == 1:
        return exact[0]
    sub = [c for c in candidates if q <= norm(c) or norm(c) <= q]
    if len(sub) == 1:
        return sub[0]
    partial = [c for c in candidates if q & norm(c) and len(q & norm(c)) >= min(len(q), 2) - 0]
    partial = [c for c in candidates if len(q & norm(c)) >= 1 and any(len(t) > 3 for t in q & norm(c))]
    if len(partial) == 1:
        return partial[0]
    return None


def fail(msg: str):
    RESULT.write_text(f"### ❌ Byttet ble IKKE registrert\n\n{msg}\n\n"
                      "team.json er uendret. Rett opp og opprett en ny issue.\n")
    sys.exit(1)


def main():
    body = Path(sys.argv[1]).read_text()
    team = json.loads((ROOT / "team.json").read_text())
    rules = json.loads((ROOT / "data" / "rules_2026.json").read_text())
    pool = json.loads((ROOT / "data" / "players.json").read_text())
    riders_pool = [p for p in pool if p["position"] != "SPORT_DIRECTOR"]
    sd_pool = [p for p in pool if p["position"] == "SPORT_DIRECTOR"]
    rider_names = [p["name"] for p in riders_pool]
    sd_names = [p["name"] for p in sd_pool]

    inn, ut, new_sd = [], [], None
    for line in body.splitlines():
        m = re.match(r"^\s*(inn|ut|sd)\s*[:=]\s*(.+)$", line.strip(), re.I)
        if not m:
            continue
        key, val = m.group(1).lower(), m.group(2).strip()
        if key == "sd":
            new_sd = val
        else:
            names = [x.strip() for x in val.split(",") if x.strip()]
            (inn if key == "inn" else ut).extend(names)

    if not inn and not ut and not new_sd:
        fail("Fant ingen `inn:`/`ut:`/`sd:`-linjer i meldingen.\n\nFormat:\n"
             "```\ninn: Jasper Philipsen, Tim Merlier\nut: Anthony Turgis, Clément Russo\n"
             "sd: Alpecin - Premier Tech\n```")
    if len(inn) != len(ut):
        fail(f"Antall inn ({len(inn)}) og ut ({len(ut)}) må være likt.")

    problems, inn_m, ut_m = [], [], []
    for n in inn:
        hit = match_name(n, rider_names)
        (inn_m.append(hit) if hit else problems.append(f"inn: fant ikke entydig rytter «{n}»"))
    for n in ut:
        hit = match_name(n, team["riders"])
        (ut_m.append(hit) if hit else problems.append(f"ut: «{n}» er ikke (entydig) i laget ditt"))
    sd_m = team["sport_director"]
    if new_sd:
        sd_m = match_name(new_sd, sd_names)
        if not sd_m:
            problems.append(f"sd: fant ikke entydig lag «{new_sd}»")
    if problems:
        fail("\n".join(f"- {p}" for p in problems))

    new_riders = [r for r in team["riders"] if r not in ut_m] + inn_m
    if len(new_riders) != len(set(new_riders)):
        fail("Duplikat i nytt lag — er noen av inn-rytterne allerede i laget?")

    # valider mot reglene
    by_name = {p["name"]: p for p in pool}
    pos_count: dict = {}
    team_count: dict = {}
    cost = 0
    for n in new_riders:
        p = by_name[n]
        pos_count[p["position"]] = pos_count.get(p["position"], 0) + 1
        team_count[p["team"]["name"]] = team_count.get(p["team"]["name"], 0) + 1
        cost += p["prices"][-1]["priceCents"]
        if not p["isAvailable"]:
            fail(f"{n} er ikke tilgjengelig (ute av touren).")
    sd_p = by_name[sd_m]
    cost += sd_p["prices"][-1]["priceCents"]
    team_count[sd_p["team"]["name"]] = team_count.get(sd_p["team"]["name"], 0) + 1

    errs = []
    for pos, need in rules["team"]["positions"].items():
        if pos_count.get(pos, 0) != need:
            errs.append(f"posisjon {pos}: {pos_count.get(pos, 0)} (krav: {need})")
    if cost > rules["budget"]["total"]:
        errs.append(f"budsjett: {cost:,} > {rules['budget']['total']:,}")
    for t, c in team_count.items():
        if c > rules["team"]["max_per_pro_team"]:
            errs.append(f"maks 3 fra samme lag brutt: {t} ({c})")
    if errs:
        fail("Byttet ville gitt ULOVLIG lag:\n" + "\n".join(f"- {e}" for e in errs))

    n_transfers = len(inn_m) + (1 if new_sd and sd_m != team["sport_director"] else 0)
    team["riders"] = sorted(new_riders)
    team["sport_director"] = sd_m
    team["transfers_used"] = team.get("transfers_used", 0) + n_transfers
    (ROOT / "team.json").write_text(json.dumps(team, ensure_ascii=False, indent=2))

    pot_left = 25 - team["transfers_used"]
    RESULT.write_text(
        "### ✅ Byttet er registrert\n\n"
        + (f"**Inn:** {', '.join(inn_m)}\n**Ut:** {', '.join(ut_m)}\n" if inn_m else "")
        + (f"**Ny SD:** {sd_m}\n" if new_sd else "")
        + f"\nBytter brukt nå: {n_transfers} → totalt {team['transfers_used']} "
        f"(pott igjen: {pot_left}, reserve: {team['transfer_reserve']})\n"
        f"Lagkost: {cost:,} / {rules['budget']['total']:,}\n\n"
        f"**Laget:** {', '.join(team['riders'])} + SD {sd_m}\n\n"
        "Morgenbriefen og optimizeren bruker dette laget fra neste kjøring.\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
