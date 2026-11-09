"""Источники данных.

MVP работает на синтетических JSON-данных (sandbox). Боевой источник —
T-Invest MCP: адаптер ниже описывает, какие данные из него нужны.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Protocol

from .models import Instrument, Lot, Portfolio


class DataSource(Protocol):
    def portfolio(self) -> Portfolio: ...
    def catalog(self) -> dict[str, Instrument]: ...


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def load_catalog(path: str | Path) -> dict[str, Instrument]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for item in raw["instruments"]:
        item = dict(item)
        item["maturity"] = _d(item.get("maturity"))
        inst = Instrument(**item)
        out[inst.ticker] = inst
    return out


def load_portfolio(path: str | Path) -> Portfolio:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    lots = [Lot(l["ticker"], l["qty"], l["price"], date.fromisoformat(l["bought"]))
            for l in raw.pop("lots")]
    raw.pop("_comment", None)
    return Portfolio(lots=lots, **raw)


class JsonSandboxSource:
    def __init__(self, portfolio_path: str | Path, catalog_path: str | Path):
        self._p = portfolio_path
        self._c = catalog_path

    def portfolio(self) -> Portfolio:
        return load_portfolio(self._p)

    def catalog(self) -> dict[str, Instrument]:
        return load_catalog(self._c)


class TInvestMcpSource:
    """Адаптер к T-Invest MCP (этап SYSTEM BUILD).

    Что нужно получить (в терминах T-Invest API, которое оборачивает MCP):
      * счета и их тип (брокерский / ИИС)            — UsersService.GetAccounts
      * статус квалинвестора                          — UsersService.GetInfo
      * позиции, средняя цена, текущая цена           — OperationsService.GetPortfolio
      * сделки, купоны, дивиденды, удержанный налог   — OperationsService.GetOperationsByCursor
      * справочник облигаций и акций, риск, флаг квал — InstrumentsService.Bonds / Shares
      * график купонов                                — InstrumentsService.GetBondCoupons
      * последние цены и стакан (спред)               — MarketDataService.GetLastPrices / GetOrderBook
      * выставление заявок (после подтверждения)      — OrdersService.PostOrder

    Точные имена MCP-инструментов сверим с документацией developer.tbank.ru/invest/mcp
    на этапе CONTEXT LOADED. Налоговой базы и финреза в API нет — восстанавливаем
    по операциям (FIFO) и сверяем с отчётом брокера.
    """

    def __init__(self, mcp_session, account_id: str):
        self.session = mcp_session
        self.account_id = account_id

    def portfolio(self) -> Portfolio:  # pragma: no cover
        raise NotImplementedError("Подключение к T-Invest MCP — этап SYSTEM BUILD")

    def catalog(self) -> dict[str, Instrument]:  # pragma: no cover
        raise NotImplementedError("Подключение к T-Invest MCP — этап SYSTEM BUILD")
