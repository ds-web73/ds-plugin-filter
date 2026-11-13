# DS Plugin Filter — données

Index quotidien du catalogue de plugins WordPress.org, classé par catégories, publié
sous forme de fichiers JSON statiques sur GitHub Pages. Utilisé par le plugin WordPress
DS Plugin Filter (DS-Web).

## Fonctionnement

Chaque nuit, GitHub Actions :

1. aspire le catalogue via l'API publique WordPress.org (`scripts/wp_catalog.py`) ;
   passe complète avec descriptions le dimanche, passe légère les autres jours ;
2. classe les plugins dans les catégories de `categories.json` (`scripts/wp_categories.py`) ;
3. génère les fichiers JSON (`scripts/wp_export.py`) et les publie sur GitHub Pages.

## Fichiers publiés

- `index.json` : date de génération, liste des catégories, format des champs
- `cat/<id>.json` : plugins d'une catégorie, triés par installations actives
- `lookup/<lettre>.json` : catégories et indicateurs par slug

## En local

```bash
python3 scripts/wp_catalog.py fetch --mode full
python3 scripts/wp_categories.py --save
python3 scripts/wp_export.py --out site
```
