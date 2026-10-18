FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/leitor.db \
    CACHE_DIR=/data/cache \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY static ./static

# Os dados (SQLite + cache) ficam em /data: monte um volume persistente aí.
# Obs.: não usamos a instrução VOLUME nem um usuário sem privilégios porque o
# Railway não aceita VOLUME no Dockerfile e monta volumes como root.
RUN mkdir -p /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)"

CMD ["python", "-m", "app"]
