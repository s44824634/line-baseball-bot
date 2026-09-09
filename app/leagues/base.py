"""聯盟供應器共通介面。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from app.analysis.engine import LeagueContext, TeamInput


@dataclass
class GameInfo:
    """比賽條件與資料完整度（報告第 5 段與信心等級用）。"""
    venue: Optional[str] = None
    start_time: Optional[str] = None
    weather: Optional[str] = None
    roof: Optional[bool] = None        # True=室內固定屋頂 / False=開放式 / None=未知
    confirmed: dict = field(default_factory=dict)  # starter/lineup/weather/rs_ra: bool
    notes: list = field(default_factory=list)      # 資料缺口與影響說明
    source: str = ""


class LeagueOffSeason(Exception):
    """國際賽事非賽事期間。"""


class LeagueProvider(ABC):
    """每個聯盟一個供應器：隊名解析、近期賽程、單場資料組裝。"""
    key: str = ""
    display: str = ""
    keywords: tuple = ()               # 觸發此聯盟的關鍵字（小寫）
    aliases: dict = {}                 # 別名(小寫) → 標準隊名
    context: LeagueContext = None      # 各聯盟得分環境與賽制
    source_name: str = ""

    def find_team(self, query: str) -> Optional[str]:
        q = query.strip().lower()
        if q in self.aliases:
            return self.aliases[q]
        for alias, canonical in self.aliases.items():
            if alias in q or q in alias:
                return canonical
        return None

    @abstractmethod
    def list_upcoming(self, days: int = 2) -> list[dict]:
        """近期賽程：[{date, away, home, time?}]"""

    @abstractmethod
    def analyze_matchup(self, away_query: str, home_query: str):
        """回傳 (away: TeamInput, home: TeamInput, info: GameInfo)。"""
