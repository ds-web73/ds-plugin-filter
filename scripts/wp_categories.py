#!/usr/bin/env python3
"""
Classement des plugins par catégories (categories.json) et mesure de couverture.

Trois passes successives :
  1. tag exact            ex. "gallery"
  2. variantes de tag     pluriels ("thumbnails" -> "thumbnail") et morceaux de tags
                          composés ("image-gallery" -> "gallery")
  3. nom + description courte, uniquement pour les plugins encore non classés,
     en ignorant les mots trop génériques pour un texte libre

Usage :
  python3 wp_categories.py              # rapport + échantillons à vérifier
  python3 wp_categories.py --top 100    # nombre de tags non classés affichés
  python3 wp_categories.py --save       # enregistre aussi le résultat dans la table plugin_categories
"""
import argparse
import json
import random
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

DB = "catalog.db"
CATEGORIES = "categories.json"
OVERRIDES = "overrides.json"
FRESH_DAYS = 730

# Morceaux de tags composés trop ambigus ("user-friendly", "promo-code", "share-price"…)
COMPONENT_SKIP = {"user", "users", "code", "support", "store", "share", "post", "page", "pages", "custom",
                  "import", "export", "demo", "https", "ssl"}

# Termes ignorés dans le nom et la description : trop courants dans un texte libre
TEXT_SKIP = {
    "post", "posts", "page", "pages", "user", "users", "image", "images", "content", "admin",
    "search", "email", "mail", "editor", "block", "blocks", "widget", "widgets", "menu",
    "support", "contact", "link", "links", "store", "product", "products", "share", "sharing",
    "social", "media", "tag", "tags", "category", "categories", "filter", "feed", "blog",
    "comment", "comments", "profile", "roles", "members", "event", "events", "schedule",
    "language", "languages", "privacy", "tracking", "ads", "dashboard", "table", "plugins",
    "updates", "author", "archive", "news", "publishing", "location", "directions", "import",
    "export", "upload", "button", "template", "drag-and-drop", "community", "feedback",
    "tickets", "meta", "keywords", "https", "member", "duplicate", "developer", "development",
    "api", "rest-api", "css", "html", "php", "javascript", "jquery", "ajax", "shortcode",
    "shortcodes", "hide", "disable", "sales", "order", "orders", "speed", "optimize",
    "optimization", "statistics", "stats",
}


def load_mapping():
    with open(CATEGORIES, encoding="utf-8") as f:
        cats = json.load(f)
    mapping = {}
    for cat, tags in cats.items():
        for t in tags:
            mapping[t.strip().lower()] = cat
    return cats, mapping


def variants(word):
    v = {word}
    if len(word) > 3:
        if word.endswith("ies"):
            v.add(word[:-3] + "y")
        if word.endswith("es"):
            v.add(word[:-2])
        if word.endswith("s"):
            v.add(word[:-1])
        else:
            v.add(word + "s")
    return v


def lookup(word, mapping):
    for v in variants(word):
        if v in mapping:
            return mapping[v]
    return None


def text_patterns(mapping):
    pats = defaultdict(list)
    for term, cat in mapping.items():
        if term in TEXT_SKIP:
            continue
        words = term.replace("-", " ")
        if len(term) <= 3:
            # Sigles (AI, SEO, CDN…) : en majuscules, pour éviter "j'ai", "map", etc.
            rx = re.compile(r"(?<![\w-])" + re.escape(words.upper()) + r"s?(?![\w-])")
        else:
            body = re.escape(words).replace(r"\ ", r"[\s-]")
            rx = re.compile(r"(?<![\w-])" + body + r"(?:s|es)?(?![\w-])", re.I)
        pats[cat].append(rx)
    return pats


def classify(name, short_desc, tags, mapping, pats, unclassified, stats):
    """Retourne {catégorie: méthode} en ne gardant que les catégories dominantes."""
    scores = Counter()
    methods = {}
    for t in tags:
        if t in mapping:
            scores[mapping[t]] += 1
            methods.setdefault(mapping[t], "tag")
    has_exact = bool(scores)
    for t in tags:
        if t in mapping:
            continue
        found = lookup(t, mapping)
        if found:
            scores[found] += 1
            methods.setdefault(found, "variante")
            continue
        parts = t.split("-")
        hit = False
        # Découpage des tags composés seulement si aucun tag exact n'a déjà classé le plugin
        if len(parts) > 1 and not has_exact:
            for p in parts:
                if p in COMPONENT_SKIP:
                    continue
                found = lookup(p, mapping)
                if found:
                    scores[found] += 1
                    methods.setdefault(found, "variante")
                    hit = True
        if not hit:
            unclassified[t] += 1

    text = f"{name or ''} {short_desc or ''}"
    if scores:
        # Le nom et la description confirment (+1) les catégories déjà trouvées par les tags
        for cat in list(scores):
            if any(rx.search(text) for rx in pats.get(cat, ())):
                scores[cat] += 1
    else:
        for cat, rxs in pats.items():
            if any(rx.search(text) for rx in rxs):
                scores[cat] += 1
                methods.setdefault(cat, "description")
    if not scores:
        return {}

    # Catégories dominantes : au moins la moitié du score de la catégorie principale
    top = max(scores.values())
    kept = {c: methods[c] for c, sc in scores.items() if sc >= top / 2}
    stats["retirées"] += len(scores) - len(kept)
    return kept


def load_overrides():
    try:
        with open(OVERRIDES, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    cats_def, mapping = load_mapping()
    pats = text_patterns(mapping)
    limit = (datetime.now(timezone.utc) - timedelta(days=FRESH_DAYS)).strftime("%Y-%m-%d")

    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT slug, name, short_description, tags, active_installs, last_updated FROM plugins"
    ).fetchall()

    overrides = load_overrides()
    unknown = {c for cats in overrides.values() for c in cats if c not in cats_def}
    if unknown:
        print("Attention, catégories inconnues dans overrides.json :", ", ".join(sorted(unknown)))
    stats = Counter()
    unclassified = Counter()
    per_cat = Counter()
    nb_cats = Counter()
    results = []  # (slug, name, cats, best_method, installs, fresh)
    order = {"manuel": 0, "tag": 1, "variante": 2, "description": 3}

    for slug, name, sd, tags_json, installs, updated in rows:
        cats = classify(name, sd, json.loads(tags_json), mapping, pats, unclassified, stats)
        if slug in overrides:
            cats = {c: "manuel" for c in overrides[slug] if c in cats_def}
            stats["manuels"] += 1
        best = min(cats.values(), key=order.get) if cats else None
        results.append((slug, name, cats, best, installs or 0, (updated or "") >= limit))
        per_cat.update(cats.keys())
        nb_cats[min(len(cats), 4)] += 1

    n = len(results)
    n_fresh = sum(r[5] for r in results)
    inst_total = sum(r[4] for r in results)
    pct = lambda a, b: f"{100 * a / b:5.1f} %" if b else "    -"

    print(f"\n=== Couverture cumulée ({len(cats_def)} catégories, {len(mapping)} tags) ===\n")
    print(f"  {'':<16} {'tous':>8} {'actifs':>8} {'installs':>9}")
    allowed = set()
    for method, label in (("manuel", "manuels"), ("tag", "+ tags exacts"), ("variante", "+ variantes"), ("description", "+ description")):
        allowed.add(method)
        sel = [r for r in results if r[3] in allowed]
        print(f"  {label:<16} {pct(len(sel), n):>8} {pct(sum(r[5] for r in sel), n_fresh):>8} "
              f"{pct(sum(r[4] for r in sel), inst_total):>9}")

    print(f"\nCatégories minoritaires écartées : {stats['retirées']}"
          f" — corrections manuelles appliquées : {stats['manuels']}")
    print("\nNombre de catégories par plugin :")
    for k in sorted(nb_cats):
        label = f"{k}+" if k == 4 else str(k)
        print(f"  {label:<3} {nb_cats[k]:>6}  {pct(nb_cats[k], n)}")

    print("\nPlugins par catégorie :")
    for cat, c in per_cat.most_common():
        print(f"  {cat:<26} {c:>6}")

    print(f"\n{args.top} tags toujours non classés les plus fréquents :")
    for tag, c in unclassified.most_common(args.top):
        print(f"  {tag:<30} {c:>6}")

    rnd = random.Random(42)
    for method, label in (("variante", "classés par variantes"), ("description", "classés par description")):
        pool = [r for r in results if r[3] == method and r[5]]
        print(f"\nÉchantillon à vérifier — {label} ({len(pool)} plugins actifs) :")
        for slug, name, cats, *_ in rnd.sample(pool, min(12, len(pool))):
            print(f"  {slug:<40} {', '.join(sorted(cats))}")

    if args.save:
        con.executescript("""
            DROP TABLE IF EXISTS plugin_categories;
            CREATE TABLE plugin_categories (
                slug TEXT, category TEXT, method TEXT, PRIMARY KEY (slug, category));
        """)
        con.executemany(
            "INSERT INTO plugin_categories VALUES (?, ?, ?)",
            [(r[0], c, m) for r in results for c, m in r[2].items()],
        )
        con.commit()
        print("\nRésultat enregistré dans la table plugin_categories.")
    con.close()


if __name__ == "__main__":
    main()
