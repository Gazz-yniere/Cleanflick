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

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--worker-class", "gevent", "--workers", "2", "--timeout", "120", "app:app"]
