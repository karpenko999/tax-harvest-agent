"""Расчёт денежных потоков, НКД, доходности к погашению и дюрации облигации."""
from __future__ import annotations

from datetime import date

from .models import Instrument


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return date(y, m, day)


def coupon_schedule(inst: Instrument) -> list[date]:
    """Даты купонов, строятся назад от даты погашения."""
    if inst.maturity is None:
        raise ValueError(f"{inst.ticker}: нет даты погашения")
    step = 12 // inst.coupon_freq
    dates = []
    # 40 лет назад — с запасом для любой облигации
    for i in range(inst.coupon_freq * 40):
        dates.append(_add_months(inst.maturity, -step * i))
    return sorted(dates)


def coupon_amount(inst: Instrument) -> float:
    return inst.nominal * inst.coupon_rate / 100 / inst.coupon_freq


def cashflows(inst: Instrument, asof: date) -> list[tuple[float, float]]:
    """Будущие потоки: (срок в годах, сумма ₽) по одной облигации."""
    flows = []
    c = coupon_amount(inst)
    for d in coupon_schedule(inst):
        if d > asof:
            t = (d - asof).days / 365.0
            amount = c + (inst.nominal if d == inst.maturity else 0.0)
            flows.append((t, amount))
    return flows


def accrued_interest(inst: Instrument, asof: date) -> float:
    sched = coupon_schedule(inst)
    prev = max((d for d in sched if d <= asof), default=None)
    nxt = min((d for d in sched if d > asof), default=None)
    if prev is None or nxt is None:
        return 0.0
    return coupon_amount(inst) * (asof - prev).days / (nxt - prev).days


def dirty_price(inst: Instrument, asof: date) -> float:
    return inst.price + accrued_interest(inst, asof)


def _pv(flows: list[tuple[float, float]], y: float) -> float:
    return sum(cf / (1 + y) ** t for t, cf in flows)


def ytm(inst: Instrument, asof: date) -> float:
    """Эффективная доходность к погашению, % годовых (метод бисекции)."""
    flows = cashflows(inst, asof)
    price = dirty_price(inst, asof)
    lo, hi = -0.5, 2.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if _pv(flows, mid) > price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2 * 100


def duration(inst: Instrument, asof: date) -> float:
    """Дюрация Маколея, лет."""
    flows = cashflows(inst, asof)
    y = ytm(inst, asof) / 100
    pv = _pv(flows, y)
    return sum(t * cf / (1 + y) ** t for t, cf in flows) / pv


def current_yield(inst: Instrument) -> float:
    """Текущая купонная доходность, %."""
    return inst.nominal * inst.coupon_rate / inst.price
