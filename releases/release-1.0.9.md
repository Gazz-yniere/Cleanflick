# CleanFlick v1.0.9

## ✨ Nouveautés
- **CI GitHub Actions** — lint (ruff) + tests (pytest) sur push/PR, rapport HTML, couverture de code et check-run par test
- **Posters dans la bibliothèque** — les épisodes et dossiers de série sans poster héritent du poster de la série (sans requête API)
- **Endpoint `/health`** + `HEALTHCHECK` Docker
- **Badge CI** dans le README

## 🔧 Améliorations
- **Restructuration en paquet `src/`** — le code applicatif est empaqueté sous `src/` ; `app.py` ne fait plus que l'assemblage
- **Build Docker sur PR** — l'image est construite sur chaque pull request pour valider le Dockerfile

## 🐛 Corrections
- Déplacements de fichiers en Docker : `Permission denied` sur les volumes montés (le conteneur repasse en root)

## 🗑️ Retraits
- `gevent` retiré des dépendances de production
