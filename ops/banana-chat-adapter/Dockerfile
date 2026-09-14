FROM python:3.12-alpine

RUN addgroup -g 10001 -S adapter && adduser -u 10001 -S -G adapter adapter
WORKDIR /app
COPY --chown=adapter:adapter app.py /app/app.py
USER 10001:10001
EXPOSE 8093
ENTRYPOINT ["python", "/app/app.py"]
