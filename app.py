"""Lanceur Cleanflick : crée l'application Flask et démarre le serveur.

Tout le métier (routes, services, caches) est dans le paquet `src/` ; ce
fichier ne fait que l'assemblage et le `app.run()`.
    - Développement : `python app.py`
    - Production    : `gunicorn ... app:app`
"""
import os

from src.routes import create_app

app = create_app()


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug, host='0.0.0.0', port=5000, threaded=True)
