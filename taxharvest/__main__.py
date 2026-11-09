"""CLI: python -m taxharvest data/portfolios/client_a.json [--asof 2026-11-09] [--agent]"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .optimizer import naive_plan, optimize
from .report import render, rub
from .sources import JsonSandboxSource

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="taxharvest", description="Налоговый оптимизатор портфеля")
    ap.add_argument("portfolio", help="JSON с портфелем клиента")
    ap.add_argument("--catalog", default=str(ROOT / "data" / "catalog.json"))
    ap.add_argument("--asof", default="2026-11-09", help="дата расчёта, YYYY-MM-DD")
    ap.add_argument("--agent", action="store_true", help="запустить LLM-агента (нужен ANTHROPIC_API_KEY)")
    ap.add_argument("--compare", action="store_true", help="сравнить с базовым правилом")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    src = JsonSandboxSource(args.portfolio, args.catalog)
    p, catalog, asof = src.portfolio(), src.catalog(), date.fromisoformat(args.asof)

    if args.agent:
        from .agent import run_agent
        print(run_agent(p, catalog, asof))
        return 0

    plan = optimize(p, catalog, asof)
    print(render(p, plan, catalog))
    if args.compare:
        base = naive_plan(p, catalog, asof)
        print("\n## Сравнение с базовым правилом «продать все убыточные и откупить»")
        print(f"- Агент: экономия {rub(plan.saving)}, издержки {rub(plan.costs)}, "
              f"чистая выгода {rub(plan.net_benefit)}, сделок {len(plan.actions)}")
        print(f"- Правило: экономия {rub(base.saving)}, издержки {rub(base.costs)}, "
              f"чистая выгода {rub(base.net_benefit)}, сделок {len(base.actions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
