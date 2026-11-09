"""Выбор набора сделок: максимум экономии налога за вычетом издержек.

Задача: выбрать позиции (и количество бумаг) для фиксации убытка так, чтобы
  экономия = НДФЛ(база) − НДФЛ(база − зафиксированный убыток) − издержки → max,
при ограничениях: портфель после замены сохраняет дюрацию/доходность/риск,
не трогаем лоты рядом с ЛДВ, учитываем FIFO-списание.

Экономия — вогнутая кусочно-линейная функция убытка (убыток сверх базы
ничего не даёт), поэтому жадный выбор по «издержкам на рубль убытка» с
частичной последней позицией близок к оптимуму. В тестах он сверяется
с полным перебором.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations

from . import tax
from .analogs import Analog, find_analogs
from .models import Instrument, Portfolio, Settings


@dataclass
class Candidate:
    instrument: Instrument
    qty: int
    loss: float  # отрицательный финрез при продаже всей позиции
    analog: Analog | None
    skip_reason: str | None = None


@dataclass
class Action:
    sell: Instrument
    sell_qty: int
    realized: float
    buy: Instrument
    buy_qty: int
    cost: float
    saving: float = 0.0
    analog: Analog | None = None


@dataclass
class Plan:
    asof: date
    tax_before: tax.TaxState
    tax_after: tax.TaxState
    actions: list[Action] = field(default_factory=list)
    skipped: list[Candidate] = field(default_factory=list)

    @property
    def saving(self) -> float:
        return round(self.tax_before.tax_total - self.tax_after.tax_total, 2)

    @property
    def costs(self) -> float:
        return round(sum(a.cost for a in self.actions), 2)

    @property
    def net_benefit(self) -> float:
        return round(self.saving - self.costs, 2)


def trade_cost(src: Instrument, dst: Instrument, value: float, p: Portfolio) -> float:
    """Комиссия за продажу и покупку + половина спреда с каждой стороны."""
    return round(value * (2 * p.commission_rate
                          + src.spread_pct / 100 / 2 + dst.spread_pct / 100 / 2), 2)


def collect_candidates(p: Portfolio, catalog: dict[str, Instrument], asof: date,
                       s: Settings) -> list[Candidate]:
    out: list[Candidate] = []
    for ticker in p.tickers():
        inst = catalog[ticker]
        lots = p.lots_of(ticker)
        qty = p.qty_of(ticker)
        result, _ = tax.fifo_result(lots, qty, inst.price, asof, s)
        if result >= 0:
            continue
        cand = Candidate(inst, qty, round(result, 2), None)
        held = [tax.years_held(l, asof) for l in lots]
        if any(s.ldv_guard_years <= h < s.ldv_years for h in held):
            cand.skip_reason = (f"лоту {max(held):.1f} г. — продажа обнулит срок "
                                f"для льготы долгосрочного владения ({s.ldv_years:g} г.)")
        else:
            analogs = find_analogs(inst, catalog, p, asof, s, top=1)
            if analogs:
                cand.analog = analogs[0]
            else:
                cand.skip_reason = "нет аналога с той же дюрацией, доходностью и риском"
        out.append(cand)
    return out


def _min_qty_for(c: Candidate, p: Portfolio, need: float, asof: date,
                 s: Settings) -> tuple[int, float]:
    """Минимальное число бумаг (кратно лоту), чей FIFO-финрез ≤ −need."""
    lots = p.lots_of(c.instrument.ticker)
    step = c.instrument.lot
    q = step
    while q < c.qty:
        r, _ = tax.fifo_result(lots, q, c.instrument.price, asof, s)
        if r <= -need:
            return q, r
        q += step
    r, _ = tax.fifo_result(lots, c.qty, c.instrument.price, asof, s)
    return c.qty, r


def _make_action(c: Candidate, qty: int, realized: float, p: Portfolio) -> Action:
    dst = c.analog.instrument if c.analog else c.instrument
    value = c.instrument.price * qty
    buy_qty = int(value // (dst.price * dst.lot)) * dst.lot
    return Action(c.instrument, qty, round(realized, 2), dst, buy_qty,
                  trade_cost(c.instrument, dst, value, p), analog=c.analog)


def _finalize(p: Portfolio, actions: list[Action], skipped: list[Candidate],
              asof: date, s: Settings) -> Plan:
    before = tax.tax_state(p)
    # Пересчёт экономии по каждой сделке в порядке исполнения; убираем убыточные.
    kept: list[Action] = []
    acc = 0.0
    for a in actions:
        t0 = tax.tax_for(p.realized_securities_result + acc, p.dividends_ytd,
                         p.other_investment_income)
        t1 = tax.tax_for(p.realized_securities_result + acc + a.realized,
                         p.dividends_ytd, p.other_investment_income)
        a.saving = round(t0 - t1, 2)
        if a.saving - a.cost < s.min_saving_rub:
            continue
        kept.append(a)
        acc += a.realized
    after = tax.tax_state(p, extra_result=acc)
    return Plan(asof, before, after, kept, skipped)


def optimize(p: Portfolio, catalog: dict[str, Instrument], asof: date,
             s: Settings | None = None) -> Plan:
    s = s or Settings()
    cands = collect_candidates(p, catalog, asof, s)
    usable = [c for c in cands if c.skip_reason is None]
    skipped = [c for c in cands if c.skip_reason is not None]

    def cost_per_rub(c: Candidate) -> float:
        a = _make_action(c, c.qty, c.loss, p)
        return a.cost / -c.loss

    usable.sort(key=cost_per_rub)
    need = tax.tax_state(p).harvestable_base
    actions: list[Action] = []
    for c in usable:
        if need <= 0:
            break
        if -c.loss <= need:
            qty, realized = c.qty, c.loss
        else:
            qty, realized = _min_qty_for(c, p, need, asof, s)
        actions.append(_make_action(c, qty, realized, p))
        need += realized
    return _finalize(p, actions, skipped, asof, s)


def brute_force(p: Portfolio, catalog: dict[str, Instrument], asof: date,
                s: Settings | None = None) -> float:
    """Эталон для тестов: лучшая чистая выгода перебором целых позиций."""
    s = s or Settings()
    usable = [c for c in collect_candidates(p, catalog, asof, s) if c.skip_reason is None]
    base = p.realized_securities_result
    t0 = tax.tax_for(base, p.dividends_ytd, p.other_investment_income)
    best = 0.0
    for k in range(1, len(usable) + 1):
        for combo in combinations(usable, k):
            loss = sum(c.loss for c in combo)
            cost = sum(_make_action(c, c.qty, c.loss, p).cost for c in combo)
            t1 = tax.tax_for(base + loss, p.dividends_ytd, p.other_investment_income)
            best = max(best, t0 - t1 - cost)
    return round(best, 2)


def naive_plan(p: Portfolio, catalog: dict[str, Instrument], asof: date) -> Plan:
    """Базовый способ: в декабре продать все убыточные позиции и откупить их же.

    Не учитывает ЛДВ, размер налоговой базы и издержки.
    """
    s = Settings(allow_same_ticker=True, ldv_guard_years=math.inf, min_saving_rub=-math.inf)
    actions = []
    for ticker in p.tickers():
        inst = catalog[ticker]
        r, _ = tax.fifo_result(p.lots_of(ticker), p.qty_of(ticker), inst.price, asof, s)
        if r < 0:
            c = Candidate(inst, p.qty_of(ticker), r, None)
            actions.append(_make_action(c, c.qty, r, p))
    return _finalize(p, actions, [], asof, s)
