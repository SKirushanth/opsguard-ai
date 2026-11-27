FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 BACKEND_HOST=0.0.0.0 BACKEND_PORT=8001 DATABASE_PATH=/data/opsguard.sqlite3
WORKDIR /app
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && groupadd --gid 10001 opsguard \
    && useradd --uid 10001 --gid opsguard --no-create-home opsguard \
    && install -d -m 0700 -o opsguard -g opsguard /data
COPY backend/ backend/
COPY investigation.py .
USER 10001:10001
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+str(os.getenv('PORT') or os.environ['BACKEND_PORT'])+'/readyz',timeout=3).close()"]
CMD ["python", "-m", "backend.app.main"]
