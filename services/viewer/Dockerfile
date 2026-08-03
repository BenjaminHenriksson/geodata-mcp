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

COPY main.py compile_maplibre.py compile_origo.py dbq.py page.py /app/

EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
