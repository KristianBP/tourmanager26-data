"""Henter TourManager-API-data + cyclingstage-favoritter + WielerOrakel-
prediksjon til data/*.json, med daterte snapshot i data/history/ slik at
den daglige briefen kan regne gårsdagens poeng (diff) og etterprøve
prediksjonene. Kjøres av GitHub Actions hver morgen. Kun stdlib.
"""
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://vm-fantasyapi-production.up.railway.app/tournaments/tourmanager26"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
    "Accept": "application/json, text/html",
    "Origin": "https://www.tourmanager.no",
    "Referer": "https://www.tourmanager.no/",
}
OUT = Path(__file__).parent / "data"
HIST = OUT / "history"


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def strip_tags(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def main():
    OUT.mkdir(exist_ok=True)
    HIST.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    status = {"fetched_at_utc": datetime.now(timezone.utc).isoformat(), "errors": []}

    active = None
    for name, ep in [("active_round", "/active-round"), ("players", "/players"),
                     ("player_points", "/player-points")]:
        try:
            raw = get(BASE + ep)
            (OUT / f"{name}.json").write_bytes(raw)
            if name == "active_round":
                active = json.loads(raw)
            if name == "player_points":
                # datert snapshot -> briefen kan diffe mot gårsdagen
                (HIST / f"points_{today}.json").write_bytes(raw)
        except Exception as e:
            status["errors"].append(f"{name}: {e}")

    n = None
    try:
        n = active["round"]["number"]
        status["active_stage"] = n
    except Exception as e:
        status["errors"].append(f"stage number: {e}")

    # cyclingstage-favoritter for aktiv etappe (+ datert kopi for etterprøving)
    if n:
        try:
            html = get(f"https://www.cyclingstage.com/tour-de-france-2026-favourites/"
                       f"stage-{n}-contenders-tdf-2026/").decode("utf-8", "replace")
            favs = []
            for line in strip_tags(html).splitlines():
                m = re.match(r"^\s*(\*{1,4})\s+(.+)$", line.strip())
                if m:
                    for nm in m.group(2).split(","):
                        favs.append({"name": nm.strip(), "stars": len(m.group(1))})
            payload = json.dumps({"stage": n, "favorites": favs}, ensure_ascii=False, indent=1)
            (OUT / "favourites.json").write_text(payload)
            (HIST / f"favourites_stage{n:02d}.json").write_text(payload)
        except Exception as e:
            status["errors"].append(f"favourites: {e}")

        # WielerOrakel-prediksjon for aktiv etappe (+ datert kopi).
        # xW-tabellen ligger som HTML-entity-escaped JSON i et data-attributt.
        try:
            import html as htmllib
            url = (f"https://www.cyclingoracle.com/nl/blog/"
                   f"tour-de-france-2026-voorspelling-etappe-{n}")
            try:
                raw = get(url).decode("utf-8", "replace")
            except Exception:
                # Cloudflare blokkerer ofte datasenter-IP-er -> cloudscraper
                import cloudscraper
                raw = cloudscraper.create_scraper().get(url, timeout=30).text
            unescaped = htmllib.unescape(htmllib.unescape(raw))
            preds = []
            m = re.search(r'"predictions":\[(.*?)\]', unescaped, re.S)
            if m:
                for obj in re.finditer(r'\{[^{}]*\}', m.group(1)):
                    try:
                        d = json.loads(obj.group(0).replace("\\/", "/"))
                        preds.append({"name": d.get("col1") or d.get("searchQuery"),
                                      "win_pct": float(str(d.get("winPercentage", "0")).replace(",", "."))})
                    except (ValueError, KeyError):
                        pass
            text = strip_tags(raw)
            payload = json.dumps({"stage": n, "source": "cyclingoracle.com (WielerOrakel)",
                                  "predictions_xw": preds,
                                  "raw_text": text[:6000]}, ensure_ascii=False, indent=1)
            (OUT / "oracle.json").write_text(payload)
            (HIST / f"oracle_stage{n:02d}.json").write_text(payload)
            if not preds:
                status["errors"].append("oracle: xW-tabell ikke funnet (kun prosa lagret)")
        except Exception as e:
            status["errors"].append(f"oracle: {e}")

    # per-rytter-breakdowns med klartekst-noter ("Spurt: X 1.: 25p", "Mest
    # offensive rytter (x4): 100p") for lagets ryttere + spillets topp 15 —
    # gir briefen ekte narrativ (brudd, mellomsprinter, bonuser).
    try:
        import time
        team = json.loads((Path(__file__).parent / "team.json").read_text())
        pool = json.loads((OUT / "players.json").read_text())
        pts = json.loads((OUT / "player_points.json").read_text())
        top15 = sorted(pool, key=lambda p: -pts.get(p["id"], 0))[:15]
        want = {p["id"]: p["name"] for p in pool
                if p["name"] in set(team["riders"]) | {team["sport_director"]}}
        want.update({p["id"]: p["name"] for p in top15})
        bds = {}
        for pid, nm in want.items():
            try:
                det = json.loads(get(f"{BASE}/players/{pid}"))
                done = [s for s in det.get("stages", [])
                        if (s.get("breakdown") or {}).get("notes")]
                if done:
                    last = max(done, key=lambda s: s["number"])
                    bds[nm] = {"stage": last["number"], "points": last["points"],
                               "notes": (last.get("breakdown") or {}).get("notes", [])}
            except Exception:
                pass
            time.sleep(0.35)
        (OUT / "breakdowns.json").write_text(json.dumps(bds, ensure_ascii=False, indent=1))
    except Exception as e:
        status["errors"].append(f"breakdowns: {e}")

    # indeks over history-filer (raw.githubusercontent har ingen mappelisting)
    (HIST / "index.json").write_text(json.dumps(
        sorted(f.name for f in HIST.glob("*.json") if f.name != "index.json"), indent=1))
    (OUT / "status.json").write_text(json.dumps(status, indent=1))
    print(json.dumps(status, indent=1))


if __name__ == "__main__":
    main()
