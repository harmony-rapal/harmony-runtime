FROM python:3.12-slim
WORKDIR /app
COPY demo /app/demo
COPY docs /app/docs
USER 65534:65534
EXPOSE 8765
CMD ["python3", "-B", "-m", "demo.server", "--container"]
