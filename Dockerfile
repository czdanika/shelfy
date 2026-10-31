FROM python:3.12-slim-bookworm

# ebook-meta (metaadat-olvasás) és ebook-convert (konvertálás)
RUN apt-get update && apt-get install -y --no-install-recommends calibre fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY templates ./templates
COPY static ./static
COPY locales ./locales

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && mkdir -p /data /ingest

# PUID/PGID: melyik (NAS-)felhasználó nevében fusson; a mappák jogosultságát indításkor rendbe teszi

ENV DATA_DIR=/data PORT=8080 PUID=1000 PGID=1000 QT_QPA_PLATFORM=offscreen PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8080'))" || exit 1
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips '*'"]
