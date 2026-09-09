"""MLB 美國職棒供應器 — MLB 官方 Stats API（免費、免金鑰）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from app.analysis.engine import LeagueContext, TeamInput
from app.data import mlb as statsapi
from app.leagues.base import GameInfo, LeagueProvider
from app.teams import TEAM_ALIASES
from app.weather import fetch_weather, roof_status


class MLBProvider(LeagueProvider):
    key = "mlb"
    display = "MLB 美國職棒"
    keywords = ("mlb", "美職", "美職棒", "大聯盟")
    aliases = {a.lower(): str(tid) for tid, aliases in TEAM_ALIASES.items()
               for a in aliases}
    source_name = "MLB 官方 Stats API"
    context = LeagueContext(
        avg_runs=statsapi.LEAGUE_AVG_RUNS, avg_era=statsapi.LEAGUE_AVG_ERA,
        tie_allowed=False, league_name="MLB 美國職棒",
        tie_rule_note="無和局，延長賽至分出勝負",
    )

    def _resolve(self, query: str) -> Optional[dict]:
        team = statsapi.find_team(query)
        return team

    def list_upcoming(self, days: int = 2) -> list[dict]:
        return statsapi.list_upcoming(days=days)

    def analyze_matchup(self, away_query: str, home_query: str):
        from app.teams import TEAM_ZH
        away_team = self._resolve(away_query)
        home_team = self._resolve(home_query)
        if not away_team or not home_team:
            return None
        game = statsapi.find_game(away_team["id"])
        away_zh = TEAM_ZH.get(away_team["id"], away_team["teamName"])
        home_zh = TEAM_ZH.get(home_team["id"], home_team["teamName"])
        if not game:
            raise ValueError(f"找不到 {away_zh} 近期賽程。")
        g_home_id = game["teams"]["home"]["team"]["id"]
        if g_home_id != home_team["id"]:
            ga = statsapi._zh_team(game["teams"]["away"]["team"])
            gh = statsapi._zh_team(game["teams"]["home"]["team"])
            raise ValueError(
                f"{away_zh} 近期並未對上 {home_zh}；"
                f"他們的下一場是：{ga} @ {gh}。"
                "請輸入正確的對戰組合。")

        away = statsapi.build_team_input(
            game["teams"]["away"]["team"]["id"], "away", game)
        home = statsapi.build_team_input(
            game["teams"]["home"]["team"]["id"], "home", game)

        venue = (game.get("venue") or {}).get("name")
        game_date = game.get("gameDate", "")
        roof = roof_status(venue)
        weather = None if roof else fetch_weather(venue, game_date)
        starter_known = bool(
            game["teams"]["away"].get("probablePitcher")
            and game["teams"]["home"].get("probablePitcher"))
        notes = ["先發打線（打順）於賽前不經官方 API 公布，本報告不預測未確認打線。"]
        if not starter_known:
            notes.append("雙方先發投手尚未公布，投手因子以團隊數據估計，"
                         "預期得分區間可能放寬。")
        info = GameInfo(
            venue=venue,
            start_time=statsapi.to_taipei_str(game_date),
            weather=weather, roof=roof,
            confirmed={"starter": starter_known, "lineup": False,
                       "weather": weather is not None, "rs_ra": True},
            notes=notes, source=self.source_name,
        )
        return away, home, info
