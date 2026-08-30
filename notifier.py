#!/usr/bin/env python3
"""
通知系统 - 企业微信 Webhook
"""
import json
import time

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
            lines.append(f"{i}. {t['share_file_name']} → {t['to_file_name']}")
        lines.append(f" ✅状态: success")

        # 画质升级原因
        upgrades = [t for t in transfers if t.get("reason")]
        if upgrades:
            lines.append(" 📈画质升级:")
            for t in upgrades:
                lines.append(f"   {t['to_file_name']}: {t['reason']}")

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

    def notify_token_expired(self):
        """发送 token 失效通知"""
        now = datetime.now().strftime("%m-%d %H:%M")
        self.send(
            f"[{now}] ⚠️ 阿里云盘 Token 已失效！\n\n"
            f"请打开 alisub-ng 管理页面，进入「云盘设置」tab，\n"
            f"点击「📱 扫码获取」重新登录。\n\n"
            f"🤖 来自 alisub-ng"
        )

    def notify_network_error(self, error: str):
        """发送网络异常通知（连续多次失败才触发）"""
        now = datetime.now().strftime("%m-%d %H:%M")
        self.send(
            f"[{now}] 🌐 阿里云盘网络连接异常\n\n"
            f"连续多次无法连接阿里云盘服务器，可能是网络波动或 DNS 问题。\n"
            f"Token 本身可能没问题，等网络恢复后会自动继续。\n\n"
            f"错误: {error}\n\n"
            f"🤖 来自 alisub-ng"
        )

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
        """刷新 OpenList 存储源（先禁用再启用）"""
        if not self.openlist_url or not self.openlist_token or not storage_id:
            return
        base = self.openlist_url.rstrip("/")
        headers = {"Authorization": self.openlist_token}
        try:
            # 禁用
            r1 = requests.post(f"{base}/api/admin/storage/disable?id={storage_id}", headers=headers, timeout=15)
            d1 = r1.json()
            if d1.get("code") != 200:
                log.warning(f"⚠️ OpenList 存储源 #{storage_id} 禁用失败: {d1.get('message','')}")
                return
            time.sleep(1)
            # 启用
            r2 = requests.post(f"{base}/api/admin/storage/enable?id={storage_id}", headers=headers, timeout=15)
            d2 = r2.json()
            if d2.get("code") == 200:
                log.info(f"📂 OpenList 存储源 #{storage_id} 刷新成功")
            else:
                log.warning(f"⚠️ OpenList 存储源 #{storage_id} 启用失败: {d2.get('message','')}")
        except Exception as e:
            log.error(f"❌ OpenList 存储源刷新异常: {e}")
