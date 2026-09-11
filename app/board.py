"""群組「球賽文字直播看板」— /board 網頁 + /api/live 資料端點。

設計：
- MLB：官方 Stats API live feed（免費無鑰匙），即時比分、球數、
  關鍵事件時間軸（得分 play + 最近 play）。
- CPBL / NPB：即時比分列（沿用 app.scores 的資料供應器）。
- 伺服器端 12 秒快取，前端每 20 秒輪詢。

群組成員直接開 /board 連結即可看即時文字直播，
LINE 機器人只負責低頻重要事件推播（後續再做）。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from app.data import mlb as statsapi
from app.leagues import cpbl as cpbl_mod
from app.leagues import npb as npb_mod
from app.scores import _npb_scores, _cpbl_scores  # noqa: F401  (列格式沿用)

router = APIRouter()
TAIPEI = timezone(timedelta(hours=8))

_CACHE: dict = {"at": 0.0, "payload": None}
_TTL = 12  # 秒


# ---------------------------------------------------------------- 事件標籤
_EVENT_ZH = {
    "home run": "全壘打", "single": "一壘安打", "double": "二壘安打",
    "triple": "三壘安打", "walk": "四壞保送", "intent walk": "故意四壞",
    "strikeout": "三振", "strikeout double play": "三振雙殺",
    "sac fly": "高飛犧牲打", "sac bunt": "犧牲觸擊",
    "field error": "失誤", "double play": "雙殺",
    "grounded into dp": "雙殺", "stolen base": "盜壘成功",
    "caught stealing": "盜壘失敗", "hit by pitch": "觸身球",
    "balk": "投手犯規", "wild pitch": "暴投", "passed ball": "捕逸",
    "pickoff": "牽制出局", "fielders choice": "野手選擇",
    "forceout": "封殺", "groundout": "滾地出局", "flyout": "飛球出局",
    "lineout": "平飛出局", "pop out": "高飛出局", "foul out": "界外出局",
}


def _zh_event(play: dict) -> str:
    ev = (play.get("result") or {}).get("event") or ""
    return _EVENT_ZH.get(ev.lower(), ev or "事件")


# ---------------------------------------------------------------- MLB 即時
def _mlb_live_games(now_tp: datetime) -> list[dict]:
    """今天（台灣時間視窗）的 MLB 比賽，Live 附加文字直播時間軸。"""
    today_utc = now_tp.astimezone(timezone.utc).date()
    data = statsapi._get("/schedule", {
        "sportId": statsapi.SPORT_MLB,
        "startDate": (today_utc - timedelta(days=1)).isoformat(),
        "endDate": today_utc.isoformat(),
        "gameTypes": "R",
        "hydrate": "linescore",
    })
    cutoff = now_tp - timedelta(hours=20)
    games = []
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
            game = {
                "state": state, "away": away, "home": home,
                "away_score": sa, "home_score": sh,
                "start": f"{start_tp:%m/%d %H:%M}",
            }
            if state == "Live":
                game.update(_mlb_feed(g["gamePk"]))
            games.append(game)
    games.sort(key=lambda x: (x["state"] != "Live", x["start"]))
    return games


def _mlb_feed(game_pk: int) -> dict:
    """MLB live feed → 局數、球數、得分時間軸、最近 play。

    feed/live 走 /api/v1.1/（Stats API 的 live 端點），與 v1 不同路徑。
    """
    import requests
    r = requests.get(
        f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live",
        timeout=15)
    r.raise_for_status()
    feed = r.json()
    ls = feed.get("liveData", {}).get("linescore", {})
    plays = feed.get("liveData", {}).get("plays", {})
    all_plays = plays.get("allPlays", [])

    def inn_of(play):
        about = play.get("about", {})
        half = "上" if about.get("halfInning") == "top" else "下"
        return f"{about.get('inning', '?')}局{half}"

    timeline = []
    for idx in plays.get("scoringPlays", []):
        if 0 <= idx < len(all_plays):
            p = all_plays[idx]
            desc = (p.get("result") or {}).get("description", "")
            timeline.append({
                "mark": "⚾ 得分", "inning": inn_of(p),
                "tag": _zh_event(p), "text": desc,
            })
    recent = []
    for p in all_plays[-3:]:
        desc = (p.get("result") or {}).get("description", "")
        if desc:
            recent.append({
                "mark": "・", "inning": inn_of(p),
                "tag": _zh_event(p), "text": desc,
            })

    odds = ls.get("outs") if ls.get("outs") is not None else 0
    return {
        "inning": f"{ls.get('currentInning', '?')}局"
                  f"{'上' if ls.get('inningHalf') == 'Top' else '下'}",
        "count": f"{ls.get('balls', 0)}-{ls.get('strikes', 0)}",
        "outs": odds,
        "timeline": timeline[-8:],
        "recent": recent,
    }


# ---------------------------------------------------------------- 其他聯盟
def _cpbl_rows(now_tp: datetime) -> list[str]:
    try:
        return _cpbl_scores(now_tp)
    except Exception:
        return []


def _npb_rows(now_tp: datetime) -> list[str]:
    try:
        return _npb_scores(now_tp)
    except Exception:
        return []


# ---------------------------------------------------------------- 路由
@router.get("/api/live")
def api_live():
    now = time.time()
    if _CACHE["payload"] and now - _CACHE["at"] < _TTL:
        return JSONResponse(_CACHE["payload"])
    now_tp = datetime.now(TAIPEI)
    try:
        mlb = _mlb_live_games(now_tp)
    except Exception:
        mlb = []
    payload = {
        "updated": f"{now_tp:%m/%d %H:%M:%S}",
        "mlb": mlb[:6],
        "cpbl": _cpbl_rows(now_tp)[:8],
        "npb": _npb_rows(now_tp)[:8],
    }
    _CACHE.update(at=now, payload=payload)
    return JSONResponse(payload)


@router.get("/board", response_class=HTMLResponse)
def board():
    return HTMLResponse(_BOARD_HTML)


_BOARD_HTML = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>⚾ 球賽文字直播</title>
<style>
:root{color-scheme:dark}
body{margin:0;background:#0d1117;color:#e6edf3;
font-family:-apple-system,"Noto Sans TC","Microsoft JhengHei",sans-serif}
header{position:sticky;top:0;background:#161b22cc;backdrop-filter:blur(8px);
padding:12px 16px;border-bottom:1px solid #30363d;display:flex;
justify-content:space-between;align-items:center;z-index:5}
h1{font-size:16px;margin:0}
#upd{font-size:12px;color:#8b949e}
main{max-width:720px;margin:0 auto;padding:12px}
.sec{font-size:13px;color:#58a6ff;margin:18px 4px 8px;letter-spacing:1px}
.card{background:#161b22;border:1px solid #30363d;border-radius:12px;
padding:14px;margin-bottom:12px}
.live{border-color:#f8514966;box-shadow:0 0 0 1px #f8514933 inset}
.score{display:flex;align-items:baseline;justify-content:space-between;
font-size:17px;font-weight:700}
.score .n{font-size:24px;font-variant-numeric:tabular-nums}
.meta{font-size:12px;color:#8b949e;margin-top:4px}
.badge{display:inline-block;font-size:11px;padding:1px 8px;border-radius:10px;
background:#23863622;color:#3fb950;border:1px solid #23863655;margin-right:6px}
.badge.live{background:#f8514922;color:#ff7b72;border-color:#f8514955}
.badge.final{background:#8b949e22;color:#8b949e;border-color:#8b949e55}
.tl{margin-top:10px;border-top:1px dashed #30363d;padding-top:8px}
.tl .ev{font-size:13px;padding:5px 0;border-bottom:1px solid #21262d}
.tl .ev:last-child{border-bottom:none}
.ev .m{color:#d29922;font-weight:700;margin-right:6px}
.ev .inn{color:#8b949e;font-size:11px;margin-right:6px}
.ev .tag{color:#58a6ff;font-size:11px;border:1px solid #30363d;
border-radius:8px;padding:0 6px;margin-right:6px}
.ev .txt{color:#c9d1d9}
.row{font-size:14px;padding:7px 4px;border-bottom:1px solid #21262d}
.empty{color:#8b949e;font-size:13px;padding:8px 4px}
#err{color:#ff7b72;font-size:12px}
</style>
</head>
<body>
<header><h1>⚾ 球賽文字直播</h1><span id="upd">更新中…</span></header>
<main>
<div id="err"></div>
<div id="mlb"></div>
<div id="cpbl"></div>
<div id="npb"></div>
<p style="color:#8b949e;font-size:11px">資料來源：MLB 官方 Stats API、CPBL 官方、Yahoo! プロ野球・僅供參考</p>
</main>
<script>
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function mlbCard(g){
  const badge=g.state==="Live"?'<span class="badge live">LIVE</span>'
    :g.state==="Final"?'<span class="badge final">終場</span>'
    :'<span class="badge">預定</span>';
  let html=`<div class="card ${g.state==="Live"?"live":""}">
    <div class="score"><span>${esc(g.away)}</span><span class="n">${esc(g.away_score??"-")} : ${esc(g.home_score??"-")}</span><span>${esc(g.home)}</span></div>
    <div class="meta">${badge}${g.state==="Live"?` ${esc(g.inning)}｜球數 ${esc(g.count)}｜${esc(g.outs)} 出局`
      :esc(g.start)}</div>`;
  if(g.timeline&&g.timeline.length){
    html+='<div class="tl">'+g.timeline.map(e=>
      `<div class="ev"><span class="m">${esc(e.mark)}</span><span class="inn">${esc(e.inning)}</span><span class="tag">${esc(e.tag)}</span><span class="txt">${esc(e.text)}</span></div>`).join("")+"</div>";
  }
  if(g.recent&&g.recent.length){
    html+='<div class="tl">'+g.recent.map(e=>
      `<div class="ev"><span class="m">${esc(e.mark)}</span><span class="inn">${esc(e.inning)}</span><span class="tag">${esc(e.tag)}</span><span class="txt">${esc(e.text)}</span></div>`).join("")+"</div>";
  }
  return html+"</div>";
}
async function load(){
  try{
    const r=await fetch("/api/live",{cache:"no-store"});
    const d=await r.json();
    document.getElementById("upd").textContent="更新於 "+d.updated+"（台灣時間）";
    document.getElementById("err").textContent="";
    document.getElementById("mlb").innerHTML=
      '<div class="sec">MLB 美國職棒</div>'+
      (d.mlb.length?d.mlb.map(mlbCard).join(""):'<div class="empty">今日無比賽</div>');
    document.getElementById("cpbl").innerHTML=
      '<div class="sec">CPBL 中華職棒</div>'+
      (d.cpbl.length?d.cpbl.map(x=>`<div class="row">${esc(x)}</div>`).join(""):'<div class="empty">今日無比賽</div>');
    document.getElementById("npb").innerHTML=
      '<div class="sec">NPB 日本職棒</div>'+
      (d.npb.length?d.npb.map(x=>`<div class="row">${esc(x)}</div>`).join(""):'<div class="empty">今日無比賽</div>');
  }catch(e){
    document.getElementById("err").textContent="連線失敗，20 秒後重試…";
  }
}
load();setInterval(load,20000);
</script>
</body></html>"""
