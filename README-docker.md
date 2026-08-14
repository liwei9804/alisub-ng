# alisub-ng Docker

## 构建镜像

```bash
docker build -t alisub-ng .
```

## 保存镜像（迁移到其他机器）

```bash
docker save alisub-ng:latest | gzip > alisub-ng.tar.gz
```

## 部署

### docker compose（推荐）

```bash
docker compose up -d
```

### docker CLI

```bash
docker run -d \
  --name alisub-ng \
  --restart unless-stopped \
  -p 8003:8003 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/settings.json:/app/settings.json \
  -v $(pwd)/data.db:/app/data.db \
  -e TZ=Asia/Shanghai \
  alisub-ng:latest
```

## 其他机器恢复

```bash
# 导入镜像
docker load < alisub-ng.tar.gz

# 创建数据目录
mkdir -p data logs

# 复制配置文件
cp settings.json ./
cp data.db ./

# 启动
docker compose up -d
```
