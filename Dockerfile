FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 创建数据目录
RUN mkdir -p /app/data /app/logs

# 内置默认配置（写入 data 目录，持久化）
RUN echo '{"token":"","drive_id":"","webhook":"","interval":3600,"strm_webhook":"","strm_tasks":"","openlist_url":"","openlist_token":"","openlist_storage_id":0}' > /app/data/settings.json

# 初始化数据库
RUN python3 -c "import models; models.init_db()"

# Data volumes
VOLUME ["/app/data", "/app/logs"]

ENV PORT=8003
EXPOSE 8003

CMD ["python3", "app.py"]
