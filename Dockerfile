FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --home-dir /app --shell /usr/sbin/nologin app

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY docs ./docs

RUN pip install --no-cache-dir . \
    && mkdir -p /data \
    && chown -R app:app /app /data

USER app

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD ["calendar-slack-bot-healthcheck"]

# The image intentionally does not include credentials.json, token.json, Slack tokens, or SQLite state.
CMD ["calendar-slack-bot"]
