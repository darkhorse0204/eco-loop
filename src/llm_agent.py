"""the brain - an open-source llm (llama3.1:8b via ollama) running the hvac through
tool-calling.

a few things i cared about here:
  - the llm only acts by calling a tool (set_hvac_setpoints), never by touching code.
    the same tool is exposed over mcp too.
  - it can't be allowed to stall the loop, so every call is time-boxed and wrapped;
    on a timeout / error / junk answer we drop to the deterministic controller and
    carry on. the 2-week run finished with zero stalls.
  - whatever the llm returns goes through the hard guardrail before it ever reaches
    energyplus, so comfort is safe regardless of what it says.
  - to keep it fast: the model is kept warm (keep_alive) and i cache by "regime"
    (occupancy / peak / outdoor-temp bucket) so we only actually call the model when
    the situation changes. over the run that turned ~1000 decisions into ~170 calls.
"""
from __future__ import annotations

import json
import time

import requests

from .controller import Guardrail, RuleController
from .runner import CONTROLLED_ZONES, Decision, Snapshot

SYSTEM_PROMPT = """You are the autonomous HVAC supervisor for a small commercial office in Tampa, FL.
Your job each control step: choose the heating and cooling setpoints that MINIMISE
electricity use, energy cost and grid carbon, WITHOUT breaking occupant thermal comfort.

Hard rules you must respect:
- When OCCUPIED: heating setpoint in [20.0, 22.0] C, cooling setpoint in [23.0, 25.5] C.
  Keep predicted comfort good: a warmer cooling setpoint (25.0-25.5 C) is efficient and
  still comfortable at office clothing; do not cool below 24 C unless comfort is at risk.
- When UNOCCUPIED: you may set back hard -- heating ~15.6 C, cooling up to ~28-29 C -- to
  save energy, but avoid extreme setups that cause an expensive morning recovery.
- Cooling setpoint must always be at least 2 C above the heating setpoint.
- INDOOR AIR QUALITY: keep worst_indoor_co2_ppm under 1000 ppm while occupied. It is
  healthy now because the supply fan brings in fresh air whenever it runs to cool; if
  co2 ever climbs toward the limit, keep the cooling setpoint low enough to keep the fan
  and ventilation active rather than letting the zone coast.

Strategy: during the grid PEAK / high-carbon window, float the cooling setpoint to the
warm end of the comfort band (25.5 C) to shave the most expensive, dirtiest kWh. When the
grid is clean/cheap, you may hold a slightly cooler, more comfortable setpoint.

Always respond by calling the `set_hvac_setpoints` tool. Keep the rationale to one short sentence."""

TOOL = {
    "type": "function",
    "function": {
        "name": "set_hvac_setpoints",
        "description": "Set the building heating and cooling temperature setpoints for this control interval.",
        "parameters": {
            "type": "object",
            "properties": {
                "cooling_setpoint_c": {
                    "type": "number",
                    "description": "Cooling (AC) setpoint in Celsius.",
                },
                "heating_setpoint_c": {
                    "type": "number",
                    "description": "Heating setpoint in Celsius.",
                },
                "rationale": {
                    "type": "string",
                    "description": "One short sentence explaining the choice.",
                },
            },
            "required": ["cooling_setpoint_c", "heating_setpoint_c", "rationale"],
        },
    },
}


class LLMAgent:
    name = "llm"

    def __init__(self, cfg, progress=None):
        self.cfg = cfg
        lc = cfg["llm"]
        self.base_url = lc["base_url"].rstrip("/")
        self.model = lc["model"]
        self.temperature = lc.get("temperature", 0.1)
        self.num_ctx = lc.get("num_ctx", 4096)
        self.timeout = cfg["control"]["llm_timeout_s"]
        self.progress = progress or (lambda m: None)
        self.guard = Guardrail(cfg)
        self.fallback = RuleController(cfg)
        self._last_regime = None
        self._last_decision: Decision | None = None
        self.available = self._check()

    # ------------------------------------------------------------- ollama
    def _check(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            models = [m["name"] for m in r.json().get("models", [])]
            ok = any(self.model.split(":")[0] in m for m in models)
            self.progress(f"[llm] ollama up; models={models}; target={self.model} ok={ok}")
            return ok
        except Exception as e:
            self.progress(f"[llm] ollama not reachable ({e}); running in fallback mode")
            return False

    def warmup(self):
        """preload the model so the first real decision isn't a cold start."""
        if not self.available:
            return
        try:
            requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": "ready?"}],
                    "stream": False,
                    "keep_alive": "30m",
                    "options": {"num_ctx": self.num_ctx},
                },
                timeout=120,
            )
            self.progress("[llm] model warmed up and resident")
        except Exception as e:
            self.progress(f"[llm] warmup failed: {e}")

    def _regime(self, snap: Snapshot):
        """coarse operating regime -> cache key, so we don't call the model every time."""
        return (
            snap.occupied,
            snap.grid["is_peak_period"],
            round(snap.outdoor_temp_c / 2.0),  # 2 c buckets
            snap.max_abs_pmv > self.cfg["comfort"]["pmv_limit"],  # comfort-at-risk flag
        )

    def _call_ollama(self, snap: Snapshot) -> Decision | None:
        user = (
            "Current building + grid state (JSON):\n"
            + json.dumps(snap.to_prompt_dict())
            + "\n\nChoose setpoints now by calling set_hvac_setpoints."
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "tools": [TOOL],
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        r = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        r.raise_for_status()
        msg = r.json().get("message", {})
        calls = msg.get("tool_calls") or []
        if not calls:
            # sometimes the model answers in prose - treat that as a miss and fall back
            raise ValueError(f"no tool_call returned: {msg.get('content','')[:80]}")
        args = calls[0]["function"]["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        h = float(args["heating_setpoint_c"])
        c = float(args["cooling_setpoint_c"])
        why = str(args.get("rationale", ""))[:200]
        # clamp through the guardrail before anything reaches energyplus
        hc = self.guard.clamp(h, c, snap.occupied)
        source = "llm" if (hc[0] == round(h, 2) and hc[1] == round(c, 2)) else "llm-guardrailed"
        sp = {z: hc for z in CONTROLLED_ZONES}
        return Decision(setpoints=sp, rationale=why, source=source)

    def __call__(self, snap: Snapshot) -> Decision:
        # cache hit: reuse the last decision while the regime hasn't changed
        regime = self._regime(snap)
        if self._last_decision is not None and regime == self._last_regime:
            d = self._last_decision
            return Decision(setpoints=d.setpoints, rationale="(cached) " + d.rationale,
                            source=d.source)

        if not self.available:
            d = self.fallback(snap)
            d.source = "rule-fallback"
            self._last_regime, self._last_decision = regime, d
            return d

        try:
            d = self._call_ollama(snap)
        except Exception as e:
            self.progress(f"[llm] decision failed ({e}); using deterministic fallback")
            d = self.fallback(snap)
            d.source = "rule-fallback"
        self._last_regime, self._last_decision = regime, d
        return d
