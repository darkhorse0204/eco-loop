"""guardrail + a plain deterministic controller.

the guardrail is the hard safety net: whatever setpoints something (the llm or the
rule policy) hands over, it clamps them so we never trade away comfort for energy.

the rule controller is a simple, readable grid-aware policy. it does two jobs:
it's the fallback when the llm is slow or down, and it's a solid non-ai baseline to
measure the llm against. the llm should match or beat it, and explain itself while
doing it.
"""
from __future__ import annotations

from .runner import CONTROLLED_ZONES, Decision, Snapshot


class Guardrail:
    def __init__(self, cfg):
        c = cfg["comfort"]
        self.oh_min, self.oh_max = c["occupied_heating_min_c"], c["occupied_heating_max_c"]
        self.oc_min, self.oc_max = c["occupied_cooling_min_c"], c["occupied_cooling_max_c"]
        self.deadband = c["min_deadband_c"]
        self.uh_min = c["unocc_heating_min_c"]
        self.uc_max = c["unocc_cooling_max_c"]

    def clamp(self, heating: float, cooling: float, occupied: bool) -> tuple[float, float]:
        if occupied:
            h = min(max(heating, self.oh_min), self.oh_max)
            c = min(max(cooling, self.oc_min), self.oc_max)
        else:
            # let it set back, but never past [unocc_min .. occupied_max]
            h = min(max(heating, self.uh_min), self.oh_max)
            c = min(max(cooling, self.oc_min), self.uc_max)
        # keep a minimum deadband so cooling stays above heating
        if c - h < self.deadband:
            mid = (c + h) / 2.0
            h = mid - self.deadband / 2.0
            c = mid + self.deadband / 2.0
            if occupied:  # re-clamp to the occupied envelope after widening
                h = min(max(h, self.oh_min), self.oh_max)
                c = min(max(c, self.oc_min), self.oc_max)
        return round(h, 2), round(c, 2)

    def apply(self, setpoints: dict, occupied: bool) -> dict:
        return {
            z: self.clamp(hc[0], hc[1], occupied) for z, hc in setpoints.items()
        }


class RuleController:
    """occupancy- and grid-aware setpoint policy, no llm involved."""

    name = "rule"

    def __init__(self, cfg):
        self.cfg = cfg
        self.guard = Guardrail(cfg)

    def __call__(self, snap: Snapshot) -> Decision:
        h, c, why = self._policy(snap)
        sp = self.guard.apply({z: (h, c) for z in CONTROLLED_ZONES}, snap.occupied)
        return Decision(setpoints=sp, rationale=why, source="rule")

    def _policy(self, snap: Snapshot) -> tuple[float, float, str]:
        if not snap.occupied:
            # only a moderate setup, not a deep setback. in a humid climate a deep
            # setback just buys a big morning latent-recovery bill that eats the
            # overnight savings. 27.5c coasts along and comes back cheap.
            return 15.6, 27.5, "unoccupied: moderate setup 27.5c (avoids the recovery penalty)."

        # occupied: sit at a comfy-but-efficient 25.0c (pmv ~0.3, still inside the
        # +/-0.5 band), and nudge up to 25.5c during the peak / dirty-grid window to
        # shave the priciest, dirtiest kwh.
        heating = 20.0
        peak = snap.grid["is_peak_period"]
        dirty = snap.grid["carbon_gco2_kwh"] >= 0.7 * (
            self.cfg["grid"]["carbon_base"] + self.cfg["grid"]["carbon_peak"]
        )
        if peak or dirty:
            return heating, 25.5, "occupied + peak/dirty grid: float to 25.5c to shave costly load."
        return heating, 25.0, "occupied: efficient 25.0c setpoint (pmv stays in the band)."
