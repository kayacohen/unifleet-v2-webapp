# UniFleet v2 webapp — local dev image
# Mirrors the Railway build env: Python 3.11 slim, Poetry, gunicorn entrypoint.
# System deps: libfreetype (for Pillow text rendering in generate_voucher.py).
# `python3` symlink is added so the subprocess call at main.py:260 works.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=2.4.1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libfreetype-dev \
 && rm -rf /var/lib/apt/lists/* \
 && pip install "poetry==${POETRY_VERSION}"

WORKDIR /app

COPY pyproject.toml poetry.lock ./
RUN poetry install --only main

COPY . .

RUN mkdir -p /app/data/presets

EXPOSE 5000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/healthz').read()" || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "main:app"]
