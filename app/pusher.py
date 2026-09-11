"""重要事件自動推播 — 得分／全壘打／終場 → LINE 群組。

背景工作（FastAPI startup 啟動）：
- 每 POLL_SEC 秒掃描各聯盟即時比賽
  - MLB：官方 Stats API live feed，得分事件（含全壘打標記）+ 終場
  - NPB／CPBL／KBO：比分變化（得分）+ 終場
  - NBA：每節結束比分 + 終場（NBA 僅球季期間有資料）
- 同一場比賽同一輪的新事件合併成一則訊息推播
- 只推給「已開啟推播」的群組：群組中輸入「@機器人 開啟自動推播」

群組與開關狀態持久化到 data/push_settings.json。
資料來源：MLB Stats API（官方）、CPBL 官方、Yahoo! プロ野球、ESPN。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from app.data import mlb as statsapi
from app.scores import _NPB_ZH

log = logging.getLogger("pusher")
TAIPEI = timezone(timedelta(hours=8))

POLL_SEC = int(os.environ.get("PUSH_POLL_SEC", "45"))
# 0 = 不設上限（使用者方案無訊息限制）；可用環境變數重新啟用
DAILY_LIMIT = int(os.environ.get("PUSH_DAILY_LIMIT", "0"))
_SETTINGS = Path(__file__).resolve().parent.parent / "data" / "push_settings.json"

# 比賽狀態：key → {"last": tuple|int|None, "final": bool}
_state: dict[str, dict] = {}
_settings_data: dict = {"groups": {}}


# ---------------------------------------------------------------- 設定
def _save() -> None:
    try:
        _SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS.write_text(
            json.dumps(_settings_data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log.exception("儲存推播設定失敗")


def load_settings() -> None:
    global _settings_data
    try:
        if _SETTINGS.exists():
            _settings_data = json.loads(
                _SETTINGS.read_text(encoding="utf-8"))
            log.info("已載入推播設定：%d 群組",
                     len(_settings_data.get("groups", {})))
    except Exception:
        log.exception("載入推播設定失敗")


def register_group(group_id: str | None) -> bool:
    """登記群組（預設關閉推播）。回傳是否為新群組。"""
    if not group_id:
        return False
    g = _settings_data.setdefault("groups", {})
    if group_id in g:
        return False
    g[group_id] = {"enabled": False}
    _save()
    log.info("註冊新群組：%s", group_id)
    return True


def set_enabled(group_id: str | None, enabled: bool) -> bool:
    if not group_id:
        return False
    g = _settings_data.setdefault("groups", {})
    if group_id not in g:
        g[group_id] = {}
    g[group_id]["enabled"] = enabled
    _save()
    return True


def enabled_groups() -> list[str]:
    return sorted(gid for gid, s in _settings_data.get("groups", {}).items()
                  if s.get("enabled"))


def known_groups() -> dict:
    return {gid: s.get("enabled", False)
            for gid, s in _settings_data.get("groups", {}).items()}


# ---------------------------------------------------------------- 推播
def _push(text: str) -> bool:
    groups = enabled_groups()
    if not groups:
        log.info("（無已開啟推播的群組，僅記錄）\n%s", text)
        return False
    global _count, _day
    today = datetime.now(TAIPEI).strftime("%Y-%m-%d")
    if today != _day:
        _day, _count = today, 0
    if DAILY_LIMIT and _count >= DAILY_LIMIT:
        log.warning("已達每日推播上限 %d，略過", DAILY_LIMIT)
        return False
    try:
        from linebot.v3.messaging import (
            ApiClient, MessagingApi, PushMessageRequest, TextMessage,
        )
        from app.main import configuration
    except Exception:
        log.exception("推播模組匯入失敗")
        return False
    ok = False
    with ApiClient(configuration) as client:
        api = MessagingApi(client)
        for gid in groups:
            try:
                api.push_message(PushMessageRequest(
                    to=gid, messages=[TextMessage(text=text)]))
                ok = True
            except Exception:
                log.exception("推送到群組 %s 失敗", gid)
    if ok:
        _count += 1
    return ok


_count = 0
_day = ""


# ---------------------------------------------------------------- MLB
def _fetch_mlb_events(game_pk: int) -> dict | None:
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live",
            timeout=15)
        r.raise_for_status()
        feed = r.json()
    except Exception:
        return None
    plays = feed.get("liveData", {}).get("plays", {})
    all_plays = plays.get("allPlays", [])
    ls = feed.get("liveData", {}).get("linescore", {})
    gd = feed.get("gameData", {})
    events = []
    for idx in plays.get("scoringPlays", []):
        if not (0 <= idx < len(all_plays)):
            continue
        p = all_plays[idx]
        about = p.get("about", {})
        result = p.get("result", {})
        ev_txt = (result.get("event") or "").lower()
        events.append({
            "idx": idx,
            "inning": f"{about.get('inning', '?')}局"
                      f"{'上' if about.get('halfInning') == 'top' else '下'}",
            "batter": (p.get("matchup", {}) or {}).get("batter", {}).get("fullName", ""),
            "hr": result.get("type") == "home_run" or "home run" in ev_txt,
            "rbi": result.get("rbi", 0),
        })
    return {
        "state": (gd.get("status", {}) or {}).get("abstractGameState", ""),
        "away": statsapi._zh_team(gd.get("teams", {}).get("away", {})),
        "home": statsapi._zh_team(gd.get("teams", {}).get("home", {})),
        "away_score": (ls.get("teams", {}).get("away", {}) or {}).get("runs"),
        "home_score": (ls.get("teams", {}).get("home", {}) or {}).get("runs"),
        "events": events,
    }


def check_mlb() -> list[str]:
    now_tp = datetime.now(TAIPEI)
    today_utc = now_tp.astimezone(timezone.utc).date()
    try:
        data = statsapi._get("/schedule", {
            "sportId": statsapi.SPORT_MLB,
            "startDate": (today_utc - timedelta(days=1)).isoformat(),
            "endDate": today_utc.isoformat(),
            "gameTypes": "R", "hydrate": "linescore",
        })
    except Exception:
        return []
    msgs = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            state = (g.get("status") or {}).get("abstractGameState")
            pk = g["gamePk"]
            # Live 一定追蹤；Final 只有「先前追蹤過」的才補推終場
            if state != "Live" and not (state == "Final" and f"mlb:{pk}" in _state):
                continue
            info = _fetch_mlb_events(pk)
            if not info:
                continue
            st = _state.setdefault(f"mlb:{pk}", {"seen": set(), "final": False})
            new_evs = [e for e in info["events"] if e["idx"] not in st["seen"]]
            if new_evs:
                st["seen"].update(e["idx"] for e in new_evs)
                msgs.append(_fmt_mlb_events(info, new_evs))
            if info["state"] == "Final" and not st["final"]:
                st["final"] = True
                msgs.append(f"🏁 MLB 終場｜{info['away']} {info['away_score']} : "
                            f"{info['home_score']} {info['home']}")
    return msgs


def _fmt_mlb_events(info: dict, evs: list[dict]) -> str:
    head = f"{info['away']} {info['away_score']} : {info['home_score']} {info['home']}"
    lines = []
    for ev in evs:
        mark = "🎆 全壘打" if ev["hr"] else "⚾ 得分"
        who = ev["batter"] or ""
        if who and ev.get("rbi"):
            who += f" {ev['rbi']}分打點"
        lines.append(f"{mark} {ev['inning']}｜{head}")
        if who:
            lines.append(f"　{who}")
    return "\n".join(lines)


# ---------------------------------------------------------------- NPB / CPBL / KBO / NBA
def _score_change_msgs(league: str, games: list[dict]) -> list[str]:
    """通用比分變化偵測：games = [{key, away, home, a, h, state, extra}]。"""
    msgs = []
    for g in games:
        st = _state.setdefault(f"{league}:{g['key']}",
                               {"last": None, "final": False})
        score = (g["a"], g["h"])
        if g["state"] in ("live", "進行中"):
            if st["last"] is not None and score != st["last"] and \
                    None not in score:
                msgs.append(
                    f"⚾ 得分 {g.get('extra', '')}｜{g['away']} {g['a']} : "
                    f"{g['h']} {g['home']}")
            st["last"] = score
        elif g["state"] in ("final", "終了") and not st["final"]:
            st["final"] = True
            msgs.append(f"🏁 {league.upper()} 終場｜{g['away']} {g['a']} : "
                        f"{g['h']} {g['home']}")
        elif g["state"] in ("cancel", "中止") and not st["final"]:
            st["final"] = True
            msgs.append(f"🚫 {league.upper()} 取消｜{g['away']} @ {g['home']}")
    return msgs


def check_npb() -> list[str]:
    from app.leagues import npb as npb_mod
    today = datetime.now(TAIPEI).date().isoformat()
    try:
        cards = npb_mod._fetch_cards(today)
    except Exception:
        return []
    games = [{
        "key": f"{today}:{c['game_id']}",
        "away": _NPB_ZH.get(c["away"], c["away"]),
        "home": _NPB_ZH.get(c["home"], c["home"]),
        "a": c.get("away_score"), "h": c.get("home_score"),
        "state": {"進行中": "live", "終了": "final", "中止": "cancel"}.get(
            c.get("status"), "pre"),
        "extra": c.get("inning") or "",
    } for c in cards]
    return _score_change_msgs("npb", games)


def check_cpbl() -> list[str]:
    from app.leagues import cpbl as cpbl_mod
    now_tp = datetime.now(TAIPEI)
    today = now_tp.date().isoformat()
    try:
        games_all = cpbl_mod._all_games_fresh(now_tp.year)
    except Exception:
        return []
    games = []
    for g in games_all:
        if cpbl_mod._iso(g.get("GameDate") or "") != today:
            continue
        vs, hs = g.get("VisitingScore"), g.get("HomeScore")
        try:
            vs = int(vs) if vs is not None else None
            hs = int(hs) if hs is not None else None
        except (TypeError, ValueError):
            vs = hs = None
        finished = bool(g.get("GameDateTimeE"))
        state = "final" if finished else \
            ("live" if str(g.get("IsPlayBall") or "").upper() == "Y" else "pre")
        games.append({
            "key": f"{today}:{g.get('GameSno') or g.get('GameDateTime')}",
            "away": g["VisitingTeamName"], "home": g["HomeTeamName"],
            "a": vs, "h": hs, "state": state, "extra": "",
        })
    return _score_change_msgs("cpbl", games)


# KBO 官方（www.koreabaseball.com，需先造訪頁面取得 session 再帶 Referer 呼叫）
KBO_TEAMS_ZH = {
    "키움": "培證英雄", "삼성": "三星獅", "KT": "KT巫師", "롯데": "樂天巨人",
    "SSG": "SSG登陸者", "KIA": "KIA虎", "NC": "NC恐龍", "한화": "韓華鷹",
    "LG": "LG雙子", "두산": "斗山熊",
}
_kbo_session: requests.Session | None = None
_kbo_session_at = 0.0


def _kbo_games(date_yyyymmdd: str) -> list[dict]:
    global _kbo_session, _kbo_session_at
    H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 Chrome/124 Safari/537.36"}
    try:
        if _kbo_session is None or time.time() - _kbo_session_at > 600:
            _kbo_session = requests.Session()
            _kbo_session.headers.update(H)
            _kbo_session.get(
                "https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx",
                timeout=20)
            _kbo_session_at = time.time()
        r = _kbo_session.post(
            "https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList",
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx",
            },
            data={"leId": "1", "srId": "0,1,3,4,5,6,7,9", "date": date_yyyymmdd},
            timeout=20)
        return r.json().get("game", [])
    except Exception:
        log.warning("KBO 官方 API 取得失敗")
        return []


def check_kbo() -> list[str]:
    """KBO：官方 GameCenter API。狀態碼 1=未開賽 2=進行中 3=完賽。"""
    date = datetime.now(TAIPEI).strftime("%Y%m%d")
    games = []
    for g in _kbo_games(date):
        state_sc = str(g.get("GAME_STATE_SC") or "1")
        cancel = str(g.get("CANCEL_SC_ID") or "0")
        if cancel != "0":
            state = "cancel"
        else:
            state = {"1": "pre", "2": "live", "3": "final"}.get(state_sc, "pre")
        inn = g.get("GAME_INN_NO")
        tb = (g.get("GAME_TB_SC_NM") or "")
        extra = ""
        if state == "live" and inn:
            extra = f"{inn}局{'上' if tb == '초' else '下'}"
        try:
            a = int(g.get("T_SCORE_CN"))
            h = int(g.get("B_SCORE_CN"))
        except (TypeError, ValueError):
            a = h = None
        games.append({
            "key": f"kbo:{g.get('G_ID')}",
            "away": KBO_TEAMS_ZH.get(g.get("AWAY_NM", ""), g.get("AWAY_NM", "?")),
            "home": KBO_TEAMS_ZH.get(g.get("HOME_NM", ""), g.get("HOME_NM", "?")),
            "a": a, "h": h, "state": state, "extra": extra,
        })
    return _score_change_msgs("kbo", games)


# NBA：Sofascore 公開 API（ESPN / NBA 官方 CDN 對伺服器端均封鎖）
NBA_TOURNAMENT_ID = 138  # Sofascore uniqueTournament id


def check_nba() -> list[str]:
    """NBA：每節結束推比分、終場推結果（不推每次得分，太頻繁）。"""
    date = datetime.now(TAIPEI).date().isoformat()
    try:
        r = requests.get(
            f"https://www.sofascore.com/api/v1/sport/basketball/"
            f"scheduled-events/{date}",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        items = r.json().get("events", [])
    except Exception:
        log.warning("NBA（Sofascore）取得失敗")
        return []
    msgs = []
    for item in items:
        e = item.get("event", item)
        ut = (e.get("tournament", {}) or {}).get("uniqueTournament", {}) or {}
        if ut.get("id") != NBA_TOURNAMENT_ID:
            continue
        status = e.get("status", {}) or {}
        stype = status.get("type", "")
        period = (e.get("time", {}) or {}).get("currentPeriod", 0) or 0
        key = f"nba:{e.get('id')}"
        st = _state.setdefault(key, {"period": 0, "final": False})
        try:
            away = (e.get("awayTeam", {}) or {}).get("name", "?")
            home = (e.get("homeTeam", {}) or {}).get("name", "?")
            a = int((e.get("awayScore", {}) or {}).get("current") or 0)
            h = int((e.get("homeScore", {}) or {}).get("current") or 0)
        except (AttributeError, ValueError):
            continue
        if stype == "inprogress" and period > st["period"]:
            if st["period"] > 0:  # 新節開始 = 上一節結束
                msgs.append(f"🏀 NBA 第{st['period']}節結束｜{away} {a} : {h} {home}")
            st["period"] = period
        elif stype == "finished" and not st["final"]:
            st["final"] = True
            msgs.append(f"🏁 NBA 終場｜{away} {a} : {h} {home}")
    return msgs


# ---------------------------------------------------------------- 主迴圈
def check_once() -> int:
    msgs: list[str] = []
    for fn in (check_mlb, check_npb, check_cpbl, check_kbo, check_nba):
        try:
            msgs.extend(fn())
        except Exception:
            log.exception("%s 掃描失敗", fn.__name__)
    pushed = 0
    for m in msgs:
        if _push(m):
            pushed += 1
    if len(_state) > 200:
        done = [k for k, v in _state.items()
                if v.get("final") or v.get("last") is None]
        for k in done[:-100]:
            _state.pop(k, None)
    return pushed


async def loop() -> None:
    load_settings()
    await asyncio.sleep(10)
    while True:
        try:
            check_once()
        except Exception:
            log.exception("推播掃描失敗")
        await asyncio.sleep(POLL_SEC)
