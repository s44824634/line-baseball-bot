"""球場天氣（Open-Meteo 免費 API，無需金鑰）與屋頂狀態。

若為固定室內球場則不查天氣，直接標示屋頂封閉。
"""
from __future__ import annotations

from typing import Optional

import requests

# 固定室內屋頂球場（不受天氣影響）
ROOF_FIXED = {
    "tropicana field", "globe life field", "daikin park",
    "東京ドーム", "东京巨蛋", "京セラドーム", "大阪巨蛋",
    "バンテリンドーム", "ナゴヤドーム", "名古屋巨蛋",
    "高尺天空巨蛋", "고척 스카이돔", "gocheok sky dome",
}

# 可開關式屋頂（狀態無法由資料確認，標示即可）
ROOF_RETRACTABLE = {
    "rogers center", "chase field", "minute maid park",
    "american family field", "loandepot park", "t-mobile park",
    "エスコンフィールド", "マリン", "zozoマリン", "paypayドーム",
    "みずほpaypayドーム", "福岡巨蛋",
}

# 球場名稱 → (緯度, 經度)；覆蓋 MLB / CPBL / NPB / KBO 主要球場
STADIUM_COORDS = {
    # MLB
    "dodger stadium": (34.0739, -118.2400),
    "yankee stadium": (40.8296, -73.9262),
    "fenway park": (42.3467, -71.0972),
    "wrigley field": (41.9484, -87.6553),
    "oracle park": (37.7786, -122.3893),
    "petco park": (32.7073, -117.1566),
    "truist park": (33.8908, -84.4678),
    "citizens bank park": (39.9061, -75.1665),
    "citi field": (40.7571, -73.8458),
    "nationals park": (38.8730, -77.0074),
    "loandepot park": (25.7781, -80.2196),
    "busch stadium": (38.6226, -90.1928),
    "coors field": (39.7561, -104.9942),
    "chase field": (33.4455, -112.0667),
    "comerica park": (42.3390, -83.0485),
    "daikin park": (29.7573, -95.3555),
    "minute maid park": (29.7573, -95.3555),
    "kauffman stadium": (39.0517, -94.4803),
    "angel stadium": (33.8003, -117.8827),
    "t-mobile park": (47.5914, -122.3325),
    "ringcentral coliseum": (37.7516, -122.2005),
    "oakland coliseum": (37.7516, -122.2005),
    "tropicana field": (27.7683, -82.6534),
    "globe life field": (32.7473, -97.0843),
    "camden yards": (39.2839, -76.6217),
    "oriole park at camden yards": (39.2839, -76.6217),
    "rogers centre": (43.6414, -79.3894),
    "rogers center": (43.6414, -79.3894),
    "progressive field": (41.4962, -81.6852),
    "target field": (44.9817, -93.2776),
    "guaranteed rate field": (41.8299, -87.6338),
    "pnc park": (40.4469, -80.0057),
    "great american ball park": (39.0975, -84.5071),
    "american family field": (43.0279, -87.9711),
    # CPBL
    "桃園": (25.0005, 121.2028), "樂天桃園": (25.0005, 121.2028),
    "洲際": (24.1995, 120.6849), "台中洲際": (24.1995, 120.6849),
    "台南": (22.9804, 120.2064), "台南市立": (22.9804, 120.2064),
    "新莊": (25.0413, 121.4480),
    "天母": (25.1145, 121.5330),
    "澄清湖": (22.6490, 120.3500),
    "斗六": (23.7111, 120.6011), "斗六棒球場": (23.7111, 120.6011),
    "花蓮": (23.9771, 121.6044),
    # NPB
    "東京ドーム": (35.7056, 139.7519), "東京巨蛋": (35.7056, 139.7519),
    "神宮": (35.6745, 139.7172), "明治神宮": (35.6745, 139.7172),
    "横浜": (35.4437, 139.6400), "横須賀": (35.2815, 139.6723),
    "甲子園": (34.7213, 135.3617),
    "マツダ": (34.4290, 132.5140), "マツダスタジアム": (34.4290, 132.5140),
    "ナゴヤ": (35.1855, 136.9475), "バンテリン": (35.1855, 136.9475),
    "zozoマリン": (35.6455, 140.0308), "マリン": (35.6455, 140.0308),
    "楽天生命パーク": (38.2564, 140.9021), "楽天モバイル": (38.2564, 140.9021),
    "西武ドーム": (35.7685, 139.4207), "ベルーナドーム": (35.7685, 139.4207),
    "京セラ": (34.6693, 135.4763), "京セラドーム": (34.6693, 135.4763),
    "ほっと神戸": (34.6693, 135.4763),
    "みずほpaypay": (33.5953, 130.3621), "paypayドーム": (33.5953, 130.3621),
    "エスコンフィールド": (43.9908, 141.5497),
    "広島": (34.4290, 132.5140), "広島市民": (34.4290, 132.5140),
    # KBO
    "잠실": (37.5123, 127.0728), "jamsil": (37.5123, 127.0728),
    "고척": (37.4982, 126.8671), "gocheok": (37.4982, 126.8671),
    "사직": (35.1940, 129.0615), "sajik": (35.1940, 129.0615),
    "대구": (35.8412, 128.6817), "라이온즈파크": (35.8412, 128.6817),
    "수원": (37.2978, 126.9714), "케이티위즈파크": (37.2978, 126.9714),
    "인천": (37.4367, 126.6933), "문학": (37.4367, 126.6933),
    "광주": (35.1687, 126.8885), "챔피언스필드": (35.1687, 126.8885),
    "창원": (35.2226, 128.5820), "nc파크": (35.2226, 128.5820),
    "대전": (36.3170, 127.4292), "한화생명이글스파크": (36.3170, 127.4292),
}


def roof_status(venue: Optional[str]) -> Optional[bool]:
    """True=固定室內頂 / False=開放或可開關頂 / None=無場地資訊。"""
    if not venue:
        return None
    v = venue.strip().lower()
    for name in ROOF_FIXED:
        if name in v:
            return True
    for name in ROOF_RETRACTABLE:
        if name in v:
            return False
    return False


def _coords(venue: str):
    v = venue.strip().lower()
    for name, c in STADIUM_COORDS.items():
        if name in v:
            return c
    return None


def fetch_weather(venue: Optional[str], when_iso: Optional[str] = None) -> Optional[str]:
    """回傳如「26°C、降水機率 20%」；球場或預報不可用時回傳 None。"""
    if not venue:
        return None
    c = _coords(venue)
    if not c:
        return None
    params = {
        "latitude": c[0], "longitude": c[1],
        "hourly": "temperature_2m,precipitation_probability",
        "timezone": "auto", "forecast_days": 3,
    }
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast",
                         params=params, timeout=10)
        r.raise_for_status()
        h = r.json().get("hourly", {})
        times, temps, pops = h.get("time", []), h.get("temperature_2m", []), \
            h.get("precipitation_probability", [])
        if not times:
            return None
        # 找最接近的比賽時段（無時間資訊時取當地 18:00 前後）
        target_hour = 18
        if when_iso:  # ISO 時間 → 當地小時
            try:
                target_hour = int(when_iso[11:13])
            except (ValueError, IndexError):
                pass
        best, best_d = 0, 99
        for i, t in enumerate(times):
            try:
                d = abs(int(t[11:13]) - target_hour)
            except (ValueError, IndexError):
                continue
            if d < best_d:
                best, best_d = i, d
        pop = pops[best] if best < len(pops) and pops[best] is not None else None
        temp = temps[best] if best < len(temps) and temps[best] is not None else None
        parts = []
        if temp is not None:
            parts.append(f"{temp:.0f}°C")
        if pop is not None:
            parts.append(f"降水機率 {pop:.0f}%")
        return "、".join(parts) if parts else None
    except Exception:
        return None
