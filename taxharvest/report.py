"""Отчёт для клиента: налог до/после, сделки, характеристики портфеля."""
from __future__ import annotations

from datetime import date

from . import bonds
from .models import Instrument, Portfolio
from .optimizer import Plan


def rub(x: float) -> str:
    return f"{x:,.0f} ₽".replace(",", " ")


def portfolio_profile(holdings: dict[str, int], catalog: dict[str, Instrument],
                      asof: date) -> dict:
    """Средневзвешенные дюрация, YTM облигационной части и дивдоходность акций."""
    bv = bd = by = 0.0
    sv = sd = 0.0
    for t, q in holdings.items():
        if q <= 0:
            continue
        inst = catalog[t]
        v = inst.price * q
        if inst.kind == "bond":
            bv += v
            bd += v * bonds.duration(inst, asof)
            by += v * bonds.ytm(inst, asof)
        else:
            sv += v
            sd += v * inst.dividend_yield
    return {
        "bonds_value": round(bv),
        "bonds_duration": round(bd / bv, 2) if bv else None,
        "bonds_ytm": round(by / bv, 2) if bv else None,
        "shares_value": round(sv),
        "shares_div_yield": round(sd / sv, 2) if sv else None,
    }


def holdings_after(p: Portfolio, plan: Plan) -> dict[str, int]:
    h = {t: p.qty_of(t) for t in p.tickers()}
    for a in plan.actions:
        h[a.sell.ticker] -= a.sell_qty
        h[a.buy.ticker] = h.get(a.buy.ticker, 0) + a.buy_qty
    return h


def render(p: Portfolio, plan: Plan, catalog: dict[str, Instrument]) -> str:
    b, a = plan.tax_before, plan.tax_after
    lines = [
        f"# Налоговый оптимизатор — клиент {p.client_id}, на {plan.asof:%d.%m.%Y}",
        "",
        "## Налог сейчас",
        f"- Финрез по ценным бумагам с начала года (с купонами): {rub(b.securities_result)}",
        f"- Дивиденды: {rub(b.dividends)}",
        f"- НДФЛ за год: {rub(b.tax_total)}, уже удержано {rub(b.tax_withheld)}, "
        f"брокер удержит ещё {rub(b.tax_to_withhold)}",
        f"- Предельная ставка: {b.marginal_rate:.0%}",
        "",
    ]
    if not plan.actions:
        lines += ["Сделок, которые окупаются, не найдено — клиента не беспокоим.", ""]
    else:
        lines += [
            f"## Рекомендация: сэкономить {rub(plan.net_benefit)}",
            f"(налог {rub(b.tax_total)} → {rub(a.tax_total)}, издержки на сделки {rub(plan.costs)})",
            "",
            "| # | Продать | Кол-во | Убыток | Купить | Кол-во | Экономия | Издержки |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for i, x in enumerate(plan.actions, 1):
            lines.append(
                f"| {i} | {x.sell.name} ({x.sell.ticker}) | {x.sell_qty} | {rub(x.realized)} | "
                f"{x.buy.name} ({x.buy.ticker}) | {x.buy_qty} | {rub(x.saving)} | {rub(x.cost)} |")
        lines.append("")
        for i, x in enumerate(plan.actions, 1):
            if x.analog:
                lines.append(f"{i}. Почему {x.buy.ticker}: " + "; ".join(x.analog.reasons))
        lines.append("")
        before = portfolio_profile({t: p.qty_of(t) for t in p.tickers()}, catalog, plan.asof)
        after = portfolio_profile(holdings_after(p, plan), catalog, plan.asof)
        lines += [
            "## Портфель до и после",
            "| Показатель | До | После |",
            "|---|---|---|",
            f"| Дюрация облигаций, лет | {before['bonds_duration']} | {after['bonds_duration']} |",
            f"| YTM облигаций, % | {before['bonds_ytm']} | {after['bonds_ytm']} |",
            f"| Дивдоходность акций, % | {before['shares_div_yield']} | {after['shares_div_yield']} |",
            "",
        ]
    if plan.skipped:
        lines.append("## Не трогаем")
        for c in plan.skipped:
            lines.append(f"- {c.instrument.name} ({c.instrument.ticker}), убыток "
                         f"{rub(c.loss)}: {c.skip_reason}")
    return "\n".join(lines)
