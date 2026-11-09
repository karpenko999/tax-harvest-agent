import unittest
from datetime import date
from pathlib import Path

from taxharvest import bonds, tax
from taxharvest.agent import TOOLS, Toolbox
from taxharvest.analogs import find_analogs
from taxharvest.models import Instrument, Lot, Settings
from taxharvest.optimizer import brute_force, naive_plan, optimize
from taxharvest.sources import load_catalog, load_portfolio

ROOT = Path(__file__).resolve().parent.parent
ASOF = date(2026, 11, 9)
CATALOG = load_catalog(ROOT / "data" / "catalog.json")


def portfolio(name: str):
    return load_portfolio(ROOT / "data" / "portfolios" / f"client_{name}.json")


class TaxTests(unittest.TestCase):
    def test_progressive_rate(self):
        self.assertEqual(tax.ndfl(1_000_000), 130_000)
        self.assertEqual(tax.ndfl(2_400_000), 312_000)
        self.assertEqual(tax.ndfl(3_400_000), 312_000 + 150_000)

    def test_losses_do_not_reduce_dividends(self):
        # убыток по ЦБ больше прибыли — дивиденды всё равно облагаются
        self.assertEqual(tax.tax_for(-500_000, 100_000), 13_000)

    def test_fifo_and_ldv(self):
        s = Settings()
        lots = [Lot("X", 10, 50.0, date(2022, 1, 1)),   # под ЛДВ, прибыль не облагается
                Lot("X", 10, 150.0, date(2026, 1, 1))]
        r, used = tax.fifo_result(lots, 15, 100.0, ASOF, s)
        self.assertEqual(r, 0 + (100 - 150) * 5)
        self.assertEqual([q for _, q in used], [10, 5])


class BondTests(unittest.TestCase):
    def test_par_bond_ytm_equals_coupon(self):
        b = Instrument("P", "par", "bond", price=1000.0, sector="x", risk_level=1,
                       coupon_rate=10.0, coupon_freq=1, maturity=date(2030, 11, 9))
        self.assertAlmostEqual(bonds.ytm(b, date(2026, 11, 9)), 10.0, places=1)

    def test_duration_below_maturity(self):
        b = CATALOG["OFZ-A31"]
        years_to_maturity = (b.maturity - ASOF).days / 365
        self.assertLess(bonds.duration(b, ASOF), years_to_maturity)


class AnalogTests(unittest.TestCase):
    def test_qual_only_hidden_from_non_qualified(self):
        p = portfolio("a")
        found = find_analogs(CATALOG["CORP-L29"], CATALOG, p, ASOF, Settings(), top=10)
        self.assertNotIn("CORP-Q29", [a.instrument.ticker for a in found])

    def test_qual_only_visible_to_qualified(self):
        p = portfolio("b")
        found = find_analogs(CATALOG["CORP-L29"], CATALOG, p, ASOF, Settings(), top=10)
        self.assertIn("CORP-Q29", [a.instrument.ticker for a in found])

    def test_no_riskier_analog(self):
        p = portfolio("a")
        for a in find_analogs(CATALOG["OILA"], CATALOG, p, ASOF, Settings(), top=10):
            self.assertLessEqual(a.instrument.risk_level, CATALOG["OILA"].risk_level)


class OptimizerTests(unittest.TestCase):
    def test_client_a_skips_ldv_and_saves(self):
        plan = optimize(portfolio("a"), CATALOG, ASOF)
        sold = {a.sell.ticker for a in plan.actions}
        self.assertNotIn("BANKA", sold)          # близко к ЛДВ
        self.assertNotIn("TECHA", sold)          # в прибыли
        self.assertGreater(plan.net_benefit, 0)

    def test_client_b_partial_sale_does_not_overshoot(self):
        p = portfolio("b")
        plan = optimize(p, CATALOG, ASOF)
        realized = sum(a.realized for a in plan.actions)
        self.assertLessEqual(p.realized_securities_result + realized, 0)
        self.assertGreater(p.realized_securities_result + realized, -5_000)
        self.assertEqual(plan.tax_after.tax_total, tax.ndfl(p.dividends_ytd))

    def test_client_c_no_actions(self):
        plan = optimize(portfolio("c"), CATALOG, ASOF)
        self.assertEqual(plan.actions, [])

    def test_greedy_close_to_brute_force(self):
        for name in "abc":
            p = portfolio(name)
            best = brute_force(p, CATALOG, ASOF)
            got = optimize(p, CATALOG, ASOF).net_benefit
            self.assertGreaterEqual(got, 0.9 * best, name)

    def test_beats_naive_on_costs_when_base_is_small(self):
        p = portfolio("b")
        self.assertGreater(optimize(p, CATALOG, ASOF).net_benefit,
                           naive_plan(p, CATALOG, ASOF).net_benefit)


class AgentToolTests(unittest.TestCase):
    def test_every_tool_is_callable(self):
        box = Toolbox(portfolio("a"), CATALOG, ASOF)
        for t in TOOLS:
            args = {"ticker": "OILA"} if t["name"] == "find_analogs" else {}
            self.assertTrue(box.call(t["name"], args))

    def test_exclude(self):
        box = Toolbox(portfolio("a"), CATALOG, ASOF)
        plan = box.build_plan(exclude=["OILA"])
        self.assertNotIn("OILA", [a["sell"] for a in plan["actions"]])


if __name__ == "__main__":
    unittest.main()
