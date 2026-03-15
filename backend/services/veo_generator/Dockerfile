FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# video_server.py uses VIDEO_SERVER_PORT env var (default 8092)
# Cloud Run expects 8080
ENV VIDEO_SERVER_PORT=8080
EXPOSE 8080

CMD ["python", "video_server.py"]
