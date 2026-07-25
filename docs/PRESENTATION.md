# Eco-Loop Building Agent — Presentation Content

Slide-by-slide content to paste into the provided template. Each block = one slide.
Figures referenced live in `outputs/figs/`.

---

## Slide 1 — Title
**Eco-Loop Building Agent**
Autonomous closed-loop HVAC control with EnergyPlus + an open-source LLM
*Honeywell Hackathon — Eco-Loop Building Agents*
Team · Date

---

## Slide 2 — The Problem
- Buildings ≈ **40% of global energy** and a primary carbon driver.
- Traditional BMS run **rigid, fixed schedules** — blind to real-time weather,
  occupancy, and grid conditions.
- Result: energy wasted cooling empty rooms and running full-tilt during the
  dirtiest, most expensive grid hours.
- **Opportunity:** make the building an *active, self-correcting agent.*

---

## Slide 3 — Our Solution (one line)
> A physics-based **EnergyPlus** building, driven in a **live closed loop** by an
> open-source **LLM (llama3.1:8b)** that senses, reasons about comfort + carbon +
> cost, and injects HVAC setpoints back into the running simulation — autonomously.

---

## Slide 4 — System Architecture
*(insert the diagram from `docs/ARCHITECTURE.md` §1)*
- **Sense:** EnergyPlus Runtime API streams zone temps, humidity, CO₂/IAQ, occupancy, meters.
- **Reason:** LLM agent (tool-calling) + PMV comfort + grid carbon/price signals.
- **Act:** guardrail-clamped setpoints injected via `Zone Temperature Control` actuators.
- **Bus:** same tools exposed over **Model Context Protocol (MCP)**.

---

## Slide 5 — The Closed Loop (how a decision is made)
1. Runtime API → compact JSON **Snapshot** (state + grid signals).
2. LLM **calls `set_hvac_setpoints(cooling, heating, rationale)`** — never edits code.
3. Hard **Guardrail** clamps to the comfort envelope.
4. Setpoints **injected live** into EnergyPlus; repeat every 15 min.

*Key idea: the LLM reasons in words but acts through a typed tool.*

---

## Slide 6 — Results: Energy, Cost & Carbon
*(insert `outputs/figs/savings_bars.png`)*

| Metric | Baseline | AI | Reduction |
|---|---:|---:|---:|
| Total facility electricity | 2 689 kWh | 2 509 kWh | **−6.7%** |
| HVAC electricity | 1 209 kWh | 1 029 kWh | **−14.9%** |
| Cooling | 833 kWh | 650 kWh | **−21.9%** |
| Energy cost (TOU) | $354 | $324 | **−8.6%** |
| Carbon | 926 kg | 868 kg | **−6.2%** |

> Cost & carbon fall **more than kWh** — the agent optimises *when* to use energy.

---

## Slide 7 — Results: Comfort & Air Quality Maintained
*(insert `outputs/figs/pmv.png`, `outputs/figs/temp_comfort.png`, `outputs/figs/iaq.png`)*
- **100%** of occupied timesteps kept within the PMV comfort limit (max |PMV| = 0.56).
- **100%** of occupied timesteps kept **indoor CO₂ under 1000 ppm** (max ~777 ppm) — IAQ never sacrificed for energy.
- Savings achieved **without** sacrificing a single occupant-comfort constraint —
  because the guardrail makes unsafe setpoints physically impossible.

---

## Slide 8 — Intelligent, Grid-Aware Control
*(insert `outputs/figs/setpoints.png`)*
- AI floats the cooling setpoint to the comfort-band top **during the grid peak**
  (amber), then relaxes off-peak.
- This is **demand + carbon response**, not blind setback — the reason cost/carbon
  savings outrun raw energy savings.

---

## Slide 9 — Agentic Engineering
- **Tool-calling autonomy:** `set_hvac_setpoints`; the LLM acts only through tools.
- **MCP server:** `get_building_state`, `get_grid_signals`, `propose_setpoints`,
  `get_savings_summary`, `get_simulation_errors`, `list_actuators`.
- **Self-correction:** parses EnergyPlus `.err`, fixes the IDF, re-runs — no human edit.
- **Prompt engineering:** role + numeric constraints, tiny JSON state, temp 0.1,
  guardrail as physical backstop.

---

## Slide 10 — Robustness & Latency (System Integration)
- **Never stalls:** every LLM call time-boxed + wrapped; deterministic fallback on
  timeout/error. A controller error can't crash the EnergyPlus callback. 2-week
  loop completes end-to-end.
- **Real-time:** model kept warm + **semantic caching** → 1,008 candidate decisions
  collapsed to 171 real inferences (816 cache hits, 21 safe fallbacks).
- **Lengthy logs:** structured at the source; only a small `.err` digest ever
  reaches the LLM.

---

## Slide 11 — Tech Stack
- **Simulation:** EnergyPlus 25.1 (portable) + `pyenergyplus` Runtime API.
- **Brain:** llama3.1:8b via **Ollama** (fully local, open-source), tool-calling.
- **Comfort:** Fanger PMV/PPD (ISO 7730), computed in Python.
- **Protocol:** Model Context Protocol (FastMCP).
- **Dashboard:** Streamlit + Plotly; self-contained HTML report.
- Building: DOE Reference Small Office, localized to Tampa, FL.

---

## Slide 12 — Impact & Next Steps
- Drop-in to real grid APIs (WattTime / Electricity Maps) — one seam, no rewrite.
- Scales from setpoints to lighting, ventilation, battery/PV dispatch.
- Path to real buildings: swap the EnergyPlus digital twin for a live BMS via the
  same MCP tool interface.
- **Eco-Loop turns a passive energy consumer into an active, self-optimising agent.**
