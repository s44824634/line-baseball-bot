"""棒球賽前預測引擎。

以聯盟平均得分環境為基底，結合球隊得失分、先發投手 ERA、牛棚 ERA
與主場優勢，估計雙方預期得分，再以 Poisson 分布推導：

- 全場勝 / 和（進延長）機率（依聯盟和局制度調整）
- 最大機率完賽比分與高機率比分區間
- 總得分低 / 中 / 高情境機率
- 前 5 局雙方領先 / 平手机率與前 5 局最大機率比分
- 兩隊 80% 得分區間與比賽波動性

⚠ 僅提供賽事分析，不提供任何投注或博弈建議。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import factorial
from typing import Optional

import numpy as np

MAX_RUNS = 16  # 機率矩陣單邊上限（含延長賽緩衝）


@dataclass
class TeamInput:
    """單一球隊的預測輸入。"""
    name: str
    runs_scored_pg: float              # 平均得分 / 場
    runs_allowed_pg: float             # 平均失分 / 場
    starter_name: Optional[str] = None
    starter_era: Optional[float] = None        # 先發投手 ERA
    starter_record: Optional[str] = None       # 先發投手 勝-敗
    starter_ip: Optional[str] = None           # 先發投手局數（顯示用）
    starter_recent: Optional[str] = None       # 近期先發簡述
    bullpen_era: Optional[float] = None        # 牛棚 ERA（無則用聯盟平均）
    wins_last10: Optional[int] = None          # 近 10 場勝場數
    home_record: Optional[str] = None          # 主場戰績（顯示用）
    road_record: Optional[str] = None          # 客場戰績（顯示用）
    home_rs_pg: Optional[float] = None         # 主場平均得分（拆分輸入）
    home_ra_pg: Optional[float] = None
    road_rs_pg: Optional[float] = None
    road_ra_pg: Optional[float] = None
    lineup: Optional[list[str]] = None         # 已確認先發打線（None = 未公布）


@dataclass
class LeagueContext:
    """聯盟得分環境與賽制設定。"""
    avg_runs: float = 4.5              # 聯盟平均每場得分
    avg_era: float = 4.20              # 聯盟平均 ERA
    home_adv: float = 1.04             # 主場預期得分加成係數
    starter_innings_share: float = 0.60
    f5_share: float = 0.55             # 前 5 局得分佔全場比例（先發主導）
    tie_allowed: bool = False          # 例行賽和局制度（NPB/CPBL 12 局上限）
    league_name: str = "MLB"
    tie_rule_note: str = ""            # 例：「12 局上限和局」


def _poisson_pmf(lam: float) -> np.ndarray:
    k = np.arange(MAX_RUNS)
    fact = np.array([factorial(int(i)) for i in k], dtype=float)
    return np.exp(-lam) * np.power(lam, k) / fact


def _matrix(lam_a: float, lam_h: float) -> np.ndarray:
    return np.outer(_poisson_pmf(lam_a), _poisson_pmf(lam_h))  # [away, home]


def _expected_runs(team: TeamInput, opp: TeamInput, ctx: LeagueContext,
                   is_home: bool) -> float:
    """估計單隊預期得分（主客場拆分資料優先）。"""
    if is_home and team.home_rs_pg and opp.home_ra_pg:
        base = team.home_rs_pg * (opp.home_ra_pg / ctx.avg_runs)
    elif not is_home and team.road_rs_pg and opp.road_ra_pg:
        base = team.road_rs_pg * (opp.road_ra_pg / ctx.avg_runs)
    else:
        lg = ctx.avg_runs
        base = lg * (team.runs_scored_pg / lg) * (opp.runs_allowed_pg / lg)

    opp_starter = opp.starter_era / ctx.avg_era if opp.starter_era else 1.0
    opp_bullpen = opp.bullpen_era / ctx.avg_era if opp.bullpen_era else 1.0
    pitching = (ctx.starter_innings_share * opp_starter
                + (1 - ctx.starter_innings_share) * opp_bullpen)
    exp = base * pitching
    if is_home:
        exp *= ctx.home_adv
    return float(np.clip(exp, 1.0, 9.0))


def _run_range(lam: float) -> list[int]:
    """單隊 80% 得分區間。"""
    pmf = _poisson_pmf(lam)
    cdf = np.cumsum(pmf)
    lo = int(np.searchsorted(cdf, 0.10))
    hi = int(np.searchsorted(cdf, 0.90))
    return [max(0, lo), min(MAX_RUNS - 1, hi)]


def analyze(away: TeamInput, home: TeamInput,
            ctx: Optional[LeagueContext] = None) -> dict:
    """產出完整賽前分析結果（dictionary）。"""
    ctx = ctx or LeagueContext()
    lam_a = _expected_runs(away, home, ctx, is_home=False)
    lam_h = _expected_runs(home, away, ctx, is_home=True)

    m = _matrix(lam_a, lam_h)
    p_away = float(np.tril(m, -1).sum())   # away > home
    p_home = float(np.triu(m, 1).sum())    # home > away
    p_tie = float(np.trace(m))             # 正規局數平手

    if ctx.tie_allowed:
        # 和局為最終結果之一（12 局上限等制度）
        win_away, win_home, tie_final = p_away, p_home, p_tie
    else:
        # 延長賽直到分出勝負：依正規局數勝率強度分配
        total = p_away + p_home
        s = p_away / total if total else 0.5
        win_away = p_away + p_tie * s
        win_home = p_home + p_tie * (1 - s)
        tie_final = 0.0

    # 最大機率完賽比分
    idx = np.unravel_index(int(np.argmax(m)), m.shape)
    top_score = (int(idx[0]), int(idx[1]))

    # 高機率比分帶：同勝方差距的相鄰比分
    band = [(top_score[0] + d, top_score[1] + d) for d in (0, 1, 2)]
    band_p = float(sum(m[a, h] for a, h in band))

    # 總得分分布
    tot = np.arange(2 * MAX_RUNS - 1)
    total_dist = np.zeros(2 * MAX_RUNS - 1)
    for a in range(MAX_RUNS):
        total_dist[a:a + MAX_RUNS] += m[a, :]
    low = float(total_dist[tot < 7].sum())
    mid = float(total_dist[(tot >= 7) & (tot <= 10)].sum())
    high = float(total_dist[tot > 10].sum())
    cdf = np.cumsum(total_dist)
    t_lo = int(np.searchsorted(cdf, 0.10))
    t_hi = int(np.searchsorted(cdf, 0.90))

    # 前 5 局（先發投手主導）
    lam_a5, lam_h5 = lam_a * ctx.f5_share, lam_h * ctx.f5_share
    m5 = _matrix(lam_a5, lam_h5)
    f5_tie = float(np.trace(m5))
    if ctx.tie_allowed:
        f5_ahead = float(np.tril(m5, -1).sum())
        f5_behind = float(np.triu(m5, 1).sum())
    else:
        # 前 5 局平手會繼續打，按比例呈現「領先 / 落後」傾向
        ta, th = float(np.tril(m5, -1).sum()), float(np.triu(m5, 1).sum())
        tt = ta + th
        f5_ahead = ta + f5_tie * (ta / tt if tt else 0.5)
        f5_behind = th + f5_tie * (th / tt if tt else 0.5)
    idx5 = np.unravel_index(int(np.argmax(m5)), m5.shape)

    # 比賽波動性：勝率越接近 50% 越高
    fav = max(win_away, win_home) * 100
    if fav < 56:
        volatility = "高"
    elif fav < 66:
        volatility = "中"
    else:
        volatility = "低"

    return {
        "away": away.name,
        "home": home.name,
        "league": ctx.league_name,
        "expected_runs": {"away": round(lam_a, 2), "home": round(lam_h, 2),
                          "total": round(lam_a + lam_h, 2)},
        "team_ranges": {"away": _run_range(lam_a), "home": _run_range(lam_h)},
        "win_prob": {"away": round(win_away * 100, 1),
                     "home": round(win_home * 100, 1),
                     "tie": round(tie_final * 100, 1)},
        "top_score": {"score": top_score,
                      "prob": round(float(m[idx]) * 100, 1)},
        "score_band": {"scores": band, "prob": round(band_p * 100, 1)},
        "total_scenarios": {"low": round(low * 100, 1),
                            "mid": round(mid * 100, 1),
                            "high": round(high * 100, 1)},
        "total_range_80": [int(t_lo), int(t_hi)],
        "f5": {"expected_runs": {"away": round(lam_a5, 2),
                                 "home": round(lam_h5, 2)},
               "away_lead_prob": round(f5_ahead * 100, 1),
               "home_lead_prob": round(f5_behind * 100, 1),
               "tie_prob": round(f5_tie * 100, 1),
               "top_score": (int(idx5[0]), int(idx5[1]))},
        "volatility": volatility,
        "tie_rule_note": ctx.tie_rule_note,
    }
