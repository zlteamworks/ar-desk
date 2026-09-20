FROM python:3.13-slim

WORKDIR /app
COPY . /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TALENTTAP_HOST=0.0.0.0 \
    TALENTTAP_SECURE_COOKIE=1 \
    TALENTTAP_DB=/data/talenttap.sqlite3 \
    TALENTTAP_BACKUP_DIR=/data/backups

VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "talenttap_server.py", "--serve"]
