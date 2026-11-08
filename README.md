# ChargeSafe MVP
Billing and load-management engine for apartment EV charging (simulation-grade, not field-tested).

Run the tests: python3 -m unittest test_ev_fee_engine -v
Run the demo:  python3 ev_fee_engine.py

Sources for parameters: Ministry of Power EV Charging Guidelines (17 Sept 2024), clause 10(1) service-charge ceilings; Telangana LT-IX EV tariff FY2026-27. The GST rate (18%) is an assumption and configurable.