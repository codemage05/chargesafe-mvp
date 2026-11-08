"""Simple EV charging fee calculations."""


def calculate_fee(
    energy_kwh: float,
    price_per_kwh: float,
    session_fee: float = 0.0,
) -> float:
    """Return the total charging fee, rounded to cents."""
    if energy_kwh < 0:
        raise ValueError("energy_kwh must be non-negative")
    if price_per_kwh < 0:
        raise ValueError("price_per_kwh must be non-negative")
    if session_fee < 0:
        raise ValueError("session_fee must be non-negative")

    return round(energy_kwh * price_per_kwh + session_fee, 2)
