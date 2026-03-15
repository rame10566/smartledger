FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock* ./
COPY src/shared ./src/shared
COPY src/mcp_servers/simulated ./src/mcp_servers/simulated

RUN uv sync --package smartledger-mcp-simulated --no-dev

# Each simulated system runs on its own port
# Ports: 8010 (oracle_los), 8011 (salesforce_los), 8012 (llas), 8013 (crm),
#        8014 (payment), 8015 (insurance), 8016 (dealer),
#        8017 (customer_portal), 8018 (mobile_app), 8019 (ivr)

CMD ["uv", "run", "--package", "smartledger-mcp-simulated", "python", "-m", "mcp_servers.simulated.launcher"]
