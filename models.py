#!/usr/bin/env python3
"""
数据库模型 - SQLite
"""

import sqlite3
import os
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("alisub-ng.models")

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库"""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            share_url TEXT NOT NULL,
            share_id TEXT NOT NULL,
            share_pwd TEXT DEFAULT '',
            parent_file_id TEXT NOT NULL,
            to_parent_id TEXT NOT NULL,
            to_file_name TEXT DEFAULT '{title}.S{season}E{episode:02d}{ext}',
            season INTEGER DEFAULT 1,
            episode_regex TEXT DEFAULT '',
            status INTEGER DEFAULT 1,
            last_file_id TEXT DEFAULT '',
            last_file_name TEXT DEFAULT '',
            last_check_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscribe_id INTEGER NOT NULL,
            share_file_id TEXT NOT NULL,
            share_file_name TEXT NOT NULL,
            to_file_id TEXT DEFAULT '',
            to_file_name TEXT DEFAULT '',
            episode_num INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            error_msg TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (subscribe_id) REFERENCES subscriptions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_records_subscribe ON records(subscribe_id);
        CREATE INDEX IF NOT EXISTS idx_records_share_file ON records(share_file_id);
        CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
    """)
    conn.commit()
    conn.close()
    log.info("✅ 数据库初始化完成")


# ─── 订阅 CRUD ──────────────────────────────────────────

def add_subscription(name: str, share_url: str, share_id: str, parent_file_id: str,
                     to_parent_id: str, share_pwd: str = "",
                     to_file_name: str = "{title}.S{season}E{episode:02d}{ext}",
                     season: int = 1, episode_regex: str = "") -> int:
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO subscriptions (name, share_url, share_id, share_pwd, parent_file_id,
                                   to_parent_id, to_file_name, season, episode_regex)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (name, share_url, share_id, share_pwd, parent_file_id,
          to_parent_id, to_file_name, season, episode_regex))
    sub_id = cur.lastrowid
    conn.commit()
    conn.close()
    log.info(f"✅ 添加订阅: {name} (ID={sub_id})")
    return sub_id


def get_subscription(sub_id: int) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_subscriptions(status: Optional[int] = None) -> list:
    conn = get_conn()
    if status is not None:
        rows = conn.execute("SELECT * FROM subscriptions WHERE status=? ORDER BY id", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM subscriptions ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_subscription(sub_id: int, **kwargs):
    if not kwargs:
        return
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [sub_id]
    conn = get_conn()
    conn.execute(f"UPDATE subscriptions SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def delete_subscription(sub_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM records WHERE subscribe_id=?", (sub_id,))
    conn.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
    conn.commit()
    conn.close()


# ─── 转存记录 ──────────────────────────────────────────

def add_record(subscribe_id: int, share_file_id: str, share_file_name: str,
               episode_num: int = 0) -> int:
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO records (subscribe_id, share_file_id, share_file_name, episode_num)
        VALUES (?, ?, ?, ?)
    """, (subscribe_id, share_file_id, share_file_name, episode_num))
    rec_id = cur.lastrowid
    conn.commit()
    conn.close()
    return rec_id


def update_record(rec_id: int, **kwargs):
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [rec_id]
    conn = get_conn()
    conn.execute(f"UPDATE records SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def get_record_by_share_file(share_file_id: str) -> Optional[dict]:
    """检查文件是否已转存"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM records WHERE share_file_id=? AND status='done'",
        (share_file_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_records(subscribe_id: int, status: Optional[str] = None) -> list:
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM records WHERE subscribe_id=? AND status=? ORDER BY episode_num",
            (subscribe_id, status)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM records WHERE subscribe_id=? ORDER BY episode_num",
            (subscribe_id,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_existing_episodes(subscribe_id: int) -> dict:
    """获取已转存的集数 → 文件名映射"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT episode_num, to_file_name, to_file_id
        FROM records
        WHERE subscribe_id=? AND status='done' AND episode_num > 0
        ORDER BY episode_num
    """, (subscribe_id,)).fetchall()
    conn.close()
    return {r["episode_num"]: dict(r) for r in rows}
