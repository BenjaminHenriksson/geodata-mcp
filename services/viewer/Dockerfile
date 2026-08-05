FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Vendor MapLibre GL JS at build time; no CDN references at runtime.
RUN mkdir -p /app/static \
 && curl -fsSL https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js -o /app/static/maplibre-gl.js \
 && curl -fsSL https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css -o /app/static/maplibre-gl.css

# Vendor Origo (OpenLayers-based municipal map framework). Origo publishes no npm
# package or release assets — the committed build/ directory at each git tag IS the
# distribution (js/origo.min.js, css/style.css, css/svg/* icon sprites fetched at
# runtime, img/*). Everything is served from /static/origo/, no CDN at runtime.
ARG ORIGO_VERSION=2.10.0
RUN mkdir -p /app/static/origo \
 && curl -fsSL "https://github.com/origo-map/origo/archive/refs/tags/v${ORIGO_VERSION}.tar.gz" \
  | tar -xz --strip-components=2 -C /app/static/origo "origo-${ORIGO_VERSION}/build" \
 && rm -rf /app/static/origo/examples /app/static/origo/data \
           /app/static/origo/index.html /app/static/origo/index.json

COPY main.py compile_maplibre.py compile_origo.py dbq.py netauth.py page.py viewer_auth.py /app/

EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
