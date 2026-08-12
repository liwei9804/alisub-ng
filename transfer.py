#!/usr/bin/env python3
"""
转存引擎 - 核心逻辑：去重、转存、重命名、验证
"""

import re
import time
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse, parse_qs

from api import AliyunDriveAPI
from detector import extract_episode
import models

log = logging.getLogger("alisub-ng.transfer")


def parse_share_url(url: str) -> tuple:
    """从分享链接提取 share_id 和 parent_file_id

    Returns:
        (share_id, parent_file_id)
    """
    # https://www.alipan.com/s/XXX/folder/YYY
    m = re.search(r'/s/([a-zA-Z0-9]+)(?:/folder/([a-zA-Z0-9]+))?', url)
    if m:
        share_id = m.group(1)
        parent_file_id = m.group(2) or "root"
        return share_id, parent_file_id
    raise ValueError(f"无法解析分享链接: {url}")


class TransferEngine:
    """转存引擎"""

    def __init__(self, api: AliyunDriveAPI):
        self.api = api

    def check_and_transfer(self, sub: dict) -> list:
        """检查订阅并转存新文件

        Args:
            sub: 订阅配置字典

        Returns:
            转存成功的记录列表
        """
        sub_id = sub["id"]
        name = sub["name"]
        share_id = sub["share_id"]
        share_pwd = sub.get("share_pwd", "")
        parent_file_id = sub["parent_file_id"]
        to_parent_id = sub["to_parent_id"]
        to_file_name_tpl = sub.get("to_file_name", "{title}.S{season}E{episode:02d}{ext}")
        season = sub.get("season", 1)
        episode_regex = sub.get("episode_regex", "")

        log.info(f"🔍 检查订阅: {name}")

        # 1. 列出分享目录文件
        try:
            share_files = self.api.list_share_files(share_id, parent_file_id, share_pwd)
        except Exception as e:
            log.error(f"❌ [{name}] 列出分享文件失败: {e}")
            return []

        if not share_files:
            log.info(f"  分享目录为空")
            return []

        # 2. 过滤出媒体文件
        media_exts = {'.mp4', '.mkv', '.avi', '.ts', '.flv', '.rmvb', '.wmv', '.mov'}
        media_files = [
            f for f in share_files
            if f.get("type") == "file" and
               any(f.get("name", "").lower().endswith(ext) for ext in media_exts)
        ]

        if not media_files:
            log.info(f"  无媒体文件")
            return []

        log.info(f"  分享目录有 {len(media_files)} 个媒体文件")

        # 3. 获取已转存的集数
        existing = models.get_existing_episodes(sub_id)
        log.info(f"  已转存 {len(existing)} 集: {sorted(existing.keys())}")

        # 4. 获取目标目录现有文件（用于去重）
        dest_files = {}
        try:
            dest_items = self.api.list_files(to_parent_id)
            dest_files = {f["name"]: f for f in dest_items}
        except Exception as e:
            log.warning(f"  ⚠️ 获取目标目录失败: {e}")

        # 5. 找出需要转存的新文件
        to_transfer = []
        for sf in media_files:
            share_file_id = sf["file_id"]
            share_file_name = sf["name"]

            # 检查是否已转存（通过 share_file_id）
            if models.get_record_by_share_file(share_file_id):
                continue

            # 提取集数
            ep = extract_episode(share_file_name, episode_regex)
            if ep == 0:
                log.warning(f"  ⚠️ 无法识别集数: {share_file_name}")
                continue

            # 检查该集数是否已有正确命名的文件
            if ep in existing:
                log.info(f"  跳过 E{ep:02d}: 已转存 ({existing[ep]['to_file_name']})")
                continue

            # 检查目标目录是否已有该集数的文件（通过文件名匹配）
            expected_name = self._format_name(to_file_name_tpl, name, season, ep, share_file_name)
            if expected_name in dest_files:
                log.info(f"  跳过 E{ep:02d}: 目标目录已有 {expected_name}")
                # 记录到数据库
                models.add_record(sub_id, share_file_id, share_file_name, ep)
                models.update_record(
                    models.get_record_by_share_file(share_file_id) or 0,
                    to_file_id=dest_files[expected_name]["file_id"],
                    to_file_name=expected_name,
                    status="done"
                )
                continue

            to_transfer.append({
                "share_file_id": share_file_id,
                "share_file_name": share_file_name,
                "episode": ep,
                "expected_name": expected_name,
            })

        if not to_transfer:
            log.info(f"  ✅ 无新文件需要转存")
            models.update_subscription(sub_id, last_check_at=datetime.utcnow().isoformat())
            return []

        log.info(f"  📥 发现 {len(to_transfer)} 个新文件:")
        for t in to_transfer:
            log.info(f"    {t['share_file_name']} → {t['expected_name']} (E{t['episode']:02d})")

        # 6. 逐个转存（避免批量 API 失败导致全部丢失）
        results = []
        for item in to_transfer:
            result = self._transfer_one(
                sub_id, share_id, share_pwd, name,
                item, to_parent_id, to_file_name_tpl, season
            )
            if result:
                results.append(result)
            time.sleep(2)  # 避免 API 限流

        # 7. 更新订阅状态
        if to_transfer:
            last = to_transfer[-1]
            models.update_subscription(
                sub_id,
                last_file_id=last["share_file_id"],
                last_file_name=last["share_file_name"],
                last_check_at=datetime.utcnow().isoformat(),
            )

        return results

    def _transfer_one(self, sub_id: int, share_id: str, share_pwd: str,
                      sub_name: str, item: dict, to_parent_id: str,
                      to_file_name_tpl: str, season: int) -> Optional[dict]:
        """转存单个文件"""
        share_file_id = item["share_file_id"]
        share_file_name = item["share_file_name"]
        ep = item["episode"]
        expected_name = item["expected_name"]

        # 创建转存记录
        rec_id = models.add_record(sub_id, share_file_id, share_file_name, ep)

        try:
            # 转存文件（带重命名）
            log.info(f"  📤 转存: {share_file_name} → {expected_name}")
            result = self.api.save_share_files(
                share_id=share_id,
                file_ids=[share_file_id],
                to_parent_id=to_parent_id,
                share_pwd=share_pwd,
                new_names=[expected_name],
            )

            # 检查结果
            saved_files = result.get("results") or result.get("saved_file_list", [])
            if saved_files:
                to_file_id = saved_files[0].get("file_id", "")
                to_file_name = saved_files[0].get("name", expected_name)
            elif result.get("task_id"):
                # 异步任务，等待完成
                log.info(f"  ⏳ 等待异步任务完成...")
                time.sleep(5)
                # 查找目标文件
                to_file_id, to_file_name = self._find_transferred_file(
                    to_parent_id, expected_name, share_file_name
                )
            else:
                to_file_id = ""
                to_file_name = expected_name

            # 验证并重命名
            if to_file_id:
                actual_name = self._verify_and_rename(
                    to_file_id, expected_name, to_parent_id
                )
                models.update_record(
                    rec_id,
                    to_file_id=to_file_id,
                    to_file_name=actual_name,
                    status="done",
                )
                log.info(f"  ✅ 转存成功: {actual_name}")
                return {
                    "share_file_name": share_file_name,
                    "to_file_name": actual_name,
                    "episode": ep,
                }
            else:
                # 没有 file_id，但 API 没报错，可能成功了
                models.update_record(rec_id, to_file_name=expected_name, status="done")
                log.info(f"  ✅ 转存完成（无 file_id 返回）")
                return {
                    "share_file_name": share_file_name,
                    "to_file_name": expected_name,
                    "episode": ep,
                }

        except Exception as e:
            error_msg = str(e)
            log.error(f"  ❌ 转存失败: {error_msg}")
            models.update_record(rec_id, status="failed", error_msg=error_msg)
            return None

    def _find_transferred_file(self, parent_id: str, expected_name: str,
                               share_file_name: str) -> tuple:
        """在目标目录查找刚转存的文件"""
        try:
            files = self.api.list_files(parent_id)
            # 先找完全匹配的
            for f in files:
                if f["name"] == expected_name:
                    return f["file_id"], f["name"]
            # 再找原始文件名的
            for f in files:
                if f["name"] == share_file_name:
                    return f["file_id"], f["name"]
        except Exception as e:
            log.warning(f"  查找文件失败: {e}")
        return "", expected_name

    def _verify_and_rename(self, file_id: str, expected_name: str,
                           parent_id: str) -> str:
        """验证文件名，不匹配则重命名"""
        try:
            info = self.api.get_file(file_id)
            actual_name = info.get("name", "")
            if actual_name == expected_name:
                return actual_name

            log.info(f"  🔧 重命名: {actual_name} → {expected_name}")
            ok = self.api.rename_file(file_id, expected_name)
            if ok:
                # 验证重命名是否生效
                time.sleep(1)
                info2 = self.api.get_file(file_id)
                final_name = info2.get("name", expected_name)
                if final_name != expected_name:
                    log.warning(f"  ⚠️ 重命名未生效: {final_name}")
                return final_name
            else:
                log.warning(f"  ⚠️ 重命名 API 返回失败")
                return actual_name
        except Exception as e:
            log.warning(f"  ⚠️ 验证/重命名异常: {e}")
            return expected_name

    @staticmethod
    def _format_name(template: str, title: str, season: int,
                     episode: int, original_name: str) -> str:
        """格式化文件名

        支持变量:
            {title}    - 订阅标题
            {season}   - 季数
            {episode}  - 集数
            {episode:02d} - 集数（补零）
            {ext}      - 原始扩展名
        """
        import os
        ext = os.path.splitext(original_name)[1] or ".mp4"
        try:
            name = template.format(
                title=title,
                season=season,
                episode=episode,
                ext=ext,
            )
        except (KeyError, ValueError) as e:
            log.warning(f"文件名模板错误: {e}, 使用默认格式")
            name = f"{title}.S{season:02d}E{episode:02d}{ext}"
        return name

    def cleanup_duplicates(self, sub: dict) -> int:
        """清理目标目录中的重复文件（旧的未改名文件）

        策略：如果目录中同时存在「01 4K.mp4」和「九门 (2026).S01E01.mp4」，
        删除「01 4K.mp4」
        """
        sub_id = sub["id"]
        name = sub["name"]
        to_parent_id = sub["to_parent_id"]
        to_file_name_tpl = sub.get("to_file_name", "{title}.S{season}E{episode:02d}{ext}")
        season = sub.get("season", 1)
        episode_regex = sub.get("episode_regex", "")

        try:
            files = self.api.list_files(to_parent_id)
        except Exception as e:
            log.error(f"❌ [{name}] 列出文件失败: {e}")
            return 0

        # 分类：正确命名 vs 编号命名
        correct_files = {}  # episode → file
        numbered_files = []  # (episode, file)

        media_exts = {'.mp4', '.mkv', '.avi', '.ts'}
        for f in files:
            fname = f["name"]
            if not any(fname.lower().endswith(ext) for ext in media_exts):
                continue

            # 检查是否是正确命名格式（包含标题）
            if name in fname or f"S{season:02d}E" in fname:
                ep = extract_episode(fname, episode_regex)
                if ep > 0:
                    correct_files[ep] = f
            else:
                # 可能是编号命名
                ep = extract_episode(fname, episode_regex)
                if ep > 0:
                    numbered_files.append((ep, f))

        # 删除有对应正确命名文件的编号文件
        deleted = 0
        for ep, f in numbered_files:
            if ep in correct_files:
                log.info(f"  🗑️ 删除重复: {f['name']} (E{ep:02d} 已有 {correct_files[ep]['name']})")
                try:
                    self.api.delete_file(f["file_id"])
                    deleted += 1
                except Exception as e:
                    log.error(f"  ❌ 删除失败: {e}")

        if deleted:
            log.info(f"  ✅ [{name}] 清理了 {deleted} 个重复文件")
        return deleted
