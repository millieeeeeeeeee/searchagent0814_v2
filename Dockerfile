# 使用官方 Python 3.11 映像
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run 自動用 $PORT
ENV PORT=8080
CMD exec gunicorn --bind :$PORT app:app
