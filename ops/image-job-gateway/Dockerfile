FROM xtai-image-job-gateway-image-job-gateway
COPY app.py /app/app.py
COPY test_gateway_audit.py /app/test_gateway_audit.py
RUN python -m unittest discover -s /app -p 'test_gateway_audit.py' -q
LABEL com.aixingtuyun.change=issue183-image-audit
