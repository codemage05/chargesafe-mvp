# ChargeSafe MVP
Billing and load-management engine for apartment EV charging (simulation-grade, not field-tested).

This repository is a small, self-contained implementation submitted for challenge
verification. It is intended to make the source code and automated tests easy to
review; it is not a production deployment or field-tested charging system.

## Run locally

The project uses only the Python standard library.

```bash
python3 -m unittest test_ev_fee_engine -v
python3 ev_fee_engine.py
```

The first command runs the automated test suite. The second runs the demo.

## Contents

- `ev_fee_engine.py` - billing, time-of-use energy splitting, and fair-share load management
- `test_ev_fee_engine.py` - automated tests for the engine

No separate repository link is required for local verification; this repository
can be submitted as the optional challenge repository link.

Sources for parameters: Ministry of Power EV Charging Guidelines (17 Sept 2024), clause 10(1) service-charge ceilings; Telangana LT-IX EV tariff FY2026-27. The GST rate (18%) is an assumption and configurable.