#!/usr/bin/env python3
"""
alisub-ng - 阿里云盘订阅转存系统

替代 alisub 的新一代方案：
- 使用 v2 Open API（更可靠）
- 转存前去重（防止重复转存）
- 重命名后验证（防止命名失败）
- 重复文件自动清理

用法:
    python main.py                    # 启动（Web API + 定时检查）
    python main.py --port 8003        # 指定端口
    python main.py --once             # 只检查一次
    python main.py --cleanup-all      # 清理所有订阅的重复文件
"""

import os
import sys
import time
import signal
import logging
import argparse
import threading
from datetime import datetime

from api import AliyunDriveAPI
from transfer import TransferEngine, parse_share_url
from notifier import Notifier
from web import create_app
import models

# ─── 日志配置 ──────────────────────────────────────────

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(
            os.path.join(LOG_DIR, f"alisub-ng-{datetime.now().strftime('%Y%m')}.log"),
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("alisub-ng")

# ─── 配置 ──────────────────────────────────────────────

CONFIG = {
    "refresh_token": os.environ.get("REFRESH_TOKEN", ""),
    "drive_id": os.environ.get("DRIVE_ID", ""),
    "webhook_url": os.environ.get("WEBHOOK_URL", ""),
    "check_interval": int(os.environ.get("CHECK_INTERVAL", "3600")),  # 默认1小时
    "port": int(os.environ.get("PORT", "8003")),
}


class AlisubNG:
    """主应用"""

    def __init__(self):
        self.api = AliyunDriveAPI(CONFIG["refresh_token"], CONFIG["drive_id"])
        self.engine = TransferEngine(self.api)
        self.notifier = Notifier(CONFIG["webhook_url"])
        self.running = True

    def check_all_subscriptions(self):
        """检查所有订阅"""
        subs = models.list_subscriptions(status=1)
        if not subs:
            log.info("无活跃订阅")
            return

        log.info(f"开始检查 {len(subs)} 个订阅...")
        for sub in subs:
            try:
                # 先清理重复文件
                self.engine.cleanup_duplicates(sub)
                # 检查并转存
                transfers = self.engine.check_and_transfer(sub)
                if transfers:
                    self.notifier.notify_transfer(sub["name"], transfers)
            except Exception as e:
                log.error(f"❌ [{sub['name']}] 检查失败: {e}", exc_info=True)
                self.notifier.notify_error(sub["name"], str(e))
            time.sleep(3)  # 订阅间隔

    def scheduler_loop(self):
        """定时检查循环"""
        log.info(f"⏰ 定时检查已启动（间隔 {CONFIG['check_interval']}s）")
        while self.running:
            try:
                self.check_all_subscriptions()
            except Exception as e:
                log.error(f"❌ 检查循环异常: {e}", exc_info=True)

            # 等待下次检查
            for _ in range(CONFIG["check_interval"]):
                if not self.running:
                    break
                time.sleep(1)

    def run_once(self):
        """只检查一次"""
        self.check_all_subscriptions()

    def cleanup_all(self):
        """清理所有订阅的重复文件"""
        subs = models.list_subscriptions(status=1)
        total = 0
        for sub in subs:
            count = self.engine.cleanup_duplicates(sub)
            total += count
            if count:
                self.notifier.notify_cleanup(sub["name"], count)
        log.info(f"✅ 共清理 {total} 个重复文件")


def main():
    parser = argparse.ArgumentParser(description="alisub-ng 阿里云盘订阅转存系统")
    parser.add_argument("--port", type=int, default=CONFIG["port"], help="Web API 端口")
    parser.add_argument("--once", action="store_true", help="只检查一次")
    parser.add_argument("--cleanup-all", action="store_true", help="清理所有重复文件")
    parser.add_argument("--interval", type=int, default=CONFIG["check_interval"], help="检查间隔（秒）")
    args = parser.parse_args()

    CONFIG["port"] = args.port
    CONFIG["check_interval"] = args.interval

    # 初始化数据库
    models.init_db()

    # 创建主应用
    app_instance = AlisubNG()

    if args.cleanup_all:
        app_instance.cleanup_all()
        return

    if args.once:
        app_instance.run_once()
        return

    # 启动定时检查线程
    scheduler_thread = threading.Thread(target=app_instance.scheduler_loop, daemon=True)
    scheduler_thread.start()

    # 启动 Web API
    web_app = create_app(
        api=app_instance.api,
        engine=app_instance.engine,
        notifier=app_instance.notifier,
    )

    # 优雅退出
    def signal_handler(sig, frame):
        log.info("收到退出信号，正在关闭...")
        app_instance.running = False
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    log.info(f"🚀 alisub-ng 启动成功 (端口: {CONFIG['port']}, 间隔: {CONFIG['check_interval']}s)")
    web_app.run(host="0.0.0.0", port=CONFIG["port"], debug=False)


if __name__ == "__main__":
    main()
