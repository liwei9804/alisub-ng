#!/usr/bin/env python3
"""
阿里云盘 v2 Open API 客户端
"""

import time
import logging
import requests
from typing import Optional

log = logging.getLogger("alisub-ng.api")


class AliyunDriveAPI:
    """阿里云盘 v2 Open API"""

    AUTH_URL = "https://auth.aliyundrive.com/v2/account/token"
    API_BASE = "https://api.aliyundrive.com"
    V2_SHARE_BASE = "https://api.aliyundrive.com/v2/share_link"

    def __init__(self, refresh_token: str, drive_id: Optional[str] = None):
        self.refresh_token = refresh_token
        self.drive_id = drive_id
        self.access_token = None
        self.token_expires = 0
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
        })
        self._ensure_token()

    def _ensure_token(self):
        """确保 access_token 有效"""
        if self.access_token and time.time() < self.token_expires - 60:
            return
        self._refresh_access_token()

    def _refresh_access_token(self):
        """刷新 access_token"""
        resp = requests.post(self.AUTH_URL, json={
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }, timeout=15)
        data = resp.json()
        if "access_token" not in data:
            raise Exception(f"Token 刷新失败: {data}")
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token", self.refresh_token)
        self.token_expires = time.time() + data.get("expires_in", 7200)
        if not self.drive_id:
            self.drive_id = data["default_drive_id"]
        log.info(f"✅ Token 刷新成功, drive_id={self.drive_id}")

    def _headers(self):
        self._ensure_token()
        return {"Authorization": f"Bearer {self.access_token}"}

    def _api(self, method: str, path: str, **kwargs) -> dict:
        """通用 API 调用"""
        url = f"{self.API_BASE}{path}"
        kwargs.setdefault("headers", {}).update(self._headers())
        kwargs.setdefault("timeout", 30)
        resp = getattr(self.session, method)(url, **kwargs)
        # 204 No Content 或空响应
        if resp.status_code in (204, 202) or not resp.text.strip():
            return {"code": "OK"}
        data = resp.json()
        if data.get("code") and data["code"] not in ("OK", "NotFound.FileId"):
            log.warning(f"API 错误 [{path}]: {data}")
        return data

    # ─── 文件操作 ──────────────────────────────────────────

    def list_files(self, parent_file_id: str, drive_id: Optional[str] = None,
                   limit: int = 200, marker: str = "") -> list:
        """列出目录下所有文件"""
        did = drive_id or self.drive_id
        all_items = []
        while True:
            body = {
                "drive_id": did,
                "parent_file_id": parent_file_id,
                "limit": limit,
            }
            if marker:
                body["marker"] = marker
            data = self._api("post", "/v2/file/list", json=body)
            items = data.get("items", [])
            all_items.extend(items)
            if not data.get("next_marker"):
                break
            marker = data["next_marker"]
        return all_items

    def get_file(self, file_id: str, drive_id: Optional[str] = None) -> dict:
        """获取文件信息"""
        did = drive_id or self.drive_id
        return self._api("post", "/v2/file/get", json={
            "drive_id": did,
            "file_id": file_id,
        })

    def rename_file(self, file_id: str, new_name: str, drive_id: Optional[str] = None) -> bool:
        """重命名文件"""
        did = drive_id or self.drive_id
        data = self._api("post", "/v2/file/update", json={
            "drive_id": did,
            "file_id": file_id,
            "name": new_name,
        })
        return data.get("file_id") == file_id

    def delete_file(self, file_id: str, drive_id: Optional[str] = None) -> bool:
        """删除文件"""
        did = drive_id or self.drive_id
        data = self._api("post", "/v2/file/delete", json={
            "drive_id": did,
            "file_id": file_id,
        })
        return "domain_id" in data or data.get("code") == "OK"

    # ─── 分享链接操作 ──────────────────────────────────────

    def get_share_token(self, share_id: str, share_pwd: str = "") -> str:
        """获取分享 token"""
        body = {"share_id": share_id}
        if share_pwd:
            body["share_pwd"] = share_pwd
        resp = requests.post(
            f"{self.V2_SHARE_BASE}/get_share_token",
            json=body, timeout=15,
        )
        data = resp.json()
        token = data.get("share_token")
        if not token:
            raise Exception(f"获取 share_token 失败: {data}")
        return token

    def list_share_files(self, share_id: str, parent_file_id: str,
                         share_pwd: str = "", limit: int = 200) -> list:
        """列出分享目录下的文件"""
        share_token = self.get_share_token(share_id, share_pwd)
        headers = {"x-share-token": share_token}
        all_items = []
        marker = ""
        while True:
            body = {
                "share_id": share_id,
                "parent_file_id": parent_file_id,
                "limit": limit,
            }
            if marker:
                body["marker"] = marker
            resp = requests.post(
                "https://api.aliyundrive.com/adrive/v2/file/list",
                json=body, headers=headers, timeout=30,
            )
            data = resp.json()
            items = data.get("items", [])
            all_items.extend(items)
            if not data.get("next_marker"):
                break
            marker = data["next_marker"]
        return all_items

    def save_share_files(self, share_id: str, file_ids: list,
                         to_parent_id: str, share_pwd: str = "",
                         new_names: Optional[list] = None) -> dict:
        """将分享文件逐个复制到自己的网盘（使用 v2/file/copy）

        使用已验证可用的 API：user token + x-share-token 调用 v2/file/copy
        """
        share_token = self.get_share_token(share_id, share_pwd)
        results = []

        for i, fid in enumerate(file_ids):
            body = {
                "share_id": share_id,
                "drive_id": "",  # 会从 share_token 自动获取
                "file_id": fid,
                "to_drive_id": self.drive_id,
                "to_parent_file_id": to_parent_id,
                "auto_rename": False,
            }
            if new_names and i < len(new_names):
                body["new_name"] = new_names[i]

            resp = self.session.post(
                "https://api.aliyundrive.com/v2/file/copy",
                json=body,
                headers={**self._headers(), "x-share-token": share_token},
                timeout=60,
            )
            data = resp.json()
            log.info(f"保存结果 [{fid}]: {data}")

            if resp.status_code == 201 or data.get("file_id"):
                results.append({
                    "file_id": data.get("file_id", ""),
                    "name": data.get("name", ""),
                })
            elif resp.status_code == 409 or "already exists" in str(data).lower():
                log.warning(f"文件已存在: {data}")
                results.append({"file_id": "", "name": "", "existed": True})
            else:
                raise Exception(f"保存失败: {data}")

            time.sleep(1)

        return {"results": results}

    # ─── 创建目录 ──────────────────────────────────────────

    def create_folder(self, parent_file_id: str, name: str,
                      drive_id: Optional[str] = None) -> dict:
        """创建目录"""
        did = drive_id or self.drive_id
        data = self._api("post", "/v2/file/create", json={
            "drive_id": did,
            "parent_file_id": parent_file_id,
            "name": name,
            "type": "folder",
            "check_name_mode": "auto_rename",
        })
        return data

    # ─── Token 管理 ────────────────────────────────────────

    def update_refresh_token(self, new_token: str):
        """更新 refresh_token（用于持久化）"""
        self.refresh_token = new_token

    def get_current_refresh_token(self) -> str:
        """获取当前 refresh_token"""
        return self.refresh_token
