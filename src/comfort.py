"""fanger pmv/ppd thermal comfort (iso 7730).

i compute pmv in python instead of leaning on energyplus people objects, so the
agent has a comfort number it can actually reason about and the guardrail can hold
the line on, no matter what the building model does.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ComfortResult:
    pmv: float
    ppd: float  # predicted percentage dissatisfied, %

    @property
    def acceptable(self) -> bool:
        return abs(self.pmv) <= 0.5  # ashrae-55 "comfortable" band


def pmv_ppd(
    ta: float,          # air temp, degC
    tr: float,          # mean radiant temp, degC
    vel: float = 0.1,   # relative air velocity, m/s
    rh: float = 50.0,   # relative humidity, %
    met: float = 1.1,   # metabolic rate, met
    clo: float = 0.5,   # clothing insulation, clo
    wme: float = 0.0,   # external work, met
) -> ComfortResult:
    """the standard fanger model. returns pmv and ppd."""
    pa = rh * 10.0 * math.exp(16.6536 - 4030.183 / (ta + 235.0))  # water vapour pressure, Pa

    icl = 0.155 * clo
    m = met * 58.15
    w = wme * 58.15
    mw = m - w

    fcl = 1.05 + 0.645 * icl if icl > 0.078 else 1.0 + 1.29 * icl

    hcf = 12.1 * math.sqrt(vel)
    taa = ta + 273.0
    tra = tr + 273.0

    # solve for the clothing surface temp by iterating
    tcla = taa + (35.5 - ta) / (3.5 * icl + 0.1)
    p1 = icl * fcl
    p2 = p1 * 3.96
    p3 = p1 * 100.0
    p4 = p1 * taa
    p5 = 308.7 - 0.028 * mw + p2 * (tra / 100.0) ** 4
    xn = tcla / 100.0
    xf = xn
    n = 0
    eps = 1e-5
    while True:
        xf = (xf + xn) / 2.0
        hcn = 2.38 * abs(100.0 * xf - taa) ** 0.25
        hc = hcf if hcf > hcn else hcn
        xn = (p5 + p4 * hc - p2 * xf ** 4) / (100.0 + p3 * hc)
        n += 1
        if abs(xn - xf) <= eps or n > 150:
            break
    tcl = 100.0 * xn - 273.0

    hl1 = 3.05e-3 * (5733.0 - 6.99 * mw - pa)
    hl2 = 0.42 * (mw - 58.15) if mw > 58.15 else 0.0
    hl3 = 1.7e-5 * m * (5867.0 - pa)
    hl4 = 0.0014 * m * (34.0 - ta)
    hl5 = 3.96 * fcl * (xn ** 4 - (tra / 100.0) ** 4)
    hl6 = fcl * hc * (tcl - ta)

    ts = 0.303 * math.exp(-0.036 * m) + 0.028
    pmv = ts * (mw - hl1 - hl2 - hl3 - hl4 - hl5 - hl6)
    ppd = 100.0 - 95.0 * math.exp(-0.03353 * pmv ** 4 - 0.2179 * pmv ** 2)
    return ComfortResult(pmv=round(pmv, 3), ppd=round(ppd, 2))


def clo_for_season(outdoor_c: float, cfg) -> float:
    """rough clothing level from the outdoor temp (just a heating/cooling split)."""
    return cfg["comfort"]["clo_summer"] if outdoor_c >= 15 else cfg["comfort"]["clo_winter"]
