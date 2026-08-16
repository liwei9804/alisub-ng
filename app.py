#!/usr/bin/env python3
"""alisub-ng Web 管理系统"""

import os, sys, json, time, sqlite3, logging, requests, hashlib, base64
from urllib.parse import urlencode
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, send_from_directory, session, redirect, url_for

sys.path.insert(0, os.path.dirname(__file__))
from api import AliyunDriveAPI
from detector import extract_episode
from transfer import TransferEngine
from scheduler import Scheduler
from notifier import Notifier
import models

log_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(log_dir, "app.log"), encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("alisub-ng")

REFRESH_TOKEN = os.environ.get("REFRESH_TOKEN", "")
DRIVE_ID = os.environ.get("DRIVE_ID", "")
ALISUB_DB = os.path.join(os.path.dirname(__file__), "data", "alisub-ng.db")
PORT = int(os.environ.get("PORT", "8003"))

# ─── 云盘设置 ──────────────────────────────────────

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")


def load_settings():
    defaults = {"token": REFRESH_TOKEN, "drive_id": DRIVE_ID, "webhook": "", "interval": 3600}
    # 优先从新数据库读取 token
    try:
        conn = sqlite3.connect(ALISUB_DB)
        row = conn.execute("SELECT value FROM alisub_config WHERE key='refresh_token'").fetchone()
        if row and row[0]:
            defaults["token"] = row[0]
        conn.close()
    except:
        pass
    # 再从 settings.json 读取覆盖
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                saved = json.load(f)
                defaults.update(saved)
    except:
        pass
    return defaults


def save_settings(data):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "alisub-ng-secret-key-change-me")

# 默认账号密码
AUTH_USER = os.environ.get("AUTH_USER", "admin")
AUTH_PASS = os.environ.get("AUTH_PASS", "admin")

api = None
scheduler = None


def get_api():
    global api
    if api is None:
        s = load_settings()
        token = s.get("token", REFRESH_TOKEN)
        drive_id = s.get("drive_id", DRIVE_ID)
        api = AliyunDriveAPI(token, drive_id)
    return api


def get_scheduler():
    global scheduler
    s = load_settings()
    webhook = s.get("webhook", "")
    interval = s.get("interval", 3600)
    strm_webhook = s.get("strm_webhook", "")
    strm_tasks = s.get("strm_tasks", "")
    openlist_url = s.get("openlist_url", "")
    openlist_token = s.get("openlist_token", "")
    openlist_storage_id = int(s.get("openlist_storage_id", 0))
    notifier = Notifier(webhook, strm_webhook, strm_tasks, openlist_url, openlist_token, openlist_storage_id) if webhook or strm_webhook or openlist_url else None
    if scheduler is None:
        scheduler = Scheduler(get_api(), notifier, interval)
    else:
        # 更新 notifier（webhook 可能变了）
        scheduler.notifier = notifier
        scheduler.check_interval = interval
    return scheduler


# ─── 登录认证 ──────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"code": -1, "msg": "未登录"})
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == AUTH_USER and password == AUTH_PASS:
            session["logged_in"] = True
            return redirect("/")
        return render_template("login.html", error="账号或密码错误")
    if session.get("logged_in"):
        return redirect("/")
    return render_template("login.html", error="")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ─── alisub 数据库读取 ──────────────────────────────────

def alisub_list_subscriptions():
    conn = sqlite3.connect(ALISUB_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, share_title, share_url, share_pwd, to_parent_id,
               to_file_name, status, last_file_name, last_file_no, check_days, upgrade_quality, created_at
        FROM ali_subscribe ORDER BY id
    """).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["name"] = d["share_title"]
        st = int(d["status"]) if d["status"] else 0
        d["status"] = st
        d["status_text"] = {0: "未知", 1: "订阅中", 2: "暂停", 3: "失效", 4: "完结"}.get(st, "未知")
        d["status_color"] = {0: "#999", 1: "#52c41a", 2: "#faad14", 3: "#ff4d4f", 4: "#1890ff"}.get(st, "#999")
        d["rename_preview"] = _preview_rename(d["share_title"], d["to_file_name"], d["last_file_name"])
        result.append(d)
    return result


def _preview_rename(title, tpl, last_file):
    if not last_file:
        return ""
    import os
    ep = extract_episode(last_file)
    ext = os.path.splitext(last_file)[1] or ".mp4"
    if ep > 0:
        if tpl:
            return tpl + str(ep) + ext
        return f"{title} - S01E{ep}{ext}"
    return ""


def alisub_list_records(sub_id, limit=50):
    conn = sqlite3.connect(ALISUB_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, share_file_name, to_file_name, to_file_id, created_at
        FROM ali_record WHERE subscribe_id=? ORDER BY id DESC LIMIT ?
    """, (sub_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def alisub_count_records(sub_id):
    conn = sqlite3.connect(ALISUB_DB)
    row = conn.execute("SELECT COUNT(*) FROM ali_record WHERE subscribe_id=?", (sub_id,)).fetchone()
    conn.close()
    return row[0] if row else 0


# ─── 文件浏览器 ──────────────────────────────────────────

def list_drive_files(parent_id="root"):
    a = get_api()
    try:
        files = a.list_files(parent_id)
        files.sort(key=lambda x: (x.get("type", "") != "folder", x.get("name", "")))
        return files
    except Exception as e:
        log.error(f"列出文件失败: {e}")
        return []


# ─── 路由 ──────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/subscriptions")
@login_required
def api_subs():
    subs = alisub_list_subscriptions()
    for s in subs:
        s["record_count"] = alisub_count_records(s["id"])
    return jsonify({"code": 0, "data": subs})


@app.route("/api/subscriptions", methods=["POST"])
@login_required
def api_create_sub():
    data = request.json
    try:
        conn = sqlite3.connect(ALISUB_DB)
        # 从 share_url 提取 share_id
        share_url = data.get("share_url", "")
        share_id = ""
        if "s/" in share_url:
            share_id = share_url.split("s/")[1].split("?")[0].split("/")[0]
        elif "#" in share_url:
            share_id = share_url.split("#")[1].split("?")[0].split("/")[0]
        
        conn.execute("""
            INSERT INTO ali_subscribe (share_title, share_url, share_id, parent_file_id, to_parent_id, to_file_name, status, check_days, upgrade_quality, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """, (
            data.get("name", ""),
            share_url,
            share_id,
            "root",
            data.get("to_parent_id", ""),
            data.get("to_file_name", "S01E"),
            str(data.get("status", 1)),
            data.get("check_days", ""),
            1 if data.get("upgrade_quality") else 0,
        ))
        conn.commit()
        conn.close()
        return jsonify({"code": 0})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})


@app.route("/api/subscriptions/<int:sub_id>", methods=["PUT"])
@login_required
def api_update_sub(sub_id):
    data = request.json
    try:
        conn = sqlite3.connect(ALISUB_DB)
        updates, vals = [], []
        if "name" in data:
            updates.append("share_title=?"); vals.append(data["name"])
        if "share_url" in data:
            updates.append("share_url=?"); vals.append(data["share_url"])
        if "to_parent_id" in data:
            updates.append("to_parent_id=?"); vals.append(data["to_parent_id"])
        if "to_file_name" in data:
            updates.append("to_file_name=?"); vals.append(data["to_file_name"])
        if "status" in data:
            updates.append("status=?"); vals.append(str(data["status"]))
        if "last_file_no" in data:
            updates.append("last_file_no=?"); vals.append(int(data["last_file_no"]))
        if "check_days" in data:
            updates.append("check_days=?"); vals.append(data["check_days"])
        if "upgrade_quality" in data:
            updates.append("upgrade_quality=?"); vals.append(1 if data["upgrade_quality"] else 0)
        if updates:
            vals.append(sub_id)
            conn.execute(f"UPDATE ali_subscribe SET {','.join(updates)} WHERE id=?", vals)
            conn.commit()
        conn.close()
        return jsonify({"code": 0})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})


@app.route("/api/subscriptions/<int:sub_id>", methods=["DELETE"])
@login_required
def api_delete_sub(sub_id):
    try:
        conn = sqlite3.connect(ALISUB_DB)
        conn.execute("DELETE FROM ali_record WHERE subscribe_id=?", (sub_id,))
        conn.execute("DELETE FROM ali_subscribe WHERE id=?", (sub_id,))
        conn.commit()
        conn.close()
        return jsonify({"code": 0})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})


@app.route("/api/subscriptions/<int:sub_id>/records")
@login_required
def api_records(sub_id):
    return jsonify({"code": 0, "data": alisub_list_records(sub_id)})


@app.route("/api/files")
@login_required
def api_files():
    parent_id = request.args.get("parent_file_id", "root")
    return jsonify({"code": 0, "data": list_drive_files(parent_id)})


@app.route("/api/files/<file_id>/delete", methods=["POST"])
@login_required
def api_delete_file(file_id):
    try:
        get_api().delete_file(file_id)
        return jsonify({"code": 0})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})


@app.route("/api/files/batch-delete", methods=["POST"])
@login_required
def api_batch_delete():
    file_ids = request.json.get("file_ids", [])
    a = get_api()
    deleted = 0
    for fid in file_ids:
        try:
            a.delete_file(fid); deleted += 1; time.sleep(0.5)
        except Exception as e:
            log.error(f"删除失败 {fid}: {e}")
    return jsonify({"code": 0, "deleted": deleted})


@app.route("/api/cleanup/scan")
@login_required
def api_cleanup_scan():
    subs = alisub_list_subscriptions()
    a = get_api()
    media_exts = {'.mp4', '.mkv', '.avi', '.ts', '.flv', '.rmvb'}
    result = []
    for sub in subs:
        title = sub["name"]
        to_parent_id = sub["to_parent_id"]
        if not to_parent_id:
            continue
        try:
            files = a.list_files(to_parent_id)
        except:
            continue
        media_files = [f for f in files if any(f["name"].lower().endswith(ext) for ext in media_exts)]
        correct_files, numbered_files = {}, []
        for f in media_files:
            fname = f["name"]
            ep = extract_episode(fname)
            if ep == 0:
                continue
            if title in fname or "S01E" in fname or "S0" in fname:
                correct_files.setdefault(ep, f)
            else:
                numbered_files.append((ep, f))
        dup_files = []
        for ep, f in numbered_files:
            if ep in correct_files:
                dup_files.append({"old": f["name"], "old_id": f["file_id"], "new": correct_files[ep]["name"], "episode": ep})
        if dup_files:
            result.append({"name": title, "files": dup_files})
    return jsonify({"code": 0, "data": result})


@app.route("/api/health")
@login_required
def api_health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


# ─── 转存控制 ──────────────────────────────────────

@app.route("/api/subscriptions/<int:sub_id>/check", methods=["POST"])
@login_required
def api_check_sub(sub_id):
    """手动检查单个订阅"""
    try:
        s = get_scheduler()
        result = s.check_one(sub_id)
        return jsonify({"code": 0, "data": result})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})


@app.route("/api/check-all", methods=["POST"])
@login_required
def api_check_all():
    """手动检查所有订阅"""
    try:
        log.info("🔍 检查所有订阅中任务开始")
        s = get_scheduler()
        results = s.check_all()
        log.info(f"🔍 检查所有订阅中任务结束（{len(results)} 个有更新）")
        return jsonify({"code": 0, "data": {"count": len(results), "results": results}})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})


@app.route("/api/settings")
@login_required
def api_get_settings():
    s = load_settings()
    # 隐藏 token 中间部分
    display = dict(s)
    if display.get("token") and len(display["token"]) > 10:
        display["token"] = display["token"][:6] + "..." + display["token"][-4:]
    return jsonify({"code": 0, "data": display})


@app.route("/api/settings", methods=["POST"])
@login_required
def api_save_settings():
    data = request.json
    current = load_settings()
    # 只更新非空字段
    if data.get("token") and "..." not in data["token"]:
        current["token"] = data["token"]
    if data.get("drive_id"):
        current["drive_id"] = data["drive_id"]
    if "webhook" in data:
        current["webhook"] = data["webhook"]
    if data.get("interval"):
        current["interval"] = int(data["interval"])
    if "strm_webhook" in data:
        current["strm_webhook"] = data["strm_webhook"]
    if "strm_tasks" in data:
        current["strm_tasks"] = data["strm_tasks"]
    if "openlist_url" in data:
        current["openlist_url"] = data["openlist_url"]
    if "openlist_token" in data:
        current["openlist_token"] = data["openlist_token"]
    if "openlist_storage_id" in data:
        current["openlist_storage_id"] = int(data["openlist_storage_id"])
    # drive_id 为空时自动从 token 获取
    if not current.get("drive_id") and current.get("token"):
        try:
            resp = requests.post("https://auth.aliyundrive.com/v2/account/token", json={
                "grant_type": "refresh_token",
                "refresh_token": current["token"]
            }, timeout=15)
            td = resp.json()
            if td.get("default_drive_id"):
                current["drive_id"] = td["default_drive_id"]
        except:
            pass
    save_settings(current)
    # 重置 API 缓存，让新 token 生效
    global api, scheduler
    api = None
    scheduler = None
    return jsonify({"code": 0})


@app.route("/api/settings/test")
@login_required
def api_test_settings():
    s = load_settings()
    try:
        resp = requests.post("https://auth.aliyundrive.com/v2/account/token", json={
            "grant_type": "refresh_token",
            "refresh_token": s["token"]
        }, timeout=15)
        data = resp.json()
        if "access_token" in data:
            return jsonify({"code": 0, "data": {
                "user_name": data.get("user_name", ""),
                "drive_id": data.get("default_drive_id", ""),
            }})
        return jsonify({"code": -1, "msg": data.get("message", "token 无效")})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})


@app.route("/api/drives")
@login_required
def api_drives():
    """获取所有云盘列表（备份盘 + 资源盘）"""
    s = load_settings()
    try:
        resp = requests.post("https://auth.aliyundrive.com/v2/account/token", json={
            "grant_type": "refresh_token",
            "refresh_token": s["token"]
        }, timeout=15)
        td = resp.json()
        if "access_token" not in td:
            return jsonify({"code": -1, "msg": "token 无效"})
        access_token = td["access_token"]
        default_drive_id = td.get("default_drive_id", "")
        headers = {"Authorization": f"Bearer {access_token}"}
        drives = []
        # 备份盘
        try:
            r1 = requests.post("https://api.aliyundrive.com/v2/drive/get", json={"drive_id": default_drive_id}, headers=headers, timeout=15)
            d1 = r1.json()
            if "drive_id" in d1:
                drives.append({
                    "drive_id": d1["drive_id"],
                    "drive_name": "备份盘",
                    "used_size": d1.get("used_size", 0),
                    "total_size": d1.get("total_size", 0),
                })
        except:
            pass
        # 资源盘（默认 drive_name=resource）
        resource_drive_id = s.get("drive_id", "")
        if resource_drive_id != default_drive_id:
            try:
                r2 = requests.post("https://api.aliyundrive.com/v2/drive/get", json={"drive_id": resource_drive_id}, headers=headers, timeout=15)
                d2 = r2.json()
                if "drive_id" in d2:
                    drives.append({
                        "drive_id": d2["drive_id"],
                        "drive_name": "资源盘",
                        "used_size": d2.get("used_size", 0),
                        "total_size": d2.get("total_size", 0),
                    })
            except:
                pass
        return jsonify({"code": 0, "data": drives})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})


@app.route("/api/subscriptions/<int:sub_id>/share_files")
@login_required
def api_share_files(sub_id):
    """列出分享目录文件（用于选择截止集数）"""
    conn = sqlite3.connect(ALISUB_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM ali_subscribe WHERE id=?", (sub_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"code": -1, "msg": "订阅不存在"})
    sub = dict(row)
    share_url = sub["share_url"]
    share_pwd = sub.get("share_pwd") or ""
    try:
        from transfer import parse_share_url
        share_id, parent_file_id = parse_share_url(share_url)
        a = get_api()
        files = a.list_share_files(share_id, parent_file_id, share_pwd)
        media_exts = {'.mp4', '.mkv', '.avi', '.ts', '.flv', '.rmvb'}
        media = []
        for f in files:
            if f.get("type") == "file" and any(f.get("name","").lower().endswith(ext) for ext in media_exts):
                from detector import extract_episode as _ep
                ep = _ep(f.get("name",""))
                media.append({"file_id": f["file_id"], "name": f["name"], "episode": ep})
        media.sort(key=lambda x: x["episode"])
        return jsonify({"code": 0, "data": media})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})


@app.route("/api/logs")
@login_required
def api_logs():
    """读取 alisub-ng 转存日志"""
    log_path = os.path.join(os.path.dirname(__file__), "logs", "app.log")
    try:
        with open(log_path, encoding="utf-8") as f:
            lines = f.readlines()
        # 过滤转存相关日志，取最后200行
        keywords = ['转存', '发现更新', '暂无更新', '重命名', '失败', '成功', '新增', '删除', '📤', '✅', '❌', '📥', '跳过', '检查']
        filtered = []
        for line in lines[-500:]:
            line = line.strip()
            if any(k in line for k in keywords):
                filtered.append(line)
        return jsonify({"code": 0, "data": filtered[-200:]})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})


@app.route("/api/account")
@login_required
def api_account():
    """获取阿里云盘账户信息"""
    s = load_settings()
    try:
        resp = requests.post("https://auth.aliyundrive.com/v2/account/token", json={
            "grant_type": "refresh_token",
            "refresh_token": s["token"]
        }, timeout=15)
        td = resp.json()
        if "access_token" not in td:
            return jsonify({"code": -1, "msg": "token 无效"})

        access_token = td["access_token"]
        drive_id = td.get("default_drive_id", "")
        nick_name = td.get("nick_name", "")
        user_name = td.get("user_name", "")

        # 获取资源盘容量
        headers = {"Authorization": f"Bearer {access_token}"}
        resp2 = requests.post("https://api.aliyundrive.com/v2/drive/get", json={
            "drive_id": s.get("drive_id") or drive_id
        }, headers=headers, timeout=15)
        dd = resp2.json()

        used = dd.get("used_size", 0)
        total = dd.get("total_size", 0)
        used_gb = used / 1073741824
        total_gb = total / 1073741824
        pct = (used / total * 100) if total else 0

        # 格式化容量
        if total_gb >= 1024:
            used_str = f"{used_gb/1024:.2f} TB"
            total_str = f"{total_gb/1024:.2f} TB"
        else:
            used_str = f"{used_gb:.2f} GB"
            total_str = f"{total_gb:.2f} GB"

        return jsonify({"code": 0, "data": {
            "nick_name": nick_name,
            "user_name": user_name,
            "drive_id": s.get("drive_id") or drive_id,
            "drive_name": dd.get("drive_name", ""),
            "used_str": used_str,
            "total_str": total_str,
            "percent": round(pct, 2),
            "used_bytes": used,
            "total_bytes": total,
        }})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})


# ─── 扫码登录 ──────────────────────────────────────

@app.route("/api/qrcode/generate")
@login_required
def api_qrcode_generate():
    """生成阿里云盘扫码登录二维码"""
    try:
        resp = requests.post(
            "https://passport.aliyundrive.com/newlogin/qrcode/generate.do",
            data="appName=aliyun_drive&fromSite=52",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        data = resp.json()
        qr_data = data.get("content", {}).get("data", {})
        content = qr_data.get("codeContent", "")
        ck = qr_data.get("ck", "")
        t = qr_data.get("t", 0)
        if not content or not ck:
            return jsonify({"code": -1, "msg": "生成二维码失败", "raw": data})
        return jsonify({"code": 0, "data": {"content": content, "ck": ck, "t": t}})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})


@app.route("/api/qrcode/query", methods=["POST"])
@login_required
def api_qrcode_query():
    """查询扫码登录状态"""
    ck = request.json.get("ck", "")
    t = request.json.get("t", "")
    if not ck:
        return jsonify({"code": -1, "msg": "缺少 ck 参数"})
    try:
        resp = requests.post(
            "https://passport.aliyundrive.com/newlogin/qrcode/query.do",
            data=urlencode({"ck": ck, "appName": "aliyun_drive", "fromSite": "52", "t": str(t)}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        data = resp.json()
        qr_data = data.get("content", {}).get("data", {})
        status = qr_data.get("qrCodeStatus", "")
        if status == "CONFIRMED":
            # 登录成功，从 bizExt 提取 refresh_token
            biz_ext = qr_data.get("bizExt", "")
            if biz_ext:
                try:
                    decoded = base64.b64decode(biz_ext)
                    # 尝试多种编码
                    text = None
                    for enc in ("utf-8", "gbk", "latin-1"):
                        try:
                            text = decoded.decode(enc)
                            break
                        except:
                            continue
                    if not text:
                        text = decoded.decode("utf-8", errors="ignore")
                    biz_data = json.loads(text)
                    pds = biz_data.get("pds_login_result", {})
                    refresh_token = pds.get("refreshToken", "")
                    if refresh_token:
                        return jsonify({"code": 0, "data": {
                            "status": "CONFIRMED",
                            "refresh_token": refresh_token,
                            "user_name": pds.get("userName", ""),
                            "nick_name": pds.get("nickName", ""),
                        }})
                except Exception as e:
                    log.error(f"解析 bizExt 失败: {e}")
            return jsonify({"code": -1, "msg": "登录成功但获取 token 失败"})
        elif status == "NEW":
            return jsonify({"code": 0, "data": {"status": "NEW"}})
        elif status == "SCANED":
            return jsonify({"code": 0, "data": {"status": "SCANED"}})
        elif status == "EXPIRED":
            return jsonify({"code": 0, "data": {"status": "EXPIRED"}})
        else:
            return jsonify({"code": 0, "data": {"status": status or "UNKNOWN"}})
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})


if __name__ == "__main__":
    models.init_db()
    # 初始化 alisub 数据库表
    try:
        conn = sqlite3.connect(ALISUB_DB)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS ali_subscribe (
            id INTEGER PRIMARY KEY, share_title TEXT NOT NULL, share_url TEXT NOT NULL,
            share_id TEXT NOT NULL, share_pwd TEXT DEFAULT '', parent_file_id TEXT NOT NULL,
            to_parent_id TEXT NOT NULL, to_file_name TEXT DEFAULT '', filters TEXT DEFAULT '',
            end_file_id TEXT DEFAULT '', last_file_id TEXT DEFAULT '', last_file_name TEXT DEFAULT '',
            last_update_at TEXT DEFAULT '', last_file_no INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
            status VARCHAR(1) DEFAULT '1', download INTEGER DEFAULT 0, download_dir TEXT DEFAULT '',
            copying INTEGER DEFAULT 0, remark TEXT DEFAULT '', episode_regex TEXT DEFAULT '',
            season INTEGER DEFAULT 1, created_at TEXT DEFAULT '', updated_at TEXT DEFAULT ''
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS ali_record (
            id INTEGER PRIMARY KEY, subscribe_id INTEGER NOT NULL, share_file_id TEXT NOT NULL,
            share_file_name TEXT DEFAULT '', to_file_id TEXT DEFAULT '', to_file_name TEXT DEFAULT '',
            episode_num INTEGER DEFAULT 0, status TEXT DEFAULT 'done', error TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS alisub_config (
            key TEXT PRIMARY KEY, value TEXT DEFAULT ''
        )""")
        conn.commit()
        conn.close()
        log.info("✅ 数据库初始化完成")
    except Exception as e:
        log.warning(f"⚠️ 数据库初始化: {e}")
    # 自动启动调度器
    try:
        s = get_scheduler()
        s.start()
        log.info("⏰ 调度器已自动启动")
    except Exception as e:
        log.warning(f"⚠️ 调度器自动启动失败: {e}")
    log.info(f"🚀 alisub-ng Web 启动 - 端口 {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
# build trigger
