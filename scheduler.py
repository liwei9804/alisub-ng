#!/usr/bin/env python3
"""
定时任务调度器 - 定期检查所有订阅并转存
"""

import os
import sys
import time
import sqlite3
import logging
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from api import AliyunDriveAPI
from transfer import TransferEngine
from notifier import Notifier

log = logging.getLogger("alisub-ng.scheduler")

ALISUB_DB = os.path.join(os.path.dirname(__file__), "data", "alisub-ng.db")


class Scheduler:
    """定时转存调度器"""

    def __init__(self, api: AliyunDriveAPI, notifier: Notifier = None,
                 check_interval: int = 3600):
        self.api = api
        self.engine = TransferEngine(api)
        self.notifier = notifier
        self.check_interval = check_interval
        self.running = False
        self.thread = None
        self.last_check = None
        self.check_results = []

    def start(self):
        """启动定时检查"""
        if self.running:
            log.warning("调度器已在运行")
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        log.info(f"🚀 调度器启动（间隔 {self.check_interval}s）")

    def stop(self):
        """停止定时检查"""
        self.running = False
        log.info("⏹️ 调度器停止")

    def _loop(self):
        """主循环"""
        token_failed = False
        while self.running:
            try:
                # 先检查 token 是否有效
                try:
                    self.api._ensure_token()
                    token_failed = False
                except Exception as te:
                    if not token_failed:
                        log.error(f"❌ Token 失效: {te}")
                        if self.notifier:
                            self.notifier.notify_token_expired()
                        token_failed = True
                    # token 失效，跳过本次检查
                    for _ in range(min(self.check_interval, 300)):
                        if not self.running:
                            break
                        time.sleep(1)
                    continue

                self.check_all()
            except Exception as e:
                log.error(f"❌ 检查循环异常: {e}", exc_info=True)

            for _ in range(self.check_interval):
                if not self.running:
                    break
                time.sleep(1)

    def check_all(self, force: bool = False) -> list:
        """检查所有订阅

        Args:
            force: True=手动检查，忽略 check_days；False=定时检查，按 check_days 过滤
        """
        subs = self._load_subscriptions()
        if not subs:
            log.info("无活跃订阅")
            return []

        # 按 check_days 过滤（手动检查跳过此过滤）
        from datetime import datetime
        today_weekday = datetime.now().weekday()  # 0=周一 ... 6=周日
        day_names = ['周一','周二','周三','周四','周五','周六','周日']
        if force:
            filtered = subs
            log.info(f"📋 手动检查所有 {len(subs)} 个订阅")
        else:
            filtered = []
            skipped = []
            for sub in subs:
                days = sub.get("check_days", "")
                if days:
                    try:
                        allowed = [int(d) for d in days.split(",") if d.strip()]
                        if today_weekday not in allowed:
                            skipped.append((sub, [day_names[int(d)] for d in allowed]))
                            continue
                    except:
                        pass
                filtered.append(sub)

            # 记录跳过的订阅
            for sub, allow_days in skipped:
                log.info(f"⏭️ [{sub['share_title']}] 跳过检查（设定: {'、'.join(allow_days)}，今天: {day_names[today_weekday]}）")

            if not filtered:
                log.info(f"📅 今天（{day_names[today_weekday]}）所有订阅均已跳过")
                return []

        self.last_check = datetime.now().isoformat()
        log.info(f"⏰ 开始检查 {len(filtered)} 个订阅...")

        results = []
        for sub in filtered:
            try:
                transfers = self.engine.check_and_transfer(sub, record_callback=self._save_record)
                if transfers:
                    result = {"name": sub["share_title"], "count": len(transfers), "files": [t["to_file_name"] for t in transfers]}
                    results.append(result)
                    # 通知
                    if self.notifier:
                        self.notifier.notify_transfer(sub["share_title"], transfers)
                    # 更新 alisub 数据库的 last_file_name
                    last = transfers[-1]
                    self._update_last_file(sub["id"], last["share_file_id"], last["share_file_name"])
            except Exception as e:
                log.error(f"❌ [{sub['share_title']}] 检查失败: {e}", exc_info=True)
                results.append({"name": sub["share_title"], "error": str(e)})
            time.sleep(3)

        self.check_results = results
        if results:
            log.info(f"✅ 检查完成: {len(results)} 个订阅有更新")
            # 先刷新 OpenList 存储源
            if self.notifier:
                self.notifier.refresh_openlist(self.notifier.openlist_storage_id)
            # 再触发 SmartStrm（需要 OpenList 已刷新才能生成新文件）
            if self.notifier:
                self.notifier.trigger_strm()
        else:
            log.info(f"✅ 检查完成: 无更新")
        return results

    def check_one(self, sub_id: int) -> dict:
        """检查单个订阅"""
        sub = self._load_subscription(sub_id)
        if not sub:
            return {"error": "订阅不存在"}

        try:
            transfers = self.engine.check_and_transfer(sub, record_callback=self._save_record)
            if transfers:
                if self.notifier:
                    self.notifier.notify_transfer(sub["share_title"], transfers)
                    self.notifier.trigger_strm()
                last = transfers[-1]
                self._update_last_file(sub["id"], last["share_file_id"], last["share_file_name"])
                return {"name": sub["share_title"], "count": len(transfers), "files": [t["to_file_name"] for t in transfers]}
            return {"name": sub["share_title"], "count": 0, "files": []}
        except Exception as e:
            log.error(f"❌ [{sub['share_title']}] 检查失败: {e}")
            return {"name": sub["share_title"], "error": str(e)}

    def get_status(self) -> dict:
        """获取调度器状态"""
        return {
            "running": self.running,
            "interval": self.check_interval,
            "last_check": self.last_check,
            "results": self.check_results[-10:],  # 最近10次
        }

    # ─── 数据库操作 ──────────────────────────────────────

    def _load_subscriptions(self) -> list:
        """加载活跃订阅"""
        try:
            conn = sqlite3.connect(ALISUB_DB)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM ali_subscribe WHERE status=1").fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            log.error(f"读取订阅失败: {e}")
            return []

    def _load_subscription(self, sub_id: int) -> dict:
        """加载单个订阅"""
        try:
            conn = sqlite3.connect(ALISUB_DB)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM ali_subscribe WHERE id=?", (sub_id,)).fetchone()
            conn.close()
            return dict(row) if row else None
        except:
            return None

    def _save_record(self, sub_id, share_file_id, share_file_name, to_file_id, to_file_name, status, error, to_file_size=0):
        """保存转存记录到数据库"""
        try:
            conn = sqlite3.connect(ALISUB_DB)
            conn.execute("""
                INSERT INTO ali_record (subscribe_id, share_file_id, share_file_name,
                                        to_file_id, to_file_name, to_file_size, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (sub_id, share_file_id, share_file_name, to_file_id, to_file_name, to_file_size, status))
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"保存记录失败: {e}")

    def _update_last_file(self, sub_id, file_id, file_name):
        """更新订阅的最后文件"""
        try:
            conn = sqlite3.connect(ALISUB_DB)
            conn.execute(
                "UPDATE ali_subscribe SET last_file_id=?, last_file_name=? WHERE id=?",
                (file_id, file_name, sub_id)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"更新最后文件失败: {e}")
