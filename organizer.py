#!/usr/bin/env python3
"""
影视整理引擎 — 扫描、匹配、整理云盘文件
"""

import os
import re
import time
import logging
from media_parser import is_video_file, parse_filename, parse_season_from_folder, clean_folder_name
from tmdb_matcher import match_file, map_episode_to_season

log = logging.getLogger("alisub-ng.organizer")

# 日志回调，由 app.py 注入
_ol = lambda msg, color='#d4d4d4': None  # 默认空操作

DIR_MOVIES = "电影"
DIR_ANIME = "动漫"
DIR_TV = "电视剧"


def scan_directory(api, dir_id, max_depth=3):
    """递归扫描目录下所有视频文件，最深 max_depth 层"""
    video_files = []
    _scan_recursive(api, dir_id, video_files, parent_name="", depth=0, max_depth=max_depth)
    log.info(f"扫描到 {len(video_files)} 个视频文件")
    _ol(f"✅ 扫描完成，共 {len(video_files)} 个视频文件", '#52c41a')
    return video_files


def _is_episode_range_folder(name):
    """判断文件夹名是否是纯集数分组，如 '1-99集'、'01-50'、'第1-20集' 等"""
    patterns = [
        r'^\d{1,4}\s*[-~]\s*\d{1,4}\s*集?$',      # 1-99集, 01-50, 1~99
        r'^第\d{1,4}\s*[-~]\s*第?\d{1,4}\s*集$',    # 第1-第99集, 第1-99集
        r'^[Ee][Pp]?\d{1,4}\s*[-~]\s*[Ee]?[Pp]?\d{1,4}$',  # EP01-EP50, E1-E50
        r'^[Ss]\d{1,2}$',                             # S01, S1 (单独的季文件夹)
        r'^Season\s*\d{1,2}$',                         # Season 1, Season 01
        r'^第[一二三四五六七八九十百千]+季$',              # 第一季
        r'^[\[【\(（].+[\]】\)）]$',                   # 纯括号内容如 [AVC]
    ]
    for p in patterns:
        if re.match(p, name.strip()):
            return True
    return False


def _scan_recursive(api, dir_id, result, parent_name="", depth=0, max_depth=3):
    """递归扫描辅助函数"""
    if depth > max_depth:
        return
    files = api.list_files(dir_id)
    for f in files:
        name = f.get("name", "")
        fid = f.get("file_id", "")
        ftype = f.get("type", "")
        if ftype == "folder":
            if _is_episode_range_folder(name):
                # 集数分组文件夹，跳过不匹配，直接把文件归属到父目录
                _ol(f"  📂 {name}（集数分组，归属到 {parent_name or '上级目录'}）", '#999')
                _scan_recursive(api, fid, result, parent_name=parent_name, depth=depth + 1, max_depth=max_depth)
            else:
                _ol(f"  📂 扫描文件夹: {name}", '#999')
                _scan_recursive(api, fid, result, parent_name=name, depth=depth + 1, max_depth=max_depth)
        elif ftype == "file" and is_video_file(name):
            result.append({
                "file_id": fid,
                "name": name,
                "size": f.get("size", 0),
                "folder_name": parent_name,
            })


def match_files(files):
    """批量匹配文件 — 按文件夹分组，每组只搜索 TMDB 一次"""
    # 按 folder_name 分组
    groups = {}
    for f in files:
        key = f.get("folder_name", "") or ""
        if key not in groups:
            groups[key] = []
        groups[key].append(f)

    _ol(f"共 {len(files)} 个文件，分为 {len(groups)} 组，每组只匹配一次", '#1890ff')

    results = []
    for folder_name, group_files in groups.items():
        # 每组只搜索一次 TMDB
        if folder_name:
            search_name = clean_folder_name(folder_name)
        else:
            search_name = parse_filename(group_files[0]["name"])["title"]

        _ol(f"📁 {folder_name or '(根目录)'} → 搜索: {search_name} ({len(group_files)}个文件)", '#d4d4d4')

        try:
            match = match_file(group_files[0]["name"], folder_name=search_name)
        except Exception as e:
            log.warning(f"匹配失败 [{search_name}]: {e}")
            _ol(f"⚠️ 匹配失败: {search_name}", '#faad14')
            match = None

        if match:
            _ol(f"  → {match['category']}: {match['title']} ({match.get('year','')})", '#1890ff')
        else:
            _ol(f"  → 未匹配", '#999')

        # 组内所有文件共享同一个 match 结果
        for f in group_files:
            parsed = parse_filename(f["name"])
            results.append({
                **f,
                "parsed": parsed,
                "match": match,
            })

    return results


def _build_target_name(title, year, tmdb_id, season, episode, ext):
    """生成标准文件名
    格式: 剧名（年份）[tmdbid-XXXXX].S05E105.ext
    """
    year_part = f"（{year}）" if year else ""
    tmdb_part = f"[tmdbid-{tmdb_id}]" if tmdb_id else ""
    se_part = f".S{season:02d}E{episode:03d}" if season and episode else ""
    return f"{title}{year_part}{tmdb_part}{se_part}{ext}"


def generate_plan(matches):
    """生成整理方案（target_path 是相对路径，不含输出目录ID）"""
    plan = []
    for item in matches:
        match = item.get("match")
        parsed = item.get("parsed", {})
        name = item["name"]
        file_id = item["file_id"]
        ext = os.path.splitext(name)[1]

        if not match:
            plan.append({
                "file_id": file_id,
                "name": name,
                "action": "skip",
                "reason": "未匹配到",
                "target_path": "",
            })
            continue

        category = match["category"]
        title = match["title"]
        year = match.get("year", "")
        tmdb_id = match.get("tmdb_id", "")
        season_map = match.get("season_map", [])

        # 解析文件中的集数
        episode_from_file = parsed.get("episode")
        season_from_file = parsed.get("season")
        # 从文件夹名解析季数（如 "斗破苍穹 第五季" → 5）
        folder_season = parse_season_from_folder(item.get("folder_name", ""))

        if category == "电影":
            # 电影: 电影/片名（年份）[tmdbid-xxx]/片名（年份）[tmdbid-xxx].ext
            folder_name = _build_target_name(title, year, tmdb_id, None, None, "")
            target_name = _build_target_name(title, year, tmdb_id, None, None, ext)
            target_path = f"{DIR_MOVIES}/{folder_name}"
        else:
            # 剧集/动漫
            dir_name = DIR_ANIME if category == "动漫" else DIR_TV

            # 确定季和集
            if folder_season and episode_from_file:
                # 文件夹名含季数（如 "第五季"），文件集数就是该季内的集数
                season = folder_season
                episode = episode_from_file
            elif season_from_file and episode_from_file:
                # 文件已有 S01E05 格式，直接用
                season = season_from_file
                episode = episode_from_file
            elif episode_from_file and season_map:
                # 只有集数（如 "105"），通过 TMDB 季集映射推算
                mapped = map_episode_to_season(season_map, episode_from_file)
                season = mapped["season"]
                episode = mapped["episode"]
            elif episode_from_file:
                # 没有 season_map，默认第1季
                season = 1
                episode = episode_from_file
            else:
                # 无法解析集数
                season = 1
                episode = None

            # 生成新文件名
            target_name = _build_target_name(title, year, tmdb_id, season, episode, ext)

            # 目标路径: 动漫/剧名（年份）[tmdbid-xxx]/
            folder_name = _build_target_name(title, year, tmdb_id, None, None, "")
            target_path = f"{dir_name}/{folder_name}"

        plan.append({
            "file_id": file_id,
            "name": name,
            "action": "move",
            "reason": f"→ {category}",
            "target_path": target_path,
            "target_name": target_name,
            "match": match,
        })

    return plan


def execute_plan(api, plan, base_id="root"):
    """执行整理方案"""
    results = []
    dir_cache = {}
    total = len([p for p in plan if p['action'] == 'move'])
    done = 0
    _ol(f"🚀 开始执行，共 {total} 个文件需要移动", '#52c41a')

    for i, item in enumerate(plan):
        name = item["name"]
        file_id = item["file_id"]

        if item["action"] == "skip":
            _ol(f"  ⏭️ 跳过: {name} ({item['reason']})", '#999')
            results.append({"name": name, "status": "skipped", "reason": item["reason"]})
            continue

        target_path = item["target_path"]
        target_name = item.get("target_name", name)

        try:
            parent_id = _ensure_dir(api, target_path, dir_cache, base_id)
            # 移动文件
            api._api("post", "/v2/file/move", json={
                "drive_id": api.drive_id,
                "file_id": file_id,
                "to_parent_file_id": parent_id,
                "to_drive_id": api.drive_id,
            })
            # 重命名（新文件名 ≠ 原文件名时）
            if target_name != name:
                time.sleep(0.5)
                api.rename_file(file_id, target_name)
            done += 1
            _ol(f"  ✅ [{done}/{total}] {name} → {target_name}", '#52c41a')
            results.append({"name": name, "status": "ok", "target": f"{target_path}/{target_name}"})
        except Exception as e:
            log.error(f"❌ [{i+1}/{len(plan)}] {name}: {e}")
            _ol(f"  ❌ 失败: {name} - {e}", '#ff4d4f')
            results.append({"name": name, "status": "error", "reason": str(e)})

        time.sleep(0.5)

    _ol(f"🎉 整理完成！成功 {done} 个，失败 {len(results)-done} 个", '#52c41a')
    return results


def _ensure_dir(api, path, cache, base_id="root"):
    """确保目录层级存在，返回最后一级目录的 file_id"""
    parts = [p for p in path.split("/") if p]
    current_id = base_id

    for part in parts:
        cache_key = f"{current_id}/{part}"
        if cache_key in cache:
            current_id = cache[cache_key]
            continue

        # 查找子目录
        children = api.list_files(current_id)
        found = False
        for child in children:
            if child.get("name") == part and child.get("type") == "folder":
                current_id = child["file_id"]
                cache[cache_key] = current_id
                found = True
                break

        if not found:
            result = api.create_folder(current_id, part)
            current_id = result.get("file_id", "")
            cache[cache_key] = current_id
            time.sleep(0.3)

    return current_id