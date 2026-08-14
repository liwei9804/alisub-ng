#!/usr/bin/env python3
"""
转存引擎 - 核心逻辑：检查新文件 → 转存 → 重命名 → 验证 → 去重
"""

import os
import re
import time
import logging
from datetime import datetime
from typing import Optional

from api import AliyunDriveAPI
from detector import extract_episode

log = logging.getLogger("alisub-ng.transfer")


def parse_share_url(url: str) -> tuple:
    """从分享链接提取 share_id 和 parent_file_id"""
    m = re.search(r'/s/([a-zA-Z0-9]+)(?:/folder/([a-zA-Z0-9]+))?', url)
    if m:
        return m.group(1), m.group(2) or "root"
    raise ValueError(f"无法解析分享链接: {url}")


class TransferEngine:
    """转存引擎"""

    def __init__(self, api: AliyunDriveAPI):
        self.api = api

    def check_and_transfer(self, sub: dict, record_callback=None) -> list:
        """检查订阅并转存新文件

        Args:
            sub: 订阅配置字典
            record_callback: 回调函数 (sub_id, share_file_id, share_file_name, to_file_id, to_file_name, status, error)

        Returns:
            转存成功的列表 [{"share_file_name", "to_file_name", "episode"}]
        """
        name = sub.get("share_title") or sub.get("name", "")
        share_url = sub["share_url"]
        share_pwd = sub.get("share_pwd", "")
        to_parent_id = sub["to_parent_id"]
        to_file_name_tpl = sub.get("to_file_name", "S01E")
        episode_regex = sub.get("episode_regex", "")

        try:
            share_id, parent_file_id = parse_share_url(share_url)
        except ValueError as e:
            log.error(f"[{name}] 分享链接解析失败: {e}")
            return []

        log.info(f"🔍 检查订阅: {name}")

        # 1. 列出分享目录的媒体文件
        try:
            share_files = self.api.list_share_files(share_id, parent_file_id, share_pwd)
        except Exception as e:
            log.error(f"❌ [{name}] 列出分享文件失败: {e}")
            return []

        media_exts = {'.mp4', '.mkv', '.avi', '.ts', '.flv', '.rmvb'}
        media_files = [
            f for f in share_files
            if f.get("type") == "file" and
               any(f.get("name", "").lower().endswith(ext) for ext in media_exts)
        ]

        if not media_files:
            log.info(f"  无媒体文件")
            return []

        log.info(f"  分享目录有 {len(media_files)} 个媒体文件")

        # 2. 检查哪些已转存（通过 alisub 数据库 ali_record）
        existing_share_ids = self._get_existing_share_ids(sub.get("id", 0))
        log.info(f"  已转存 {len(existing_share_ids)} 个")

        # 3. 获取目标目录现有文件（用于去重判断）
        dest_files = {}
        dest_episodes = {}  # 集数 → {"file_id", "name"} 完整信息
        try:
            dest_items = self.api.list_files(to_parent_id)
            dest_files = {f["name"]: f for f in dest_items}
            # 按集数索引目标目录文件
            for f in dest_items:
                dep = extract_episode(f["name"], "")
                if dep > 0:
                    # 同集数保留最优版本（V2 优于 V1）
                    if dep not in dest_episodes or self._is_better_version(f["name"], dest_episodes[dep]["name"]):
                        dest_episodes[dep] = f
        except Exception as e:
            log.warning(f"  ⚠️ 获取目标目录失败: {e}")

        # 4. 找出需要转存的新文件（同一集只保留最佳版本）
        to_transfer = []
        batch_episodes = {}  # episode → item，用于同批去重
        for sf in media_files:
            share_file_id = sf["file_id"]
            share_file_name = sf["name"]
            share_file_size = sf.get("size", 0)

            # 提取集数
            ep = extract_episode(share_file_name, episode_regex)
            if ep == 0:
                log.warning(f"  ⚠️ 无法识别集数: {share_file_name}")
                continue

            # 计算目标文件名
            expected_name = self._format_name(to_file_name_tpl, name, ep, share_file_name)

            # 已转存过（通过数据库记录）— 但仍需检查目标目录实际文件
            if share_file_id in existing_share_ids:
                # 检查目标目录是否真有这个文件
                dest_file = dest_files.get(expected_name)
                if dest_file and dest_file.get("size", 0) >= share_file_size:
                    # 目标文件存在且大小正常，真正跳过
                    continue
                # 目标文件不存在或比源文件小，需要重新转存
                if dest_file and dest_file.get("size", 0) < share_file_size:
                    log.info(f"  🔄 E{ep:02d}: 目标文件比源文件小（{dest_file['size']//1024//1024}MB < {share_file_size//1024//1024}MB），重新转存")
                    try:
                        self.api.delete_file(dest_file["file_id"])
                        log.info(f"  🗑️ 已删除: {expected_name}")
                        dest_files.pop(expected_name, None)
                        time.sleep(1)
                    except Exception as e:
                        log.warning(f"  ⚠️ 删除失败: {e}")
                        continue
                    sf["reason"] = f"画质升级: {dest_file['size']//1024//1024}MB → {share_file_size//1024//1024}MB"
                elif not dest_file:
                    # 目标文件不存在，检查同集数文件
                    if ep in dest_episodes:
                        existing_dest = dest_episodes[ep]
                        existing_size = existing_dest.get("size", 0)
                        if share_file_size > existing_size:
                            log.info(f"  🔄 E{ep:02d}: 同版本更大文件（{share_file_size//1024//1024}MB > {existing_size//1024//1024}MB），替换 {existing_dest['name']}")
                            try:
                                self.api.delete_file(existing_dest["file_id"])
                                log.info(f"  🗑️ 已删除: {existing_dest['name']}")
                                dest_files.pop(existing_dest["name"], None)
                                time.sleep(1)
                            except Exception as e:
                                log.warning(f"  ⚠️ 删除失败: {e}")
                                continue
                            del dest_episodes[ep]
                            sf["reason"] = f"画质升级: {existing_size//1024//1024}MB → {share_file_size//1024//1024}MB"
                        else:
                            continue

            # 同批同集数去重：优先保留 V2 版本，其次保留无括号后缀的
            if ep in batch_episodes:
                existing = batch_episodes[ep]
                if self._is_better_version(share_file_name, existing["share_file_name"]):
                    log.info(f"  替换 E{ep:02d}: {share_file_name} 优于 {existing['share_file_name']}")
                    batch_episodes[ep] = {
                        "share_file_id": share_file_id,
                        "share_file_name": share_file_name,
                        "episode": ep,
                        "expected_name": expected_name,
                    }
                else:
                    log.info(f"  跳过 E{ep:02d}: {share_file_name}（已有更优版本 {existing['share_file_name']}）")
                continue

            batch_episodes[ep] = {
                "share_file_id": share_file_id,
                "share_file_name": share_file_name,
                "episode": ep,
                "expected_name": expected_name,
            }

        to_transfer = list(batch_episodes.values())

        if not to_transfer:
            log.info(f"  ✅ 无新文件需要转存")
            return []

        log.info(f"  📥 发现 {len(to_transfer)} 个新文件:")
        for t in to_transfer:
            log.info(f"    E{t['episode']:02d}: {t['share_file_name']} → {t['expected_name']}")

        # 5. 逐个转存
        results = []
        for item in to_transfer:
            result = self._transfer_one(
                share_id=share_id,
                share_pwd=share_pwd,
                sub_name=name,
                item=item,
                to_parent_id=to_parent_id,
                sub_id=sub.get("id", 0),
                record_callback=record_callback,
            )
            if result:
                results.append(result)
            time.sleep(2)  # 限流

        # 6. 转存后去重
        if results:
            self.cleanup_duplicates(name, to_parent_id)

        return results

    def _transfer_one(self, share_id, share_pwd, sub_name, item,
                      to_parent_id, sub_id=0, record_callback=None) -> Optional[dict]:
        """转存单个文件"""
        share_file_id = item["share_file_id"]
        share_file_name = item["share_file_name"]
        ep = item["episode"]
        expected_name = item["expected_name"]

        try:
            # v2/file/copy 转存
            log.info(f"  📤 转存: {share_file_name} → {expected_name}")
            result = self.api.save_share_files(
                share_id=share_id,
                file_ids=[share_file_id],
                to_parent_id=to_parent_id,
                share_pwd=share_pwd,
                new_names=[expected_name],
            )

            saved = result.get("results", [{}])[0]
            to_file_id = saved.get("file_id", "")

            if saved.get("existed"):
                log.info(f"  ⏭️ 文件已存在，跳过")
                return None

            # 重命名验证
            if to_file_id:
                final_name = self._verify_and_rename(to_file_id, expected_name)
            else:
                final_name = expected_name

            # 记录到数据库
            if record_callback:
                record_callback(sub_id, share_file_id, share_file_name, to_file_id, final_name, "done", "")

            log.info(f"  ✅ 转存成功: {final_name}")
            result_item = {
                "share_file_id": share_file_id,
                "share_file_name": share_file_name,
                "to_file_name": final_name,
                "to_file_id": to_file_id,
                "episode": ep,
            }
            if item.get("reason"):
                result_item["reason"] = item["reason"]
            return result_item

        except Exception as e:
            error_msg = str(e)
            log.error(f"  ❌ 转存失败: {error_msg}")
            if record_callback:
                record_callback(sub_id, share_file_id, share_file_name, "", "", "failed", error_msg)
            return None

    def _verify_and_rename(self, file_id: str, expected_name: str, retries: int = 3) -> str:
        """验证文件名，不匹配则重命名，带重试"""
        for attempt in range(retries):
            try:
                info = self.api.get_file(file_id)
                actual_name = info.get("name", "")
                if actual_name == expected_name:
                    return actual_name

                log.info(f"  🔧 重命名: {actual_name} → {expected_name} (尝试 {attempt+1})")
                ok = self.api.rename_file(file_id, expected_name)
                if ok:
                    time.sleep(1)
                    info2 = self.api.get_file(file_id)
                    final = info2.get("name", expected_name)
                    if final == expected_name:
                        return final
                    log.warning(f"  ⚠️ 重命名未生效: {final}")
                else:
                    log.warning(f"  ⚠️ 重命名 API 返回失败")
            except Exception as e:
                log.warning(f"  ⚠️ 验证/重命名异常: {e}")
            time.sleep(2)

        return expected_name

    def cleanup_duplicates(self, title: str, to_parent_id: str) -> int:
        """清理目标目录中的重复文件"""
        try:
            files = self.api.list_files(to_parent_id)
        except Exception as e:
            log.warning(f"⚠️ [{title}] 去重列出文件失败: {e}")
            return 0

        media_exts = {'.mp4', '.mkv', '.avi', '.ts', '.flv', '.rmvb'}
        media_files = [f for f in files if any(f["name"].lower().endswith(ext) for ext in media_exts)]

        correct_files = {}
        numbered_files = []
        for f in media_files:
            fname = f["name"]
            ep = extract_episode(fname)
            if ep == 0:
                continue
            if title in fname or "S01E" in fname or "S0" in fname:
                correct_files.setdefault(ep, f)
            else:
                numbered_files.append((ep, f))

        deleted = 0
        for ep, f in numbered_files:
            if ep in correct_files:
                try:
                    self.api.delete_file(f["file_id"])
                    log.info(f"  🗑️ 删除重复: {f['name']} (E{ep:02d} 已有 {correct_files[ep]['name']})")
                    deleted += 1
                    time.sleep(0.5)
                except Exception as e:
                    log.warning(f"  ⚠️ 删除失败 {f['name']}: {e}")

        if deleted:
            log.info(f"  ✅ [{title}] 去重清理 {deleted} 个文件")
        return deleted

    @staticmethod
    def _is_better_version(new_name: str, existing_name: str) -> bool:
        """判断新文件是否比已有文件更优

        优先级：V2/V3 等版本号 > 无括号后缀 > 带 (1)/(2) 后缀
        """
        import re
        def _score(name: str) -> tuple:
            n = name.lower()
            # 提取版本号 V2, V3 等
            ver_match = re.search(r'[. ]v(\d+)', n)
            ver = int(ver_match.group(1)) if ver_match else 1
            # 是否带括号后缀 (1), (2) 等（视为重复文件，优先级最低）
            has_dup = bool(re.search(r'\(\d+\)', n))
            return (ver, 0 if has_dup else 1)

        return _score(new_name) > _score(existing_name)

    @staticmethod
    def _format_name(tpl: str, title: str, episode: int, original_name: str) -> str:
        """格式化文件名

        alisub 模板格式通常是 "S01E" 或 "吞噬星空 - S01E"，直接拼接集数+扩展名
        """
        ext = os.path.splitext(original_name)[1] or ".mp4"
        if tpl:
            return tpl + str(episode) + ext
        return f"{title} - S01E{episode}{ext}"

    def _get_existing_share_ids(self, sub_id: int) -> set:
        """从 alisub 数据库获取已转存的 share_file_id 集合"""
        import sqlite3
        db_path = os.path.join(os.path.dirname(__file__), "data", "alisub-ng.db")
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT share_file_id FROM ali_record WHERE subscribe_id=?",
                (sub_id,)
            ).fetchall()
            conn.close()
            return {r[0] for r in rows}
        except:
            return set()
