"""
ev_fee_engine.py - MVP core for an apartment EV charging management platform.

What it does
  1. Fee stack billing per MoP Guidelines (17 Sept 2024), clause 8:
       total = electricity (pass-through) + service charge (capped, cl. 10(1))
               + land cost (pass-through) + GST on the service charge
  2. Splits every session's energy into solar hours (09:00-16:00) and
     non-solar hours, so the right service-charge ceiling is applied.
  3. Fair-share load management: keeps total charging power under the
     society's sanctioned EV load (water-filling allocation).

Parameters and their sources
  - Service-charge ceilings (AC Rs3/Rs4, DC Rs11/Rs13 per kWh, till 31 Mar 2028,
    subject to annual review): MoP guidelines clause 10(1).
  - Supply tariff default Rs6.00/kWh: Telangana retail supply tariff schedule
    FY2026-27, category LT-IX (EV charging stations). A flat rate for LT.
  - GST rate on the service charge is an ASSUMPTION (default 18%); confirm with
    a tax advisor before relying on it. It is a configurable parameter.

This is a simulation-grade MVP, not a field pilot.
"""
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List

SOLAR_START = time(9, 0)
SOLAR_END = time(16, 0)

SERVICE_CHARGE_CAP = {
    ("AC", "solar"): Decimal("3.00"),
    ("AC", "non_solar"): Decimal("4.00"),
    ("DC", "solar"): Decimal("11.00"),
    ("DC", "non_solar"): Decimal("13.00"),
}


def _q(x: Decimal) -> Decimal:
    """Round to paise."""
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def period_of(t: datetime) -> str:
    return "solar" if SOLAR_START <= t.time() < SOLAR_END else "non_solar"


def _next_boundary(t: datetime) -> datetime:
    candidates = []
    for h in (SOLAR_START, SOLAR_END):
        b = datetime.combine(t.date(), h)
        if b > t:
            candidates.append(b)
    candidates.append(datetime.combine(t.date() + timedelta(days=1), SOLAR_START))
    return min(candidates)


def split_energy(start: datetime, end: datetime, kwh: Decimal) -> Dict[str, Decimal]:
    """Split a session's energy between solar and non-solar hours (constant power assumed)."""
    if end <= start:
        raise ValueError("session end must be after start")
    if kwh < 0:
        raise ValueError("energy cannot be negative")
    total = Decimal(str((end - start).total_seconds()))
    out = {"solar": Decimal("0"), "non_solar": Decimal("0")}
    t = start
    while t < end:
        nxt = min(_next_boundary(t), end)
        share = Decimal(str((nxt - t).total_seconds())) / total
        out[period_of(t)] += kwh * share
        t = nxt
    return out


@dataclass(frozen=True)
class Config:
    supply_tariff: Decimal = Decimal("6.00")  # Rs/kWh, pass-through (Telangana LT-IX FY26-27)
    service_rate: Dict[str, Decimal] = field(
        default_factory=lambda: {"solar": Decimal("3.00"), "non_solar": Decimal("4.00")}
    )
    charger_type: str = "AC"
    land_cost: Decimal = Decimal("0")  # Rs/kWh, pass-through at actuals
    gst_on_service: Decimal = Decimal("0.18")  # ASSUMPTION - confirm applicable GST


@dataclass
class Session:
    user: str
    start: datetime
    end: datetime
    kwh: Decimal


def effective_service_rate(cfg: Config, period: str):
    """Return (rate, was_capped). The configured rate can never exceed the ceiling."""
    cap = SERVICE_CHARGE_CAP[(cfg.charger_type, period)]
    rate = cfg.service_rate[period]
    if rate > cap:
        return cap, True
    return rate, False


def price_session(s: Session, cfg: Config) -> Dict:
    parts = split_energy(s.start, s.end, s.kwh)
    electricity = service = Decimal("0")
    capped = False
    for period, kwh in parts.items():
        rate, c = effective_service_rate(cfg, period)
        capped = capped or (c and kwh > 0)
        electricity += kwh * cfg.supply_tariff
        service += kwh * rate
    land = s.kwh * cfg.land_cost
    electricity, service, land = _q(electricity), _q(service), _q(land)
    gst = _q(service * cfg.gst_on_service)
    return {
        "user": s.user,
        "solar_kwh": _q(parts["solar"]),
        "non_solar_kwh": _q(parts["non_solar"]),
        "electricity": electricity,
        "service": service,
        "land": land,
        "gst": gst,
        "total": electricity + service + land + gst,
        "capped": capped,
    }


def bill_residents(sessions: List[Session], cfg: Config) -> Dict[str, Dict]:
    bills: Dict[str, Dict] = {}
    for s in sessions:
        p = price_session(s, cfg)
        b = bills.setdefault(
            s.user,
            {"kwh": Decimal("0"), "electricity": Decimal("0"), "service": Decimal("0"),
             "land": Decimal("0"), "gst": Decimal("0"), "total": Decimal("0")},
        )
        b["kwh"] += s.kwh
        for k in ("electricity", "service", "land", "gst", "total"):
            b[k] += p[k]
    return bills


def reconcile(sessions: List[Session], submeter_kwh: Decimal, tolerance: Decimal = Decimal("0.05")) -> Dict:
    """Compare billed energy against the society sub-meter reading (the dispute-proof check)."""
    billed = sum((s.kwh for s in sessions), Decimal("0"))
    diff = submeter_kwh - billed
    return {"billed_kwh": billed, "submeter_kwh": submeter_kwh, "difference": diff,
            "ok": abs(diff) <= tolerance}


def allocate_power(max_kw: Dict[str, float], limit_kw: float) -> Dict[str, float]:
    """Fair-share (water-filling) allocation: nobody exceeds their own maximum,
    the total never exceeds the limit, and unused share is redistributed."""
    alloc = {k: 0.0 for k in max_kw}
    remaining = float(limit_kw)
    active = {k for k, v in max_kw.items() if v > 0}
    while active and remaining > 1e-9:
        share = remaining / len(active)
        satisfied = {k for k in active if max_kw[k] - alloc[k] <= share + 1e-12}
        if not satisfied:
            for k in active:
                alloc[k] += share
            remaining = 0.0
        else:
            for k in satisfied:
                give = max_kw[k] - alloc[k]
                alloc[k] += give
                remaining -= give
            active -= satisfied
    return alloc


def simulate_night(evs: List[Dict], limit_kw: float, managed: bool = True, step_min: int = 5,
                   start: datetime = datetime(2026, 10, 27, 19, 0),
                   end: datetime = datetime(2026, 10, 28, 7, 0)) -> Dict:
    """Simulate one evening-to-morning cycle. evs: dicts with name, arrive, need_kwh, max_kw."""
    remaining = {e["name"]: float(e["need_kwh"]) for e in evs}
    dt = step_min / 60.0
    t, peak, delivered = start, 0.0, 0.0
    while t < end:
        active = {}
        for e in evs:
            n = e["name"]
            if e["arrive"] <= t and remaining[n] > 1e-9:
                active[n] = min(float(e["max_kw"]), remaining[n] / dt)
        alloc = allocate_power(active, limit_kw) if managed else active
        total = sum(alloc.values())
        peak = max(peak, total)
        for n, kw in alloc.items():
            remaining[n] -= kw * dt
            delivered += kw * dt
        t += timedelta(minutes=step_min)
    return {"peak_kw": round(peak, 2), "delivered_kwh": round(delivered, 2),
            "unserved_kwh": round(sum(max(r, 0.0) for r in remaining.values()), 3)}


if __name__ == "__main__":
    import random

    rng = random.Random(42)
    base = datetime(2026, 10, 27, 19, 0)
    evs = []
    for i in range(6):  # electric two-wheelers: 0.7 kW charger (3.5 kWh / 5 h), partial top-up
        evs.append({"name": f"2W-{i+1}", "arrive": base + timedelta(minutes=rng.randrange(0, 180, 5)),
                    "need_kwh": 2.0, "max_kw": 0.7})
    for i in range(4):  # electric cars on 7.4 kW AC
        evs.append({"name": f"4W-{i+1}", "arrive": base + timedelta(minutes=rng.randrange(0, 180, 5)),
                    "need_kwh": 15.0, "max_kw": 7.4})
    limit = 15.0
    free = simulate_night(evs, limit, managed=False)
    ctrl = simulate_night(evs, limit, managed=True)
    print("Society of 10 EVs, sanctioned EV load limit = %.0f kW" % limit)
    print("  Unmanaged: peak %.2f kW, delivered %.1f kWh" % (free["peak_kw"], free["delivered_kwh"]))
    print("  Managed:   peak %.2f kW, delivered %.1f kWh, unserved %.3f kWh"
          % (ctrl["peak_kw"], ctrl["delivered_kwh"], ctrl["unserved_kwh"]))

    cfg = Config()
    s2w = Session("Flat 101 (2W)", datetime(2026, 10, 27, 21, 0), datetime(2026, 10, 28, 0, 0), Decimal("2.0"))
    s4w = Session("Flat 202 (car)", datetime(2026, 10, 27, 20, 0), datetime(2026, 10, 27, 22, 0), Decimal("15.0"))
    for s in (s2w, s4w):
        p = price_session(s, cfg)
        print("  Bill %-15s %5s kWh: electricity %6s + service %6s + GST %5s = %7s"
              % (s.user, s.kwh, p["electricity"], p["service"], p["gst"], p["total"]))