"""grid signals - marginal carbon intensity and a time-of-use price.

these are synthetic but shaped like the real thing, so the demo runs fully offline
and repeatably. when you want real data, swap carbon_intensity()/price() for a
watttime or electricity maps call and nothing else in the pipeline has to change.
"""
from __future__ import annotations

import math


class GridSignals:
    def __init__(self, cfg):
        g = cfg["grid"]
        self.cbase = g["carbon_base"]
        self.cpeak = g["carbon_peak"]
        self.p_off = g["price_offpeak"]
        self.p_peak = g["price_peak"]
        self.peak_start, self.peak_end = g["peak_hours"]

    def carbon_intensity(self, hour: float) -> float:
        """gco2/kwh. dips midday when solar is up, spikes on the evening ramp."""
        # solar trough around 13:00, evening peak around 19:00
        solar = math.cos((hour - 13.0) / 24.0 * 2 * math.pi)      # +1 midday
        evening = math.exp(-((hour - 19.0) ** 2) / (2 * 2.5 ** 2))  # bump at 7pm
        ci = self.cbase + 0.35 * (self.cpeak - self.cbase) * (0.5 - 0.5 * solar)
        ci += (self.cpeak - self.cbase) * 0.65 * evening
        return round(ci, 1)

    def price(self, hour: float) -> float:
        """$/kwh time-of-use tariff."""
        return self.p_peak if self.peak_start <= hour < self.peak_end else self.p_off

    def is_peak(self, hour: float) -> bool:
        return self.peak_start <= hour < self.peak_end

    def snapshot(self, hour: float) -> dict:
        return {
            "hour": round(hour, 2),
            "carbon_gco2_kwh": self.carbon_intensity(hour),
            "price_usd_kwh": self.price(hour),
            "is_peak_period": self.is_peak(hour),
        }
