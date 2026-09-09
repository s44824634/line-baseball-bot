"""KBO 韓國職棒供應器。

資料來源：
- 戰績（勝率、主客場、連勝連敗）：Wikipedia KBO 賽季頁
- 得失分：由勝率以 Pythagorean 公式估算
- 賽程：KBO 官網（koreabaseball.com，部分地區無法連線時自動降級）

⚠ KBO 官網對部分海外 IP 封鎖；若連線失敗，對戰分析仍可用
（戰績資料），但賽程查詢與先發投手將標示為不可用。
"""
from __future__ import annotations

import logging
import re
from datetime import date
from functools import lru_cache
from typing import Optional

import requests
from bs4 import BeautifulSoup

from app.analysis.engine import LeagueContext, TeamInput
from app.leagues.base import GameInfo, LeagueProvider
from app.weather import fetch_weather, roof_status

log = logging.getLogger("kbo")

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/124 Safari/537.36"}

TEAMS = {
    "Samsung Lions": ("三星獅", "삼성 라이온즈"),
    "KT Wiz": ("KT 巫師", "KT 위즈"),
    "Doosan Bears": ("斗山熊", "두산 베어스"),
    "LG Twins": ("LG 雙子", "LG 트윈스"),
    "Lotte Giants": ("樂天巨人", "롯데 자이언츠"),
    "Hanwha Eagles": ("韓華鷹", "한화 이글스"),
    "Kia Tigers": ("起亞虎", "기아 타이거즈"),
    "NC Dinos": ("NC 恐龍", "NC 다이노스"),
    "SSG Landers": ("SSG 登陸者", "SSG 랜더스"),
    "Kiwoom Heroes": ("培證英雄", "키움 히어로즈"),
}

ALIASES = {}
for en, (zh, ko) in TEAMS.items():
    ALIASES[en.lower()] = en
    ALIASES[zh] = en
    ALIASES[ko] = en
for alias, en in {
    "三星": "Samsung Lions", "삼성": "Samsung Lions",
    "kt": "KT Wiz", "kt wiz": "KT Wiz",
    "斗山": "Doosan Bears", "두산": "Doosan Bears",
    "lg": "LG Twins", "lg twins": "LG Twins",
    "롯데": "Lotte Giants", "lotte giants": "Lotte Giants",
    "한화": "Hanwha Eagles", "hanwha": "Hanwha Eagles",
    "기아": "Kia Tigers", "kia": "Kia Tigers",
    "kia tigers": "Kia Tigers",
    "nc": "NC Dinos", "nc dinos": "NC Dinos",
    "ssg": "SSG Landers", "ssg landers": "SSG Landers",
    "키움": "Kiwoom Heroes", "kiwoom": "Kiwoom Heroes",
    "kiwoom heroes": "Kiwoom Heroes",
}.items():
    ALIASES[alias] = en


def _py_est(pct: float, lg: float) -> tuple[float, float]:
    pct = min(max(pct, 0.25), 0.75)
    ratio = (pct / (1 - pct)) ** (1 / 1.83)
    return lg * ratio ** 0.5, lg / ratio ** 0.5


@lru_cache(maxsize=4)
def _standings(year: int) -> dict[str, dict]:
    """Wikipedia KBO 賽季頁 → {英文名: {pct, home, road, streak}}。"""
    url = f"https://en.wikipedia.org/wiki/{year}_KBO_League_season"
    r = requests.get(url, timeout=20, headers=H)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    tables = soup.find_all("table", class_="wikitable")
    out = {}
    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue
        head = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        if "Team" not in head or "PCT" not in head:
            continue
        for row in rows[1:]:
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            # Rank Team GP W L D PCT GB STRK Home(W D L) Road(W D L) Postseason
            if len(cells) < 15:
                continue
            team = cells[1].replace("†", "").strip()
            if team not in TEAMS:
                continue
            try:
                pct = float(cells[6])
            except ValueError:
                continue
            home = f"{cells[9]}勝{cells[10]}和{cells[11]}敗"
            road = f"{cells[12]}勝{cells[13]}和{cells[14]}敗"
            out[team] = {"pct": pct, "home": home, "road": road,
                         "streak": cells[8]}
        if out:
            break
    return out


def _kbo_schedule() -> Optional[list[dict]]:
    """KBO 官網賽程（部分地區被封鎖時回傳 None）。"""
    try:
        r = requests.get(
            "https://www.koreabaseball.com/Schedule/GameSchedule.aspx",
            timeout=12, headers=H)
        if r.status_code != 200 or "errorbox" in r.text:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        rows = []
        for tr in soup.select("table tr"):
            tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(tds) >= 5 and re.search(r"\d{4}-\d{2}-\d{2}", tds[0]):
                rows.append({"raw": tds})
        return rows or None
    except Exception:
        return None


class KBOProvider(LeagueProvider):
    key = "kbo"
    display = "KBO 韓國職棒"
    keywords = ("kbo", "韓職", "韓國職棒", "한국야구")
    aliases = ALIASES
    source_name = "Wikipedia / KBO 官網"
    context = LeagueContext(
        avg_runs=5.2, avg_era=5.00, home_adv=1.04,
        tie_allowed=True, league_name="KBO 韓國職棒",
        tie_rule_note="例行賽無延長賽局數上限，可和局",
    )

    @staticmethod
    def _zh(en: str) -> str:
        return TEAMS.get(en, (en,))[0]

    def list_upcoming(self, days: int = 3) -> list[dict]:
        rows = _kbo_schedule()
        if not rows:
            raise ConnectionError(
                "KBO 官網目前無法連線（部分地區被封鎖），賽程查詢暫不可用。")
        out = []
        for row in rows:
            tds = row["raw"]
            out.append({"date": tds[0][:10], "raw": tds})
        return out[:12]

    def _team_input(self, en: str) -> TeamInput:
        st = _standings(date.today().year).get(en, {})
        pct = st.get("pct", 0.5)
        rs, ra = _py_est(pct, self.context.avg_runs)
        return TeamInput(
            name=self._zh(en),
            runs_scored_pg=round(rs, 3), runs_allowed_pg=round(ra, 3),
            home_record=st.get("home"), road_record=st.get("road"),
        )

    def analyze_matchup(self, away_query: str, home_query: str):
        away_en = self.find_team(away_query)
        home_en = self.find_team(home_query)
        if not away_en or not home_en:
            return None
        away = self._team_input(away_en)
        home = self._team_input(home_en)

        venue = start = None
        rows = _kbo_schedule()
        if rows:
            for row in rows:
                tds = row["raw"]
                joined = " ".join(tds)
                if away_en.split()[-1] in joined and home_en.split()[-1] in joined:
                    start = tds[0]
                    venue = tds[2] if len(tds) > 2 else None
                    break
        roof = roof_status(venue)
        weather = None if roof else fetch_weather(venue, start)
        st = _standings(date.today().year)
        notes = [
            "KBO 官方無公開逐隊得失分 API，得失分由勝率（Pythagorean）估算，"
            "預期得分誤差可能較大。",
            "先發投手與先發打線：KBO 官網於賽前公布，但部分地區無法自動連線"
            "時無法取得。",
            f"{away.name} 近期狀態：{st.get(away_en, {}).get('streak', '不明')}｜"
            f"{home.name}：{st.get(home_en, {}).get('streak', '不明')}",
        ]
        if rows is None:
            notes.insert(0, "KBO 官網連線失敗（地區封鎖）：本場球場、天氣、"
                             "先發投手資訊未能取得，信心等級已調降。")
        info = GameInfo(
            venue=venue, start_time=start, weather=weather, roof=roof,
            confirmed={"starter": False, "lineup": False,
                       "weather": weather is not None, "rs_ra": False},
            notes=notes, source=self.source_name,
        )
        return away, home, info
