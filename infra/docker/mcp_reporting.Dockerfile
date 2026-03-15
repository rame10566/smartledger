FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock* ./
COPY src/shared ./src/shared
COPY src/mcp_servers/reporting ./src/mcp_servers/reporting

RUN uv sync --package smartledger-mcp-reporting --no-dev

EXPOSE 8004

CMD ["uv", "run", "--package", "smartledger-mcp-reporting", "python", "-m", "mcp_servers.reporting.server"]
