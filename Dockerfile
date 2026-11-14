FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/
COPY src/ /app/src/

RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "codoc.action"]
