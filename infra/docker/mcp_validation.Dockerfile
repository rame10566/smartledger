FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock* ./
COPY src/shared ./src/shared
COPY src/mcp_servers/validation ./src/mcp_servers/validation

RUN uv sync --package smartledger-mcp-validation --no-dev

EXPOSE 8001

CMD ["uv", "run", "--package", "smartledger-mcp-validation", "python", "-m", "mcp_servers.validation.server"]
