"""Модели данных: инструменты, лоты, портфель клиента."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Kind = Literal["bond", "share"]


@dataclass(frozen=True)
class Instrument:
    """Ценная бумага из справочника (в продакшене — из T-Invest MCP)."""

    ticker: str
    name: str
    kind: Kind
    price: float  # текущая цена за 1 бумагу, ₽ (для облигаций — чистая цена)
    sector: str
    risk_level: int  # 1 — низкий, 2 — средний, 3 — высокий (как в T-Invest API)
    currency: str = "RUB"
    qual_only: bool = False  # бумага только для квалифицированных инвесторов
    lot: int = 1
    spread_pct: float = 0.1  # ширина спреда в стакане, % от цены
    # облигации
    nominal: float = 1000.0
    coupon_rate: float = 0.0  # годовой купон, % от номинала
    coupon_freq: int = 2  # выплат в год
    maturity: date | None = None
    # акции
    dividend_yield: float = 0.0  # ожидаемая дивидендная доходность, %
    beta: float = 1.0


@dataclass(frozen=True)
class Lot:
    """Покупка бумаги: брокер списывает лоты по FIFO."""

    ticker: str
    qty: int
    price: float  # цена покупки за 1 бумагу, ₽ (с учётом комиссии)
    bought: date


@dataclass
class Portfolio:
    client_id: str
    qualified: bool
    account_type: str  # "broker" | "iis"
    lots: list[Lot]
    # Финансовый результат с начала года по операциям с ЦБ (включая купоны), ₽.
    realized_securities_result: float = 0.0
    dividends_ytd: float = 0.0  # дивиденды (сальдировать с убытками нельзя)
    other_investment_income: float = 0.0
    commission_rate: float = 0.0005  # комиссия брокера за сделку, доля
    tax_withheld_ytd: float = 0.0  # налог, уже удержанный в течение года

    def tickers(self) -> list[str]:
        seen: list[str] = []
        for lot in self.lots:
            if lot.ticker not in seen:
                seen.append(lot.ticker)
        return seen

    def lots_of(self, ticker: str) -> list[Lot]:
        return sorted((l for l in self.lots if l.ticker == ticker), key=lambda l: l.bought)

    def qty_of(self, ticker: str) -> int:
        return sum(l.qty for l in self.lots if l.ticker == ticker)


@dataclass
class Settings:
    ldv_years: float = 3.0  # льгота долгосрочного владения (ст. 219.1 НК РФ)
    ldv_guard_years: float = 2.5  # не трогаем лоты, которым скоро положена ЛДВ
    max_duration_diff: float = 0.5  # лет
    max_ytm_diff: float = 1.0  # п.п.
    max_dividend_diff: float = 2.0  # п.п.
    min_saving_rub: float = 1000.0  # ниже этого порога клиента не беспокоим
    allow_same_ticker: bool = False  # True — «продать и откупить ту же бумагу»
    extra: dict = field(default_factory=dict)
