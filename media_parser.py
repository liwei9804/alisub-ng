#!/usr/bin/env python3
"""
文件名解析模块 — 从文件名中提取影片信息
"""

import re
import os

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".rmvb", ".ts", ".flv", ".wmv", ".mov", ".m4v", ".webm", ".iso"}


def is_video_file(filename):
    """判断是否为视频文件"""
    ext = os.path.splitext(filename)[1].lower()
    return ext in VIDEO_EXTS


def parse_filename(filename):
    """解析文件名，提取 剧名/片名、季数、集数"""
    name = os.path.splitext(filename)[0]  # 去掉扩展名

    # 清理常见标签
    name = re.sub(r'\[.*?\]', ' ', name)  # 去掉 [字幕组] 等标签
    name = re.sub(r'\(.*?\)', ' ', name)  # 去掉 (信息) 等
    name = re.sub(r'【.*?】', ' ', name)  # 去掉【】标签

    season = None
    episode = None

    # S01E01 格式
    m = re.search(r'[Ss](\d{1,2})[Ee](\d{1,3})', name)
    if m:
        season = int(m.group(1))
        episode = int(m.group(2))
    else:
        # EP01 / E01
        m = re.search(r'(?:EP|Ep|ep|E)(\d{1,3})', name)
        if m:
            episode = int(m.group(1))
        else:
            # 第01集 / 第1集
            m = re.search(r'第(\d{1,4})[集话]', name)
            if m:
                episode = int(m.group(1))
            else:
                # [01] 或 - 01
                m = re.search(r'(?:\[|\- )(\d{2,3})(?:\]|$)', name)
                if m:
                    episode = int(m.group(1))
                else:
                    # 纯数字开头如 "105 4K"
                    m = re.match(r'(\d{1,4})(?:[\s._\-]|$)', name)
                    if m:
                        episode = int(m.group(1))

    # 季数单独匹配 S01
    if season is None:
        m = re.search(r'[Ss](\d{1,2})', name)
        if m:
            season = int(m.group(1))

    # 提取标题：去掉 S/E/第xx集 等之后的剩余部分
    title = name
    # 去掉 S01E01, S01, EP01, E01, 第01集 等
    title = re.sub(r'[Ss]\d{1,2}[Ee]\d{1,3}', '', title)
    title = re.sub(r'[Ss]\d{1,2}', '', title)
    title = re.sub(r'(?:EP?|ep?)\s*\d{1,3}', '', title)
    title = re.sub(r'第\d{1,4}[集话]', '', title)
    title = re.sub(r'\d{1,3}(?:\.|\])\s*$', '', title)  # 尾部集数
    title = re.sub(r'\-\s*\d{1,3}\s*$', '', title)

    # 清理多余分隔符
    title = re.sub(r'[\s._\-]+', ' ', title).strip()
    title = title.strip(' -._')

    return {
        "title": title,
        "season": season,
        "episode": episode,
        "has_episode": episode is not None,
    }


def parse_season_from_folder(folder_name):
    """从文件夹名解析季数
    支持: '第五季'、'S05'、'Season 5'、'第5季' 等
    """
    if not folder_name:
        return None
    # S05 格式
    m = re.search(r'[Ss](\d{1,2})', folder_name)
    if m:
        return int(m.group(1))
    # Season 5 格式
    m = re.search(r'[Ss]eason\s*(\d{1,2})', folder_name, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # 第5季 / 第五季 格式
    cn_nums = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,
               '十一':11,'十二':12,'十三':13,'十四':14,'十五':15,'十六':16,'十七':17,'十八':18,'十九':19,'二十':20}
    # 先试中文数字
    m = re.search(r'第([一二三四五六七八九十百千]+)季', folder_name)
    if m:
        cn = m.group(1)
        if cn in cn_nums:
            return cn_nums[cn]
    # 再试阿拉伯数字
    m = re.search(r'第(\d{1,2})季', folder_name)
    if m:
        return int(m.group(1))
    return None


def clean_folder_name(folder_name):
    """清理文件夹名，提取用于 TMDB 搜索的剧名
    'D 斗破苍穹 第五季' → '斗破苍穹'
    '4K 三体 S01' → '三体'
    '进击的巨人 Season 3' → '进击的巨人'
    """
    if not folder_name:
        return ""
    name = folder_name

    # 去掉方括号内容 [xxx]
    name = re.sub(r'\[.*?\]', ' ', name)
    # 去掉圆括号内容 (xxx)
    name = re.sub(r'\(.*?\)', ' ', name)

    # 去掉季数信息
    name = re.sub(r'第[一二三四五六七八九十百千0-9]+季', ' ', name)
    name = re.sub(r'[Ss]\d{1,2}', ' ', name)
    name = re.sub(r'[Ss]eason\s*\d{1,2}', ' ', name, flags=re.IGNORECASE)

    # 去掉常见质量/编码标签
    noise_words = [
        r'4K', r'2160[Pp]', r'1080[Pp]', r'720[Pp]', r'2[Kk]',
        r'HEVC', r'H\.?265', r'H\.?264', r'AVC', r'x265', r'x264',
        r'HDR', r'DV', r'Dolby', r'Atmos', r'DTS', r'FLAC', r'AC3',
        r'AAC', r'WEB-?DL', r'BluRay', r'BDRip', r'REMUX',
        r'DDP?\d?.?\d?', r'MKV', r'MP4',
    ]
    for nw in noise_words:
        name = re.sub(r'(?<![a-zA-Z\u4e00-\u9fff])' + nw + r'(?![a-zA-Z\u4e00-\u9fff])', ' ', name, flags=re.IGNORECASE)

    # 去掉首尾的单字母/数字前缀（如 "D " 或 "W我的"）
    name = re.sub(r'^[A-Za-z0-9](?:\s+|(?=[\u4e00-\u9fff]))', '', name)

    # 清理多余空格
    name = re.sub(r'\s+', ' ', name).strip()
    name = name.strip(' -._')

    return name


def extract_search_keyword(filename):
    """从文件名中提取用于 TMDB 搜索的关键词"""
    parsed = parse_filename(filename)
    return parsed["title"]
