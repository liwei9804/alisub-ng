#!/usr/bin/env python3
"""
集数检测器 - 从文件名中提取集数
"""

import re
import os
import logging

log = logging.getLogger("alisub-ng.detector")

# 默认集数正则（按优先级排列）
DEFAULT_PATTERNS = [
    # S01E01 格式
    r'[Ss]\d{1,2}[Ee](\d{1,4})',
    # EP01 格式
    r'[Ee][Pp](\d{1,4})',
    # 第01话/集/期
    r'第(\d{1,4})[话集期]',
    # _01_ 格式
    r'_(\d{1,4})_',
    # 01 4K / 01_4K / 01-4K / 01.4K (数字后跟4K)
    r'(\d{1,4})[\s_.\-]?4[Kk]',
    # 【01】格式
    r'【(\d{1,4})】',
    # [01] 格式
    r'\[(\d{1,4})\]',
    # 文件名开头的数字（如 "24" → 24, "01 4K" 已被上面匹配）
    r'^(\d{1,4})$',
    # 文件名末尾的数字（如 "xxx 24" → 24）
    r'\s(\d{1,4})$',
    r'^(\d{1,4})\s',
]

# 要忽略的数字（仅在特定上下文中出现时才忽略）
IGNORE_NUMBERS = {
    2160, 1920, 1080, 720, 480, 360, 240,  # 分辨率
    2020, 2021, 2022, 2023, 2024, 2025, 2026, 2027,  # 年份
}


def extract_episode(filename: str, custom_regex: str = "") -> int:
    """
    从文件名中提取集数

    Args:
        filename: 文件名（不含扩展名或含扩展名均可）
        custom_regex: 自定义正则（需包含一个捕获组）

    Returns:
        集数（整数），未识别返回 0
    """
    # 先去掉扩展名用于匹配
    name = os.path.splitext(filename)[0] if '.' in filename else filename

    # 如果有自定义正则，优先使用
    if custom_regex:
        try:
            m = re.search(custom_regex, name, re.IGNORECASE)
            if m:
                num = int(m.group(1))
                if 1 <= num <= 9999:
                    return num
        except re.error as e:
            log.warning(f"自定义正则错误: {e}")

    # 使用默认正则
    for pattern in DEFAULT_PATTERNS:
        m = re.search(pattern, name, re.IGNORECASE)
        if m:
            num = int(m.group(1))
            if 1 <= num <= 9999 and num not in IGNORE_NUMBERS:
                return num

    return 0


def test_detector():
    """测试集数检测"""
    test_cases = [
        ("01 4K.mp4", 1),
        ("22 4K.mp4", 22),
        ("S01E05.mp4", 5),
        ("S02E15.mkv", 15),
        ("EP12.mp4", 12),
        ("第03话.mp4", 3),
        ("[08].mp4", 8),
        ("【16】.mp4", 16),
        ("九门 (2026).S01E22.mp4", 22),
        ("24.mp4", 24),
        ("15 4K.mkv", 15),
    ]
    for name, expected in test_cases:
        result = extract_episode(name)
        status = "✅" if result == expected else "❌"
        print(f"  {status} {name} → {result} (期望 {expected})")


if __name__ == "__main__":
    test_detector()
