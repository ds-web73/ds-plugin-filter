#!/usr/bin/env python3
"""
Génère les fichiers JSON statiques publiés sur GitHub Pages, à partir de catalog.db
(tables plugins et plugin_categories, créée par « wp_categories.py --save »).

Usage :
  python3 scripts/wp_export.py --out site

Fichiers produits :
  index.json           métadonnées : date, catégories, nombre de plugins, format des champs
  cat/<id>.json        plugins d'une catégorie (dont « autres » pour les non classés)
  lookup/<x>.json      slug -> catégories / achats intégrés / modèle, par première lettre du slug,
                       pour enrichir les résultats de recherche de l'API WordPress.org
"""
import argparse
import json
import os
import shutil
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone

DB = "catalog.db"
CATEGORIES = "categories.json"
FORMAT_VERSION = 1

# Ordre des champs de chaque plugin dans cat/<id>.json (tableaux compacts plutôt qu'objets)
FIELDS = ["slug", "name", "short_description", "added", "updated", "requires", "tested",
          "requires_php", "rating", "num_ratings", "installs", "business_model", "iap", "cats"]
LOOKUP_FIELDS = ["cats", "iap", "business_model"]


def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    return "-".join(part for part in out.split("-") if part)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    return os.path.getsize(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    args = ap.parse_args()

    with open(CATEGORIES, encoding="utf-8") as f:
        cat_names = list(json.load(f).keys())
    cat_names.append("Autres")
    cat_ids = [slugify(c) for c in cat_names]
    cat_index = {name: i for i, name in enumerate(cat_names)}
    other = cat_index["Autres"]

    con = sqlite3.connect(DB)
    plugin_cats = defaultdict(list)
    for slug, cat in con.execute("SELECT slug, category FROM plugin_categories"):
        if cat in cat_index:
            plugin_cats[slug].append(cat_index[cat])

    rows = con.execute("""
        SELECT slug, name, short_description, added, last_updated, requires, tested, requires_php,
               rating, num_ratings, active_installs, business_model, iap_score
        FROM plugins ORDER BY active_installs DESC, num_ratings DESC
    """).fetchall()
    con.close()

    if os.path.isdir(args.out):
        shutil.rmtree(args.out)
    os.makedirs(args.out)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    by_cat = defaultdict(list)
    lookup = defaultdict(dict)
    for (slug, name, sd, added, updated, req, tested, php,
         rating, nr, installs, bm, iap) in rows:
        cats = sorted(plugin_cats.get(slug) or [other])
        iap = -1 if iap is None else iap
        rec = [slug, name, sd, added or "", (updated or "")[:10], req or "", tested or "",
               php or "", rating or 0, nr or 0, installs or 0, bm or "", iap, cats]
        for c in cats:
            by_cat[c].append(rec)
        key = slug[0] if slug[0].isalnum() else "_"
        lookup[key][slug] = [cats, iap, bm or ""]

    sizes = {}
    categories = []
    for i, (name, cid) in enumerate(zip(cat_names, cat_ids)):
        plugins = by_cat.get(i, [])
        path = f"cat/{cid}.json"
        sizes[path] = write_json(os.path.join(args.out, path), {
            "generated": generated, "fields": FIELDS, "plugins": plugins})
        categories.append({"id": cid, "name": name, "count": len(plugins), "file": path})

    for key, data in lookup.items():
        path = f"lookup/{key}.json"
        sizes[path] = write_json(os.path.join(args.out, path), data)

    write_json(os.path.join(args.out, "index.json"), {
        "format": FORMAT_VERSION,
        "generated": generated,
        "total": len(rows),
        "fields": FIELDS,
        "lookup_fields": LOOKUP_FIELDS,
        "lookup_keys": sorted(lookup.keys()),
        "categories": categories,
    })
    open(os.path.join(args.out, ".nojekyll"), "w").close()
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write("<!doctype html><meta charset=utf-8><title>DS Plugin Filter — données</title>"
                "<p>Index du catalogue WordPress.org utilisé par le plugin DS Plugin Filter. "
                f"Mis à jour le {generated}. Voir <a href=index.json>index.json</a>.</p>")

    total = sum(sizes.values())
    print(f"Export terminé : {len(rows)} plugins, {len(sizes)} fichiers, {total / 1e6:.1f} Mo au total")
    for path, size in sorted(sizes.items(), key=lambda x: -x[1])[:5]:
        print(f"  {path:<40} {size / 1e6:6.2f} Mo")


if __name__ == "__main__":
    main()
