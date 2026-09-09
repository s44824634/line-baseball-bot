"""國際賽事：世界棒球經典賽（WBC）與世界棒球 12 強賽。

此二賽事為週期性錦標賽（WBC 約三年一屆、3 月舉行；12 強約兩年一屆、
11 月舉行），非賽事期間無官方例行賽資料。供應器負責：

- 判斷是否處於賽事期間（依月份）
- 非賽事期間回傳清楚訊息（含最近/下一屆資訊）
- 賽事期間提示資料源狀態（WBSC 官方賽程需另行接入）
"""
from __future__ import annotations

from datetime import date

from app.leagues.base import LeagueOffSeason, LeagueProvider


class IntlProvider(LeagueProvider):
    """國際錦標賽（無例行賽資料）。"""

    def __init__(self, key: str, display: str, keywords: tuple,
                 window_months: tuple, info_text: str):
        self.key = key
        self.display = display
        self.keywords = keywords
        self.window_months = window_months
        self.info_text = info_text
        self.aliases = {}
        self.source_name = "WBSC"

    def in_window(self) -> bool:
        return date.today().month in self.window_months

    def list_upcoming(self, days: int = 2) -> list[dict]:
        raise LeagueOffSeason(self.info_text)

    def analyze_matchup(self, away_query: str, home_query: str):
        raise LeagueOffSeason(self.info_text)


WBC = IntlProvider(
    key="wbc",
    display="世界棒球經典賽 WBC",
    keywords=("wbc", "經典賽", "世界棒球經典賽"),
    window_months=(3,),
    info_text=(
        "🏆 世界棒球經典賽（WBC）為週期性錦標賽，約每三年於 3 月舉行"
        "（2026 年 3 月已舉行第六屆）。\n"
        "目前非賽事期間，無例行賽可供分析。\n"
        "下一屆賽事公布後，本機器人會接入 WBSC 官方賽程資料。\n\n"
        "可先使用：MLB / NPB / KBO / CPBL 的例行賽分析。"
    ),
)

PREMIER12 = IntlProvider(
    key="p12",
    display="世界棒球 12 強賽 Premier12",
    keywords=("12強", "12 強", "premier12", "十二強", "世界12強"),
    window_months=(11,),
    info_text=(
        "🏆 世界棒球 12 強賽（Premier12）約每兩年於 11 月舉行"
        "（2024 年 11 月已舉行第三屆）。\n"
        "目前非賽事期間，無賽事可供分析。\n"
        "賽事期間本機器人會接入 WBSC 官方賽程資料。\n\n"
        "可先使用：MLB / NPB / KBO / CPBL 的例行賽分析。"
    ),
)

INTL_PROVIDERS = [WBC, PREMIER12]
