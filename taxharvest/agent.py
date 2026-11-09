"""AI-агент: LLM оркестрирует инструменты, расчёты — детерминированные.

Агент получает задачу «проверь портфель клиента», сам вызывает инструменты
(налоговое состояние → кандидаты → аналоги → оптимизатор), может отклонить
предложенный аналог, если он не вписывается в неявную стратегию клиента,
и пишет объяснение простым языком. Числа LLM не придумывает — только берёт
из результатов инструментов.

Запуск: `python -m taxharvest <портфель> --agent` (нужны `pip install anthropic`
и ANTHROPIC_API_KEY). Без ключа CLI работает в детерминированном режиме.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date

from . import tax
from .analogs import find_analogs, metrics
from .models import Instrument, Portfolio, Settings
from .optimizer import collect_candidates, optimize
from .report import portfolio_profile, rub

MODEL = os.environ.get("TAXHARVEST_MODEL", "claude-opus-5-5")

SYSTEM_PROMPT = """Ты — налоговый ассистент инвестора в Т-Инвестициях.
Цель: снизить НДФЛ, который брокер удержит по итогам года, фиксируя бумажные
убытки, и при этом сохранить характеристики портфеля клиента (дюрацию,
доходность, риск, частоту купонов, дивидендную доходность).

Правила:
- Все суммы и показатели бери только из результатов инструментов. Не считай налог сам.
- Сначала вызови get_tax_state и get_portfolio_profile, затем list_candidates,
  для каждого кандидата — find_analogs, затем build_plan.
- Определи по составу портфеля неявную стратегию клиента (например, «ежемесячный
  купонный доход», «короткие ОФЗ», «дивидендные акции») и проверь, что аналоги ей
  соответствуют. Если аналог не подходит, исключи бумагу через build_plan(exclude=[...]).
- Не трогай позиции, которые инструменты пометили как близкие к льготе 3 года.
- Ты не даёшь индивидуальных инвестиционных рекомендаций: ты показываешь налоговые
  последствия вариантов, решение принимает клиент.
- Итог: короткое сообщение клиенту на русском — сколько можно сэкономить, какие
  сделки, почему аналог «тот же по сути», что не трогаем и почему."""

TOOLS = [
    {
        "name": "get_tax_state",
        "description": "Налоговое состояние клиента с начала года: финрез по ЦБ с купонами, "
                       "дивиденды, НДФЛ за год, сколько брокер ещё удержит, предельная ставка.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_portfolio_profile",
        "description": "Состав портфеля и средневзвешенные характеристики: дюрация и YTM "
                       "облигаций, дивдоходность акций, позиции с параметрами бумаг.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_candidates",
        "description": "Позиции с бумажным убытком, которые можно зафиксировать, и позиции, "
                       "которые трогать нельзя (с причиной).",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "find_analogs",
        "description": "До 3 аналогов бумаги с близкими дюрацией/доходностью/риском, "
                       "доступных клиенту (учитывает статус квалинвестора).",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "name": "build_plan",
        "description": "Оптимальный набор сделок: максимум экономии НДФЛ за вычетом комиссий "
                       "и спредов. exclude — тикеры, которые не продавать.",
        "input_schema": {
            "type": "object",
            "properties": {"exclude": {"type": "array", "items": {"type": "string"}}},
            "required": [],
            "additionalProperties": False,
        },
    },
]


class Toolbox:
    """Реализация инструментов агента поверх детерминированного ядра."""

    def __init__(self, p: Portfolio, catalog: dict[str, Instrument], asof: date,
                 s: Settings | None = None):
        self.p, self.catalog, self.asof, self.s = p, catalog, asof, s or Settings()

    def get_tax_state(self) -> dict:
        return asdict(tax.tax_state(self.p))

    def get_portfolio_profile(self) -> dict:
        holdings = {t: self.p.qty_of(t) for t in self.p.tickers()}
        positions = []
        for t, q in holdings.items():
            inst = self.catalog[t]
            positions.append({"ticker": t, "name": inst.name, "kind": inst.kind, "qty": q,
                              "value": round(inst.price * q),
                              "unrealized": round(tax.unrealized(self.p, inst)),
                              **metrics(inst, self.asof)})
        return {"qualified": self.p.qualified, "account_type": self.p.account_type,
                "summary": portfolio_profile(holdings, self.catalog, self.asof),
                "positions": positions}

    def list_candidates(self) -> list[dict]:
        return [{"ticker": c.instrument.ticker, "name": c.instrument.name, "qty": c.qty,
                 "loss": c.loss, "skip_reason": c.skip_reason}
                for c in collect_candidates(self.p, self.catalog, self.asof, self.s)]

    def find_analogs(self, ticker: str) -> list[dict]:
        src = self.catalog[ticker]
        return [{"ticker": a.instrument.ticker, "name": a.instrument.name,
                 "score": a.score, "metrics": a.metrics, "reasons": a.reasons}
                for a in find_analogs(src, self.catalog, self.p, self.asof, self.s)]

    def build_plan(self, exclude: list[str] | None = None) -> dict:
        exclude = set(exclude or [])
        p = Portfolio(**{**self.p.__dict__,
                         "lots": [l for l in self.p.lots if l.ticker not in exclude]})
        plan = optimize(p, self.catalog, self.asof, self.s)
        return {
            "tax_before": plan.tax_before.tax_total, "tax_after": plan.tax_after.tax_total,
            "saving": plan.saving, "costs": plan.costs, "net_benefit": plan.net_benefit,
            "actions": [{"sell": a.sell.ticker, "sell_qty": a.sell_qty, "realized": a.realized,
                         "buy": a.buy.ticker, "buy_qty": a.buy_qty, "saving": a.saving,
                         "cost": a.cost} for a in plan.actions],
            "excluded_by_agent": sorted(exclude),
        }

    def call(self, name: str, args: dict) -> str:
        fn = getattr(self, name, None)
        if fn is None or name not in {t["name"] for t in TOOLS}:
            raise ValueError(f"Неизвестный инструмент {name}")
        return json.dumps(fn(**args), ensure_ascii=False, default=str)


def run_agent(p: Portfolio, catalog: dict[str, Instrument], asof: date,
              max_turns: int = 15) -> str:
    """Ручной цикл tool use через Anthropic SDK."""
    import anthropic  # опциональная зависимость

    client = anthropic.Anthropic()
    box = Toolbox(p, catalog, asof)
    messages: list = [{"role": "user", "content":
                       f"Проверь портфель клиента {p.client_id} на {asof:%d.%m.%Y} "
                       f"и подготовь сообщение для клиента."}]
    for _ in range(max_turns):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": "medium"},
            # при отказе классификатора запрос автоматически уходит на резервную модель
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return "Модель отказалась отвечать на запрос."
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "end_turn":
            return "".join(b.text for b in response.content if b.type == "text")
        if response.stop_reason == "pause_turn":
            continue
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                out, err = box.call(block.name, dict(block.input)), False
            except Exception as exc:  # ошибку инструмента возвращаем модели
                out, err = f"Ошибка: {exc}", True
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": out, "is_error": err})
        messages.append({"role": "user", "content": results})
    return "Агент не уложился в лимит шагов."


def explain_without_llm(plan_dict: dict) -> str:
    """Шаблонное объяснение для режима без LLM (демо, тесты)."""
    if not plan_dict["actions"]:
        return "Сейчас фиксировать убытки невыгодно — проверю портфель снова при изменениях."
    parts = [f"Можно сэкономить {rub(plan_dict['net_benefit'])} НДФЛ по итогам года."]
    for a in plan_dict["actions"]:
        parts.append(f"Продать {a['sell_qty']} {a['sell']} (убыток {rub(a['realized'])}) "
                     f"и купить {a['buy_qty']} {a['buy']}.")
    return " ".join(parts)
