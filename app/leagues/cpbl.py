"""CPBL 中華職棒供應器 — 官方網站 JSON API（免金鑰，需 CSRF token）。

資料：年度賽程（含已公布先發投手、球場、實際比分）→ 自行計算
球隊得失分、近 10 場、主客場拆分。
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from functools import lru_cache
from typing import Optional

import requests

from app.analysis.engine import LeagueContext, TeamInput
from app.leagues.base import GameInfo, LeagueProvider
from app.weather import fetch_weather, roof_status

log = logging.getLogger("cpbl")

_PAGE_URL = "https://cpbl.com.tw/schedule"
_API_PATH = "/schedule/getgamedatas"
_CSRF_RE = re.compile(r"RequestVerificationToken:\s*['\"]([^'\"]+)")
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

TEAMS = ["中信兄弟", "統一7-ELEVEn獅", "樂天桃猿", "富邦悍將", "味全龍", "台鋼雄鷹"]

ALIASES = {
    "中信兄弟": "中信兄弟", "兄弟": "中信兄弟", "brothers": "中信兄弟",
    "ctbc brothers": "中信兄弟",
    "統一7-eleven獅": "統一7-ELEVEn獅", "統一獅": "統一7-ELEVEn獅", "統一": "統一7-ELEVEn獅",
    "獅": "統一7-ELEVEn獅", "lions": "統一7-ELEVEn獅", "uni-lions": "統一7-ELEVEn獅",
    "樂天桃猿": "樂天桃猿", "桃猿": "樂天桃猿", "樂天": "樂天桃猿",
    "rakuten monkeys": "樂天桃猿", "monkeys": "樂天桃猿",
    "富邦悍將": "富邦悍將", "富邦": "富邦悍將", "悍將": "富邦悍將",
    "fubon guardians": "富邦悍將", "guardians": "富邦悍將",
    "味全龍": "味全龍", "味全": "味全龍", "龍": "味全龍",
    "wei chuan dragons": "味全龍", "dragons": "味全龍",
    "台鋼雄鷹": "台鋼雄鷹", "台鋼": "台鋼雄鷹", "雄鷹": "台鋼雄鷹",
    "tsg hawks": "台鋼雄鷹", "hawks": "台鋼雄鷹",
}


def _iso(d: str) -> str:
    """統一日期格式：'2026/09/09 18:35' / '2026-09-09T18:35' → '2026-09-09'。"""
    return str(d)[:10].replace("/", "-")


@lru_cache(maxsize=4)
def _season_games(year: int, kind_code: str) -> list[dict]:
    s = requests.Session()
    s.headers.update(_HEADERS)
    page = s.get(_PAGE_URL, timeout=15)
    m = _CSRF_RE.search(page.text)
    if not m:
        raise RuntimeError("無法取得 CPBL CSRF token")
    resp = s.post(
        "https://cpbl.com.tw" + _API_PATH,
        data={"calendar": f"{year}/01/01", "location": "", "kindCode": kind_code},
        headers={
            "RequestVerificationToken": m.group(1),
            "X-Requested-With": "XMLHttpRequest",
            "Referer": str(page.url),
        },
        timeout=20,
    )
    resp.raise_for_status()
    import json as _json
    return _json.loads(resp.json()["GameDatas"])


def _all_games(year: int) -> list[dict]:
    """例行賽 + 季後挑戰賽 + 總冠軍賽。"""
    games: list[dict] = []
    for kind in ("A", "E", "C"):
        try:
            games.extend(_season_games(year, kind))
        except Exception:
            log.warning("CPBL kindCode=%s 取得失敗", kind)
    return games


def _played(games: list[dict]) -> list[dict]:
    today = date.today().isoformat()
    out = []
    for g in games:
        if _iso(g.get("GameDate") or "") < today and g.get("VisitingScore") is not None:
            out.append(g)
    return out


def _team_stats(games: list[dict], team: str) -> dict:
    """由已完成比賽計算球隊資料。"""
    played = [g for g in _played(games)
              if g["VisitingTeamName"] == team or g["HomeTeamName"] == team]
    played.sort(key=lambda g: g.get("GameDate") or "")
    rs = ra = 0
    home_rs = home_ra = home_n = road_rs = road_ra = road_n = 0
    last10_w = None
    results = []
    for g in played:
        v, h = g["VisitingTeamName"], g["HomeTeamName"]
        vs, hs = g.get("VisitingScore") or 0, g.get("HomeScore") or 0
        is_home = h == team
        my, opp = (hs, vs) if is_home else (vs, hs)
        rs += my
        ra += opp
        results.append(1 if my > opp else (0 if my < opp else 0.5))
        if is_home:
            home_rs += my; home_ra += opp; home_n += 1
        else:
            road_rs += my; road_ra += opp; road_n += 1
    n = len(played)
    last10 = results[-10:]
    last10_w = int(sum(last10)) if last10 else None
    return {
        "games": n,
        "rs_pg": rs / n if n else 4.5,
        "ra_pg": ra / n if n else 4.5,
        "wins_last10": last10_w,
        "home_rs_pg": home_rs / home_n if home_n else None,
        "home_ra_pg": home_ra / home_n if home_n else None,
        "road_rs_pg": road_rs / road_n if road_n else None,
        "road_ra_pg": road_ra / road_n if road_n else None,
    }


@lru_cache(maxsize=4)
def _league_avg(year: int) -> float:
    """由當季實際資料計算聯盟平均得分（環境會逐年變動，不寫死）。"""
    games = _all_games(year)
    r = [_team_stats(games, t)["rs_pg"] for t in TEAMS]
    r = [x for x in r if x > 0.5]
    return round(sum(r) / len(r), 3) if r else 4.5


def _context_for(year: int) -> LeagueContext:
    import dataclasses
    avg = _league_avg(year)
    return dataclasses.replace(CPBLProvider.context, avg_runs=avg,
                               avg_era=round(avg * 0.97, 3))


def _upcoming(games: list[dict], days: int = 10) -> list[dict]:
    today = date.today()
    limit = date.fromordinal(min(today.toordinal() + days, date.max.toordinal()))
    out = []
    for g in games:
        d = _iso(g.get("PreExeDate") or g.get("GameDate") or "")
        if not d:
            continue
        try:
            od = date.fromisoformat(d)
        except ValueError:
            continue
        if today <= od <= limit:
            out.append(g)
    out.sort(key=lambda g: g.get("PreExeDate") or "")
    return out


class CPBLProvider(LeagueProvider):
    key = "cpbl"
    display = "CPBL 中華職棒"
    keywords = ("cpbl", "中職", "中華職棒", "台灣大賽")
    aliases = ALIASES
    source_name = "CPBL 官方網站"
    context = LeagueContext(
        avg_runs=5.1, avg_era=4.95, home_adv=1.03,
        tie_allowed=True, league_name="CPBL 中華職棒",
        tie_rule_note="例行賽 12 局上限和局",
    )

    def list_upcoming(self, days: int = 3) -> list[dict]:
        games = _upcoming(_all_games(date.today().year), days)
        return [{
            "date": _iso(g.get("PreExeDate") or ""),
            "away": g["VisitingTeamName"],
            "home": g["HomeTeamName"],
            "time": str(g.get("PreExeDate") or "")[11:16],
        } for g in games]

    def analyze_matchup(self, away_query: str, home_query: str):
        away_name = self.find_team(away_query)
        home_name = self.find_team(home_query)
        if not away_name or not home_name:
            return None
        games = _all_games(date.today().year)
        self.context = _context_for(date.today().year)  # 當季實際得分環境
        upcoming = _upcoming(games, days=14)
        game = next((g for g in upcoming
                     if {g["VisitingTeamName"], g["HomeTeamName"]}
                     == {away_name, home_name}), None)
        if not game:
            raise ValueError(
                f"近期 14 天內沒有 {away_name} 對 {home_name} 的賽程。")

        stats_a = _team_stats(games, game["VisitingTeamName"])
        stats_h = _team_stats(games, game["HomeTeamName"])
        away = TeamInput(
            name=game["VisitingTeamName"],
            runs_scored_pg=round(stats_a["rs_pg"], 3),
            runs_allowed_pg=round(stats_a["ra_pg"], 3),
            starter_name=(game.get("VisitingPitcherName") or None),
            wins_last10=stats_a["wins_last10"],
            home_rs_pg=stats_a["home_rs_pg"], home_ra_pg=stats_a["home_ra_pg"],
            road_rs_pg=stats_a["road_rs_pg"], road_ra_pg=stats_a["road_ra_pg"],
        )
        home = TeamInput(
            name=game["HomeTeamName"],
            runs_scored_pg=round(stats_h["rs_pg"], 3),
            runs_allowed_pg=round(stats_h["ra_pg"], 3),
            starter_name=(game.get("HomePitcherName") or None),
            wins_last10=stats_h["wins_last10"],
            home_rs_pg=stats_h["home_rs_pg"], home_ra_pg=stats_h["home_ra_pg"],
            road_rs_pg=stats_h["road_rs_pg"], road_ra_pg=stats_h["road_ra_pg"],
        )
        venue = game.get("FieldAbbe") or None
        start = str(game.get("PreExeDate") or "")
        roof = roof_status(venue)
        weather = None if roof else fetch_weather(venue, start)
        notes = ["牛棚投球數據官方未於賽前 API 提供，牛棚因子以聯盟平均估計。",
                 "先發打線通常開賽前 1 小時公布，本報告不預測未確認打線。"]
        confirmed = {"starter": bool(away.starter_name and home.starter_name),
                     "lineup": False, "weather": weather is not None,
                     "rs_ra": True}
        if not game.get("VisitingPitcherName") or not game.get("HomePitcherName"):
            notes.append("部分球隊先發投手尚未公布，投手因子以團隊數據估計，預期得分區間可能放寬。")
        info = GameInfo(
            venue=venue, start_time=start[5:16].replace("-", "/").replace("T", " "),
            weather=weather, roof=roof, confirmed=confirmed, notes=notes,
            source=self.source_name,
        )
        return away, home, info
