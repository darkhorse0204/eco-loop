# 3-Minute Demo Video Script — Eco-Loop Building Agent

Goal: show the **live closed loop** — data flowing EnergyPlus → LLM, and control
actions flowing LLM → EnergyPlus — plus the quantified savings.

Record at 1080p. Have two terminals + a browser tab open beforehand.

---

### [0:00–0:25] The problem & the pitch
> "Buildings are ~40% of global energy. They run on rigid, fixed schedules that
> ignore weather, occupancy and the grid. We built **Eco-Loop**: an autonomous
> agent that pairs a physics-based EnergyPlus simulation with an open-source LLM —
> llama-3.1 running locally — that senses the building live and controls its HVAC
> in a real closed loop."

Show: `docs/ARCHITECTURE.md` diagram on screen for 5 seconds.

### [0:25–1:10] The live loop (the money shot)
Run in Terminal 1:
```bash
python -m src.orchestrator
```
Talk over the streaming log as lines appear:
> "The baseline runs first — the building's native fixed schedules. Now the AI run.
> Every line here is one control step: EnergyPlus streams the outdoor temperature,
> occupancy, indoor comfort — and the LLM **calls a tool**, `set_hvac_setpoints`,
> to decide the new setpoints, which are injected straight back into the running
> simulation."

Point at a line like:
```
[ai] t=4375.5h OA=28.9C occ=True -> H=21.5 C=25.0 [llm 640ms]
```
> "Occupied, grid off-peak — it holds an efficient 25 degrees. Watch what happens
> at the evening peak…"

Point at a peak line:
```
[ai] ... occ=True -> H=20.0 C=25.5 [llm ...]  :: float cooling to shave costly load
```
> "…it floats the setpoint up to shed the most expensive, highest-carbon load —
> and it explains why. Note the `0ms` lines: that's the semantic cache — it only
> pays for an LLM call when the situation actually changes, so the loop stays
> real-time over two full weeks."

### [1:10–1:40] The guardrail & robustness
Show `src/controller.py` Guardrail briefly.
> "Whatever the LLM proposes passes through a hard comfort guardrail before it
> touches the physics — so we never save energy by making people uncomfortable.
> And every decision is time-boxed: if the model is slow or errors, the loop falls
> back to a deterministic controller and keeps running. It never crashes."

### [1:40–2:20] The results dashboard
Show `outputs/report.html` (or `streamlit run dashboard/app.py`).
> "Here are the numbers, baseline versus AI, over two weeks in Tampa: total
> facility electricity down **5.9%**, HVAC down **13%**, cooling down **20%** —
> while comfort is maintained on **100%** of occupied steps, PMV staying in the
> band. And notice: **cost is down 7.9% and carbon 5.5% — more than the raw energy
> — because the agent shifts load out of the dirty, expensive evening peak.**"

Scroll to the setpoint chart:
> "You can see it directly: green is the AI, floating the setpoint up in the amber
> peak window while the orange baseline stays flat."

Scroll to the decision log (Streamlit):
> "And here's the agent's full reasoning trail — every tool call and its rationale."

### [2:20–2:45] MCP + self-correction
> "The same control tools are exposed over the **Model Context Protocol**, so any
> agent can operate the building. And the agent is self-correcting —"

Run:
```bash
python scripts/demo_self_correction.py
```
> "— we injected a broken input; it read the EnergyPlus error log, chose the fix
> itself, rewrote the model, and re-ran to success. No human edit."

### [2:45–3:00] Close
> "Eco-Loop: a real, physics-in-the-loop, open-source AI that turns a passive
> building into an active, self-optimising agent — measurable energy, cost and
> carbon savings, with comfort guaranteed. Thank you."

---

**Pre-flight checklist**
- [ ] `ollama list` shows `llama3.1:8b`; run one warm-up decision so the model is resident.
- [ ] `outputs/report.html` regenerated from the latest run (`python scripts/make_report.py`).
- [ ] Streamlit already running so the tab loads instantly.
- [ ] Terminal font ≥ 16pt for readability.
