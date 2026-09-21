#!/usr/bin/env python3
"""
数据库模型 - SQLite

直接操作 alisub-ng.db 中的 ali_subscribe / ali_record 表。
"""

import sqlite3
import os
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("alisub-ng.models")

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "alisub-ng.db"))


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """确保 ali_subscribe 和 ali_record 表存在"""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ali_subscribe (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            check_days TEXT DEFAULT '',
            upgrade_quality INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS ali_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscribe_id INTEGER NOT NULL,
            share_file_id TEXT NOT NULL,
            share_file_name TEXT NOT NULL,
            to_file_name TEXT DEFAULT '',
            to_file_id TEXT DEFAULT '',
            to_file_size INTEGER DEFAULT 0,
            status TEXT DEFAULT 'done',
            error_msg TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_ali_record_subscribe ON ali_record(subscribe_id);
        CREATE INDEX IF NOT EXISTS idx_ali_record_share_file ON ali_record(share_file_id);
        CREATE INDEX IF NOT EXISTS idx_ali_record_status ON ali_record(status);
    """)
    conn.commit()
    conn.close()
    log.info("✅ 数据库初始化完成")


# ─── 订阅 CRUD ──────────────────────────────────────────

# 字段映射：web.py / api 用 name → ali_subscribe 用 share_title
_SUB_MAP = {
    "name": "share_title",
    "last_check_at": "last_update_at",
}


def _map_sub_fields(kwargs: dict) -> dict:
    """把外部字段名映射到 ali_subscribe 实际列名"""
    mapped = {}
    for k, v in kwargs.items():
        mapped[_SUB_MAP.get(k, k)] = v
    return mapped


def add_subscription(name: str, share_url: str, share_id: str, parent_file_id: str,
                     to_parent_id: str, share_pwd: str = "",
                     to_file_name: str = "",
                     season: int = 1, episode_regex: str = "") -> int:
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO ali_subscribe (share_title, share_url, share_id, share_pwd, parent_file_id,
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
    row = conn.execute("SELECT * FROM ali_subscribe WHERE id=?", (sub_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_subscriptions(status: Optional[int] = None) -> list:
    conn = get_conn()
    if status is not None:
        rows = conn.execute(
            "SELECT * FROM ali_subscribe WHERE status=? ORDER BY id", (str(status),)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM ali_subscribe ORDER BY id").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["name"] = d.get("share_title", "")
        result.append(d)
    return result


def update_subscription(sub_id: int, **kwargs):
    if not kwargs:
        return
    kwargs = _map_sub_fields(kwargs)
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [sub_id]
    conn = get_conn()
    conn.execute(f"UPDATE ali_subscribe SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def delete_subscription(sub_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM ali_record WHERE subscribe_id=?", (sub_id,))
    conn.execute("DELETE FROM ali_subscribe WHERE id=?", (sub_id,))
    conn.commit()
    conn.close()


# ─── 转存记录 ──────────────────────────────────────────

def add_record(subscribe_id: int, share_file_id: str, share_file_name: str,
               episode_num: int = 0) -> int:
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO ali_record (subscribe_id, share_file_id, share_file_name, status)
        VALUES (?, ?, ?, 'done')
    """, (subscribe_id, share_file_id, share_file_name))
    rec_id = cur.lastrowid
    conn.commit()
    conn.close()
    return rec_id


def update_record(rec_id: int, **kwargs):
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [rec_id]
    conn = get_conn()
    conn.execute(f"UPDATE ali_record SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def get_record_by_share_file(share_file_id: str) -> Optional[dict]:
    """检查文件是否已转存"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM ali_record WHERE share_file_id=? AND status='done'",
        (share_file_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_records(subscribe_id: int, status: Optional[str] = None) -> list:
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM ali_record WHERE subscribe_id=? AND status=? ORDER BY id",
            (subscribe_id, status)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ali_record WHERE subscribe_id=? ORDER BY id",
            (subscribe_id,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_existing_episodes(subscribe_id: int) -> dict:
    """获取已转存的集数 → 文件名映射（从 to_file_name 提取集数）"""
    import re
    conn = get_conn()
    rows = conn.execute("""
        SELECT share_file_name, to_file_name, to_file_id
        FROM ali_record
        WHERE subscribe_id=? AND status='done'
        ORDER BY id
    """, (subscribe_id,)).fetchall()
    conn.close()
    result = {}
    for r in rows:
        m = re.search(r'S\d+E(\d+)', r["to_file_name"])
        if m:
            ep = int(m.group(1))
            result[ep] = {"to_file_name": r["to_file_name"], "to_file_id": r["to_file_id"]}
    return result
