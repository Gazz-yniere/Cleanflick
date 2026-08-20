# CleanFlick
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Créer les dossiers et fichiers par défaut
RUN mkdir -p /downloads/movie /downloads/tv_shows && \
    cp config.example.json config.json

EXPOSE 5000

# Worker gthread (threads OS réels) avec worker unique :
# - worker unique => l'état métier (progression des déplacements, flux SSE, caches
#   mémoire) reste dans un seul processus (round-robin multi-worker = état éclaté,
#   progression invisible).
# - threads réels => les copies/network bloquantes (shutil, TVDB, OMDb) dans un thread
#   ne gèlent pas les autres requêtes. Avec gevent, shutil.copyfile bloque le loop
#   => WORKER TIMEOUT => SIGKILL => progression perdue.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--worker-class", "gthread", "--workers", "1", "--threads", "8", "--timeout", "300", "app:app"]
