# Dockerfile
#
# NOTE: this file must be named "Dockerfile" with a capital D. Your repo had it
# as lowercase "dockerfile", which works on Windows/macOS (case-insensitive
# filesystems) but fails to build on a Linux server.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# tzdata is required for zoneinfo (client timezones); curl for healthchecks.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY manage_clients.py diagnose.py ./

# Durable state (outbox queue, client snapshot). Mounted as a volume in compose.
RUN mkdir -p /data
VOLUME ["/data"]

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
 && chown -R appuser:appuser /srv /data
USER appuser

EXPOSE 8000

# Single worker on purpose: the Telegram bot uses long-polling, and two workers
# would fight over the same bot session (Telegram 409 Conflict). To scale, move
# the bot to webhooks first.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
