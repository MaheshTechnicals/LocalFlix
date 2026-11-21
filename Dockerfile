# LocalFlix — container image (Python slim + ffmpeg).
#   docker build -t localflix .
#   docker run -d --name localflix -p 8000:8000 -v /path/to/videos:/media -v localflix-data:/data localflix
# On Linux, `--network host` lets the banner show your real LAN address and enables mDNS.
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

ENV LOCALFLIX_HOME=/data \
    LOCALFLIX_MEDIA=/media \
    LOCALFLIX_PORT=8000 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY localflix.py /app/localflix.py

RUN useradd --create-home --uid 1000 localflix \
 && mkdir -p /data /media \
 && chown -R localflix:localflix /data /app
USER localflix

VOLUME ["/data", "/media"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/system/info', timeout=4)" || exit 1

CMD ["python", "/app/localflix.py", "serve", "--no-open", "--no-wizard", "--host", "0.0.0.0"]
