#!/usr/bin/env python3
"""
TMDB 影视匹配模块
"""

import time
import logging
import requests

log = logging.getLogger("alisub-ng.tmdb")

TMDB_BASE = "https://api.themoviedb.org/3"
API_KEY = ""  # 启动时设置


def set_api_key(key):
    global API_KEY
    API_KEY = key


def search_movie(query):
    """搜索电影"""
    if not API_KEY:
        return []
    try:
        resp = requests.get(f"{TMDB_BASE}/search/movie", params={
            "api_key": API_KEY, "query": query, "language": "zh-CN"
        }, timeout=10)
        data = resp.json()
        return data.get("results", [])
    except Exception as e:
        log.warning(f"TMDB 电影搜索失败 [{query}]: {e}")
        return []


def search_tv(query):
    """搜索剧集"""
    if not API_KEY:
        return []
    try:
        resp = requests.get(f"{TMDB_BASE}/search/tv", params={
            "api_key": API_KEY, "query": query, "language": "zh-CN"
        }, timeout=10)
        data = resp.json()
        return data.get("results", [])
    except Exception as e:
        log.warning(f"TMDB 剧集搜索失败 [{query}]: {e}")
        return []


def get_tv_details(tv_id):
    """获取剧集详情（含季信息等）"""
    if not API_KEY:
        return {}
    try:
        resp = requests.get(f"{TMDB_BASE}/tv/{tv_id}", params={
            "api_key": API_KEY, "language": "zh-CN"
        }, timeout=10)
        return resp.json()
    except Exception as e:
        log.warning(f"TMDB 剧集详情失败 [{tv_id}]: {e}")
        return {}


def get_season_details(tv_id, season_number):
    """获取某一季的详情（含每集信息）"""
    if not API_KEY:
        return {}
    try:
        resp = requests.get(f"{TMDB_BASE}/tv/{tv_id}/season/{season_number}", params={
            "api_key": API_KEY, "language": "zh-CN"
        }, timeout=10)
        return resp.json()
    except Exception as e:
        log.warning(f"TMDB 季详情失败 [{tv_id} S{season_number}]: {e}")
        return {}


def get_season_episode_map(tv_id):
    """获取剧集的每季集数映射
    返回: [{season: 1, episodes: 12}, {season: 2, episodes: 12}, ...]
    """
    details = get_tv_details(tv_id)
    seasons = details.get("seasons", [])
    season_map = []
    for s in seasons:
        sn = s.get("season_number", 0)
        if sn < 1:  # 跳过 specials (特别篇)
            continue
        ep_count = s.get("episode_count", 0)
        if ep_count > 0:
            season_map.append({"season": sn, "episodes": ep_count})
    season_map.sort(key=lambda x: x["season"])
    return season_map


def map_episode_to_season(season_map, total_episode):
    """将总集数映射到 季+集
    例如: season_map=[{s:1,ep:12},{s:2,ep:12}], total_episode=25
    → {"season": 3, "episode": 1}

    注意: 这里是"跨季连续编号"的映射逻辑。
    如果文件本身就是 S05E105 格式，则不需要调用此函数。
    """
    cumulative = 0
    for sm in season_map:
        if total_episode <= cumulative + sm["episodes"]:
            return {
                "season": sm["season"],
                "episode": total_episode - cumulative,
            }
        cumulative += sm["episodes"]
    # 超出已知集数，归属到最后一季
    if season_map:
        last = season_map[-1]
        return {"season": last["season"], "episode": total_episode - cumulative + last["episodes"]}
    return {"season": 1, "episode": total_episode}


def is_anime(tv_result, details=None):
    """判断是否为动漫"""
    # 检查 TMDB 类型标签
    genre_ids = tv_result.get("genre_ids", [])
    # 16 = Animation
    if 16 in genre_ids:
        return True
    if details:
        genres = [g.get("id") for g in details.get("genres", [])]
        if 16 in genres:
            return True
        # 原始语言为日语
        if details.get("original_language") == "ja":
            return True
    return False


def match_file(filename, folder_name=""):
    """匹配单个文件，返回匹配结果。优先用 folder_name 匹配。"""
    from media_parser import extract_search_keyword
    keyword = folder_name if folder_name else extract_search_keyword(filename)
    if not keyword:
        return None

    time.sleep(0.25)  # TMDB 频率限制

    movies = search_movie(keyword)
    tvs = search_tv(keyword)

    # 选最佳结果
    best = None
    best_type = None

    if movies and tvs:
        m_score = movies[0].get("popularity", 0)
        t_score = tvs[0].get("popularity", 0)
        if m_score >= t_score:
            best = movies[0]
            best_type = "movie"
        else:
            best = tvs[0]
            best_type = "tv"
    elif movies:
        best = movies[0]
        best_type = "movie"
    elif tvs:
        best = tvs[0]
        best_type = "tv"
    else:
        return None

    title = best.get("title") or best.get("name", "")
    title_cn = best.get("title") or best.get("name", "")
    original = best.get("original_title") or best.get("original_name", "")
    year = (best.get("release_date") or best.get("first_air_date", ""))[:4]
    tmdb_id = best.get("id")

    category = "电影"
    season_map = []
    if best_type == "tv":
        details = get_tv_details(tmdb_id)
        if is_anime(best, details):
            category = "动漫"
        else:
            category = "电视剧"
        # 获取季集映射
        seasons = details.get("seasons", [])
        for s in seasons:
            sn = s.get("season_number", 0)
            if sn < 1:
                continue
            ep_count = s.get("episode_count", 0)
            if ep_count > 0:
                season_map.append({"season": sn, "episodes": ep_count})
        season_map.sort(key=lambda x: x["season"])

    return {
        "tmdb_id": tmdb_id,
        "title": title,
        "title_original": original,
        "year": year,
        "type": best_type,
        "category": category,
        "keyword": keyword,
        "season_map": season_map,
    }