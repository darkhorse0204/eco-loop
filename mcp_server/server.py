"""mcp server that exposes the building as a set of agentic tools.

the same control logic the in-loop agent uses is published here over the model
context protocol, so any mcp client (a desktop assistant, an ide, another agent)
can sense and supervise the building through plain tool calls:

    get_building_state      -> latest streamed sensor snapshot
    get_grid_signals        -> carbon intensity + price for an hour
    propose_setpoints       -> guardrailed setpoint suggestion for a situation
    get_savings_summary     -> baseline-vs-ai savings
    get_simulation_errors   -> parsed energyplus .err (self-diagnosis)
    list_actuators          -> actuators the agent is allowed to drive

run it over stdio with:  python -m mcp_server.server
in a client, register command="python", args=["-m","mcp_server.server"].
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP

from src.config import Config
from src.controller import Guardrail
from src.grid_signals import GridSignals

cfg = Config.load()
OUT = cfg.output_dir
grid = GridSignals(cfg)
guard = Guardrail(cfg)

mcp = FastMCP("eco-loop-building")


def _last_row(csv_path: Path) -> dict:
    import pandas as pd

    if not csv_path.exists():
        return {}
    df = pd.read_csv(csv_path)
    return {} if df.empty else df.iloc[-1].to_dict()


@mcp.tool()
def get_building_state(run: str = "ai") -> dict:
    """latest streamed sensor snapshot from the most recent run.

    args:
        run: which run to read, "ai" or "baseline".
    """
    row = _last_row(OUT / f"{run}_timeseries.csv")
    if not row:
        return {"error": f"no timeseries for run '{run}'. run the closed loop first."}
    zones = {}
    for z in ["CORE_ZN", "PERIMETER_ZN_1", "PERIMETER_ZN_2", "PERIMETER_ZN_3", "PERIMETER_ZN_4"]:
        if f"{z}_temp" in row:
            zones[z] = {
                "air_temp_c": row[f"{z}_temp"],
                "pmv": row.get(f"{z}_pmv"),
                "heating_sp_c": row.get(f"{z}_htgsp"),
                "cooling_sp_c": row.get(f"{z}_clgsp"),
            }
    return {
        "sim_time_hours": row.get("sim_time_hours"),
        "hour_of_day": row.get("hour"),
        "outdoor_temp_c": row.get("outdoor_temp_c"),
        "occupied": bool(row.get("occupied")),
        "mean_air_temp_c": row.get("mean_air_temp_c"),
        "worst_abs_pmv": row.get("max_abs_pmv"),
        "interval_elec_kwh": row.get("interval_elec_kwh"),
        "zones": zones,
    }


@mcp.tool()
def get_grid_signals(hour: float) -> dict:
    """marginal grid carbon (gco2/kwh) and time-of-use price ($/kwh) for an hour of day (0-24)."""
    return grid.snapshot(hour)


@mcp.tool()
def propose_setpoints(occupied: bool, hour: float, outdoor_temp_c: float,
                      worst_abs_pmv: float = 0.2) -> dict:
    """suggest guardrailed heating/cooling setpoints for a situation.

    runs the grid-aware policy and clamps to the hard comfort envelope, so whatever
    comes back is always safe to push into energyplus.
    """
    peak = grid.is_peak(hour)
    if not occupied:
        h, c = 15.6, 27.5
        why = "unoccupied: moderate setup, avoids the morning recovery penalty."
    else:
        h = 20.0
        c = 25.5 if peak else 25.0
        why = ("occupied peak: float cooling to the band top to shave costly load."
               if peak else "occupied off-peak: efficient 25.0 c, comfort kept.")
    if worst_abs_pmv > cfg["comfort"]["pmv_limit"]:
        c = min(c, 24.0)  # comfort at risk -> cool a bit harder
        why = "comfort at risk: tightening the cooling setpoint to protect pmv."
    hc = guard.clamp(h, c, occupied)
    return {
        "heating_setpoint_c": hc[0],
        "cooling_setpoint_c": hc[1],
        "grid_is_peak": peak,
        "rationale": why,
    }


@mcp.tool()
def get_savings_summary() -> dict:
    """baseline-vs-ai savings from the last closed-loop run."""
    p = OUT / "summary.json"
    if not p.exists():
        return {"error": "no summary.json yet. run the closed loop first."}
    data = json.loads(p.read_text())
    return data.get("savings", data)


@mcp.tool()
def get_simulation_errors(run: str = "ai") -> dict:
    """parse the energyplus .err for a run and summarise warnings/severe/fatal."""
    err = OUT / f"run_{run}" / "eplusout.err"
    if not err.exists():
        return {"error": f"no .err for run '{run}'."}
    lines = err.read_text(errors="replace").splitlines()
    severe = [l.strip() for l in lines if "** Severe" in l]
    fatal = [l.strip() for l in lines if "** Fatal" in l]
    warn = [l for l in lines if "** Warning" in l]
    return {
        "n_warnings": len(warn),
        "n_severe": len(severe),
        "n_fatal": len(fatal),
        "severe": severe[:10],
        "fatal": fatal[:5],
        "completed_ok": not fatal,
    }


@mcp.tool()
def list_actuators() -> list:
    """list the energyplus actuators the agent is allowed to drive (from api discovery)."""
    p = OUT / "_probe_api_data.csv"
    if not p.exists():
        return ["run scripts/probe_api_data.py to populate the actuator list"]
    return [l.strip() for l in p.read_text().splitlines() if l.startswith("Actuator,")][:40]


if __name__ == "__main__":
    mcp.run()
