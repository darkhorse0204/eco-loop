"""eco-loop building agent  — comprehensive live demo.

runs through every major capability of the system, one step at a time, with
narrated output suitable for a hackathon presentation or a recorded video.

    python scripts/demo.py              # full demo with LLM (~5-8 min)
    python scripts/demo.py --quick      # skip the full AI run, use rule controller
    python scripts/demo.py --skip-heal  # skip the self-correction demo

what it covers:
  1. environment check      — energyplus, ollama, model, python deps
  2. quick rule-based run   — proves the simulation works (seconds)
  3. llm agent reasoning    — one live LLM decision, printed step by step
  4. guardrail safety       — shows clamping of dangerous setpoints
  5. grid-aware pricing     — 24h carbon + price curve
  6. self-correction        — breaks the model, lets the agent heal it
  7. full ai closed loop    — runs the 2-week simulation with the LLM
  8. results & savings      — the final numbers, comfort, air quality, autonomy
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

# force utf-8 output on windows to avoid cp1252 encoding errors
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# make sure we can import from the project root
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.config import Config
from src.controller import Guardrail, RuleController
from src.grid_signals import GridSignals

# ──────────────────────────────────────────── helpers
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def banner(title: str):
    w = 68
    print(f"\n{CYAN}{'═' * w}")
    print(f"  {BOLD}{title}{RESET}{CYAN}")
    print(f"{'═' * w}{RESET}\n")


def ok(msg: str):
    print(f"  {GREEN}✔{RESET} {msg}")


def warn(msg: str):
    print(f"  {YELLOW}⚠{RESET} {msg}")


def fail(msg: str):
    print(f"  {RED}✘{RESET} {msg}")


def info(msg: str):
    print(f"  {DIM}→{RESET} {msg}")


def pause(label: str = "next step"):
    input(f"\n  {DIM}[press enter for {label}]{RESET}")


# ──────────────────────────────────────────── 1. environment check
def step_environment():
    banner("1 · ENVIRONMENT CHECK")

    # python
    ok(f"Python {sys.version.split()[0]}")

    # energyplus
    eplus_dirs = list((REPO / "tools").glob("EnergyPlus*"))
    if eplus_dirs:
        ok(f"EnergyPlus found: {eplus_dirs[0].name}")
    else:
        fail("EnergyPlus not found in tools/. Run: python scripts/setup.py")
        return False

    # pyenergyplus
    try:
        from src import eplus_paths
        eplus_paths.ensure_on_path()
        from pyenergyplus.api import EnergyPlusAPI  # noqa: F401
        ok("pyenergyplus importable")
    except Exception as e:
        fail(f"pyenergyplus import failed: {e}")
        return False

    # ollama
    import requests
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        has_llama = any("llama3.1" in m for m in models)
        ok(f"Ollama running with models: {models}")
        if has_llama:
            ok("llama3.1:8b available")
        else:
            warn("llama3.1:8b not found — LLM steps will use fallback")
    except Exception:
        warn("Ollama not reachable — LLM steps will use deterministic fallback")

    # model & weather
    cfg = Config.load()
    ok(f"IDF model: {cfg.idf_path.name} ({cfg.idf_path.stat().st_size // 1024} KB)")
    ok(f"Weather:   {cfg.weather_path.name}")

    # python packages
    import importlib
    pkgs = ["numpy", "pandas", "yaml", "requests", "streamlit", "plotly", "matplotlib"]
    missing = [p for p in pkgs if importlib.util.find_spec(p) is None]
    if missing:
        warn(f"Missing packages: {missing}  →  pip install -r requirements.txt")
    else:
        ok(f"All {len(pkgs)} Python packages installed")

    return True


# ──────────────────────────────────────────── 2. quick rule-based run
def step_quick_rule_run():
    banner("2 · QUICK RULE-BASED RUN  (no LLM, ~30 seconds)")
    info("Running the EnergyPlus simulation with the deterministic controller…")

    cfg = Config.load()
    from src.orchestrator import run_closed_loop

    t0 = time.time()
    result = run_closed_loop(cfg, "rule", progress=lambda m: None)
    elapsed = time.time() - t0

    sv = result["savings"]
    ok(f"Completed in {elapsed:.1f}s")
    ok(f"Electricity saved: {sv['electricity_kwh']['saved']:.1f} kWh "
       f"({sv['electricity_kwh']['pct']:+.1f}%)")
    ok(f"Cost saved:        ${sv['cost_usd']['saved']:.1f} "
       f"({sv['cost_usd']['pct']:+.1f}%)")
    ok(f"Comfort:           {sv['comfort']['ai_pct_pmv_ok']}% PMV-ok  "
       f"(max |PMV| = {sv['comfort']['ai_max_abs_pmv']})")
    iaq = sv.get("iaq", {})
    ok(f"Air quality:       {iaq.get('ai_pct_co2_ok', 100)}% CO₂-ok  "
       f"(max {iaq.get('ai_max_co2_ppm', 0):.0f} ppm)")
    return result


# ──────────────────────────────────────────── 3. llm agent reasoning
def step_llm_reasoning():
    banner("3 · LLM AGENT REASONING  (one live decision)")
    info("Sending a building state snapshot to Llama 3.1 and watching it reason…")

    cfg = Config.load()
    from src.llm_agent import LLMAgent
    from src.runner import CONTROLLED_ZONES, Snapshot

    agent = LLMAgent(cfg, progress=lambda m: print(f"  {DIM}{m}{RESET}"))
    agent.warmup()  # preload the model to avoid cold-start timeout

    if not agent.available:
        warn("LLM not available — showing what the fallback controller would do instead")

    # craft a realistic occupied, peak-hour scenario
    snap = Snapshot(
        step=42,
        sim_time_hours=4380.0,  # mid-afternoon
        day_of_year=183,
        hour=15.0,
        is_weekend=False,
        outdoor_temp_c=33.2,
        occupied=True,
        total_occupants=12.0,
        grid=GridSignals(cfg).snapshot(15.0),
        zones=[{
            "name": z,
            "air_temp_c": 24.5,
            "radiant_temp_c": 24.8,
            "rh_pct": 50.0,
            "co2_ppm": 620.0,
            "occupants": 2.4,
            "pmv": 0.22,
            "ppd": 6.1,
            "heating_sp_c": 21.0,
            "cooling_sp_c": 25.0,
        } for z in CONTROLLED_ZONES],
        mean_air_temp_c=24.5,
        max_abs_pmv=0.22,
        mean_co2_ppm=620.0,
        max_co2_ppm=650.0,
        current_setpoints={z: (21.0, 25.0) for z in CONTROLLED_ZONES},
    )

    print(f"\n  {BOLD}Snapshot sent to the LLM:{RESET}")
    prompt = snap.to_prompt_dict()
    for k, v in prompt.items():
        print(f"    {k:30s} = {v}")

    print(f"\n  {BOLD}Waiting for LLM decision…{RESET}")
    t0 = time.time()
    decision = agent(snap)
    latency = (time.time() - t0) * 1000

    sp = list(decision.setpoints.values())[0]
    print(f"\n  {GREEN}{BOLD}Decision:{RESET}")
    print(f"    heating = {sp[0]}°C")
    print(f"    cooling = {sp[1]}°C")
    print(f"    source  = {decision.source}")
    print(f"    reason  = {decision.rationale}")
    print(f"    latency = {latency:.0f} ms")


# ──────────────────────────────────────────── 4. guardrail safety
def step_guardrail():
    banner("4 · GUARDRAIL SAFETY  (comfort protection layer)")
    info("Demonstrating how the guardrail clamps dangerous setpoints…")

    cfg = Config.load()
    guard = Guardrail(cfg)

    tests = [
        # (heating, cooling, occupied, description)
        (15.0, 30.0, True,  "LLM tries extreme setback while OCCUPIED"),
        (25.0, 26.0, True,  "LLM gives a narrow deadband (1°C < 2°C min)"),
        (10.0, 35.0, False, "LLM tries deep unoccupied setback"),
        (21.0, 25.0, True,  "LLM gives a reasonable occupied setpoint"),
    ]

    for h_in, c_in, occ, desc in tests:
        h_out, c_out = guard.clamp(h_in, c_in, occ)
        changed = (round(h_in, 2) != h_out) or (round(c_in, 2) != c_out)
        tag = f"{RED}CLAMPED{RESET}" if changed else f"{GREEN}PASS{RESET}"
        occ_s = "occupied" if occ else "unoccupied"
        print(f"  [{tag}] {desc}")
        print(f"         {occ_s}: H={h_in}→{h_out}°C  C={c_in}→{c_out}°C")
        print()


# ──────────────────────────────────────────── 5. grid-aware pricing
def step_grid_signals():
    banner("5 · GRID-AWARE PRICING & CARBON  (24h curve)")
    info("The agent sees these signals and shifts load away from peak / dirty hours…")

    cfg = Config.load()
    grid = GridSignals(cfg)

    print(f"  {'Hour':>6s}  {'Price':>10s}  {'Carbon':>12s}  {'Peak':>6s}")
    print(f"  {'─' * 6}  {'─' * 10}  {'─' * 12}  {'─' * 6}")
    for h in range(24):
        sig = grid.snapshot(float(h))
        peak_marker = f"{YELLOW}■ PEAK{RESET}" if sig["is_peak_period"] else ""
        bar_len = int(sig["carbon_gco2_kwh"] / 20)
        bar = f"{'█' * bar_len}"
        print(f"  {h:5d}h  ${sig['price_usd_kwh']:.2f}/kWh  "
              f"{sig['carbon_gco2_kwh']:6.0f} g/kWh  {peak_marker}")
    print(f"\n  {DIM}Peak window: {cfg['grid']['peak_hours'][0]}:00 – "
          f"{cfg['grid']['peak_hours'][1]}:00  "
          f"(${cfg['grid']['price_peak']:.2f}/kWh vs ${cfg['grid']['price_offpeak']:.2f}/kWh){RESET}")


# ──────────────────────────────────────────── 6. self-correction
def step_self_correction():
    banner("6 · SELF-CORRECTION  (break it, heal it)")
    info("Injecting a bad Timestep into the IDF to crash EnergyPlus,")
    info("then letting the agent read the .err and fix it autonomously…")

    cfg = Config.load()
    from src.self_correction import apply_timestep, self_heal

    # inject the bad value
    base_text = (REPO / "models/baseline.idf").read_text(encoding="latin-1")
    broken = apply_timestep(base_text, 0)
    broken_path = REPO / "models/broken.idf"
    broken_path.write_text(broken, encoding="latin-1")
    warn("Injected `Timestep,0;` into models/broken.idf  (EnergyPlus will fatal)")

    # heal
    result = self_heal(
        broken_path,
        str(cfg.weather_path),
        cfg.output_dir / "self_correction",
        cfg.raw["llm"]["base_url"],
        cfg.raw["llm"]["model"],
        progress=lambda m: print(f"  {DIM}{m}{RESET}"),
    )

    print()
    if result.get("healed"):
        ok(f"Healed! Fixed timestep to {result['fix_value']} via {result['fix_source']}")
        ok(f"Return code: {result['rc_before']} → {result['rc_after']}")
    else:
        fail(f"Not healed: {result.get('reason', 'unknown')}")

    # cleanup
    for p in [REPO / "models/broken.idf", REPO / "models/self_healed.idf"]:
        if p.exists():
            p.unlink()


# ──────────────────────────────────────────── 7. full AI closed loop
def step_full_ai_run():
    banner("7 · FULL AI CLOSED LOOP  (2-week simulation with LLM)")
    info("Running baseline + AI closed loop with Llama 3.1 making live decisions…")
    info("This takes ~20-30 minutes depending on your hardware.\n")

    cfg = Config.load()
    from src.orchestrator import run_closed_loop

    t0 = time.time()
    result = run_closed_loop(cfg, "llm", progress=print)
    elapsed = time.time() - t0

    print(f"\n  {GREEN}{BOLD}Completed in {elapsed / 60:.1f} minutes{RESET}")
    return result


# ──────────────────────────────────────────── 8. results summary
def step_results(result: dict):
    banner("8 · FINAL RESULTS  (AI vs Baseline)")

    sv = result["savings"]
    metrics = [
        ("Total electricity", "electricity_kwh", "kWh"),
        ("HVAC electricity",  "hvac_kwh",        "kWh"),
        ("Cooling energy",    "cooling_kwh",      "kWh"),
        ("Energy cost",       "cost_usd",         "$"),
        ("Carbon emissions",  "carbon_kg",        "kg CO₂"),
    ]

    print(f"  {'Metric':<22s}  {'Baseline':>10s}  {'AI':>10s}  {'Saved':>10s}  {'%':>8s}")
    print(f"  {'─' * 22}  {'─' * 10}  {'─' * 10}  {'─' * 10}  {'─' * 8}")
    for label, key, unit in metrics:
        s = sv[key]
        prefix = "$" if unit == "$" else ""
        suffix = "" if unit == "$" else f" {unit}"
        print(f"  {label:<22s}  {prefix}{s['baseline']:>9.1f}{suffix}  "
              f"{prefix}{s['ai']:>9.1f}{suffix}  "
              f"{prefix}{s['saved']:>9.1f}{suffix}  "
              f"{GREEN}{BOLD}−{s['pct']:.1f}%{RESET}")

    c = sv["comfort"]
    iaq = sv.get("iaq", {})
    auto = sv["autonomy"]

    print(f"\n  {BOLD}Comfort:{RESET}     {c['ai_pct_pmv_ok']}% occupied steps PMV-ok  "
          f"(max |PMV| = {c['ai_max_abs_pmv']})")
    print(f"  {BOLD}Air Quality:{RESET}  {iaq.get('ai_pct_co2_ok', 100)}% CO₂-ok  "
          f"(max {iaq.get('ai_max_co2_ppm', 0):.0f} ppm, "
          f"mean {iaq.get('ai_mean_co2_ppm', 0):.0f} ppm)")
    print(f"  {BOLD}Autonomy:{RESET}     {auto['ai_llm_calls']} LLM decisions, "
          f"{auto['ai_fallbacks']} fallbacks")

    # report generation
    report = REPO / "outputs" / "report.html"
    if report.exists():
        ok(f"Report ready: {report}")
    else:
        info("To generate the HTML report: python scripts/make_report.py")

    print(f"\n  {GREEN}{BOLD}{'═' * 50}")
    print(f"  DEMO COMPLETE — the building ran itself with AI!")
    print(f"  {'═' * 50}{RESET}\n")


# ──────────────────────────────────────────── main
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Eco-Loop comprehensive demo")
    ap.add_argument("--quick", action="store_true",
                    help="skip the full AI run (~20 min); use rule controller only")
    ap.add_argument("--skip-heal", action="store_true",
                    help="skip the self-correction demo")
    ap.add_argument("--non-interactive", action="store_true",
                    help="don't wait for [enter] between steps")
    args = ap.parse_args()

    wait = (lambda label="next step": None) if args.non_interactive else pause

    print(f"\n{BOLD}{CYAN}╔══════════════════════════════════════════════════════════╗")
    print(f"║   🌡️  ECO-LOOP BUILDING AGENT  —  COMPREHENSIVE DEMO    ║")
    print(f"║   A building that runs its own AC with an AI             ║")
    print(f"╚══════════════════════════════════════════════════════════╝{RESET}")

    # 1. environment
    if not step_environment():
        fail("Environment check failed. Fix the issues above and re-run.")
        return
    wait("quick simulation")

    # 2. quick rule run
    rule_result = step_quick_rule_run()
    wait("LLM reasoning")

    # 3. LLM reasoning
    step_llm_reasoning()
    wait("guardrail demo")

    # 4. guardrail
    step_guardrail()
    wait("grid signals")

    # 5. grid signals
    step_grid_signals()

    # 6. self-correction
    if not args.skip_heal:
        wait("self-correction")
        step_self_correction()

    # 7 & 8. full AI run or show rule results
    if args.quick:
        info(f"\n  {YELLOW}--quick mode: skipping the full AI run.{RESET}")
        info("  Showing results from the rule-based controller instead.\n")
        step_results(rule_result)
    else:
        wait("full AI run (~20-30 min)")
        ai_result = step_full_ai_run()

        # generate report
        info("Generating HTML report…")
        try:
            sys.path.insert(0, str(REPO / "scripts"))
            from make_report import main as make_report_main
            make_report_main()
        except Exception as e:
            warn(f"Report generation failed: {e}")

        step_results(ai_result)


if __name__ == "__main__":
    main()
