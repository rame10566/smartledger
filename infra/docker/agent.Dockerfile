FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy workspace files
COPY pyproject.toml uv.lock* ./
COPY src/shared ./src/shared
COPY src/agent ./src/agent

# Install dependencies
RUN uv sync --package smartledger-agent --no-dev

CMD ["uv", "run", "--package", "smartledger-agent", "python", "-m", "agent.main"]
