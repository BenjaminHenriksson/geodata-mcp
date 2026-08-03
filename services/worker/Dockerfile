# Ingestion worker: GDAL (ogr2ogr with PostgreSQL/Parquet/WFS drivers) +
# Python job loop + embedding HTTP service. linux/arm64-compatible base.
FROM ghcr.io/osgeo/gdal:ubuntu-full-3.8.5

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-pip \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8100

# First model use downloads weights into /root/.cache/huggingface (a volume).
CMD ["python3", "main.py"]
