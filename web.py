#!/usr/bin/env python3
"""
Web API - 管理接口
"""

import logging
from flask import Flask, request, jsonify
from urllib.parse import urlparse, parse_qs

import models
from transfer import parse_share_url

log = logging.getLogger("alisub-ng.web")

app = Flask(__name__)


def create_app(api=None, engine=None, notifier=None):
    """创建 Flask 应用"""

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    # ─── 订阅管理 ──────────────────────────────────────

    @app.route("/api/subscriptions", methods=["GET"])
    def list_subs():
        subs = models.list_subscriptions()
        return jsonify({"code": 0, "data": subs})

    @app.route("/api/subscriptions", methods=["POST"])
    def add_sub():
        data = request.json
        name = data.get("name", "")
        share_url = data.get("share_url", "")
        to_parent_id = data.get("to_parent_id", "")
        share_pwd = data.get("share_pwd", "")
        to_file_name = data.get("to_file_name", "{title}.S{season}E{episode:02d}{ext}")
        season = data.get("season", 1)
        episode_regex = data.get("episode_regex", "")

        if not name or not share_url or not to_parent_id:
            return jsonify({"code": -1, "msg": "缺少必要参数"}), 400

        try:
            share_id, parent_file_id = parse_share_url(share_url)
        except ValueError as e:
            return jsonify({"code": -1, "msg": str(e)}), 400

        sub_id = models.add_subscription(
            name=name, share_url=share_url, share_id=share_id,
            parent_file_id=parent_file_id, to_parent_id=to_parent_id,
            share_pwd=share_pwd, to_file_name=to_file_name,
            season=season, episode_regex=episode_regex,
        )
        return jsonify({"code": 0, "data": {"id": sub_id}})

    @app.route("/api/subscriptions/<int:sub_id>", methods=["GET"])
    def get_sub(sub_id):
        sub = models.get_subscription(sub_id)
        if not sub:
            return jsonify({"code": -1, "msg": "订阅不存在"}), 404
        return jsonify({"code": 0, "data": sub})

    @app.route("/api/subscriptions/<int:sub_id>", methods=["PUT"])
    def update_sub(sub_id):
        data = request.json
        allowed = {"name", "share_pwd", "to_parent_id", "to_file_name",
                    "season", "episode_regex", "status"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return jsonify({"code": -1, "msg": "无有效更新字段"}), 400
        models.update_subscription(sub_id, **updates)
        return jsonify({"code": 0})

    @app.route("/api/subscriptions/<int:sub_id>", methods=["DELETE"])
    def delete_sub(sub_id):
        models.delete_subscription(sub_id)
        return jsonify({"code": 0})

    # ─── 转存记录 ──────────────────────────────────────

    @app.route("/api/subscriptions/<int:sub_id>/records")
    def list_records(sub_id):
        status = request.args.get("status")
        records = models.get_records(sub_id, status)
        return jsonify({"code": 0, "data": records})

    # ─── 手动操作 ──────────────────────────────────────

    @app.route("/api/subscriptions/<int:sub_id>/check", methods=["POST"])
    def check_sub(sub_id):
        """手动触发检查"""
        sub = models.get_subscription(sub_id)
        if not sub:
            return jsonify({"code": -1, "msg": "订阅不存在"}), 404
        if not engine:
            return jsonify({"code": -1, "msg": "引擎未初始化"}), 500

        transfers = engine.check_and_transfer(sub)
        if transfers and notifier:
            notifier.notify_transfer(sub["name"], transfers)
        return jsonify({"code": 0, "data": {
            "transfers": len(transfers),
            "files": transfers,
        }})

    @app.route("/api/subscriptions/<int:sub_id>/cleanup", methods=["POST"])
    def cleanup_sub(sub_id):
        """手动清理重复文件"""
        sub = models.get_subscription(sub_id)
        if not sub:
            return jsonify({"code": -1, "msg": "订阅不存在"}), 404
        if not engine:
            return jsonify({"code": -1, "msg": "引擎未初始化"}), 500

        count = engine.cleanup_duplicates(sub)
        return jsonify({"code": 0, "data": {"deleted": count}})

    @app.route("/api/check-all", methods=["POST"])
    def check_all():
        """检查所有订阅"""
        subs = models.list_subscriptions(status=1)
        results = []
        for sub in subs:
            try:
                transfers = engine.check_and_transfer(sub)
                if transfers and notifier:
                    notifier.notify_transfer(sub["name"], transfers)
                results.append({
                    "name": sub["name"],
                    "transfers": len(transfers),
                })
            except Exception as e:
                log.error(f"检查 {sub['name']} 失败: {e}")
                results.append({
                    "name": sub["name"],
                    "error": str(e),
                })
        return jsonify({"code": 0, "data": results})

    # ─── 文件浏览 ──────────────────────────────────────

    @app.route("/api/files")
    def list_files():
        """列出指定目录的文件"""
        parent_id = request.args.get("parent_file_id", "root")
        if not api:
            return jsonify({"code": -1, "msg": "API 未初始化"}), 500
        try:
            files = api.list_files(parent_id)
            return jsonify({"code": 0, "data": files})
        except Exception as e:
            return jsonify({"code": -1, "msg": str(e)}), 500

    return app
