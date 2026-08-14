#!/usr/bin/env python3
"""
通知系统 - 企业微信 Webhook
"""

import logging
import requests
from datetime import datetime

log = logging.getLogger("alisub-ng.notifier")


class Notifier:
    """企业微信通知 + SmartStrm 触发"""

    def __init__(self, webhook_url: str = "", strm_webhook: str = "", strm_tasks: str = "",
                 openlist_url: str = "", openlist_token: str = "", openlist_storage_id: int = 0):
        self.webhook_url = webhook_url
        self.strm_webhook = strm_webhook
        self.strm_tasks = strm_tasks
        self.openlist_url = openlist_url
        self.openlist_token = openlist_token
        self.openlist_storage_id = openlist_storage_id

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
            f"[{now}]📥 阿里云盘 | ",
            f"🎬【{sub_name}】 转存成功（{len(transfers)}个文件）",
            f" 📋文件：",
        ]
        for i, t in enumerate(transfers, 1):
            reason = t.get("reason")
            if reason:
                lines.append(f"{i}. {t['share_file_name']} → {t['to_file_name']}（{reason}）")
            else:
                lines.append(f"{i}. {t['share_file_name']} → {t['to_file_name']}")
        lines.append(f" ✅状态: success")

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

    def trigger_strm(self):
        """触发 SmartStrm 任务"""
        if not self.strm_webhook or not self.strm_tasks:
            return
        tasks = [t.strip() for t in self.strm_tasks.split(",") if t.strip()]
        for task_name in tasks:
            payload = {
                "event": "a_task",
                "task": {"name": task_name},
            }
            log.info(f"🎬 触发 SmartStrm: {task_name}")
            try:
                resp = requests.post(self.strm_webhook, json=payload, timeout=15)
                result = resp.json()
                if result.get("success"):
                    log.info(f"✅ SmartStrm [{task_name}] 触发成功")
                else:
                    log.warning(f"⚠️ SmartStrm [{task_name}]: {result.get('message', '')}")
            except Exception as e:
                log.error(f"❌ SmartStrm [{task_name}] 请求异常: {e}")

    def refresh_openlist(self, storage_id: int = 0):
        """刷新 OpenList 存储源缓存"""
        if not self.openlist_url or not self.openlist_token or not storage_id:
            return
        base = self.openlist_url.rstrip("/")
        headers = {"Authorization": self.openlist_token}
        try:
            url = base + "/api/admin/storage/refresh"
            resp = requests.post(url, json={"id": storage_id}, headers=headers, timeout=15)
            data = resp.json()
            if data.get("code") == 200:
                log.info(f"📂 OpenList 存储源 #{storage_id} 刷新成功")
            else:
                log.warning(f"⚠️ OpenList 存储源刷新失败: {data.get('message', '')}")
        except Exception as e:
            log.error(f"❌ OpenList 存储源刷新异常: {e}")
