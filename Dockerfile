FROM python:3.14-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

FROM base AS experiment
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-ml.txt .
RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements-ml.txt
COPY stockpilot/ stockpilot/
COPY scripts/ scripts/
CMD ["sh", "-c", "python -m scripts.fetch_m5 && python -m scripts.evaluate"]

FROM base AS runtime
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home stockpilot
COPY stockpilot/ stockpilot/
USER stockpilot
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8080/health', timeout=2).close()"]
CMD ["python", "-m", "uvicorn", "stockpilot.api:app", "--host", "0.0.0.0", "--port", "8080"]
