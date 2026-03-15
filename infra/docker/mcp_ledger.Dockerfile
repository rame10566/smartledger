FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock* ./
COPY src/shared ./src/shared
COPY src/mcp_servers/ledger ./src/mcp_servers/ledger

RUN uv sync --package smartledger-mcp-ledger --no-dev

EXPOSE 8002

CMD ["uv", "run", "--package", "smartledger-mcp-ledger", "python", "-m", "mcp_servers.ledger.server"]
