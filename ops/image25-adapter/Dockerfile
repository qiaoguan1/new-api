FROM python:3.12-alpine
RUN addgroup -g 10001 -S adapter && adduser -u 10001 -S -G adapter adapter
WORKDIR /app
COPY app.py image_io.py /app/
USER 10001:10001
ENTRYPOINT ["python", "/app/app.py"]
