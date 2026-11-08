import random
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from ev_fee_engine import (Config, Session, allocate_power, bill_residents,
                           effective_service_rate, period_of, price_session,
                           reconcile, simulate_night, split_energy)

D = Decimal
d = lambda h, m=0, day=27: datetime(2026, 10, day, h, m)


class TestSolarWindow(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(period_of(d(8, 59)), "non_solar")
        self.assertEqual(period_of(d(9, 0)), "solar")
        self.assertEqual(period_of(d(15, 59)), "solar")
        self.assertEqual(period_of(d(16, 0)), "non_solar")

    def test_midnight_is_non_solar(self):
        self.assertEqual(period_of(d(0, 0)), "non_solar")


class TestEnergySplit(unittest.TestCase):
    def test_fully_solar(self):
        r = split_energy(d(10), d(14), D("8"))
        self.assertEqual((r["solar"], r["non_solar"]), (D("8"), D("0")))

    def test_fully_non_solar(self):
        r = split_energy(d(20), d(23), D("6"))
        self.assertEqual((r["solar"], r["non_solar"]), (D("0"), D("6")))

    def test_crosses_1600(self):
        r = split_energy(d(15), d(17), D("10"))
        self.assertEqual((r["solar"], r["non_solar"]), (D("5"), D("5")))

    def test_crosses_0900_overnight(self):
        r = split_energy(d(23), d(11, 0, 28), D("12"))  # 12 h: 10 h non-solar, 2 h solar
        self.assertEqual(r["non_solar"], D("10"))
        self.assertEqual(r["solar"], D("2"))

    def test_multi_boundary_session(self):
        r = split_energy(d(15), d(10, 0, 28), D("19"))  # 1h solar, 17h non, 1h solar
        self.assertEqual(r["solar"], D("2"))
        self.assertEqual(r["non_solar"], D("17"))

    def test_energy_is_conserved_random(self):
        rng = random.Random(7)
        for _ in range(200):
            start = d(0) + timedelta(minutes=rng.randrange(0, 1440))
            end = start + timedelta(minutes=rng.randrange(1, 2000))
            kwh = D(rng.randrange(1, 500)) / D(10)
            r = split_energy(start, end, kwh)
            self.assertLess(abs(r["solar"] + r["non_solar"] - kwh), D("1e-12"))

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            split_energy(d(10), d(10), D("1"))
        with self.assertRaises(ValueError):
            split_energy(d(10), d(9), D("1"))
        with self.assertRaises(ValueError):
            split_energy(d(9), d(10), D("-1"))


class TestServiceChargeCap(unittest.TestCase):
    def test_ac_caps(self):
        cfg = Config(service_rate={"solar": D("3.00"), "non_solar": D("4.00")})
        self.assertEqual(effective_service_rate(cfg, "solar"), (D("3.00"), False))
        self.assertEqual(effective_service_rate(cfg, "non_solar"), (D("4.00"), False))

    def test_rate_above_cap_is_clamped_and_flagged(self):
        cfg = Config(service_rate={"solar": D("5.00"), "non_solar": D("9.00")})
        self.assertEqual(effective_service_rate(cfg, "solar"), (D("3.00"), True))
        self.assertEqual(effective_service_rate(cfg, "non_solar"), (D("4.00"), True))

    def test_dc_caps(self):
        cfg = Config(charger_type="DC", service_rate={"solar": D("20"), "non_solar": D("20")})
        self.assertEqual(effective_service_rate(cfg, "solar")[0], D("11.00"))
        self.assertEqual(effective_service_rate(cfg, "non_solar")[0], D("13.00"))

    def test_rate_below_cap_preserved(self):
        cfg = Config(service_rate={"solar": D("1.50"), "non_solar": D("2.50")})
        self.assertEqual(effective_service_rate(cfg, "solar"), (D("1.50"), False))

    def test_overcharging_never_possible_in_a_bill(self):
        cfg = Config(service_rate={"solar": D("99"), "non_solar": D("99")})
        p = price_session(Session("A", d(9), d(16), D("10")), cfg)
        self.assertEqual(p["service"], D("30.00"))  # 10 kWh x Rs3 cap, not Rs990
        self.assertTrue(p["capped"])


class TestFeeStack(unittest.TestCase):
    def test_non_solar_breakdown(self):
        p = price_session(Session("A", d(20), d(23), D("10")), Config())
        self.assertEqual(p["electricity"], D("60.00"))
        self.assertEqual(p["service"], D("40.00"))
        self.assertEqual(p["gst"], D("7.20"))
        self.assertEqual(p["total"], D("107.20"))

    def test_solar_is_cheaper_than_non_solar(self):
        sol = price_session(Session("A", d(10), d(13), D("10")), Config())
        non = price_session(Session("A", d(20), d(23), D("10")), Config())
        self.assertLess(sol["total"], non["total"])
        self.assertEqual(non["service"] - sol["service"], D("10.00"))  # Rs1/kWh x 10

    def test_electricity_is_pure_pass_through(self):
        cfg = Config(supply_tariff=D("6.00"))
        p = price_session(Session("A", d(9), d(16), D("7.5")), cfg)
        self.assertEqual(p["electricity"], D("45.00"))  # 7.5 x 6.00, no markup

    def test_land_cost_pass_through_and_no_gst_on_it(self):
        cfg = Config(land_cost=D("1.00"))
        p = price_session(Session("A", d(20), d(22), D("10")), cfg)
        self.assertEqual(p["land"], D("10.00"))
        self.assertEqual(p["gst"], D("7.20"))  # GST only on the service charge

    def test_zero_energy_zero_bill(self):
        p = price_session(Session("A", d(20), d(21), D("0")), Config())
        self.assertEqual(p["total"], D("0.00"))

    def test_split_session_mixed_rate(self):
        p = price_session(Session("A", d(15), d(17), D("10")), Config())  # 5 solar, 5 non-solar
        self.assertEqual(p["service"], D("35.00"))  # 5x3 + 5x4

    def test_bill_amounts_have_paise_precision(self):
        p = price_session(Session("A", d(15, 7), d(17, 13), D("3.33")), Config())
        for k in ("electricity", "service", "gst", "total"):
            self.assertEqual(p[k], p[k].quantize(D("0.01")))


class TestResidentBilling(unittest.TestCase):
    def test_aggregation_per_user(self):
        ss = [Session("A", d(20), d(22), D("5")), Session("B", d(20), d(22), D("3")),
              Session("A", d(22), d(23), D("2"))]
        b = bill_residents(ss, Config())
        self.assertEqual(b["A"]["kwh"], D("7"))
        self.assertEqual(b["B"]["kwh"], D("3"))
        total = sum(v["total"] for v in b.values())
        self.assertEqual(total, sum(price_session(s, Config())["total"] for s in ss))

    def test_reconcile_ok_and_mismatch(self):
        ss = [Session("A", d(20), d(22), D("5")), Session("B", d(20), d(22), D("3"))]
        self.assertTrue(reconcile(ss, D("8.0"))["ok"])
        self.assertFalse(reconcile(ss, D("9.5"))["ok"])


class TestLoadManagement(unittest.TestCase):
    def test_total_never_exceeds_limit(self):
        rng = random.Random(3)
        for _ in range(200):
            mx = {f"e{i}": rng.uniform(0.5, 11) for i in range(rng.randrange(1, 15))}
            limit = rng.uniform(1, 40)
            a = allocate_power(mx, limit)
            self.assertLessEqual(sum(a.values()), limit + 1e-6)
            for k in mx:
                self.assertLessEqual(a[k], mx[k] + 1e-9)

    def test_small_demand_fully_served(self):
        a = allocate_power({"x": 1.0, "y": 2.0}, 10)
        self.assertEqual(a, {"x": 1.0, "y": 2.0})

    def test_equal_demand_equal_share(self):
        a = allocate_power({"x": 7.4, "y": 7.4, "z": 7.4}, 12)
        for v in a.values():
            self.assertAlmostEqual(v, 4.0)

    def test_leftover_is_redistributed(self):
        a = allocate_power({"a": 1.0, "b": 10.0, "c": 10.0}, 12)
        self.assertAlmostEqual(a["a"], 1.0)
        self.assertAlmostEqual(a["b"], 5.5)
        self.assertAlmostEqual(a["c"], 5.5)

    def test_zero_limit(self):
        self.assertEqual(sum(allocate_power({"a": 5}, 0).values()), 0.0)


class TestSimulation(unittest.TestCase):
    def _evs(self):
        rng = random.Random(42)
        base = d(19)
        evs = [{"name": f"2W-{i}", "arrive": base + timedelta(minutes=rng.randrange(0, 180, 5)),
                "need_kwh": 2.0, "max_kw": 0.7} for i in range(6)]
        evs += [{"name": f"4W-{i}", "arrive": base + timedelta(minutes=rng.randrange(0, 180, 5)),
                 "need_kwh": 15.0, "max_kw": 7.4} for i in range(4)]
        return evs

    def test_managed_peak_within_limit_and_everyone_served(self):
        r = simulate_night(self._evs(), 15.0, managed=True)
        self.assertLessEqual(r["peak_kw"], 15.0 + 1e-6)
        self.assertLess(r["unserved_kwh"], 0.01)

    def test_unmanaged_would_breach_limit(self):
        r = simulate_night(self._evs(), 15.0, managed=False)
        self.assertGreater(r["peak_kw"], 15.0)

    def test_same_energy_delivered_either_way(self):
        a = simulate_night(self._evs(), 15.0, managed=True)
        b = simulate_night(self._evs(), 15.0, managed=False)
        self.assertAlmostEqual(a["delivered_kwh"], b["delivered_kwh"], places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)