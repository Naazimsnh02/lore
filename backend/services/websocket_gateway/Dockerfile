# LORE WebSocket Gateway — Cloud Run container
#
# Cloud Run configuration:
#   min-instances: 2   (Requirement 20.6 — always-warm for low latency)
#   max-instances: 100 (Requirement 20.6 — auto-scale to handle 1000+ conns)
#   concurrency:   80  (WebSocket connections per instance)
#   port:          8080

FROM python:3.11-slim

# Security: run as non-root
RUN addgroup --system lore && adduser --system --ingroup lore lore

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Switch to non-root user
USER lore

# Cloud Run injects PORT env var (default 8080)
ENV PORT=8080

# Expose port for local testing; Cloud Run ignores EXPOSE but documents intent
EXPOSE 8080

# uvicorn with:
#   --workers 1      One process per container (Cloud Run model)
#   --loop uvloop    High-performance event loop
#   --http h11       Lightweight HTTP/1.1 for WebSocket upgrades
#   --timeout-keep-alive 65   Longer than Cloud Run's 60s idle timeout
CMD exec uvicorn \
    backend.services.websocket_gateway.app:app \
    --host 0.0.0.0 \
    --port ${PORT} \
    --workers 1 \
    --loop uvloop \
    --http h11 \
    --timeout-keep-alive 65 \
    --log-level info
