"""即時比分 — 跨聯盟今日戰況（MLB / CPBL / NPB）。

- MLB：官方 Stats API（hydrate=linescore，含局中狀態）
- CPBL：官方網站 JSON API（年度賽程含實際比分）
- NPB：Yahoo プロ野球 今日卡片（比分欄位）
- KBO：官網部分地區連線受限，暫不支援即時比分
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.data import mlb as statsapi
from app.leagues import cpbl as cpbl_mod
from app.leagues import npb as npb_mod

TAIPEI = timezone(timedelta(hours=8))


def _mlb_scores(now_tp: datetime) -> list[str]:
    """MLB：最近 24 小時內已賽／進行中／即將開打的比賽。

    以 UTC 日期取資料（MLB 以 ET 歸屬比賽日），再用台灣時間過濾。
    """
    today_utc = now_tp.astimezone(timezone.utc).date()
    data = statsapi._get("/schedule", {
        "sportId": statsapi.SPORT_MLB,
        "startDate": (today_utc - timedelta(days=1)).isoformat(),
        "endDate": today_utc.isoformat(),
        "gameTypes": "R",
        "hydrate": "linescore",
    })
    cutoff = now_tp - timedelta(hours=20)   # 仍在視窗內的比賽
    lines = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            raw = g.get("gameDate") or ""
            try:
                start_tp = datetime.fromisoformat(
                    raw.replace("Z", "+00:00")).astimezone(TAIPEI)
            except ValueError:
                continue
            state = (g.get("status") or {}).get("abstractGameState", "")
            if state == "Preview":
                if start_tp < now_tp or start_tp > now_tp + timedelta(hours=16):
                    continue
            elif start_tp < cutoff:
                continue
            away = statsapi._zh_team(g["teams"]["away"]["team"])
            home = statsapi._zh_team(g["teams"]["home"]["team"])
            sa = g["teams"]["away"].get("score")
            sh = g["teams"]["home"].get("score")
            if state == "Live":
                ls = g.get("linescore") or {}
                half = "上" if ls.get("inningHalf") == "Top" else "下"
                inn = ls.get("currentInning", "?")
                lines.append(f"🔴 進行中 {inn} 局{half}｜{away} {sa} : {sh} {home}")
            elif state == "Final":
                lines.append(f"🏁 終場｜{away} {sa} : {sh} {home}")
            else:
                lines.append(f"🕐 {start_tp:%m/%d %H:%M}｜{away} @ {home}")
    return lines


def _cpbl_scores(now_tp: datetime) -> list[str]:
    today = now_tp.date().isoformat()
    games = [g for g in cpbl_mod._all_games(now_tp.year)
             if cpbl_mod._iso(g.get("GameDate") or "") == today]
    games.sort(key=lambda g: g.get("GameDate") or "")
    lines = []
    for g in games:
        away, home = g["VisitingTeamName"], g["HomeTeamName"]
        vs, hs = g.get("VisitingScore"), g.get("HomeScore")
        start = str(g.get("PreExeDate") or "")[11:16]
        finished = bool(g.get("GameDateTimeE"))          # 有結束時間 = 已完賽
        started = str(g.get("IsPlayBall") or "").upper() == "Y"
        if finished:
            lines.append(f"🏁 終場｜{away} {vs} : {hs} {home}")
        elif started:
            lines.append(f"🔴 進行中｜{away} {vs} : {hs} {home}")
        else:
            lines.append(f"🕐 {start}｜{away} @ {home}")
    return lines


_NPB_ZH = {std: zh for _short, (std, zh) in npb_mod.JP_TEAMS.items()}


def _npb_scores(now_tp: datetime) -> list[str]:
    today = now_tp.date().isoformat()
    lines = []
    for g in npb_mod._cards_for_date(today):
        away = _NPB_ZH.get(g["away"], g["away"])
        home = _NPB_ZH.get(g["home"], g["home"])
        status = g.get("status") or "予定"
        asc, hsc = g.get("away_score"), g.get("home_score")
        if asc is not None and hsc is not None:
            if status == "終了":
                lines.append(f"🏁 終場｜{away} {asc} : {hsc} {home}")
            else:
                lines.append(f"🔴 {status}｜{away} {asc} : {hsc} {home}")
        elif status == "予定":
            lines.append(f"🕐 {g.get('time') or ''}｜{away} @ {home}")
        else:
            lines.append(f"・{status}｜{away} @ {home}")
    return lines


def format_scores() -> str:
    now_tp = datetime.now(TAIPEI)
    sections = [f"⚾ 即時比分（台灣時間 {now_tp:%m/%d %H:%M}）", ""]
    has_any = False
    for title, fn in (("MLB 美國職棒", _mlb_scores),
                      ("CPBL 中華職棒", _cpbl_scores),
                      ("NPB 日本職棒", _npb_scores)):
        try:
            rows = fn(now_tp)
        except Exception:
            rows = []
        if rows:
            has_any = True
            sections.append(f"【{title}】")
            sections.extend(rows[:12])
            sections.append("")
    if not has_any:
        sections.append("目前四聯盟都沒有進行中或今日的比賽。")
    sections.append("KBO 韓國職棒：官網部分地區連線受限，暫無即時比分。")
    return "\n".join(sections)
