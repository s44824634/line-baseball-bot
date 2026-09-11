"""重要事件自動推播 — 得分／全壘打／終場 → LINE 群組。

背景工作（FastAPI startup 啟動）：
- 每 POLL_SEC 秒掃描 MLB 即時比賽（官方 Stats API，免費無鑰匙）
- 以 scoringPlays 索引比對，找出「新發生的得分事件」（含全壘打）
- 同一場比賽同一輪的新事件合併成一則訊息推播到所有已知群組
- 比賽轉為 Final 推一次終場比分
- 每日推播上限（LINE 免費額度 200 則/月）以防爆量

群組註冊：機器人收到群組訊息時自動記住 group_id，
並持久化到 data/known_groups.json（Render 重啟後仍保留）。
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

log = logging.getLogger("pusher")
TAIPEI = timezone(timedelta(hours=8))

POLL_SEC = int(os.environ.get("PUSH_POLL_SEC", "45"))
# LINE 免費方案每月僅 200 則推播（回覆不限）：
# MLB 一晚可能數十個得分事件，預設每日上限 50，可依需求用環境變數調整
DAILY_LIMIT = int(os.environ.get("PUSH_DAILY_LIMIT", "50"))
_GROUPS_FILE = Path(__file__).resolve().parent.parent / "data" / "known_groups.json"

# gamePk → {"seen": set[int], "final": bool}
_seen: dict[int, dict] = {}
_groups: set[str] = set()
_day = ""
_count = 0


# ---------------------------------------------------------------- 群組註冊
def load_groups() -> None:
    global _groups
    try:
        if _GROUPS_FILE.exists():
            _groups = set(json.loads(_GROUPS_FILE.read_text(encoding="utf-8")))
            log.info("已載入 %d 個已知群組", len(_groups))
    except Exception:
        log.exception("載入群組清單失敗")


def register_group(group_id: str | None) -> None:
    if not group_id or group_id in _groups:
        return
    _groups.add(group_id)
    try:
        _GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _GROUPS_FILE.write_text(json.dumps(sorted(_groups)), encoding="utf-8")
    except Exception:
        log.exception("儲存群組清單失敗")
    log.info("註冊新群組：%s", group_id)


def known_groups() -> list[str]:
    return sorted(_groups)


# ---------------------------------------------------------------- 事件解析
def _fetch_events(game_pk: int) -> dict | None:
    """MLB live feed → 當前比分、狀態、全部得分事件（含全壘打標記）。"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live",
            timeout=15)
        r.raise_for_status()
        feed = r.json()
    except Exception:
        log.exception("抓不到 feed：%s", game_pk)
        return None
    ls = feed.get("liveData", {}).get("linescore", {})
    plays = feed.get("liveData", {}).get("plays", {})
    all_plays = plays.get("allPlays", [])
    gd = feed.get("gameData", {})
    away = statsapi._zh_team(gd.get("teams", {}).get("away", {}))
    home = statsapi._zh_team(gd.get("teams", {}).get("home", {}))
    state = (gd.get("status", {}) or {}).get("abstractGameState", "")

    events = []
    for idx in plays.get("scoringPlays", []):
        if not (0 <= idx < len(all_plays)):
            continue
        p = all_plays[idx]
        about = p.get("about", {})
        result = p.get("result", {})
        batter = (p.get("matchup", {}) or {}).get("batter", {}) or {}
        ev_txt = (result.get("event") or "").lower()
        events.append({
            "idx": idx,
            "inning": f"{about.get('inning', '?')}局"
                      f"{'上' if about.get('halfInning') == 'top' else '下'}",
            "desc": result.get("description", ""),
            "batter": batter.get("fullName", ""),
            "hr": result.get("type") == "home_run" or "home run" in ev_txt,
            "rbi": result.get("rbi", 0),
        })
    return {
        "state": state, "away": away, "home": home,
        "away_score": (ls.get("teams", {}).get("away", {}) or {}).get("runs"),
        "home_score": (ls.get("teams", {}).get("home", {}) or {}).get("runs"),
        "events": events,
    }


def _fmt_events(info: dict, evs: list[dict]) -> str:
    sa, sh = info.get("away_score"), info.get("home_score")
    head = f"{info['away']} {sa} : {sh} {info['home']}"
    lines = []
    for ev in evs:
        mark = "🎆 全壘打" if ev["hr"] else "⚾ 得分"
        lines.append(f"{mark} {ev['inning']}｜{head}")
        who = ev["batter"] or ""
        if who:
            who += f" {ev['rbi']}分打點" if ev.get("rbi") else ""
        if who:
            lines.append(f"　{who}")
    lines.append("　" + (evs[-1].get("desc") or "")[:120])
    return "\n".join(lines)


def _fmt_final(info: dict) -> str:
    return (f"🏁 MLB 終場｜{info['away']} {info.get('away_score')} : "
            f"{info.get('home_score')} {info['home']}")


# ---------------------------------------------------------------- 推播
def _push(text: str) -> bool:
    """推播到所有已知群組，受每日上限管制。回傳是否有實際送出。"""
    global _day, _count
    today = datetime.now(TAIPEI).strftime("%Y-%m-%d")
    if today != _day:
        _day, _count = today, 0
    if not _groups:
        log.info("（無已知群組，以下僅記錄不推送）\n%s", text)
        return False
    if _count >= DAILY_LIMIT:
        log.warning("已達每日推播上限 %d，略過：%s", DAILY_LIMIT, text[:40])
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
        for gid in _groups:
            try:
                api.push_message(PushMessageRequest(
                    to=gid, messages=[TextMessage(text=text)]))
                ok = True
            except Exception:
                log.exception("推送到群組 %s 失敗", gid)
    if ok:
        _count += 1
    return ok


def check_once() -> int:
    """掃描一次所有 Live 比賽並推播新事件，回傳推播則數。"""
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
        log.exception("取得賽程失敗")
        return 0
    pushed = 0
    for d in data.get("dates", []):
        for g in d.get("games", []):
            if (g.get("status") or {}).get("abstractGameState") != "Live":
                continue
            pk = g["gamePk"]
            info = _fetch_events(pk)
            if not info:
                continue
            st = _seen.setdefault(pk, {"seen": set(), "final": False})
            new_evs = [e for e in info["events"] if e["idx"] not in st["seen"]]
            if new_evs:
                st["seen"].update(e["idx"] for e in new_evs)
                if _push(_fmt_events(info, new_evs)):
                    pushed += 1
            if info["state"] == "Final" and not st["final"]:
                st["final"] = True
                if _push(_fmt_final(info)):
                    pushed += 1
    # 清掉已完賽超過一天的狀態
    if len(_seen) > 40:
        for pk in [k for k, v in _seen.items() if v["final"]][:-20]:
            _seen.pop(pk, None)
    return pushed


async def loop() -> None:
    """背景主迴圈（FastAPI startup 啟動）。"""
    load_groups()
    await asyncio.sleep(10)  # 等伺服器完全起來
    while True:
        try:
            check_once()
        except Exception:
            log.exception("推播掃描失敗")
        await asyncio.sleep(POLL_SEC)
