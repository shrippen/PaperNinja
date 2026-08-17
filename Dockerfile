FROM python:3.12-alpine AS build

WORKDIR /src

RUN apk add --no-cache gcc g++ musl-dev libffi-dev

COPY pyproject.toml README.md ./
COPY app ./app

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir .

FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH" \
    DATA_DIR=/app/data

RUN apk add --no-cache libffi \
    && adduser -D -u 1000 -h /app paperninja

WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY --from=build /src/app ./app

RUN mkdir -p /app/data && chown -R paperninja:paperninja /app

USER paperninja
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
