# One image for the API, the worker and migrations; the command picks which.
# Build from the repo root: docker build -f backend/Dockerfile .
FROM python:3.13-slim-bookworm

# libgomp: ONNX Runtime threads; libglib: OpenCV (headless) for page preparation
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH=/app/backend/.venv/bin:$PATH

# Everything below runs as, and is owned by, an unprivileged user (no chown pass at the end)
RUN useradd --create-home --uid 10001 lane && mkdir -p /app/backend && chown -R lane /app
USER lane
WORKDIR /app/backend

# Dependencies first, so code changes do not reinstall them
COPY --chown=lane backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# What the services read at runtime, at the paths the code expects (repo root = /app)
COPY --chown=lane backend/ /app/backend/
COPY --chown=lane config/ /app/config/
COPY --chown=lane data/ /app/data/
COPY --chown=lane evals/ /app/evals/
COPY --chown=lane scripts/ /app/scripts/
RUN uv sync --frozen --no-dev
# The demo's agent email, built from data/ (var/ is not in git, and CI builds from git)
RUN python -I /app/scripts/make_sample_eml.py

# Bake the OCR models in: RapidOCR fetches them on first use, which must never happen at runtime
RUN python -c "from PIL import Image; from lane.documents.ocr import read_words; read_words(Image.new('RGB', (64, 32), 'white'))"

EXPOSE 8000
CMD ["uvicorn", "lane.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
