#!/usr/bin/env python3
"""
通知系统 - 企业微信 Webhook
"""

import logging
import requests
from datetime import datetime

log = logging.getLogger("alisub-ng.notifier")


class Notifier:
    """企业微信通知"""

    def __init__(self, webhook_url: str = ""):
        self.webhook_url = webhook_url

    def send(self, content: str, msg_type: str = "text"):
        """发送消息"""
        if not self.webhook_url:
            return
        try:
            if msg_type == "markdown":
                payload = {"msgtype": "markdown", "markdown": {"content": content}}
            else:
                payload = {"msgtype": "text", "text": {"content": content}}
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            if resp.json().get("errcode") == 0:
                log.info("📤 企业微信通知发送成功")
            else:
                log.warning(f"📤 企业微信通知发送失败: {resp.text}")
        except Exception as e:
            log.error(f"📤 企业微信通知异常: {e}")

    def notify_transfer(self, sub_name: str, transfers: list):
        """发送转存通知"""
        if not transfers:
            return

        now = datetime.now().strftime("%m-%d %H:%M")
        lines = [
            f"[{now}] ✅ 阿里云盘转存通知",
            f"📦 【{sub_name}】转存成功（{len(transfers)}个文件）",
            f"",
            f"📄 文件列表：",
        ]
        for i, t in enumerate(transfers, 1):
            lines.append(f"  {i}. {t['share_file_name']} → {t['to_file_name']}")
        lines.append(f"")
        lines.append(f"🤖 来自 alisub-ng")

        self.send("\n".join(lines))

    def notify_error(self, sub_name: str, error: str):
        """发送错误通知"""
        now = datetime.now().strftime("%m-%d %H:%M")
        self.send(f"[{now}] ❌ 【{sub_name}】转存失败\n{error}\n🤖 来自 alisub-ng")

    def notify_cleanup(self, sub_name: str, count: int):
        """发送清理通知"""
        if count == 0:
            return
        now = datetime.now().strftime("%m-%d %H:%M")
        self.send(f"[{now}] 🗑️ 【{sub_name}】清理了 {count} 个重复文件\n🤖 来自 alisub-ng")
