#!/usr/bin/env python3
"""
迁移脚本 - 从旧版 alisub 复制数据到 alisub-ng 自己的数据库
"""

import os
import sqlite3
import shutil
from datetime import datetime

OLD_DB = "/vol1/1000/aliyundrive-subscribe/config/data.db"
NEW_DB = os.path.join(os.path.dirname(__file__), "data", "alisub-ng.db")


def migrate():
    os.makedirs(os.path.dirname(NEW_DB), exist_ok=True)

    # 如果新数据库已存在，备份
    if os.path.exists(NEW_DB):
        backup = NEW_DB + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(NEW_DB, backup)
        print(f"📦 已备份旧数据库: {backup}")

    # 创建新数据库（复制 alisub 表结构）
    new_conn = sqlite3.connect(NEW_DB)
    new_conn.execute("PRAGMA journal_mode=WAL")

    # 创建订阅表（保持和 alisub 一样的结构，便于后续扩展）
    new_conn.execute("""
        CREATE TABLE IF NOT EXISTS ali_subscribe (
            id INTEGER PRIMARY KEY,
            share_title TEXT NOT NULL,
            share_url TEXT NOT NULL,
            share_id TEXT NOT NULL,
            share_pwd TEXT DEFAULT '',
            parent_file_id TEXT NOT NULL,
            to_parent_id TEXT NOT NULL,
            to_file_name TEXT DEFAULT '',
            filters TEXT DEFAULT '',
            end_file_id TEXT DEFAULT '',
            last_file_id TEXT DEFAULT '',
            last_file_name TEXT DEFAULT '',
            last_update_at TEXT DEFAULT '',
            last_file_no INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            status VARCHAR(1) DEFAULT '1',
            download INTEGER DEFAULT 0,
            download_dir TEXT DEFAULT '',
            copying INTEGER DEFAULT 0,
            remark TEXT DEFAULT '',
            episode_regex TEXT DEFAULT '',
            season INTEGER DEFAULT 1,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )
    """)

    # 创建转存记录表
    new_conn.execute("""
        CREATE TABLE IF NOT EXISTS ali_record (
            id INTEGER PRIMARY KEY,
            subscribe_id INTEGER NOT NULL,
            share_file_name TEXT NOT NULL,
            share_file_id TEXT NOT NULL,
            to_file_name TEXT DEFAULT '',
            to_file_id TEXT DEFAULT '',
            to_file_size INTEGER DEFAULT 0,
            status TEXT DEFAULT 'done',
            error_msg TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )
    """)

    # 创建配置表
    new_conn.execute("""
        CREATE TABLE IF NOT EXISTS alisub_config (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        )
    """)

    # 创建索引
    new_conn.execute("CREATE INDEX IF NOT EXISTS idx_record_subscribe ON ali_record(subscribe_id)")
    new_conn.execute("CREATE INDEX IF NOT EXISTS idx_record_share_file ON ali_record(share_file_id)")
    new_conn.execute("CREATE INDEX IF NOT EXISTS idx_record_status ON ali_record(status)")

    new_conn.commit()

    # 从旧数据库复制数据
    if not os.path.exists(OLD_DB):
        print(f"❌ 旧数据库不存在: {OLD_DB}")
        new_conn.close()
        return

    old_conn = sqlite3.connect(OLD_DB)
    old_conn.row_factory = sqlite3.Row

    # 复制订阅
    old_subs = old_conn.execute("SELECT * FROM ali_subscribe ORDER BY id").fetchall()
    cols_sub = [
        'id', 'share_title', 'share_url', 'share_id', 'share_pwd', 'parent_file_id',
        'to_parent_id', 'to_file_name', 'filters', 'end_file_id', 'last_file_id',
        'last_file_name', 'last_update_at', 'last_file_no', 'total', 'status',
        'download', 'download_dir', 'copying', 'remark', 'created_at', 'updated_at'
    ]
    for row in old_subs:
        vals = [row[c] if c in row.keys() else '' for c in cols_sub]
        placeholders = ','.join(['?'] * len(cols_sub))
        cols_str = ','.join(cols_sub)
        new_conn.execute(f"INSERT OR REPLACE INTO ali_subscribe ({cols_str}) VALUES ({placeholders})", vals)
    print(f"✅ 复制了 {len(old_subs)} 个订阅")

    # 复制转存记录
    old_records = old_conn.execute("SELECT * FROM ali_record ORDER BY id").fetchall()
    cols_rec = [
        'id', 'subscribe_id', 'share_file_name', 'share_file_id',
        'to_file_name', 'to_file_id', 'to_file_size', 'created_at', 'updated_at'
    ]
    for row in old_records:
        vals = [row[c] if c in row.keys() else '' for c in cols_rec]
        placeholders = ','.join(['?'] * len(cols_rec))
        cols_str = ','.join(cols_rec)
        new_conn.execute(f"INSERT OR REPLACE INTO ali_record ({cols_str}) VALUES ({placeholders})", vals)
    print(f"✅ 复制了 {len(old_records)} 条转存记录")

    # 保存 refresh_token
    try:
        old_config = old_conn.execute("SELECT refresh_token FROM ali_config WHERE id=1").fetchone()
        if old_config and old_config[0]:
            new_conn.execute("INSERT OR REPLACE INTO alisub_config (key, value) VALUES (?, ?)",
                           ("refresh_token", old_config[0]))
            print(f"✅ 保存了 refresh_token")
    except:
        pass

    old_conn.close()
    new_conn.commit()
    new_conn.close()
    print(f"✅ 迁移完成！新数据库: {NEW_DB}")


if __name__ == "__main__":
    migrate()
