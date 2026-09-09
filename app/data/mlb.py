"""MLB 官方 Stats API 資料客戶端（免費、無需金鑰）。

資料來源：https://statsapi.mlb.com （MLB 官方後端，公開使用）
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import requests

BASE = "https://statsapi.mlb.com/api/v1"
SPORT_MLB = 1
_TIMEOUT = 15

# MLB 2026 球季平均得分環境（可於球季中更新；約 4.3 ~ 4.8）
LEAGUE_AVG_RUNS = 4.5
LEAGUE_AVG_ERA = 4.20

_session = requests.Session()


def _get(path: str, params: dict | None = None) -> dict:
    r = _session.get(f"{BASE}{path}", params=params or {}, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def find_team(name: str) -> Optional[dict]:
    """依中文或英文名稱找出球隊（id, name, teamName）。"""
    from app.teams import TEAM_ALIASES  # 延後匯入避免循環
    teams = _get("/teams", {"sportId": SPORT_MLB, "season": date.today().year})
    name_l = name.strip().lower()
    for t in teams.get("teams", []):
        aliases = TEAM_ALIASES.get(t["id"], set())
        candidates = {t["name"].lower(), t["teamName"].lower()} | {
            a.lower() for a in aliases
        }
        if name_l in candidates or any(name_l in c or c in name_l for c in candidates):
            return {"id": t["id"], "name": t["name"], "teamName": t["teamName"]}
    return None


def _team_record_stats(team_id: int) -> tuple[float, float, int, int]:
    """回傳 (平均得分, 平均失分, 勝, 負)。"""
    data = _get("/standings", {"leagueId": "103,104", "season": date.today().year,
                               "standingsTypes": "regularSeason"})
    for rec in data.get("records", []):
        for team_rec in rec.get("teamRecords", []):
            if team_rec["team"]["id"] == team_id:
                rs = team_rec.get("runsScored", 0)
                ra = team_rec.get("runsAllowed", 0)
                games = team_rec.get("gamesPlayed", 1) or 1
                return rs / games, ra / games, team_rec.get("wins", 0), team_rec.get("losses", 0)
    raise ValueError(f"找不到球隊 {team_id} 的戰績資料")


def _recent_form(team_id: int, n: int = 10) -> int:
    """近 n 場勝場數。"""
    today = date.today()
    start = (today - timedelta(days=n * 2 + 10)).isoformat()
    data = _get("/schedule", {
        "sportId": SPORT_MLB, "teamId": team_id,
        "startDate": start, "endDate": today.isoformat(),
        "gameTypes": "R",
    })
    wins = 0
    games = 0
    for d in reversed(data.get("dates", [])):
        for g in d.get("games", []):
            if games >= n:
                return wins
            home_win = g["teams"]["home"].get("isWinner")
            if home_win is None:
                continue
            is_home = g["teams"]["home"]["team"]["id"] == team_id
            won = home_win if is_home else not home_win
            wins += 1 if won else 0
            games += 1
    return wins


def get_pitcher_stats(person_id: int) -> dict:
    """先發投手本季 ERA / 投球局數。"""
    data = _get(f"/people/{person_id}/stats", {
        "stats": "season", "group": "pitching", "season": date.today().year,
    })
    for split in data.get("stats", [{}])[0].get("splits", []):
        s = split.get("stat", {})
        era = s.get("era")
        if era:
            return {"era": float(era), "innings": s.get("inningsPitched", "-")}
    return {"era": None, "innings": "-"}


def _bullpen_era(team_id: int) -> Optional[float]:
    """由團隊投球 ERA 近似牛棚水準（扣掉先發不易取得時的簡化做法）。"""
    data = _get(f"/teams/{team_id}/stats", {
        "stats": "season", "group": "pitching", "season": date.today().year,
    })
    for split in data.get("stats", [{}])[0].get("splits", []):
        era = split.get("stat", {}).get("era")
        if era:
            return float(era)
    return None


def find_game(team_id: int, on: Optional[date] = None) -> Optional[dict]:
    """找出該隊最近一場（on 當天或未來 3 天內）的比賽。"""
    on = on or date.today()
    data = _get("/schedule", {
        "sportId": SPORT_MLB, "teamId": team_id,
        "startDate": on.isoformat(),
        "endDate": (on + timedelta(days=3)).isoformat(),
        "gameTypes": "R,P",
    })
    for d in data.get("dates", []):
        for g in d.get("games", []):
            status = g.get("status", {}).get("abstractGameState", "")
            if status in ("Preview", "Live", "Final"):
                return g
    return None


def list_upcoming(league_hint: str = "mlb", days: int = 2) -> list[dict]:
    """列出近期賽程（供『分析最近比賽』指令使用）。"""
    today = date.today()
    data = _get("/schedule", {
        "sportId": SPORT_MLB,
        "startDate": today.isoformat(),
        "endDate": (today + timedelta(days=days)).isoformat(),
        "gameTypes": "R",
    })
    games = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            games.append({
                "date": d.get("date"),
                "away": g["teams"]["away"]["team"]["name"],
                "home": g["teams"]["home"]["team"]["name"],
                "time": (g.get("gameDate", "")[11:16] + " UTC")
                        if g.get("gameDate") else None,
            })
    return games


def build_team_input(team_id: int, side: str, game: dict) -> "TeamInput":
    """組合引擎所需的 TeamInput（side = 'away' / 'home'）。"""
    from app.analysis.engine import TeamInput

    name = game["teams"][side]["team"]["name"]
    rs_pg, ra_pg, _, _ = _team_record_stats(team_id)
    prob = game["teams"][side].get("probablePitcher") or {}
    starter_era = None
    starter_name = None
    if prob.get("id"):
        starter_name = prob.get("fullName")
        starter_era = get_pitcher_stats(prob["id"])["era"]
    return TeamInput(
        name=name,
        runs_scored_pg=round(rs_pg, 3),
        runs_allowed_pg=round(ra_pg, 3),
        starter_name=starter_name,
        starter_era=starter_era,
        bullpen_era=_bullpen_era(team_id),
        wins_last10=_recent_form(team_id),
    )
