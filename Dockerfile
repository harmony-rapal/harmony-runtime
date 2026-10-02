FROM python:3.12-slim
WORKDIR /app
COPY demo/__init__.py demo/server.py /app/demo/
COPY docs/index.html docs/demo.html docs/style.css docs/demo.js /app/docs/
COPY docs/assets/logo.svg docs/assets/logo-mark.svg docs/assets/logo-mono.svg docs/assets/landscape.svg docs/assets/hero-wordmark.svg /app/docs/assets/
USER 65534:65534
EXPOSE 8765
CMD ["python3", "-B", "-m", "demo.server", "--container"]
