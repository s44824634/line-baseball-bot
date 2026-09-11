"""LINE Messaging API Webhook 伺服器（FastAPI）。

本機開發：
    uvicorn app.main:app --reload --port 8000
對外暴露（LINE webhook 需要 HTTPS）：
    ngrok http 8000
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi, ReplyMessageRequest, TextMessage,
)
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from app.analysis.engine import analyze
from app.leagues import PROVIDERS, detect_by_teams, provider_for_keyword
from app.leagues.base import LeagueOffSeason
from app.report import DISCLAIMER, HELP_TEXT, format_report, format_schedule

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("line-baseball-bot")

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]

app = FastAPI(title="LINE 棒球賽前分析機器人")

from app.board import router as board_router  # noqa: E402  (路由模組)
app.include_router(board_router)

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)

VS_PATTERN = re.compile(r"(.+?)\s*(?:vs\.?|對上|對|@)\s*(.+)")
RECENT_PATTERN = re.compile(r"最近.*比賽|即將開打|近期賽程")
SCORE_PATTERN = re.compile(r"即時比分|^比分$|比分表|live score|^live$", re.I)

_bot_info: tuple[str | None, str | None] = (None, None)


def _get_bot_info() -> tuple[str | None, str | None]:
    """向 LINE 查詢機器人自己的 userId 與名稱（群組中判斷被標註用）。"""
    global _bot_info
    if _bot_info != (None, None):
        return _bot_info
    try:
        import requests
        r = requests.get("https://api.line.me/v2/bot/info",
                         headers={"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"},
                         timeout=10)
        if r.ok:
            data = r.json()
            _bot_info = (data.get("userId"), data.get("displayName"))
    except Exception:
        log.exception("查詢機器人資訊失敗")
    return _bot_info


def _is_mentioned(event: MessageEvent) -> bool:
    """群組／聊天室中，只有標註「本機器人」才回覆；標註其他人不回。"""
    message = event.message
    bot_id, bot_name = _get_bot_info()
    mention = getattr(message, "mention", None)
    mentionees = getattr(mention, "mentionees", None) if mention else None
    if mentionees and bot_id:
        for m in mentionees:
            uid = getattr(m, "user_id", None) or getattr(m, "userId", None)
            if uid and uid == bot_id:
                return True
    # 手打 @ 或 mention 資料缺少 userId 時：
    # 只有文字中明確叫到「@機器人名稱」才算，避免標註其他人也觸發
    text = getattr(message, "text", "") or ""
    if bot_name:
        return "@" + bot_name in text
    return False


def _reply(reply_token: str, text: str) -> None:
    with ApiClient(configuration) as api_client:
        line = MessagingApi(api_client)
        line.reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=text[:4900])],  # LINE 上限 5000 字
        ))


def _handle_schedule(text: str, reply_token: str) -> None:
    provider = provider_for_keyword(text)
    if provider is None:
        from app.leagues.mlb import MLBProvider
        provider = MLBProvider()
    try:
        games = provider.list_upcoming()
        _reply(reply_token, format_schedule(games, provider.display))
    except LeagueOffSeason as exc:
        _reply(reply_token, str(exc))
    except ConnectionError as exc:
        _reply(reply_token, str(exc))
    except Exception:
        log.exception("賽程查詢失敗 (%s)", provider.key)
        _reply(reply_token, f"{provider.display} 賽程查詢暫時失敗，請稍後再試。")


def _handle_matchup(text: str, reply_token: str) -> None:
    m = VS_PATTERN.match(text)
    if not m:
        # 國際賽事關鍵字（無對戰組合）→ 賽事資訊
        p = provider_for_keyword(text)
        if p is not None and p.key in ("wbc", "p12"):
            try:
                p.list_upcoming()
            except LeagueOffSeason as exc:
                _reply(reply_token, str(exc))
                return
        _reply(reply_token, HELP_TEXT)
        return
    away_q, home_q = m.group(1).strip(), m.group(2).strip()

    # 有聯盟關鍵字用該聯盟；否則依隊名自動判斷；預設 MLB
    provider = provider_for_keyword(text)
    if provider is None:
        provider = detect_by_teams(away_q, home_q)
    if provider is None:
        from app.leagues.mlb import MLBProvider
        provider = MLBProvider()

    try:
        matchup = provider.analyze_matchup(away_q, home_q)
    except LeagueOffSeason as exc:
        _reply(reply_token, str(exc))
        return
    except (ValueError, ConnectionError) as exc:
        _reply(reply_token, str(exc))
        return
    if matchup is None:
        _reply(reply_token,
               f"在 {provider.display} 找不到「{away_q}」或「{home_q}」。"
               "請確認隊名，或輸入 help 查看支援的聯盟與隊名。")
        return

    away, home, info = matchup
    result = analyze(away, home, provider.context)
    report = format_report(result, away, home, info, datetime.now())
    _reply(reply_token, report)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": datetime.now().isoformat()}


@app.get("/api/groups")
def api_groups() -> dict:
    """已註冊的群組與推播開關狀態。僅供設定時確認用。"""
    from app import pusher
    return {"groups": pusher.known_groups()}


@app.on_event("startup")
async def _start_pusher() -> None:
    """背景推播：得分／全壘打／終場自動發到已註冊群組。"""
    import asyncio
    from app import pusher
    asyncio.create_task(pusher.loop())


@app.post("/callback")
async def callback(request: Request) -> str:
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")
    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="invalid signature")

    for event in events:
        if not isinstance(event, MessageEvent):
            continue
        register_id = None
        # 群組／聊天室：註冊 group_id（事件推播用），且只在被標註時回覆
        if event.source.type in ("group", "room"):
            from app import pusher
            register_id = getattr(event.source, "group_id", None) or \
                getattr(event.source, "room_id", None)
            pusher.register_group(register_id)
            mentioned = _is_mentioned(event)
            snippet = ""
            if isinstance(event.message, TextMessageContent):
                snippet = event.message.text[:30]
            log.info("群組訊息（%s mention=%s）：%s",
                     event.source.type, mentioned, snippet)
            if not mentioned:
                continue
        try:
            if not isinstance(event.message, TextMessageContent):
                if event.source.type == "user":
                    _reply(event.reply_token, "請輸入文字，例如「道奇 vs 洋基」。")
                continue
            text = event.message.text.strip()
            log.info("收到訊息（%s）：%s", event.source.type, text)
            if "開啟自動推播" in text or "開啟自動推送" in text:
                if not register_id:
                    _reply(event.reply_token,
                           "請在「群組裡」輸入這個指令（@機器人 開啟自動推播）。")
                    continue
                from app import pusher
                pusher.set_enabled(register_id, True)
                _reply(event.reply_token,
                       "✅ 已開啟自動推播！\n"
                       "之後得分／全壘打／終場（NBA 為每節結束）"
                       "會自動發到這個群組。\n"
                       "輸入「@機器人 關閉自動推播」可停止。")
            elif "關閉自動推播" in text or "關閉自動推送" in text:
                if register_id:
                    from app import pusher
                    pusher.set_enabled(register_id, False)
                _reply(event.reply_token, "已關閉自動推播。")
            if text.lower() in ("help", "幫助", "使用說明", "?", "？", "選單"):
                _reply(event.reply_token, HELP_TEXT)
            elif SCORE_PATTERN.search(text):
                from app.scores import format_scores
                try:
                    _reply(event.reply_token, format_scores())
                except Exception:
                    log.exception("即時比分查詢失敗")
                    _reply(event.reply_token, "即時比分查詢暫時失敗，請稍後再試。")
            elif RECENT_PATTERN.search(text):
                _handle_schedule(text, event.reply_token)
            else:
                _handle_matchup(text, event.reply_token)
        except Exception:
            # LINE 對非 200 回應會大量重試，任何錯誤都必須吞掉並回 OK
            log.exception("處理事件失敗")
            try:
                _reply(event.reply_token, f"分析時發生錯誤，請稍後再試。\n{DISCLAIMER}")
            except Exception:
                log.exception("錯誤回覆也失敗")
    return "OK"
