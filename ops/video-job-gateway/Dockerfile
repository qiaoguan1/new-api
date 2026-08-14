FROM python:3.12-slim

ARG XTAI_VCS_REF
ARG XTAI_CATALOG_SHA256
ARG XTAI_SOURCE_SHA256

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY app.py adapters.py billing_collectors.py credential_lifecycle.py catalog.py reference_contract.py release_integrity.py relay_pricing.py routing.py store.py catalog.json relay-pricing.json /app/

RUN python -c "import sys; from release_integrity import verify_release_identity; verify_release_identity(sys.argv[1], sys.argv[2], '/app/catalog.json', sys.argv[3], '/app')" "$XTAI_VCS_REF" "$XTAI_CATALOG_SHA256" "$XTAI_SOURCE_SHA256" \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10002 --home /nonexistent --shell /usr/sbin/nologin gateway \
    && mkdir -p /data \
    && chown -R gateway:gateway /data

LABEL org.opencontainers.image.revision="${XTAI_VCS_REF}" \
      com.aixingtuyun.video.catalog-sha256="${XTAI_CATALOG_SHA256}" \
      com.aixingtuyun.video.source-sha256="${XTAI_SOURCE_SHA256}"

USER gateway
EXPOSE 8091
CMD ["python", "/app/app.py"]
