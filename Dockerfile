# Stage 1: Build frontend
FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm config set registry https://registry.npmmirror.com && npm ci
COPY frontend/ ./
RUN npx vite build --outDir dist

# Stage 2: Production image
FROM python:3.12-slim

WORKDIR /app

# Install uv (via Tsinghua PyPI mirror)
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv

# Create app user first so files get correct ownership automatically
RUN useradd --create-home --uid 1000 appuser && \
    mkdir -p /data /app/logs && \
    chown -R appuser:appuser /app /data /app/logs

# Switch to appuser — COPY and RUN commands inherit appuser ownership
USER appuser

# Install Python dependencies (cached when pyproject.toml/uv.lock unchanged)
COPY pyproject.toml uv.lock ./
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN uv sync --frozen --no-dev && \
    rm -rf $(uv cache dir)

# Copy backend source
COPY main_server.py ./
COPY src/ ./src/

# Copy built frontend from Stage 1
COPY --from=frontend-build --chown=appuser:appuser /build/frontend/dist/ ./src/static/

# Switch back to root for HEALTHCHECK (avoids permission issues on probe),
# then CMD runs as appuser
USER root

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

ENV PROD=true \
    PYTHONUNBUFFERED=1 \
    UV_FROZEN=true

EXPOSE 8000
USER appuser
CMD ["uv", "run", "uvicorn", "main_server:app", "--host", "0.0.0.0", "--port", "8000"]
