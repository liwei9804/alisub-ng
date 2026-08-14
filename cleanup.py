#!/usr/bin/env python3
"""
去重脚本 - 清理目标目录中未改名的重复文件

策略：如果目录中同时存在：
  - 「01 4K.mp4」（未改名）
  - 「九门 (2026).S01E01.mp4」（正确命名）
则删除未改名的那个。
"""

import os
import sys
import re
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("cleanup")

# 添加当前目录到 path
sys.path.insert(0, os.path.dirname(__file__))

from api import AliyunDriveAPI
from detector import extract_episode

# ─── 配置 ──────────────────────────────────────────────

REFRESH_TOKEN = os.environ.get("REFRESH_TOKEN", "")
DRIVE_ID = os.environ.get("DRIVE_ID", "")


def cleanup_subscription(api: AliyunDriveAPI, sub_name: str,
                         to_parent_id: str, to_file_name_tpl: str,
                         season: int = 1, episode_regex: str = "",
                         dry_run: bool = False) -> int:
    """清理单个订阅的重复文件"""

    log.info(f"🔍 扫描 【{sub_name}】 目录...")

    try:
        files = api.list_files(to_parent_id)
    except Exception as e:
        log.error(f"❌ 列出文件失败: {e}")
        return 0

    media_exts = {'.mp4', '.mkv', '.avi', '.ts', '.flv', '.rmvb'}
    media_files = [
        f for f in files
        if any(f["name"].lower().endswith(ext) for ext in media_exts)
    ]

    log.info(f"  共 {len(media_files)} 个媒体文件")

    # 分类：正确命名 vs 编号命名
    correct_files = {}   # episode → file
    numbered_files = []   # (episode, file)

    for f in media_files:
        fname = f["name"]
        ep = extract_episode(fname, episode_regex)

        if ep == 0:
            continue

        # 判断是否是正确命名格式（包含标题或 S01E 格式）
        if sub_name in fname or f"S{season:02d}E" in fname or f"S01E" in fname:
            if ep not in correct_files:
                correct_files[ep] = f
            else:
                # 同集数有多个正确命名文件，保留较新的
                log.info(f"  ⚠️ E{ep:02d} 有多个正确命名文件: {correct_files[ep]['name']} vs {fname}")
        else:
            numbered_files.append((ep, f))

    log.info(f"  正确命名: {len(correct_files)} 个")
    log.info(f"  编号命名: {len(numbered_files)} 个")

    # 找出有对应正确命名文件的编号文件
    to_delete = []
    for ep, f in numbered_files:
        if ep in correct_files:
            to_delete.append((ep, f, correct_files[ep]))

    if not to_delete:
        log.info(f"  ✅ 无重复文件需要清理")
        return 0

    log.info(f"  🗑️ 发现 {len(to_delete)} 个重复文件:")
    for ep, old, new in to_delete:
        log.info(f"    E{ep:02d}: {old['name']} → 已有 {new['name']}")

    if dry_run:
        log.info(f"  (dry-run 模式，不实际删除)")
        return len(to_delete)

    # 删除重复文件
    deleted = 0
    for ep, old, new in to_delete:
        try:
            api.delete_file(old["file_id"])
            log.info(f"  ✅ 已删除: {old['name']}")
            deleted += 1
            time.sleep(0.5)
        except Exception as e:
            log.error(f"  ❌ 删除失败 {old['name']}: {e}")

    log.info(f"  ✅ 清理完成: 删除 {deleted} 个重复文件")
    return deleted


def main():
    import argparse
    parser = argparse.ArgumentParser(description="清理重复文件")
    parser.add_argument("--dry-run", action="store_true", help="只检查不删除")
    parser.add_argument("--sub", type=str, default="", help="只清理指定订阅（名称关键词）")
    args = parser.parse_args()

    api = AliyunDriveAPI(REFRESH_TOKEN, DRIVE_ID)

    # 从 alisub 数据库读取订阅列表
    import sqlite3
    db_path = os.path.join(os.path.dirname(__file__), "data", "alisub-ng.db")
    if not os.path.exists(db_path):
        log.error(f"alisub 数据库不存在: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 读取订阅
    rows = conn.execute("SELECT * FROM ali_subscribe WHERE status=1").fetchall()

    total_deleted = 0
    for row in rows:
        title = row["share_title"]
        to_parent_id = row["to_parent_id"]

        if args.sub and args.sub not in title:
            continue

        if not to_parent_id:
            log.warning(f"⚠️ 【{title}】无目标目录，跳过")
            continue

        # 从 to_file_name 推断模板
        to_file_name = row["to_file_name"] or "{title}.S{season}E{episode:02d}{ext}"
        if not to_file_name.endswith("{ext}"):
            to_file_name += "{ext}"

        deleted = cleanup_subscription(
            api, title, to_parent_id, to_file_name,
            season=1, episode_regex="",
            dry_run=args.dry_run,
        )
        total_deleted += deleted
        time.sleep(1)

    conn.close()
    log.info(f"\n{'='*50}")
    log.info(f"总计清理: {total_deleted} 个重复文件")


if __name__ == "__main__":
    main()
