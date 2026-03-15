FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# image_server.py uses IMAGE_SERVER_PORT env var (default 8091)
# Cloud Run expects 8080
ENV IMAGE_SERVER_PORT=8080
EXPOSE 8080

CMD ["python", "image_server.py"]
