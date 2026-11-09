"""Подбор аналога: бумага, которая сохраняет характеристики портфеля.

Детерминированная часть — жёсткие фильтры (тип, валюта, квалстатус, риск)
и метрика близости. В агентном режиме LLM получает топ-N кандидатов и
выбирает/объясняет с учётом неявной стратегии клиента (см. agent.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import bonds
from .models import Instrument, Portfolio, Settings


@dataclass
class Analog:
    instrument: Instrument
    score: float  # чем меньше, тем ближе
    metrics: dict
    reasons: list[str]


def bond_metrics(inst: Instrument, asof: date) -> dict:
    return {
        "ytm": round(bonds.ytm(inst, asof), 2),
        "duration": round(bonds.duration(inst, asof), 2),
        "coupon_freq": inst.coupon_freq,
        "risk_level": inst.risk_level,
        "sector": inst.sector,
    }


def share_metrics(inst: Instrument) -> dict:
    return {
        "dividend_yield": inst.dividend_yield,
        "beta": inst.beta,
        "risk_level": inst.risk_level,
        "sector": inst.sector,
    }


def metrics(inst: Instrument, asof: date) -> dict:
    return bond_metrics(inst, asof) if inst.kind == "bond" else share_metrics(inst)


def _eligible(src: Instrument, cand: Instrument, p: Portfolio, s: Settings) -> str | None:
    if cand.kind != src.kind:
        return "другой тип"
    if cand.currency != src.currency:
        return "другая валюта"
    if cand.ticker == src.ticker and not s.allow_same_ticker:
        return "та же бумага"
    if cand.qual_only and not p.qualified:
        return "только для квалифицированных инвесторов"
    if cand.risk_level > src.risk_level:
        return "риск выше исходного"
    return None


def find_analogs(src: Instrument, catalog: dict[str, Instrument], p: Portfolio,
                 asof: date, s: Settings, top: int = 3) -> list[Analog]:
    src_m = metrics(src, asof)
    found: list[Analog] = []
    for cand in catalog.values():
        if _eligible(src, cand, p, s):
            continue
        m = metrics(cand, asof)
        reasons: list[str] = []
        if src.kind == "bond":
            dd = abs(m["duration"] - src_m["duration"])
            dy = m["ytm"] - src_m["ytm"]
            if dd > s.max_duration_diff or abs(dy) > s.max_ytm_diff:
                continue
            score = dd / s.max_duration_diff + abs(dy) / s.max_ytm_diff
            if m["coupon_freq"] != src_m["coupon_freq"]:
                score += 0.5
                reasons.append(f"купон {m['coupon_freq']} раз в год вместо {src_m['coupon_freq']}")
            if m["sector"] != src_m["sector"]:
                score += 0.3
                reasons.append(f"сектор «{m['sector']}» вместо «{src_m['sector']}»")
            reasons.insert(0, f"дюрация {src_m['duration']}→{m['duration']} г., "
                              f"YTM {src_m['ytm']}→{m['ytm']}%")
        else:
            if m["sector"] != src_m["sector"]:
                continue
            ddiv = abs(m["dividend_yield"] - src_m["dividend_yield"])
            if ddiv > s.max_dividend_diff:
                continue
            score = ddiv / s.max_dividend_diff + abs(m["beta"] - src_m["beta"]) / 0.3
            reasons.append(f"сектор «{m['sector']}», дивдоходность "
                           f"{src_m['dividend_yield']}→{m['dividend_yield']}%, "
                           f"бета {src_m['beta']}→{m['beta']}")
        score += cand.spread_pct  # штраф за неликвидность
        found.append(Analog(cand, round(score, 3), m, reasons))
    found.sort(key=lambda a: a.score)
    return found[:top]
