"""the top-level runner.

runs the baseline (native schedules) and then the ai run (llm agent inside the
energyplus loop), then works out the savings and writes everything the dashboard
and the report read.

    python -m src.orchestrator            # llm agent (falls back to rule if ollama is down)
    python -m src.orchestrator --rule     # deterministic controller only, no llm
    python -m src.orchestrator --baseline-only
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Config
from .controller import RuleController
from .llm_agent import LLMAgent
from .runner import EnergyPlusRunner


def _pct(base: float, ai: float) -> float:
    return round(100.0 * (base - ai) / base, 2) if base else 0.0


def compute_savings(base: dict, ai: dict) -> dict:
    keys = [
        ("total_elec_kwh", "electricity_kwh"),
        ("total_hvac_kwh", "hvac_kwh"),
        ("total_cool_kwh", "cooling_kwh"),
        ("total_cost_usd", "cost_usd"),
        ("total_carbon_kg", "carbon_kg"),
    ]
    out = {}
    for src, name in keys:
        out[name] = {
            "baseline": base[src],
            "ai": ai[src],
            "saved": round(base[src] - ai[src], 3),
            "pct": _pct(base[src], ai[src]),
        }
    out["comfort"] = {
        "baseline_pct_pmv_ok": base["pct_occupied_steps_pmv_ok"],
        "ai_pct_pmv_ok": ai["pct_occupied_steps_pmv_ok"],
        "ai_mean_abs_pmv": ai["mean_occupied_abs_pmv"],
        "ai_max_abs_pmv": ai["max_occupied_abs_pmv"],
        "comfort_maintained": ai["pct_occupied_steps_pmv_ok"] >= 99.0,
    }
    out["autonomy"] = {
        "ai_llm_calls": ai.get("n_llm_calls", 0),
        "ai_fallbacks": ai.get("n_fallbacks", 0),
    }
    return out


def run_closed_loop(cfg: Config, controller_kind: str = "llm",
                    baseline_only: bool = False, progress=print) -> dict:
    idf, epw, out = str(cfg.idf_path), str(cfg.weather_path), cfg.output_dir

    progress("\n============ baseline (native schedules) ============")
    base_runner = EnergyPlusRunner(cfg, idf, epw, out / "run_baseline", "baseline",
                                   None, progress)
    base = base_runner.run()
    base_runner.write_outputs(out)  # -> outputs/baseline_timeseries.csv, baseline_summary.json

    if baseline_only:
        (out / "summary.json").write_text(json.dumps({"baseline": base}, indent=2))
        return {"baseline": base}

    if controller_kind == "rule":
        controller = RuleController(cfg)
        agent = None
    else:
        agent = LLMAgent(cfg, progress)
        agent.warmup()
        controller = agent

    progress("\n============ ai closed loop ============")
    ai_runner = EnergyPlusRunner(cfg, idf, epw, out / "run_ai", "ai", controller, progress)
    ai = ai_runner.run()
    ai_runner.write_outputs(out)

    savings = compute_savings(base, ai)
    result = {
        "config": {
            "idf": cfg.raw["simulation"]["idf"],
            "weather": cfg.raw["simulation"]["weather"],
            "controller": controller_kind,
            "control_interval_min": cfg.raw["control"]["interval_minutes"],
            "llm_model": cfg.raw["llm"]["model"],
        },
        "baseline": base,
        "ai": ai,
        "savings": savings,
    }
    (out / "summary.json").write_text(json.dumps(result, indent=2))
    _write_ai_effective_idf(cfg, ai_runner, out)

    progress("\n================= savings =================")
    for k in ["electricity_kwh", "hvac_kwh", "cooling_kwh", "cost_usd", "carbon_kg"]:
        s = savings[k]
        progress(f"  {k:16s}: {s['baseline']:9.1f} -> {s['ai']:9.1f}  "
                 f"saved {s['saved']:8.1f} ({s['pct']:+.1f}%)")
    c = savings["comfort"]
    progress(f"  comfort         : occupied pmv-ok {c['ai_pct_pmv_ok']}% "
             f"(max|pmv|={c['ai_max_abs_pmv']}) maintained={c['comfort_maintained']}")
    a = savings["autonomy"]
    progress(f"  autonomy        : {a['ai_llm_calls']} llm decisions, "
             f"{a['ai_fallbacks']} deterministic fallbacks")
    progress(f"\nwrote {out/'summary.json'}")
    return result


def _write_ai_effective_idf(cfg: Config, ai_runner, out: Path):
    """dump a 'modified' idf that bakes in the setpoint schedule the ai actually ran,
    so we have the runtime-generated .idf the brief asks for."""
    try:
        import pandas as pd

        dec = pd.DataFrame(ai_runner.decisions)
        if dec.empty:
            return
        base = Path(cfg.idf_path).read_text(encoding="latin-1")
        # build readable Schedule:Compact objects out of the decisions we logged
        lines = ["! ===================================================================",
                 "! ai-effective setpoint schedules (auto-generated from the closed loop)",
                 "! these reproduce the setpoints the agent injected live through the",
                 "! energyplus runtime api during the ai run.",
                 "! ==================================================================="]
        for kind, col in [("Heating", "heating_sp_c"), ("Cooling", "cooling_sp_c")]:
            lines.append(f"Schedule:Compact,\n  AI_{kind}_Setpoint_Effective,  !- Name")
            lines.append("  Temperature,             !- Schedule Type Limits Name")
            lines.append("  Through: 12/31,")
            lines.append("  For: AllDays,")
            # take hourly medians just to keep it compact
            hourly = dec.groupby(dec["hour"].astype(int))[col].median()
            for hr in range(24):
                val = float(hourly.get(hr, hourly.median()))
                end = "24:00" if hr == 23 else f"{hr+1:02d}:00"
                term = ";" if hr == 23 else ","
                lines.append(f"  Until: {end},{val:.1f}{term}")
        snippet = "\n".join(lines) + "\n\n"
        (out / "ai_effective.idf").write_text(snippet + base, encoding="latin-1")
    except Exception as e:  # a broken artifact writer must not sink the run
        print(f"[orchestrator] could not write ai_effective.idf: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule", action="store_true", help="use the deterministic controller (no llm)")
    ap.add_argument("--baseline-only", action="store_true")
    args = ap.parse_args()
    cfg = Config.load()
    run_closed_loop(cfg, "rule" if args.rule else "llm", args.baseline_only)


if __name__ == "__main__":
    main()
