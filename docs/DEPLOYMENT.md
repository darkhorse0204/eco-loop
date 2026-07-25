# Deployment & Run Guide (plain-English, step by step)

This guide takes you from a fresh computer to a running Eco-Loop Building Agent,
and then to sharing the results. No prior EnergyPlus or AI experience needed.

---

## 1. What this project is (in one paragraph)

We have a **computer model of a real office building** (run by a physics engine
called **EnergyPlus**). A **local AI** (an open-source language model called
**Llama 3.1**, run through a tool called **Ollama**) watches the building second by
second and keeps changing the air-conditioning settings to use **less energy, spend
less money, and make less carbon** — while keeping people comfortable and the air
fresh. Everything runs on **one laptop, offline**. There is no cloud bill.

---

## 2. What you need before you start (prerequisites)

| Thing | Why | How to check |
|---|---|---|
| **Windows 10/11** (64-bit) | the project was built and tested here | — |
| **Python 3.11 or 3.12** | runs all the code | open a terminal, type `python --version` |
| **~8 GB free disk** | EnergyPlus (~0.5 GB) + the AI model (~5 GB) | — |
| **Internet (first time only)** | to download EnergyPlus + the AI model once | — |
| **Git** (optional) | to clone the code | `git --version` |

> Mac/Linux also work — EnergyPlus and Ollama both support them. The setup script
> downloads the Windows build of EnergyPlus; on Mac/Linux, install EnergyPlus from
> energyplus.net and set the `ECO_LOOP_EPLUS_DIR` environment variable to its folder.

---

## 3. One-time setup (do this once)

### Step 3.1 — Get the code
```bash
git clone https://github.com/darkhorse0204/eco-loop.git
cd eco-loop
```
(or download the ZIP from GitHub and open a terminal inside the folder.)

### Step 3.2 — Install the Python libraries
```bash
python -m pip install -r requirements.txt
```
This installs the small helpers we use (numpy, pandas, plotly, streamlit, the MCP
library, etc.). Takes 1–2 minutes.

### Step 3.3 — Download the two big tools (EnergyPlus + the AI)
```bash
python scripts/setup.py
```
This does two things automatically:
1. Downloads **EnergyPlus** (the building physics engine, ~173 MB) into `tools/` and
   unzips it. No admin rights needed — it's a self-contained folder.
2. Finds **Ollama** and pulls the **llama3.1:8b** AI model (~5 GB).

> If it says *"Ollama not found"*: install Ollama once from **https://ollama.com/download**
> (it installs for your user, no admin needed), then run `python scripts/setup.py` again.
> After installing, Ollama runs quietly in the background and serves the AI on
> `http://localhost:11434`.

### Step 3.4 — Build the building model
```bash
python scripts/build_model.py
```
This creates `models/baseline.idf` — the office building, set in Tampa, Florida, with
indoor-air-quality (CO₂) tracking turned on. You only need to re-run this if you want
to change the location or the simulated dates.

**You are now set up.** Steps 3.1–3.4 are one-time.

---

## 4. Run the closed loop (the main event)

Make sure Ollama is running (it usually starts on its own; if not, open a terminal
and run `ollama serve`). Then:

```bash
python -m src.orchestrator
```

What happens, in order:
1. It runs the building **once on its normal fixed schedule** — this is the
   **baseline** (how the building behaves today).
2. It runs the building **again with the AI in control** — the AI reads the sensors
   every 15 minutes and changes the temperature settings live.
3. It prints the **savings** and writes all the result files.

You'll see live lines like:
```
[ai] t= 178.5h OA=31.6C occ=True -> H=20.0 C=25.5 [llm 640ms]
```
That means: at this hour, outdoor air is 31.6 °C, the room is occupied, and the AI
chose heating 20 °C / cooling 25.5 °C, taking 640 ms to decide.

**Time:** the baseline takes a few seconds. The AI run takes ~20–30 minutes, because
the AI actually "thinks" for each decision. (It's smart about this — see §8.)

**Faster option (no AI, for a quick test):**
```bash
python -m src.orchestrator --rule
```
This uses a simple rule-based controller instead of the AI. It finishes in seconds
and is a good way to confirm everything works.

---

## 5. See the results

### Option A — the one-file report (easiest)
```bash
python scripts/make_report.py
```
Then open **`outputs/report.html`** in any web browser. It's a self-contained page
with the savings, charts, comfort, and air-quality results. Nothing else needed.
To turn it into a PDF: open it and use the browser's **Print → Save as PDF**.

### Option B — the interactive dashboard
```bash
streamlit run dashboard/app.py
```
This opens a live dashboard in your browser (usually `http://localhost:8501`) with
interactive charts and the **full log of every decision the AI made, with its
reasons**.

---

## 6. The other pieces (optional, for the demo)

**Self-correction demo** — shows the AI fixing a broken model by itself:
```bash
python scripts/demo_self_correction.py
```
It deliberately breaks the model, the AI reads the EnergyPlus error, picks the fix,
rewrites the file, and re-runs successfully — no human edit.

**MCP server** — exposes the building's controls as standard agent tools:
```bash
python -m mcp_server.server
```
This speaks the Model Context Protocol over stdio. In an MCP client, register it with
command `python`, arguments `-m mcp_server.server`.

---

## 7. Changing settings

Everything you might tune lives in **`config.yaml`** (plain text, with comments):
- **comfort** — the allowed temperature range and the comfort / CO₂ limits.
- **grid** — the pretend electricity price and carbon curve over the day.
- **control** — how often the AI is allowed to change settings (default 15 min).
- **llm** — which AI model to use and how long to wait per decision.
- **simulation** — which building file and which weather file to use.

Change a value, save the file, and re-run `python -m src.orchestrator`.

---

## 8. How it stays fast and never crashes (good to know)

- **Fast:** the AI model is kept loaded in memory, and the code only asks the AI a
  fresh question when the situation actually changes (occupancy, peak hour, or a big
  outdoor-temperature change). Over two weeks this turned ~1,000 decision points into
  a couple hundred real AI calls.
- **Never crashes:** every AI call has a time limit. If the AI is slow, errors, or is
  even completely switched off, the loop **automatically falls back to a safe
  rule-based controller and keeps running**. (We saw this live once: the AI server
  stopped mid-run and the two-week simulation still finished on its own.)
- **Comfort is guaranteed:** whatever the AI suggests is passed through a hard
  "guardrail" that clamps it into the safe comfort range before it reaches the
  building. The physics literally never sees an unsafe setting.

---

## 9. Sharing / "hosting" the results

This is a simulation tool, so "deploy" mostly means **run it and share the output**:

- **Share the report:** `outputs/report.html` is one self-contained file — email it or
  put it on any static host (GitHub Pages, Netlify, S3). It needs no server.
- **Share the live dashboard on your network:**
  ```bash
  streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port 8501
  ```
  Others on the same Wi-Fi can open `http://<your-computer-ip>:8501`.
- **Cloud note:** the *dashboard* can run on Streamlit Community Cloud from the
  committed result files, but the *simulation itself* (EnergyPlus + the local AI) is
  meant to run on a real machine, not a tiny cloud container. For a demo, run the
  loop locally and host the resulting report/dashboard.

---

## 10. From simulation to a real building (the future)

The building here is a **digital twin**. To control a real building, you swap the
EnergyPlus twin for a live **Building Management System (BMS)** behind the *same* tool
interface (the MCP tools in `mcp_server/`). The AI brain, the guardrail, the grid
signals, and the dashboard all stay exactly the same. That is the intended path from
this proof-of-concept to a product.

---

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `ollama not reachable ... running in fallback mode` | Ollama isn't running. Open a terminal and run `ollama serve`, or start the Ollama app, then re-run. |
| `couldn't find energyplus` | Run `python scripts/setup.py`, or set `ECO_LOOP_EPLUS_DIR` to your EnergyPlus folder. |
| The AI run is very slow | Normal on CPU (~9 s per real decision). Use `--rule` for a quick test, or run on a machine with a GPU. |
| `No results yet` in the dashboard | Run `python -m src.orchestrator` first — the dashboard reads its output files. |
| Charts look empty | Make sure the run finished (it prints a `savings` summary at the end) before running `make_report.py`. |
| Push to GitHub is huge / fails | Don't commit `tools/` (EnergyPlus) or the AI model — they're already git-ignored and re-downloaded by `setup.py`. |
