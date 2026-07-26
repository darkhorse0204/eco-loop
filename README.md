# 🌡️ Eco-Loop Building Agent

**A building that runs its own air-conditioning with an AI — using less energy, less
money, and less carbon, while keeping people comfortable and the air fresh.**

Everything runs on **one laptop, fully offline**: a physics-accurate building
simulation (**EnergyPlus**) is controlled in a live loop by a **local open-source AI**
(**Llama 3.1 8B**, via Ollama). The AI reads the building's sensors every 15 minutes
and adjusts the temperature settings on the fly.

> Built for the Honeywell *Eco-Loop Building Agents* hackathon — a live, working
> Physical-AI proof-of-concept.

---

## 🏆 What it achieves (2-week summer run, Tampa office)

| What we measured | Normal building | With the AI | Improvement |
|---|---:|---:|---:|
| **Total electricity** | 2 689 kWh | 2 506 kWh | **−6.8%** |
| **Air-conditioning electricity** | 1 209 kWh | 1 026 kWh | **−15.1%** |
| **Cooling energy** | 833 kWh | 648 kWh | **−22.2%** |
| **Energy bill** (time-of-use price) | $354 | $323 | **−8.7%** |
| **Carbon** | 926 kg | 867 kg | **−6.4%** |
| **Comfort** (people kept comfortable) | 100% | **100%** | maintained |
| **Air quality** (CO₂ under 1000 ppm) | 100% | **100%** (max ~777 ppm) | maintained |

**The clever part:** the **bill** drops even *more* than the energy (−8.7% vs −6.8%),
because the agent saves about **twice as much during the expensive 4–9pm peak**
(−11.7%) as it does off-peak (−5.6%) — so the savings land where each kWh is priciest.
(Honest note: **carbon falls roughly in line with energy**, not more — the grid is
actually cleanest at midday, when a lot of the cooling saving happens.) It even beat a
carefully hand-tuned rule-based controller, and ran the full two weeks **without a
single crash** (the safe backup is there if the AI ever stalls — see reliability below).

We proved the AI earns its place with a head-to-head **ablation** (baseline vs. rule
controller vs. AI, same building): the LLM wins on **every** metric — ~45% more total
savings than the hand-tuned rules — while both keep comfort and air quality at 100%.
See [docs/ARCHITECTURE.md §11](docs/ARCHITECTURE.md) or run `python scripts/ablation.py`.

---

## 🤔 What problem does this solve?

Buildings use about **40% of all energy in the world**. Most of them run on **fixed
timers** — the air-conditioning follows the same schedule every day, ignoring the
weather, whether anyone is actually in the room, and how dirty or expensive the
electricity is right now. That wastes a huge amount of energy.

Eco-Loop turns a "dumb," scheduled building into a **smart agent** that senses what's
happening and adapts every few minutes — the way a very attentive human operator
would, but automatically and non-stop.

---

## 🧠 How it works, in plain English

Think of it as a loop that repeats all day:

1. **The building senses itself.** EnergyPlus (the physics engine) reports the room
   temperatures, humidity, **CO₂ / air quality**, how many people are in, and how much
   electricity is being used — every simulated 10 minutes.
2. **The AI gets a short summary.** Every 15 minutes we hand the AI a tiny summary:
   how warm it is inside and out, whether the room is occupied, the comfort level, the
   air quality, and how expensive/dirty the electricity is right now.
3. **The AI decides — by calling a tool.** The AI doesn't write code or free text. It
   **calls a function** called `set_hvac_setpoints(cooling, heating, reason)` — like
   pressing buttons on a thermostat — and gives a one-line reason.
4. **A safety guardrail checks it.** Whatever the AI asks for is clamped into a safe
   comfort range *before* it reaches the building. So the AI can never make people
   uncomfortable, even if it makes a mistake.
5. **The setting goes back into the live building.** The new temperature target is
   injected straight into the running simulation. Then the loop repeats.

A few important ideas, explained simply:

- **EnergyPlus** = a highly realistic building simulator used by real engineers. It's
  our stand-in for a real building.
- **LLM (Llama 3.1)** = the "brain." An open-source AI that runs on your own computer
  (no internet, no API key) through a tool called **Ollama**.
- **Tool-calling** = the AI acts by calling defined functions, not by typing text.
  This makes it reliable and safe.
- **Guardrail** = a hard rulebook that keeps the AI's choices inside comfortable and
  healthy limits.
- **PMV** = a standard comfort score (from ASHRAE-55 / ISO 7730). 0 is perfect; we
  keep it comfortable (well within the ±0.7 limit, aiming for ±0.5).
- **CO₂ (air quality)** = we track indoor carbon dioxide; if it goes above 1000 ppm
  the air feels stuffy, so the AI keeps it below that.
- **MCP (Model Context Protocol)** = a standard way for any AI app to use these same
  building controls as tools.

**Why it's reliable:** every AI request has a time limit. If the AI is slow, errors,
or is switched off entirely, the loop **automatically falls back to a simple safe
controller and keeps going** — so it never gets stuck. (This actually happened once
during testing when the AI server stopped, and the two-week run still finished.)

**Why it's fast:** the AI model stays loaded in memory, and we only ask it a new
question when the situation genuinely changes — so ~1,000 decision points became only
171 real AI calls (the rest were served instantly from a cache).

---

## ⚡ Try it in 3 commands

Full step-by-step instructions (with troubleshooting) are in
**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**. The short version:

```bash
python -m pip install -r requirements.txt   # 1. install python libraries
python scripts/setup.py                      # 2. download EnergyPlus + the AI model (once)
python scripts/build_model.py                # 3. build the Tampa office model (once)

python -m src.orchestrator                   # run the AI closed loop (~20-30 min)
python scripts/make_report.py                # then open outputs/report.html
```

Quick test without the AI (finishes in seconds): `python -m src.orchestrator --rule`
Interactive dashboard: `streamlit run dashboard/app.py`

---

## 🗂️ What's in this repo

```
config.yaml               all the settings you can change (comfort limits, price, AI model...)
src/
  runner.py               the core loop: read sensors, ask controller, inject settings   ← heart
  llm_agent.py            the AI brain: talks to Llama 3.1, caches, falls back safely     ← brain
  controller.py           the safety guardrail + the simple rule-based backup controller
  comfort.py              the PMV comfort score (ASHRAE-55 / ISO 7730)
  grid_signals.py         the electricity price + carbon curve over the day
  orchestrator.py         runs baseline + AI, computes the savings, saves everything
  self_correction.py      reads EnergyPlus errors and fixes the model by itself
mcp_server/server.py      exposes the building controls as standard MCP tools
dashboard/app.py          the interactive Streamlit dashboard
scripts/
  setup.py                one-time download of EnergyPlus + the AI model
  build_model.py          builds the building model (Tampa, CO₂ tracking on)
  demo.py                 narrated end-to-end walkthrough (great for the video)
  make_report.py          builds outputs/report.html (the savings dashboard)
  ablation.py             baseline vs rule controller vs LLM, head-to-head
  grid_savings.py         shows the savings land in the expensive peak (cost > energy)
  demo_self_correction.py the self-healing demo
  probe_api_data.py       lists every sensor/control EnergyPlus exposes
models/baseline.idf       the office building model
weather/                  the Tampa weather file
outputs/                  results: charts, CSVs, summary.json, report.html
docs/                     ARCHITECTURE (with diagrams), DEPLOYMENT
```

---

## ✅ How this meets the hackathon brief

| The brief asked for | Where it is |
|---|---|
| EnergyPlus simulation + Python bridge | `src/runner.py` (EnergyPlus Runtime API) |
| Open-source LLM running locally | `src/llm_agent.py` (Llama 3.1 8B via Ollama) |
| MCP server / agentic tools | `mcp_server/server.py` (6 tools) |
| AI parses files, reads runtime errors, fixes things itself | `src/self_correction.py` |
| Stream temps, **air quality**, energy, PMV | `src/runner.py` (all four, every timestep) |
| Reason about comfort, peak cost, grid carbon | `src/llm_agent.py` |
| Compute measures, update setpoints, inject them live | `src/runner.py` actuators |
| Quantified kWh **and cost** savings, comfort held | table above · `outputs/report.html` |
| Baseline + runtime-modified `.idf` files | `models/baseline.idf`, `outputs/ai_effective.idf` |
| System architecture document | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Demo video | recorded live from `python -m src.orchestrator` (submitted separately) |
| Presentation | slide deck (submitted separately) |

**Scored against the judging criteria:** robust closed loop that never crashes
(System Integration), real measured energy + cost + carbon savings (Efficiency),
comfort *and* air quality both maintained (Comfort & Constraints), tool-calling + MCP
+ self-correction + smart caching (Agentic Autonomy), and a clear dashboard + docs
(Presentation).

---

## 📚 Documentation

- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — full setup, run, share & troubleshoot guide (start here).
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — how the system is built, in depth (with diagrams).

---

## 🧪 A note on honesty of the numbers

The baseline and the AI run use the **same building model**; the only difference is
that the AI changes the settings live. Energy is measured the **same way for both**
(EnergyPlus electricity meters read through the API), so the percentage savings are a
fair, apples-to-apples comparison. The grid price/carbon curve is realistic but
simulated — it's a one-line change to plug in a live data source (e.g. WattTime or
Electricity Maps).
