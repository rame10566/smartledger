FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock* ./
COPY src/shared ./src/shared
COPY src/dashboard_api ./src/dashboard_api

RUN uv sync --package smartledger-dashboard-api --no-dev

EXPOSE 8000

CMD ["uv", "run", "--package", "smartledger-dashboard-api", "uvicorn", "dashboard_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
