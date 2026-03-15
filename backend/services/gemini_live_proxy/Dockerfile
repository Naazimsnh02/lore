FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# server.py uses GEMINI_PROXY_PORT env var (default 8090)
# Cloud Run expects 8080
ENV GEMINI_PROXY_PORT=8080
EXPOSE 8080

CMD ["python", "server.py"]
