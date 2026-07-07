"""Match tourmanager-rytternavn mot PCS-startlisten.

TM: "Tadej Pogačar" / PCS: "POGAČAR Tadej". Normalisering: unicode-fold,
lowercase, tokenisér, sorter. Eksakt tokensett-match først, deretter
delmengde-match (håndterer mellomnavn), til slutt manuell alias-tabell.
Umatchede ryttere rapporteres — ALDRI gjettes stille.
"""
import json
import unicodedata
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# TM-navn -> PCS rider_url, for navn som ikke matcher automatisk
ALIASES: dict[str, str] = {
    "Felix Grosschartner": "rider/felix-grossschartner",  # PCS: GROßSCHARTNER
}


def norm_tokens(name: str) -> frozenset[str]:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("-", " ").replace("'", " ").replace("’", " ")
    return frozenset(t for t in s.split() if t)


def match(tm_riders: list[dict], pcs_startlist: list[dict]) -> tuple[dict, list]:
    """Returnerer ({tm_id: pcs_rider_dict}, [umatchede tm-ryttere])."""
    by_tokens: dict[frozenset, dict] = {}
    for r in pcs_startlist:
        by_tokens[norm_tokens(r["rider_name"])] = r

    mapping, unmatched = {}, []
    for p in tm_riders:
        toks = norm_tokens(p["name"])
        hit = by_tokens.get(toks)
        if hit is None and p["name"] in ALIASES:
            url = ALIASES[p["name"]]
            hit = next((r for r in pcs_startlist if r["rider_url"] == url), None)
        if hit is None:
            # delmengde: TM-navn ⊆ PCS-navn eller omvendt (mellomnavn/suffiks)
            cands = [r for t, r in by_tokens.items() if toks <= t or t <= toks]
            if len(cands) == 1:
                hit = cands[0]
        if hit is None:
            # siste utvei: etternavn + minst ett fornavn-token felles, unik
            cands = [r for t, r in by_tokens.items() if len(toks & t) >= 2]
            if len(cands) == 1:
                hit = cands[0]
        if hit is not None:
            mapping[p["id"]] = hit
        else:
            unmatched.append(p)
    return mapping, unmatched


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(DATA.parent))
    from scrapers import tourmanager as tm
    from scrapers import pcs

    riders = tm.riders()
    sl = pcs.startlist()
    mapping, unmatched = match(riders, sl)
    print(f"matchet {len(mapping)}/{len(riders)}")
    for p in unmatched:
        print("UMATCHET:", p["name"], "|", p["team"]["name"], "|", p["position"])
