FROM python:3.11-slim

# ── System hardening & image hygiene ──────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120

WORKDIR /app

# ── Install runtime deps first (better layer caching) ─────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy source ───────────────────────────────────────────────
COPY app ./app

# ── Drop root ─────────────────────────────────────────────────
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV PORT=8000 \
    APP_ENV=production

EXPOSE 8000

# Uvicorn is started via the entry module — `--workers 2` is a sensible
# default on a 512 MB Render free-tier box. Bump with --workers $WEB_CONCURRENCY
# if you move to a bigger dyno.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips=*"]
