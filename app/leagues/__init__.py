"""聯盟供應器註冊表與訊息路由輔助。"""
from __future__ import annotations

from typing import Optional

from app.leagues.base import LeagueProvider
from app.leagues.cpbl import CPBLProvider
from app.leagues.intl import INTL_PROVIDERS
from app.leagues.kbo import KBOProvider
from app.leagues.mlb import MLBProvider
from app.leagues.npb import NPBProvider

# 順序即優先順序（隊名自動判斷時）：MLB → CPBL → NPB → KBO
PROVIDERS: list[LeagueProvider] = [
    MLBProvider(), CPBLProvider(), NPBProvider(), KBOProvider(),
]
ALL = PROVIDERS + INTL_PROVIDERS


def provider_for_keyword(text: str) -> Optional[LeagueProvider]:
    """依聯盟關鍵字找供應器（如『最近 NPB 比賽』『中職賽程』）。"""
    t = text.lower()
    for p in ALL:
        for kw in p.keywords:
            if kw in t:
                return p
    return None


def detect_by_teams(query_a: str, query_b: str) -> Optional[LeagueProvider]:
    """依隊名別名判斷聯盟；兩隊都命中才成立。"""
    best: Optional[LeagueProvider] = None
    for p in PROVIDERS:
        if p.find_team(query_a) and p.find_team(query_b):
            best = p
            break
    return best
