"""NPB 日本職棒供應器。

資料來源：
- 賽程 / 球場 / 先發投手：Yahoo! Japan プロ野球「試合カード」
  （baseball.yahoo.co.jp/npb/schedule/?date=YYYY-MM-DD，伺服器端渲染）
- 戰績（勝率、主客場戰績）：Wikipedia NPB 賽季頁
- 得失分：由勝率以 Pythagorean 公式估算（官方逐隊得失分無公開 API）

⚠ 估算資料於報告中標示，並調降分析信心等級。
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from functools import lru_cache
from typing import Optional

import requests
from bs4 import BeautifulSoup

from app.analysis.engine import LeagueContext, TeamInput
from app.leagues.base import GameInfo, LeagueProvider
from app.weather import fetch_weather, roof_status

log = logging.getLogger("npb")

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/124 Safari/537.36"}

# 日文短名 → (標準名, 中文名)
JP_TEAMS = {
    "巨人": ("読売ジャイアンツ", "讀賣巨人"),
    "阪神": ("阪神タイガース", "阪神虎"),
    "中日": ("中日ドラゴンズ", "中日龍"),
    "横浜": ("横浜DeNAベイスターズ", "橫濱DeNA"),
    "DeNA": ("横浜DeNAベイスターズ", "橫濱DeNA"),
    "広島": ("広島東洋カープ", "廣島鯉魚"),
    "ヤクルト": ("東京ヤクルトスワローズ", "養樂多燕子"),
    "オリックス": ("オリックス・バファローズ", "歐力士猛牛"),
    "ソフトバンク": ("福岡ソフトバンクホークス", "福岡軟銀鷹"),
    "楽天": ("東北楽天ゴールデンイーグルス", "東北樂天金鷲"),
    "西武": ("埼玉西武ライオンズ", "西武獅"),
    "ロッテ": ("千葉ロッテマリーンズ", "千葉羅德"),
    "日本ハム": ("北海道日本ハムファイターズ", "日本火腿鬥士"),
}

NPB_TEAM_IDS = {   # Yahoo npbTeamN → 短名
    "npbTeam1": "巨人", "npbTeam2": "ヤクルト", "npbTeam3": "DeNA",
    "npbTeam4": "中日", "npbTeam5": "阪神", "npbTeam6": "広島",
    "npbTeam7": "西武", "npbTeam8": "日本ハム", "npbTeam9": "ロッテ",
    "npbTeam10": "オリックス", "npbTeam11": "ソフトバンク",
    "npbTeam12": "楽天",
}

ALIASES = {}
for short, (std, zh) in JP_TEAMS.items():
    ALIASES[short.lower()] = std
    ALIASES[std.lower()] = std
    ALIASES[zh] = std
for en, std in {
    "giants": "読売ジャイアンツ", "yomiuri": "読売ジャイアンツ",
    "tigers": "阪神タイガース", "hanshin": "阪神タイガース",
    "dragons": "中日ドラゴンズ", "chunichi": "中日ドラゴンズ",
    "baystars": "横浜DeNAベイスターズ", "dena": "横浜DeNAベイスターズ",
    "yokohama": "横浜DeNAベイスターズ", "carp": "広島東洋カープ",
    "hiroshima": "広島東洋カープ", "swallows": "東京ヤクルトスワローズ",
    "yakult": "東京ヤクルトスワローズ", "buffaloes": "オリックス・バファローズ",
    "orix": "オリックス・バファローズ", "hawks": "福岡ソフトバンクホークス",
    "softbank": "福岡ソフトバンクホークス", "eagles": "東北楽天ゴールデンイーグルス",
    "rakuten": "東北楽天ゴールデンイーグルス", "lions": "埼玉西武ライオンズ",
    "seibu": "埼玉西武ライオンズ", "marines": "千葉ロッテマリーンズ",
    "lotte": "千葉ロッテマリーンズ", "fighters": "北海道日本ハムファイターズ",
    "nippon-ham": "北海道日本ハムファイターズ",
}.items():
    ALIASES[en] = std

WIKI_NAME_MAP = {
    "Yomiuri Giants": "読売ジャイアンツ",
    "Hanshin Tigers": "阪神タイガース",
    "Chunichi Dragons": "中日ドラゴンズ",
    "Yokohama DeNA BayStars": "横浜DeNAベイスターズ",
    "DeNA BayStars": "横浜DeNAベイスターズ",
    "Hiroshima Toyo Carp": "広島東洋カープ",
    "Tokyo Yakult Swallows": "東京ヤクルトスワローズ",
    "Yakult Swallows": "東京ヤクルトスワローズ",
    "Orix Buffaloes": "オリックス・バファローズ",
    "Fukuoka SoftBank Hawks": "福岡ソフトバンクホークス",
    "SoftBank Hawks": "福岡ソフトバンクホークス",
    "Tohoku Rakuten Golden Eagles": "東北楽天ゴールデンイーグルス",
    "Rakuten Eagles": "東北楽天ゴールデンイーグルス",
    "Saitama Seibu Lions": "埼玉西武ライオンズ",
    "Seibu Lions": "埼玉西武ライオンズ",
    "Chiba Lotte Marines": "千葉ロッテマリーンズ",
    "Lotte Marines": "千葉ロッテマリーンズ",
    "Hokkaido Nippon-Ham Fighters": "北海道日本ハムファイターズ",
    "Nippon-Ham Fighters": "北海道日本ハムファイターズ",
}


def _py_est(pct: float, lg: float) -> tuple[float, float]:
    """由勝率以 Pythagorean（exp 1.83）反推得失分。"""
    pct = min(max(pct, 0.25), 0.75)
    ratio = (pct / (1 - pct)) ** (1 / 1.83)
    return lg * ratio ** 0.5, lg / ratio ** 0.5


@lru_cache(maxsize=4)
def _standings(year: int) -> dict[str, dict]:
    """Wikipedia NPB 賽季頁 → {標準名: {pct, home, road}}。"""
    url = (f"https://en.wikipedia.org/wiki/{year}_Nippon_Professional_"
           f"Baseball_season")
    r = requests.get(url, timeout=20, headers=H)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = {}
    for table in soup.find_all("table", class_="wikitable")[:2]:
        for row in table.find_all("tr")[1:]:
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            if len(cells) < 10:
                continue
            team = cells[1].replace("†", "").replace("*", "").strip()
            std = WIKI_NAME_MAP.get(team)
            if not std or std in out:
                continue
            try:
                pct = float(cells[6])
            except ValueError:
                continue
            out[std] = {"pct": pct, "home": cells[8], "road": cells[9]}
    return out


@lru_cache(maxsize=16)
def _cards_for_date(day_iso: str) -> list[dict]:
    """Yahoo 指定日期的比賽卡片（含球場、先發投手）。

    Yahoo 僅「今日」的卡片資料完整正確；未來日期的卡片對戰組合
    可信，但球場欄位可能錯位，故僅今日保留球場資訊。
    """
    trusted = day_iso == date.today().isoformat()
    url = f"https://baseball.yahoo.co.jp/npb/schedule/?date={day_iso}"
    r = requests.get(url, timeout=15, headers=H)
    r.encoding = r.apparent_encoding
    soup = BeautifulSoup(r.text, "html.parser")
    games = []
    for li in soup.select("li.bb-score__item"):
        a = li.find("a", href=re.compile(r"/npb/game/(\d+)/"))
        if not a:
            continue
        gid = re.search(r"/npb/game/(\d+)/", a["href"]).group(1)

        def team_of(css):
            p = li.select_one(css)
            if not p:
                return None
            cls = " ".join(p.get("class", []))
            m = re.search(r"(npbTeam\d+)", cls)
            if m and m.group(1) in NPB_TEAM_IDS:
                return JP_TEAMS[NPB_TEAM_IDS[m.group(1)]][0]
            txt = p.get_text(strip=True)
            for short, (std, _zh) in JP_TEAMS.items():
                if short in txt:
                    return std
            return None

        home = team_of(".bb-score__homeLogo")
        away = team_of(".bb-score__awayLogo")
        if not home or not away:
            continue
        venue_el = li.select_one(".bb-score__venue")
        time_el = li.select_one(".bb-score__status")
        ttxt = time_el.get_text(strip=True) if time_el else ""
        tm = re.fullmatch(r"(\d{1,2}):(\d{2})", ttxt)
        status = "予定" if tm else (ttxt or "予定")

        def starter(css):
            el = li.select_one(css)
            if not el:
                return None
            t = el.get_text(strip=True)
            t = re.sub(r"^\(予\)", "", t)
            return t or None

        games.append({
            "date": day_iso, "away": away, "home": home,
            "time": tm.group(0) if tm else None,
            "stadium": venue_el.get_text(strip=True) if (venue_el and trusted)
                       else None,
            "game_id": gid, "status": status,
            "away_starter": starter(".bb-score__playerAway"),
            "home_starter": starter(".bb-score__playerHome"),
        })
    return games


class NPBProvider(LeagueProvider):
    key = "npb"
    display = "NPB 日本職棒"
    keywords = ("npb", "日職", "日本職棒", "日本プロ野球")
    aliases = ALIASES
    source_name = "Yahoo! Japan プロ野球 / Wikipedia"
    context = LeagueContext(
        avg_runs=4.1, avg_era=3.95, home_adv=1.03,
        tie_allowed=True, league_name="NPB 日本職棒",
        tie_rule_note="例行賽 12 局上限和局",
    )

    @staticmethod
    def _zh(std: str) -> str:
        for short, (s, zh) in JP_TEAMS.items():
            if s == std:
                return zh
        return std

    def _iter_cards(self, days: int):
        today = date.today()
        for i in range(days + 1):
            d = today + timedelta(days=i)
            for g in _cards_for_date(d.isoformat()):
                yield g

    def list_upcoming(self, days: int = 3) -> list[dict]:
        # Yahoo 僅「今日」卡片資料完整正確；未來日期頁面內容不可信，只列今日。
        out = []
        for g in _cards_for_date(date.today().isoformat()):
            if g["status"] != "予定":
                continue
            out.append({
                "date": g["date"], "away": self._zh(g["away"]),
                "home": self._zh(g["home"]), "time": g["time"],
            })
        return out

    @staticmethod
    def _fmt_record(rec: Optional[str]) -> Optional[str]:
        """'30–28–2'（W-L-T）→ '30勝28敗2和'。"""
        if not rec:
            return rec
        m = re.match(r"(\d+)\s*[–-]\s*(\d+)\s*[–-]\s*(\d+)", rec)
        if not m:
            return rec
        return f"{m.group(1)}勝{m.group(2)}敗{m.group(3)}和"

    def _team_input(self, std: str) -> TeamInput:
        st = _standings(date.today().year).get(std, {})
        pct = st.get("pct", 0.5)
        rs, ra = _py_est(pct, self.context.avg_runs)
        return TeamInput(
            name=self._zh(std),
            runs_scored_pg=round(rs, 3), runs_allowed_pg=round(ra, 3),
            home_record=self._fmt_record(st.get("home")),
            road_record=self._fmt_record(st.get("road")),
        )

    def analyze_matchup(self, away_query: str, home_query: str):
        away_std = self.find_team(away_query)
        home_std = self.find_team(home_query)
        if not away_std or not home_std:
            return None
        game = next((g for g in self._iter_cards(7)
                     if g["status"] == "予定"
                     and {g["away"], g["home"]} == {away_std, home_std}), None)
        if not game:
            raise ValueError(
                f"近期 7 天內沒有 {self._zh(away_std)} 對 {self._zh(home_std)} 的賽程。")

        away = self._team_input(game["away"])
        home = self._team_input(game["home"])
        away.starter_name = game.get("away_starter")
        home.starter_name = game.get("home_starter")

        roof = roof_status(game.get("stadium"))
        weather = None if roof else fetch_weather(
            game.get("stadium"),
            f"{game['date']}T{game.get('time') or '18:00'}")
        notes = [
            "NPB 官方無公開逐隊得失分 API，得失分由勝率（Pythagorean）估算，"
            "預期得分誤差可能較大。",
            "先發投手僅有姓名（Yahoo 預告先発），賽季 ERA 無法自動對應，"
            "投手因子以團隊估算取代。",
            "先發打線（打順）於 Yahoo 賽前資料未提供。",
        ]
        if game["date"] != date.today().isoformat():
            notes.append("此為未來賽程：Yahoo 未來日期頁面僅對戰組合可信，"
                         "球場與天氣資訊暫不提供。")
        info = GameInfo(
            venue=game.get("stadium"),
            start_time=f"{game['date']} {game.get('time') or ''}".strip(),
            weather=weather, roof=roof,
            confirmed={"starter": bool(away.starter_name and home.starter_name),
                       "lineup": False,
                       "weather": weather is not None, "rs_ra": False},
            notes=notes, source=self.source_name,
        )
        return away, home, info
