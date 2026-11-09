"""Налоговый движок: НДФЛ по инвестиционным доходам физлица — резидента РФ.

Упрощения MVP (см. docs/tax-rules.md):
* Ставки по ст. 224 НК РФ для «инвестиционной» налоговой базы:
  13% с дохода до 2,4 млн ₽ и 15% с превышения (с 2025 года).
* Финансовый результат по обращающимся ЦБ (ст. 214.1), включая купоны,
  сальдируется внутри одной базы. Отрицательный результат обнуляется
  (перенос убытков на будущие годы — только через декларацию, брокер его не делает).
* Дивиденды входят в общий порог 2,4 млн ₽, но убытками по ЦБ не уменьшаются.
* ПФИ, валюта и ИИС в MVP не рассматриваются.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import Instrument, Lot, Portfolio, Settings

THRESHOLD = 2_400_000.0
RATE_LOW = 0.13
RATE_HIGH = 0.15


def ndfl(base: float) -> float:
    base = max(0.0, base)
    low = min(base, THRESHOLD)
    return round(low * RATE_LOW + (base - low) * RATE_HIGH, 2)


def tax_for(securities_result: float, dividends: float, other: float = 0.0) -> float:
    return ndfl(max(0.0, securities_result) + dividends + other)


def marginal_rate(securities_result: float, dividends: float, other: float = 0.0) -> float:
    base = max(0.0, securities_result) + dividends + other
    return RATE_HIGH if base > THRESHOLD else RATE_LOW


@dataclass
class TaxState:
    securities_result: float
    dividends: float
    other: float
    tax_total: float
    tax_withheld: float
    tax_to_withhold: float
    marginal_rate: float

    @property
    def harvestable_base(self) -> float:
        """Сколько убытка ещё имеет смысл зафиксировать в этом году."""
        return max(0.0, self.securities_result)


def tax_state(p: Portfolio, extra_result: float = 0.0) -> TaxState:
    sec = p.realized_securities_result + extra_result
    total = tax_for(sec, p.dividends_ytd, p.other_investment_income)
    return TaxState(
        securities_result=sec,
        dividends=p.dividends_ytd,
        other=p.other_investment_income,
        tax_total=total,
        tax_withheld=p.tax_withheld_ytd,
        tax_to_withhold=round(max(0.0, total - p.tax_withheld_ytd), 2),
        marginal_rate=marginal_rate(sec, p.dividends_ytd, p.other_investment_income),
    )


def years_held(lot: Lot, asof: date) -> float:
    return (asof - lot.bought).days / 365.25


def fifo_result(lots: list[Lot], qty: int, price: float, asof: date,
                settings: Settings) -> tuple[float, list[tuple[Lot, int]]]:
    """Финрез продажи qty бумаг по FIFO. Лоты под ЛДВ дают 0 к налоговой базе."""
    remaining = qty
    result = 0.0
    used: list[tuple[Lot, int]] = []
    for lot in lots:
        if remaining <= 0:
            break
        take = min(lot.qty, remaining)
        pnl = (price - lot.price) * take
        if years_held(lot, asof) >= settings.ldv_years and pnl > 0:
            pnl = 0.0  # прибыль освобождена льготой долгосрочного владения
        result += pnl
        used.append((lot, take))
        remaining -= take
    return result, used


def unrealized(p: Portfolio, inst: Instrument) -> float:
    return sum((inst.price - l.price) * l.qty for l in p.lots_of(inst.ticker))
