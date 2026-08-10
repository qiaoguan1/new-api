FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY app.py adapters.py catalog.py relay_pricing.py routing.py store.py catalog.json relay-pricing.json /app/

RUN useradd --system --uid 10002 --home /nonexistent --shell /usr/sbin/nologin gateway \
    && mkdir -p /data \
    && chown -R gateway:gateway /data

USER gateway
EXPOSE 8091
CMD ["python", "/app/app.py"]
