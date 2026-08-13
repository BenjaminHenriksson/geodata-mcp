# Ingestion worker: GDAL (ogr2ogr with PostgreSQL/Parquet/WFS drivers) +
# Python job loop + embedding HTTP service. linux/arm64-compatible base.
FROM ghcr.io/osgeo/gdal:ubuntu-full-3.8.5

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# The GDAL 3.8.5 base image ships an Apache Arrow apt repo whose signing key
# has since rotated — apt-get update fails on it. Arrow is already baked into
# the image; the repo list is only needed to upgrade it, so drop it.
RUN rm -f /etc/apt/sources.list.d/apache-arrow.sources \
      /etc/apt/sources.list.d/apache-arrow.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends python3-pip \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8100

# First model use downloads weights into /root/.cache/huggingface (a volume).
CMD ["python3", "main.py"]
