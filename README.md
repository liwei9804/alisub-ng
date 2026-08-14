# alisub-ng

阿里云盘订阅转存管理系统，自动检查订阅更新并转存到自己的云盘。

## 功能

- 📋 **订阅管理** — 添加/编辑/删除订阅，支持状态过滤
- 🔄 **自动转存** — 定时检查订阅更新，自动转存新文件
- 📝 **智能重命名** — 按模板重命名文件（如 `S01E01.mp4`）
- 🗑️ **同批去重** — 同一集只转存一个文件，优先保留 V2 版本
- 📅 **星期过滤** — 指定周几检查，未选则每天检查
- 📂 **文件浏览器** — 浏览阿里云盘文件目录
- 🧹 **去重清理** — 扫描并清理重复文件
- 🔔 **企业微信通知** — 转存/清理/错误自动通知
- 🎬 **SmartStrm 集成** — 转存后自动生成 STRM 文件
- 📂 **OpenList 刷新** — 转存后自动刷新 OpenList 存储源
- 🔐 **登录认证** — 支持账号密码登录

## 快速开始

### Docker 部署（推荐）

```bash
mkdir alisub-ng && cd alisub-ng
```

创建 `docker-compose.yml`：

```yaml
services:
  alisub-ng:
    image: ghcr.io/liwei9804/alisub-ng:latest
    container_name: alisub-ng
    restart: unless-stopped
    ports:
      - "8003:8003"
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      - TZ=Asia/Shanghai
      - PORT=8003
      - AUTH_USER=admin
      - AUTH_PASS=admin
```

启动：

```bash
docker-compose up -d
```

打开浏览器访问 `http://你的IP:8003`，在**云盘设置**中填写配置即可。

### 直接运行

```bash
git clone https://github.com/liwei9804/alisub-ng.git
cd alisub-ng
pip install -r requirements.txt
python3 app.py
```

## 配置说明

所有配置均可在 Web 页面的**云盘设置**中完成，无需手动编辑文件。

| 配置项 | 说明 |
|--------|------|
| Refresh Token | 阿里云盘 refresh_token |
| Drive ID | 云盘 ID（填 Token 后自动获取） |
| 企业微信 Webhook | 企业机器人 Webhook 地址 |
| 检查间隔 | 自动检查间隔（秒），默认 3600 |
| SmartStrm Webhook | SmartStrm 触发地址 |
| SmartStrm 任务名 | 用逗号分隔的任务名 |
| OpenList 地址 | OpenList/AList 服务地址 |
| OpenList Token | OpenList API Token |
| OpenList 存储 ID | 要刷新的存储源 ID |

## 默认账号

- 用户名：`admin`
- 密码：`admin`

可通过环境变量 `AUTH_USER` 和 `AUTH_PASS` 修改。

## 许可证

MIT
