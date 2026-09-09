"""將分析結果格式化為 LINE 文字訊息（五段完整報告）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# 台灣固定 UTC+8（無日光節約時間）
TAIPEI = timezone(timedelta(hours=8))


def _tp(dt: datetime) -> datetime:
    """naive（假設本機）或 aware datetime → 台灣時間。"""
    if dt.tzinfo is None:
        dt = dt.astimezone()  # 本機時區
    return dt.astimezone(TAIPEI)

from app.analysis.engine import TeamInput
from app.leagues.base import GameInfo

DISCLAIMER = "⚠ 僅提供棒球賽事分析，不含任何投注或博弈建議 ⚠"
HELP_TEXT = (
    "⚾ 棒球賽前分析機器人\n\n"
    "【對戰分析】輸入一場比賽：\n"
    "「道奇 vs 洋基」\n"
    "「統一 vs 中信兄弟」（CPBL）\n"
    "「巨人 vs 阪神」（NPB）\n"
    "「三星 vs LG」（KBO）\n\n"
    "【近期賽程】\n"
    "「分析最近即將開打的 MLB 比賽」\n"
    "（MLB / NPB / KBO / CPBL 皆可）\n\n"
    "【支援賽事】\n"
    "MLB 美職｜NPB 日職｜KBO 韓職｜CPBL 中職｜\n"
    "世界棒球經典賽｜世界棒球 12 強賽\n\n"
    + DISCLAIMER
)


def _confidence(info: GameInfo) -> tuple[str, list[str]]:
    """依官方賽前資料完整度評定信心等級。"""
    c = info.confirmed
    missing = []
    if not c.get("starter"):
        missing.append("先發投手")
    if not c.get("lineup"):
        missing.append("先發打線")
    if not c.get("weather"):
        missing.append("天氣")
    if not c.get("rs_ra"):
        missing.append("實際得失分（以勝率估算）")
    if not missing:
        return "高（賽前官方資料完整）", []
    if c.get("starter") and c.get("rs_ra"):
        level = "中"
    else:
        level = "低"
    effect = ("缺漏會放寬預期得分與比分區間、降低模型鑑別度："
              + "、".join(missing))
    return f"{level}（{effect}）", missing


def _fmt_record(t: TeamInput) -> str:
    parts = []
    if t.home_record:
        parts.append(f"主場 {t.home_record}")
    if t.road_record:
        parts.append(f"客場 {t.road_record}")
    if t.wins_last10 is not None:
        parts.append(f"近10場 {t.wins_last10} 勝")
    return "｜".join(parts)


def format_report(result: dict, away: TeamInput, home: TeamInput,
                  info: GameInfo, fetched_at: datetime) -> str:
    """五段完整賽前分析報告。"""
    wp = result["win_prob"]
    er = result["expected_runs"]
    tr = result["team_ranges"]
    top = result["top_score"]
    band = result["score_band"]
    ts = result["total_scenarios"]
    f5 = result["f5"]
    lo, hi = result["total_range_80"]
    confidence, _ = _confidence(info)

    lines = [
        f"⚾ {result['away']} @ {result['home']}",
        f"   {result['league']} 賽前分析",
        "",
        "【1️⃣ 全場賽果與預期得分】",
        f"客 {result['away']} 勝率：{wp['away']}%",
        f"主 {result['home']} 勝率：{wp['home']}%",
    ]
    if wp.get("tie"):
        lines.append(f"和局機率：{wp['tie']}%（{result['tie_rule_note']}）")
    lines += [
        f"預期得分：{result['away']} {er['away']} : {er['home']} {result['home']}",
        f"合計預期總分：{er['total']} 分",
        f"80% 總分區間：{lo} ~ {hi} 分",
        f"兩隊 80% 得分區間：{result['away']} {tr['away'][0]}~{tr['away'][1]} 分｜"
        f"{result['home']} {tr['home'][0]}~{tr['home'][1]} 分",
        "",
        "【2️⃣ 比分分布與得分節奏】",
        f"最大機率完賽比分：{top['score'][0]} : {top['score'][1]}"
        f"（{top['prob']}%）",
        f"高機率比分帶：{band['scores'][0][0]}:{band['scores'][0][1]} ~ "
        f"{band['scores'][-1][0]}:{band['scores'][-1][1]}（{band['prob']}%）",
        f"總得分情境：低(<7) {ts['low']}%｜中(7~10) {ts['mid']}%｜高(>10) {ts['high']}%",
        "",
        "【3️⃣ 前 5 局與先發投手】",
        f"{result['away']} 前5局領先：{f5['away_lead_prob']}%｜"
        f"{result['home']} 領先：{f5['home_lead_prob']}%",
        f"前5局平手機率：{f5['tie_prob']}%",
        f"前5局預期得分：{f5['expected_runs']['away']} : {f5['expected_runs']['home']}",
        f"前5局最大機率比分：{f5['top_score'][0]} : {f5['top_score'][1]}",
        f"先發：{result['away']} {away.starter_name or '尚未公布'}",
        f"　　 {result['home']} {home.starter_name or '尚未公布'}",
    ]
    if away.starter_era or home.starter_era:
        a_desc = (f"{away.starter_record or '-'}、防禦率 {away.starter_era:.2f}、"
                  f"{away.starter_ip or '-'} 局") if away.starter_era else "數據未取得"
        h_desc = (f"{home.starter_record or '-'}、防禦率 {home.starter_era:.2f}、"
                  f"{home.starter_ip or '-'} 局") if home.starter_era else "數據未取得"
        lines.append(f"　　 本季：{a_desc}　vs　{h_desc}")
        lines.append("　　 預計投球局數：約 5~7 局（依近況與用球數調整）")

    lines += ["", "【4️⃣ 打線、牛棚與球隊狀態】"]
    if away.lineup and home.lineup:
        lines.append(f"{result['away']} 先發打線：{' → '.join(away.lineup[:9])}")
        lines.append(f"{result['home']} 先發打線：{' → '.join(home.lineup[:9])}")
    else:
        lines.append("先發打線：尚未確認（開賽前公布，不以未確認名單推測）")
    bp_a = f"{away.bullpen_era:.2f}" if away.bullpen_era else "資料未提供"
    bp_h = f"{home.bullpen_era:.2f}" if home.bullpen_era else "資料未提供"
    lines.append(f"牛棚 ERA：{result['away']} {bp_a}｜{result['home']} {bp_h}"
                 f"（近期使用量與疲勞：僅 MLB 有逐場資料）")
    rec_a, rec_h = _fmt_record(away), _fmt_record(home)
    if rec_a:
        lines.append(f"{result['away']}：{rec_a}")
    if rec_h:
        lines.append(f"{result['home']}：{rec_h}")

    lines += ["", "【5️⃣ 比賽條件與分析信心】"]
    cond = []
    if info.venue:
        cond.append(f"球場：{info.venue}")
    if info.start_time:
        cond.append(f"開賽：{info.start_time}")
    if info.roof is True:
        cond.append("屋頂：室內固定（不受天氣影響）")
    elif info.roof is False:
        cond.append("屋頂：開放式／可開關")
    if info.weather:
        cond.append(f"天氣：{info.weather}")
    lines.append("｜".join(cond) if cond else "球場與天氣：資料未提供")
    lines.append(f"分析信心：{confidence}")
    lines.append(f"比賽波動性：{result['volatility']}"
                 f"（勝率越接近 50% 波動越高）")
    if info.notes:
        for n in info.notes[:4]:
            lines.append(f"・{n}")
    lines += [
        "",
        f"🕒 資料源：{info.source or '官方資料'}｜擷取（台灣時間）："
        f"{_tp(fetched_at):%Y-%m-%d %H:%M}",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def format_schedule(games: list[dict], league_display: str) -> str:
    if not games:
        return f"近期找不到 {league_display} 賽程。"
    lines = [f"📅 近期 {league_display} 賽程（以下皆為台灣時間）", ""]
    for g in games[:12]:
        time_part = f" {g['time']}" if g.get("time") else ""
        lines.append(f"{g['date']}{time_part}｜{g['away']} @ {g['home']}")
    lines += ["", "輸入「隊名 vs 隊名」即可分析該場比賽。"]
    return "\n".join(lines)
