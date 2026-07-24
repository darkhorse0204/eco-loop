# 🌡️ Eco-Loop Building Agent

**An autonomous, closed-loop AI that runs a building's HVAC in real time — pairing a
physics-based EnergyPlus simulation with an open-source LLM (`llama3.1:8b`) that
senses live sensor data, reasons about comfort + grid carbon + cost, and injects
supervisory setpoints straight back into the running simulation. No human in the
loop, no code edits.**

> Honeywell Hackathon · *Eco-Loop Building Agents* · a live, operational Physical-AI PoC.

---

## 🏆 Headline result (2-week Tampa cooling season, DOE small office)

| Metric | Baseline (standard schedule) | AI closed-loop | Reduction |
|---|---:|---:|---:|
| **Total facility electricity** | 2 689 kWh | 2 529 kWh | **−5.9 %** |
| **HVAC electricity** | 1 209 kWh | 1 049 kWh | **−13.2 %** |
| **Cooling electricity** | 833 kWh | 668 kWh | **−19.7 %** |
| **Energy cost** (time-of-use) | $354 | $326 | **−7.9 %** |
| **Carbon** | 926 kg | 876 kg | **−5.5 %** |
| **Occupant comfort** (occupied \|PMV\| in band) | 100 % | **100 %** (max 0.52) | **maintained** |

*Live llama3.1:8b agent, full 2-week run: 1 008 setpoint decisions = **170 real LLM
inferences** + 838 semantic-cache hits + **0 fallbacks** (the loop never stalled).*

Cost and carbon drop **more than raw kWh** — the agent optimises *when* energy is
used, shifting load out of the high-price / high-carbon evening peak. Intelligent
savings, not blind setback. (The LLM agent even beats our hand-tuned deterministic
controller, which lands −4.6 % total / −10 % HVAC.)

---

## ⚡ Quickstart

Prereqs are installed automatically by the setup below: **EnergyPlus 25.1**
(portable, no admin) and **Ollama + llama3.1:8b**.

```bash
# 1. Python deps
python -m pip install -r requirements.txt

# 2. One-time setup: download portable EnergyPlus + pull the LLM (idempotent)
python scripts/setup.py

# 3. (Re)build the Tampa-localized building model from the pristine example
python scripts/build_model.py

# 4. Run the full closed loop: baseline + AI, write savings + artifacts
python -m src.orchestrator            # LLM agent (auto-falls back to rule if Ollama down)
#   python -m src.orchestrator --rule # deterministic controller only (no LLM)

# 5. See the results
python scripts/make_report.py         # -> outputs/report.html (self-contained)
streamlit run dashboard/app.py        # -> interactive dashboard + LLM decision log
```

---

## 🧠 How it works (30-second version)

1. **EnergyPlus Runtime API** streams live sensors every 10-min timestep (zone
   temps, radiant, humidity, occupancy, meters, outdoor conditions).
2. Every 15 min the runner builds a compact JSON **Snapshot** (state + grid carbon
   + TOU price) and hands it to the **LLM agent**.
3. The LLM **calls a tool** — `set_hvac_setpoints(cooling, heating, rationale)` — it
   reasons in words, acts through a typed tool, never edits code.
4. A hard **Guardrail** clamps the setpoints to the comfort envelope (so energy is
   never saved at comfort's expense).
5. Setpoints are **injected live** into EnergyPlus via the `Zone Temperature
   Control` actuators. Loop repeats for the whole run.

Full write-up (prompt engineering, latency management, log handling, self-correction):
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

### Why it's robust (System Integration)
Every LLM call is **time-boxed and wrapped**; on timeout/error/garbage output the
loop falls back to a **deterministic grid-aware controller** and keeps running. A
controller exception can *never* crash the EnergyPlus callback. The 2-week loop runs
start-to-finish without stalling.

### Why it's fast (Latency)
Model kept warm (`keep_alive`) + **semantic caching**: the LLM is only called when
the *operating regime* changes (occupancy / peak / outdoor-temp bucket), turning
1,008 candidate decisions into 170 real inferences (838 cache hits).

### Agentic autonomy + MCP
The agent acts only through tools. The **same tools are exposed over the Model
Context Protocol** (`mcp_server/server.py`) so any MCP client (a desktop assistant,
an IDE, another agent) can sense and supervise the building:
`get_building_state`, `get_grid_signals`, `propose_setpoints`, `get_savings_summary`,
`get_simulation_errors`, `list_actuators`.

---

## 📦 Deliverables map

| Required deliverable | Where |
|---|---|
| Fully functional source code (API wrapper + LLM orchestration + comms bus) | `src/`, `mcp_server/` |
| Building models (baseline + runtime-modified `.idf`) | `models/baseline.idf`, `outputs/ai_effective.idf` |
| Quantitative savings dashboard (proves % kWh ↓ within comfort) | `outputs/report.html`, `dashboard/app.py` |
| System Architecture document | `docs/ARCHITECTURE.md` |
| Demo video script | `docs/DEMO_SCRIPT.md` |

---

## 🗂️ Repository layout

```
config.yaml               every tunable knob (comfort bands, grid, LLM, cadence)
src/
  runner.py               EnergyPlus Runtime-API closed-loop bus            ← core
  llm_agent.py            Ollama tool-calling agent + cache + fallback      ← brain
  controller.py           hard Guardrail + deterministic grid-aware policy
  comfort.py              Fanger PMV/PPD (ISO 7730)
  grid_signals.py         carbon intensity + time-of-use price
  orchestrator.py         baseline + AI runs → savings summary + artifacts
mcp_server/server.py      MCP tools over the building
dashboard/app.py          Streamlit dashboard (+ live LLM decision log)
scripts/                  setup, build_model, make_report, self-correction, probes
models/baseline.idf       DOE small office, localized to Tampa FL
outputs/                  timeseries, decisions, summary.json, report.html, figs/
```

## 🔧 Configuration
Everything lives in [`config.yaml`](config.yaml): comfort envelope + PMV limit,
grid carbon/price profile, LLM model + timeout, control interval, run paths. Swap
the weather file or model there and re-run — nothing else changes.

## 🧪 Reproducibility notes
- Building: **DOE Reference Small Office** (ships with EnergyPlus), localized to
  Tampa, FL (Site:Location + weather). Baseline uses the building's native fixed
  setpoint schedules — the "standard scheduling" the brief asks us to beat.
- Energy accounting sums `Electricity:Building` + `Electricity:HVAC` (the facility
  electricity), read live through the API with the **identical** method for both
  runs, so the % comparison is strictly apples-to-apples. Cross-checked against the
  EnergyPlus SQLite tabular output.
