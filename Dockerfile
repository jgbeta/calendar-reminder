FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY scripts ./scripts
COPY docs ./docs
COPY README.md ./README.md

# The image intentionally does not include credentials.json, token.json, or Slack tokens.
CMD ["python", "-m", "calendar_slack_bot.main"]
