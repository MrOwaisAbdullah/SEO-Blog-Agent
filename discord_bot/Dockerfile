FROM python:3.13-slim

# Install uv from the official source (same pattern as the main Dockerfile).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

RUN useradd --create-home --uid 1000 botuser

COPY requirements.txt ./
RUN uv pip install --system --no-cache -r requirements.txt

COPY bot.py ./

USER botuser

# No EXPOSE: this is a background worker holding a persistent Discord
# gateway (outbound) connection, not an HTTP service. Deploy it in Dokploy
# with no domain and no port mapping.
CMD ["python", "bot.py"]
