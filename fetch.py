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
            # Cloudflare blokkerer datasenter-IP-er -> cloudscraper med retry.
            raw, last_err = None, None
            import cloudscraper
            for attempt in range(4):
                try:
                    scraper = cloudscraper.create_scraper(
                        browser={"browser": "chrome", "platform": "windows", "mobile": False})
                    resp = scraper.get(url, timeout=40)
                    if resp.status_code == 200 and "winPercentage" in resp.text:
                        raw = resp.text
                        break
                    last_err = f"status {resp.status_code}"
                except Exception as e:
                    last_err = str(e)
                time.sleep(3 * (attempt + 1))
            if raw is None:
                try:
                    raw = get(url).decode("utf-8", "replace")
                except Exception as e:
                    last_err = str(e)
            if raw and "winPercentage" in raw:
                u = htmllib.unescape(htmllib.unescape(raw))
                # les winPercentage-oppføringene direkte (robust mot wrapper-endring)
                preds = []
                for m in re.finditer(r'"col1":"([^"]+)"[^}]*?"winPercentage":"([0-9,\.]+)"', u):
                    preds.append({"name": m.group(1), "win_pct": float(m.group(2).replace(",", "."))})
                if not preds:
                    for m in re.finditer(r'"winPercentage":"([0-9,\.]+)"[^}]*?"searchQuery":"([^"]+)"', u):
                        preds.append({"name": m.group(2), "win_pct": float(m.group(1).replace(",", "."))})
                preds.sort(key=lambda x: -x["win_pct"])
                payload = json.dumps({"stage": n, "source": "cyclingoracle.com (WielerOrakel)",
                                      "fetched": today, "predictions_xw": preds,
                                      "raw_text": strip_tags(raw)[:6000]}, ensure_ascii=False, indent=1)
                (OUT / "oracle.json").write_text(payload)
                (HIST / f"oracle_stage{n:02d}.json").write_text(payload)
                if not preds:
                    status["errors"].append("oracle: hentet, men xW ikke parset")
            else:
                status["errors"].append(f"oracle et.{n}: ikke tilgjengelig ({last_err}) — beholder forrige oracle.json")
        except Exception as e:
            status["errors"].append(f"oracle: {e}")

        # WielerOrakel klassement-prediksjoner (klatretrøye/sammenlagt/poeng).
        # Oppdateres sjelden -> hentes daglig men feiler stille (beholder forrige).
        try:
            import cloudscraper
            sc = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False})
            classifications = {}
            for key, slug in [("kom", "voorspelling-berg-klassement"),
                              ("gc", "voorspelling-algemeen-klassement"),
                              ("points", "voorspelling-punten-klassement")]:
                try:
                    r = sc.get(f"https://www.cyclingoracle.com/nl/blog/"
                               f"tour-de-france-2026-{slug}", timeout=40)
                    if r.status_code == 200 and "winPercentage" in r.text:
                        u = htmllib.unescape(htmllib.unescape(r.text))
                        rows = [{"name": m.group(1).encode().decode("unicode_escape"),
                                 "pct": float(m.group(2).replace(",", "."))}
                                for m in re.finditer(
                                    r'"col1":"([^"]+)"[^}]*?"winPercentage":"([0-9,\.]+)"', u)]
                        if rows:
                            classifications[key] = sorted(rows, key=lambda x: -x["pct"])[:12]
                    time.sleep(2)
                except Exception:
                    pass
            if classifications:
                (OUT / "classifications.json").write_text(
                    json.dumps({"fetched": today, **classifications}, ensure_ascii=False, indent=1))
        except Exception as e:
            status["errors"].append(f"classifications: {e}")

    # GC-stilling + bruddfrihet-flagg. Ryttere nær GC-toppen (<~6 min) blir
    # ofte ikke sluppet i brudd av eget lag; ryttere lenger bak er "ufarlige"
    # og får fritt leide -> høyere bruddsannsynlighet på fjelletapper.
    try:
        import cloudscraper as _cs
        from procyclingstats import Stage as _Stage
        prev_done = (n or 2) - 1
        gc = _Stage(f"race/tour-de-france/2026/stage-{prev_done}").gc()

        def _sec(t):
            p = [int(x) for x in t.split(":")]
            while len(p) < 3:
                p = [0] + p
            return p[0] * 3600 + p[1] * 60 + p[2]

        lead = _sec(gc[0]["time"])
        rows = []
        for r in gc[:40]:
            gap = _sec(r["time"]) - lead
            rows.append({"rank": r["rank"], "name": r["rider_name"],
                         "gap_sec": gap,
                         "break_freedom": "bundet" if gap < 360 else
                                          "delvis" if gap < 600 else "fri"})
        (OUT / "gc.json").write_text(json.dumps(
            {"after_stage": prev_done, "standings": rows}, ensure_ascii=False, indent=1))
    except Exception as e:
        status["errors"].append(f"gc: {e}")

    # Nyheter/intervjuer siste 2 dager (Google News RSS) — fanger ambisjons-
    # uttalelser (hvem jager hva), DNF/skader og bruddsignaler som ingen
    # struktur-kilde har. Briefen (har LLM) leser overskrifter+snippet og
    # trekker ut det relevante for laget/kandidatene.
    try:
        import urllib.parse
        from xml.etree import ElementTree as ET
        q = ("Tour de France 2026 (breakaway OR climber OR sprint OR jersey OR "
             "interview OR withdrawal OR abandons OR Martinez OR Paret-Peintre OR "
             "Healy OR Quinn OR Vacek OR Philipsen OR Merlier)")
        rss = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q)
               + "+when:2d&hl=en-US&gl=US&ceid=US:en")
        req = urllib.request.Request(rss, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            root = ET.fromstring(r.read())
        news = []
        for it in root.findall(".//item")[:40]:
            title = (it.findtext("title") or "").strip()
            desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
            news.append({"title": title, "date": (it.findtext("pubDate") or "")[:16],
                         "snippet": desc.strip()[:200]})
        (OUT / "news.json").write_text(json.dumps(
            {"fetched": today, "items": news}, ensure_ascii=False, indent=1))
    except Exception as e:
        status["errors"].append(f"news: {e}")

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
