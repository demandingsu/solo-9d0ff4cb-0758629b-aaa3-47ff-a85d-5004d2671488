FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SAMPLE_PORT=8080

WORKDIR /app
COPY sample_site/server.py ./server.py

EXPOSE 8080

CMD ["python", "-I", "server.py"]
