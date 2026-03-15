FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock* ./
COPY src/shared ./src/shared
COPY src/mcp_servers/semantic_ai ./src/mcp_servers/semantic_ai

RUN uv sync --package smartledger-mcp-semantic-ai --no-dev

EXPOSE 8003

CMD ["uv", "run", "--package", "smartledger-mcp-semantic-ai", "python", "-m", "mcp_servers.semantic_ai.server"]
