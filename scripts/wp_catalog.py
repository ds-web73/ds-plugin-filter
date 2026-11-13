#!/usr/bin/env python3
"""
Aspiration du catalogue de plugins WordPress.org dans catalog.db.

Usage :
  python3 scripts/wp_catalog.py fetch [--mode full|light] [--reset]
  python3 scripts/wp_catalog.py report

Modes :
  full   avec les descriptions complètes : calcule l'indice d'achats intégrés (lourd)
  light  sans les descriptions : met à jour dates, notes, installations, tags,
         et conserve l'indice d'achats intégrés déjà calculé (léger)

--reset démarre une nouvelle passe complète au lieu de reprendre la précédente.
À la fin d'une passe complète, les plugins absents du catalogue (fermés) sont supprimés.
"""
import argparse
import html
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

API = "https://api.wordpress.org/plugins/info/1.2/"
DB = "catalog.db"
PER_PAGE = 250
PAUSE = 1.0
CONTACT = os.environ.get("WPF_CONTACT", "a-remplacer@ds-web.fr")
UA = f"DS-Web-Plugin-Filter/0.2 (contact: {CONTACT})"

IAP_PATTERNS = {
    "pro_premium": r"\b(pro|premium) (version|plan|edition|add-?ons?|features?)\b",
    "upgrade": r"\bupgrade to (pro|premium)\b|\bgo pro\b",
    "premium": r"\bpremium\b",
    "pricing": r"\bpricing\b",
    "license_key": r"\blicen[cs]e key\b",
    "freemius": r"freemius",
    "paid": r"\bpaid (version|plans?|add-?ons?|features?)\b",
}
IAP_REGEX = {k: re.compile(v, re.I) for k, v in IAP_PATTERNS.items()}

COLUMNS = [
    "slug", "name", "version", "author", "requires", "tested", "requires_php",
    "rating", "num_ratings", "active_installs", "support_threads", "support_threads_resolved",
    "added", "last_updated", "business_model", "tags", "short_description",
    "iap_score", "iap_signals", "seen_run",
]
IAP_COLUMNS = {"iap_score", "iap_signals"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS plugins (
    slug TEXT PRIMARY KEY, name TEXT, version TEXT, author TEXT,
    requires TEXT, tested TEXT, requires_php TEXT,
    rating INTEGER, num_ratings INTEGER, active_installs INTEGER,
    support_threads INTEGER, support_threads_resolved INTEGER,
    added TEXT, last_updated TEXT, business_model TEXT,
    tags TEXT, short_description TEXT, iap_score INTEGER, iap_signals TEXT,
    seen_run INTEGER
);
CREATE TABLE IF NOT EXISTS pages_done (page INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


def open_db():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(plugins)")}
    if "seen_run" not in cols:  # base créée par la première version du script
        con.execute("ALTER TABLE plugins ADD COLUMN seen_run INTEGER")
    return con


def get_state(con, key, default=None):
    row = con.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_state(con, key, value):
    con.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, str(value)))


def api_page(page, with_description):
    params = {
        "action": "query_plugins",
        "request[page]": page,
        "request[per_page]": PER_PAGE,
        "request[fields][description]": 1 if with_description else 0,
        "request[fields][business_model]": 1,
        "request[fields][icons]": 0,
        "request[fields][download_link]": 0,
        "request[fields][versions]": 0,
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception as e:
            wait = 10 * (attempt + 1)
            print(f"  erreur page {page} ({e}) — nouvel essai dans {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"Échec définitif sur la page {page}")


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_updated(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %I:%M%p GMT").strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return value


def to_row(p, run_id, with_description):
    tags = p.get("tags") or {}
    tag_list = sorted(tags.keys()) if isinstance(tags, dict) else []
    bm = p.get("business_model") or None
    if with_description:
        description = clean(p.get("description") or (p.get("sections") or {}).get("description", ""))
        signals = [k for k, rx in IAP_REGEX.items() if rx.search(description)]
        score = len(signals) + (2 if bm == "commercial" else 0)
        iap_score, iap_signals = score, json.dumps(signals)
    else:
        iap_score, iap_signals = None, None  # inconnu pour un nouveau plugin, conservé sinon
    return (
        p.get("slug"), clean(p.get("name")), p.get("version"), clean(p.get("author")),
        str(p.get("requires") or ""), str(p.get("tested") or ""), str(p.get("requires_php") or ""),
        p.get("rating"), p.get("num_ratings"), p.get("active_installs"),
        p.get("support_threads"), p.get("support_threads_resolved"),
        p.get("added"), parse_updated(p.get("last_updated")), bm,
        json.dumps(tag_list), clean(p.get("short_description")),
        iap_score, iap_signals, run_id,
    )


def store(con, plugins, run_id, with_description):
    updatable = [c for c in COLUMNS if c != "slug" and (with_description or c not in IAP_COLUMNS)]
    sql = (
        f"INSERT INTO plugins ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))}) "
        f"ON CONFLICT(slug) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in updatable)}"
    )
    con.executemany(sql, [to_row(p, run_id, with_description)
                          for p in plugins if isinstance(p, dict) and p.get("slug")])


def fetch(mode, reset):
    with_description = mode == "full"
    con = open_db()
    run_id = get_state(con, "run_id")
    if reset or run_id is None or get_state(con, "run_mode") != mode:
        run_id = int(time.time())
        con.execute("DELETE FROM pages_done")
        set_state(con, "run_id", run_id)
        set_state(con, "run_mode", mode)
        con.commit()
        print(f"Nouvelle passe ({mode})", flush=True)
    else:
        run_id = int(run_id)
        print(f"Reprise de la passe en cours ({mode})", flush=True)

    done = {r[0] for r in con.execute("SELECT page FROM pages_done")}
    first = api_page(1, with_description)
    info = first.get("info", {})
    pages, total = int(info.get("pages", 1)), int(info.get("results", 0))
    print(f"Catalogue : {total} plugins, {pages} pages de {PER_PAGE}", flush=True)

    for page in range(1, pages + 1):
        if page in done:
            continue
        data = first if page == 1 else api_page(page, with_description)
        store(con, data.get("plugins", []), run_id, with_description)
        con.execute("INSERT OR IGNORE INTO pages_done VALUES (?)", (page,))
        con.commit()
        if page % 20 == 0 or page == pages:
            print(f"  page {page}/{pages}", flush=True)
        time.sleep(PAUSE)

    seen = con.execute("SELECT COUNT(*) FROM plugins WHERE seen_run = ?", (run_id,)).fetchone()[0]
    # Sécurité : on ne supprime les plugins fermés que si la passe a bien vu presque tout le catalogue
    if total and seen >= 0.95 * total:
        removed = con.execute("DELETE FROM plugins WHERE seen_run IS NOT ?", (run_id,)).rowcount
        print(f"Plugins retirés du catalogue (fermés) : {removed}")
    else:
        print(f"Passe incomplète ({seen}/{total}) : aucune suppression")
    set_state(con, "last_complete_run", run_id)
    set_state(con, "last_complete_mode", mode)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM plugins").fetchone()[0]
    print(f"Terminé : {n} plugins en base")
    con.close()


def report():
    con = open_db()
    rows = con.execute(
        "SELECT slug, business_model, tags, iap_score, iap_signals, last_updated FROM plugins"
    ).fetchall()
    n = len(rows)
    if not n:
        print("Base vide : lance d'abord « python3 scripts/wp_catalog.py fetch »")
        return
    pct = lambda x: f"{100 * x / n:.1f} %"
    print(f"\n=== {n} plugins ===\n")
    print("Modèle économique (business_model) :")
    for bm, c in Counter(r[1] or "(non renseigné)" for r in rows).most_common():
        print(f"  {bm:<18} {c:>6}  {pct(c)}")
    print("\nIndice d'achats intégrés :")
    buckets = Counter("inconnu" if r[3] is None else str(min(r[3], 5)) for r in rows)
    for k in sorted(buckets, key=lambda x: (x == "inconnu", x)):
        print(f"  {k:<8} {buckets[k]:>6}  {pct(buckets[k])}")
    now = datetime.now(timezone.utc)
    print("\nFraîcheur :")
    for label, days in (("< 6 mois", 182), ("< 1 an", 365), ("< 2 ans", 730)):
        limit = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        c = sum(1 for r in rows if (r[5] or "") >= limit)
        print(f"  mis à jour {label:<8} {c:>6}  {pct(c)}")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["fetch", "report"])
    ap.add_argument("--mode", choices=["full", "light"], default="full")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    if args.command == "fetch":
        fetch(args.mode, args.reset)
    else:
        report()
