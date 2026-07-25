"""the energyplus runtime-api wrapper - the sense/actuate bus for the loop.

one EnergyPlusRunner drives a single simulation. while it runs it:
  - reads the sensors every timestep (zone temps, radiant, humidity, occupancy,
    outdoor conditions),
  - works out pmv comfort and adds up the electricity/gas meters,
  - every control.interval_minutes it asks the injected controller for fresh
    heating/cooling setpoints and pushes them straight back into the running
    energyplus instance through the "zone temperature control" actuators.

if controller is None the model just runs its own schedules -> that's the baseline.
the ai run reuses the exact same idf; the only difference is the live actuation, so
the comparison is genuinely apples-to-apples.

one thing i'm strict about: a controller error must never take the sim down. every
decision is wrapped, and if it throws we hold the last setpoints and keep going.
that's what lets the loop run the full stretch without falling over.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import eplus_paths

eplus_paths.ensure_on_path()
from pyenergyplus.api import EnergyPlusAPI  # noqa: E402

from .comfort import clo_for_season, pmv_ppd  # noqa: E402
from .grid_signals import GridSignals  # noqa: E402

CONTROLLED_ZONES = [
    "CORE_ZN",
    "PERIMETER_ZN_1",
    "PERIMETER_ZN_2",
    "PERIMETER_ZN_3",
    "PERIMETER_ZN_4",
]

J_TO_KWH = 1.0 / 3.6e6


@dataclass
class Decision:
    """one control action from a controller, for one interval."""

    setpoints: dict[str, tuple[float, float]]  # zone -> (heating_c, cooling_c)
    rationale: str = ""
    source: str = "rule"  # 'llm' | 'llm-guardrailed' | 'rule-fallback' | 'baseline'
    latency_ms: float = 0.0


@dataclass
class Snapshot:
    """everything the controller gets to see at a decision point."""

    step: int
    sim_time_hours: float
    day_of_year: int
    hour: float
    is_weekend: bool
    outdoor_temp_c: float
    occupied: bool
    total_occupants: float
    grid: dict
    zones: list[dict]
    mean_air_temp_c: float
    max_abs_pmv: float
    mean_co2_ppm: float
    max_co2_ppm: float
    current_setpoints: dict[str, tuple[float, float]]

    def to_prompt_dict(self) -> dict:
        """the small json the llm sees - kept tiny on purpose to keep latency down."""
        return {
            "time_of_day_h": round(self.hour, 2),
            "is_weekend": self.is_weekend,
            "outdoor_temp_c": round(self.outdoor_temp_c, 1),
            "occupied": self.occupied,
            "occupants": round(self.total_occupants, 1),
            "mean_indoor_temp_c": round(self.mean_air_temp_c, 1),
            "worst_pmv": round(self.max_abs_pmv, 2),
            "worst_indoor_co2_ppm": round(self.max_co2_ppm),
            "grid_carbon_gco2_kwh": self.grid["carbon_gco2_kwh"],
            "grid_price_usd_kwh": self.grid["price_usd_kwh"],
            "grid_peak_period": self.grid["is_peak_period"],
            "current_heating_sp_c": round(
                list(self.current_setpoints.values())[0][0], 1
            ),
            "current_cooling_sp_c": round(
                list(self.current_setpoints.values())[0][1], 1
            ),
        }


ControllerFn = Callable[[Snapshot], Decision]


class EnergyPlusRunner:
    def __init__(
        self,
        cfg,
        idf: str | Path,
        epw: str | Path,
        out_dir: str | Path,
        label: str,
        controller: Optional[ControllerFn] = None,
        progress: Optional[Callable[[str], None]] = None,
    ):
        self.cfg = cfg
        self.idf = str(idf)
        self.epw = str(epw)
        self.out_dir = str(out_dir)
        Path(self.out_dir).mkdir(parents=True, exist_ok=True)
        self.label = label
        self.controller = controller
        self.progress = progress or (lambda m: None)
        self.grid = GridSignals(cfg)
        self.control_interval = cfg["control"]["interval_minutes"]

        self.api = EnergyPlusAPI()
        self.state = self.api.state_manager.new_state()

        # running totals
        self._h: dict = {}
        self._handles_ready = False
        self.total_elec_j = 0.0   # building + hvac (facility electricity)
        self.total_hvac_j = 0.0
        self.total_cool_j = 0.0
        self.total_fans_j = 0.0
        self.total_gas_j = 0.0
        self._interval_elec_j = 0.0
        self._last_ctrl_min = -1e9
        self._cur_decision: Optional[Decision] = None
        self._step = 0
        self._n_llm_calls = 0
        self._n_fallbacks = 0

        self.timeseries: list[dict] = []
        self.decisions: list[dict] = []

    # ---------------------------------------------------------------- setup
    def _request_variables(self):
        ex = self.api.exchange
        for z in CONTROLLED_ZONES:
            ex.request_variable(self.state, "Zone Mean Air Temperature", z)
            ex.request_variable(self.state, "Zone Mean Radiant Temperature", z)
            ex.request_variable(self.state, "Zone Air Relative Humidity", z)
            ex.request_variable(self.state, "Zone Air CO2 Concentration", z)
            ex.request_variable(self.state, "Zone People Occupant Count", z)
            ex.request_variable(
                self.state, "Zone Thermostat Heating Setpoint Temperature", z
            )
            ex.request_variable(
                self.state, "Zone Thermostat Cooling Setpoint Temperature", z
            )
        ex.request_variable(
            self.state, "Site Outdoor Air Drybulb Temperature", "Environment"
        )

    def _is_run_period(self, state) -> bool:
        # kind_of_sim: 1=design day, 2=run-period design, 3=run-period weather.
        # we only sense/actuate/count during the real weather run.
        return self.api.exchange.kind_of_sim(state) == 3

    def _ensure_handles(self):
        if self._handles_ready:
            return
        ex, st = self.api.exchange, self.state
        if not ex.api_data_fully_ready(st) or not self._is_run_period(st):
            return
        h = self._h
        for z in CONTROLLED_ZONES:
            h[("tair", z)] = ex.get_variable_handle(st, "Zone Mean Air Temperature", z)
            h[("trad", z)] = ex.get_variable_handle(
                st, "Zone Mean Radiant Temperature", z
            )
            h[("rh", z)] = ex.get_variable_handle(st, "Zone Air Relative Humidity", z)
            h[("co2", z)] = ex.get_variable_handle(st, "Zone Air CO2 Concentration", z)
            h[("occ", z)] = ex.get_variable_handle(
                st, "Zone People Occupant Count", z
            )
            h[("htgsp", z)] = ex.get_variable_handle(
                st, "Zone Thermostat Heating Setpoint Temperature", z
            )
            h[("clgsp", z)] = ex.get_variable_handle(
                st, "Zone Thermostat Cooling Setpoint Temperature", z
            )
            h[("act_htg", z)] = ex.get_actuator_handle(
                st, "Zone Temperature Control", "Heating Setpoint", z
            )
            h[("act_clg", z)] = ex.get_actuator_handle(
                st, "Zone Temperature Control", "Cooling Setpoint", z
            )
        h["outdoor"] = ex.get_variable_handle(
            st, "Site Outdoor Air Drybulb Temperature", "Environment"
        )
        # heads up: "Electricity:Facility" won't resolve through the api on this
        # model, so i build the facility total from its two children instead:
        #   Electricity:Building = interior lights + plug loads (agent can't touch these)
        #   Electricity:HVAC     = cooling coils + fans + pumps  (this is what moves)
        # total = building + hvac. cooling/fans kept separately for the breakdown.
        h["m_bldg"] = ex.get_meter_handle(st, "Electricity:Building")
        h["m_hvac"] = ex.get_meter_handle(st, "Electricity:HVAC")
        h["m_gas"] = ex.get_meter_handle(st, "NaturalGas:Facility")
        h["m_cool"] = ex.get_meter_handle(st, "Cooling:Electricity")
        h["m_fans"] = ex.get_meter_handle(st, "Fans:Electricity")
        self._handles_ready = True

    # -------------------------------------------------------------- callbacks
    def _sim_minutes(self) -> float:
        ex, st = self.api.exchange, self.state
        return (ex.day_of_year(st) - 1) * 1440.0 + ex.current_time(st) * 60.0

    def _read_snapshot(self) -> Snapshot:
        ex, st = self.api.exchange, self.state
        outdoor = ex.get_variable_value(st, self._h["outdoor"])
        clo = clo_for_season(outdoor, self.cfg)
        met = self.cfg["comfort"]["met_rate"]
        vel = self.cfg["comfort"]["air_velocity_ms"]

        zones = []
        total_occ = 0.0
        temps = []
        co2s = []
        max_pmv = 0.0
        max_co2 = 0.0
        cur_sps: dict[str, tuple[float, float]] = {}
        for z in CONTROLLED_ZONES:
            ta = ex.get_variable_value(st, self._h[("tair", z)])
            tr = ex.get_variable_value(st, self._h[("trad", z)])
            rh = ex.get_variable_value(st, self._h[("rh", z)])
            co2 = ex.get_variable_value(st, self._h[("co2", z)])
            occ = ex.get_variable_value(st, self._h[("occ", z)])
            htg = ex.get_variable_value(st, self._h[("htgsp", z)])
            clg = ex.get_variable_value(st, self._h[("clgsp", z)])
            c = pmv_ppd(ta=ta, tr=tr, vel=vel, rh=rh, met=met, clo=clo)
            zones.append(
                {
                    "zone": z,
                    "air_temp_c": round(ta, 2),
                    "radiant_temp_c": round(tr, 2),
                    "rh_pct": round(rh, 1),
                    "co2_ppm": round(co2),
                    "occupants": round(occ, 2),
                    "pmv": c.pmv,
                    "ppd": c.ppd,
                    "heating_sp_c": round(htg, 2),
                    "cooling_sp_c": round(clg, 2),
                }
            )
            cur_sps[z] = (htg, clg)
            total_occ += occ
            temps.append(ta)
            co2s.append(co2)
            if occ > 0.5:
                max_pmv = max(max_pmv, abs(c.pmv))
                max_co2 = max(max_co2, co2)

        hour = ex.current_time(st)
        doy = ex.day_of_year(st)
        dow = ex.day_of_week(st)  # 1=sunday ... 7=saturday
        is_weekend = dow in (1, 7)
        occupied = total_occ > 0.5
        grid = self.grid.snapshot(hour)
        return Snapshot(
            step=self._step,
            sim_time_hours=self._sim_minutes() / 60.0,
            day_of_year=doy,
            hour=hour,
            is_weekend=is_weekend,
            outdoor_temp_c=outdoor,
            occupied=occupied,
            total_occupants=total_occ,
            grid=grid,
            zones=zones,
            mean_air_temp_c=sum(temps) / len(temps),
            max_abs_pmv=max_pmv,
            mean_co2_ppm=sum(co2s) / len(co2s),
            max_co2_ppm=max_co2,
            current_setpoints=cur_sps,
        )

    def _apply(self, decision: Decision):
        ex, st = self.api.exchange, self.state
        for z in CONTROLLED_ZONES:
            htg, clg = decision.setpoints.get(z, decision.setpoints[CONTROLLED_ZONES[0]])
            ex.set_actuator_value(st, self._h[("act_htg", z)], float(htg))
            ex.set_actuator_value(st, self._h[("act_clg", z)], float(clg))

    def _on_control(self, state):
        """begin_system_timestep_before_predictor: sense, maybe decide, actuate."""
        try:
            ex = self.api.exchange
            if not ex.api_data_fully_ready(state) or ex.warmup_flag(state):
                return
            if not self._is_run_period(state):
                return
            self._ensure_handles()
            if not self._handles_ready:
                return
            if self.controller is None:
                return  # baseline: let the native schedules drive

            now = self._sim_minutes()
            need_decision = (
                self._cur_decision is None
                or (now - self._last_ctrl_min) >= self.control_interval
            )
            if need_decision:
                snap = self._read_snapshot()
                t0 = time.perf_counter()
                try:
                    decision = self.controller(snap)
                except Exception as e:  # the controller can't be allowed to crash us
                    decision = self._hold_or_baseline(snap, f"controller error: {e}")
                    self._n_fallbacks += 1
                decision.latency_ms = (time.perf_counter() - t0) * 1000.0
                self._cur_decision = decision
                self._last_ctrl_min = now
                if decision.source == "llm" or decision.source == "llm-guardrailed":
                    self._n_llm_calls += 1
                elif decision.source == "rule-fallback":
                    self._n_fallbacks += 1
                self.decisions.append(
                    {
                        "sim_time_hours": round(snap.sim_time_hours, 3),
                        "hour": round(snap.hour, 2),
                        "occupied": snap.occupied,
                        "outdoor_temp_c": round(snap.outdoor_temp_c, 1),
                        "carbon_gco2_kwh": snap.grid["carbon_gco2_kwh"],
                        "price_usd_kwh": snap.grid["price_usd_kwh"],
                        "heating_sp_c": round(
                            decision.setpoints[CONTROLLED_ZONES[0]][0], 2
                        ),
                        "cooling_sp_c": round(
                            decision.setpoints[CONTROLLED_ZONES[0]][1], 2
                        ),
                        "source": decision.source,
                        "latency_ms": round(decision.latency_ms, 1),
                        "rationale": decision.rationale[:240],
                    }
                )
                self.progress(
                    f"[{self.label}] t={snap.sim_time_hours:6.1f}h "
                    f"OA={snap.outdoor_temp_c:4.1f}C occ={snap.occupied} "
                    f"-> H={decision.setpoints[CONTROLLED_ZONES[0]][0]:.1f} "
                    f"C={decision.setpoints[CONTROLLED_ZONES[0]][1]:.1f} "
                    f"[{decision.source} {decision.latency_ms:.0f}ms]"
                )
            # re-apply the current decision every timestep so the override sticks
            if self._cur_decision is not None:
                self._apply(self._cur_decision)
        except Exception as e:  # very last line of defense
            self.progress(f"[{self.label}] control callback error (ignored): {e}")

    def _hold_or_baseline(self, snap: Snapshot, reason: str) -> Decision:
        if self._cur_decision is not None:
            d = Decision(
                setpoints=self._cur_decision.setpoints,
                rationale=f"hold previous ({reason})",
                source="rule-fallback",
            )
            return d
        # nothing to hold yet -> fall back to sane comfort setpoints
        return Decision(
            setpoints={z: (21.0, 24.0) for z in CONTROLLED_ZONES},
            rationale=f"cold-start fallback ({reason})",
            source="rule-fallback",
        )

    def _on_report(self, state):
        """end_zone_timestep_after_zone_reporting: add up energy + log the row."""
        try:
            ex = self.api.exchange
            if not ex.api_data_fully_ready(state) or ex.warmup_flag(state):
                return
            if not self._is_run_period(state):
                return
            self._ensure_handles()
            if not self._handles_ready:
                return
            self._step += 1
            d_bldg = ex.get_meter_value(state, self._h["m_bldg"])
            d_hvac = ex.get_meter_value(state, self._h["m_hvac"])
            d_cool = ex.get_meter_value(state, self._h["m_cool"])
            d_fans = ex.get_meter_value(state, self._h["m_fans"])
            dg = ex.get_meter_value(state, self._h["m_gas"])
            de = d_bldg + d_hvac  # facility electricity for this timestep
            self.total_elec_j += de
            self.total_hvac_j += d_hvac
            self.total_cool_j += d_cool
            self.total_fans_j += d_fans
            self.total_gas_j += dg

            snap = self._read_snapshot()
            row = {
                "step": self._step,
                "sim_time_hours": round(snap.sim_time_hours, 4),
                "day_of_year": snap.day_of_year,
                "hour": round(snap.hour, 3),
                "is_weekend": snap.is_weekend,
                "outdoor_temp_c": round(snap.outdoor_temp_c, 2),
                "occupied": snap.occupied,
                "occupants": round(snap.total_occupants, 2),
                "mean_air_temp_c": round(snap.mean_air_temp_c, 3),
                "max_abs_pmv": round(snap.max_abs_pmv, 3),
                "mean_co2_ppm": round(snap.mean_co2_ppm, 1),
                "max_co2_ppm": round(snap.max_co2_ppm, 1),
                "interval_elec_kwh": round(de * J_TO_KWH, 6),
                "interval_hvac_kwh": round(d_hvac * J_TO_KWH, 6),
                "interval_cool_kwh": round(d_cool * J_TO_KWH, 6),
                "interval_gas_kwh": round(dg * J_TO_KWH, 6),
                "cum_elec_kwh": round(self.total_elec_j * J_TO_KWH, 4),
                "carbon_gco2_kwh": snap.grid["carbon_gco2_kwh"],
                "price_usd_kwh": snap.grid["price_usd_kwh"],
                "interval_cost_usd": round(
                    de * J_TO_KWH * snap.grid["price_usd_kwh"], 6
                ),
                "interval_carbon_kg": round(
                    de * J_TO_KWH * snap.grid["carbon_gco2_kwh"] / 1000.0, 6
                ),
            }
            for zd in snap.zones:
                z = zd["zone"]
                row[f"{z}_temp"] = zd["air_temp_c"]
                row[f"{z}_pmv"] = zd["pmv"]
                row[f"{z}_htgsp"] = zd["heating_sp_c"]
                row[f"{z}_clgsp"] = zd["cooling_sp_c"]
                row[f"{z}_co2"] = zd["co2_ppm"]
                row[f"{z}_occ"] = zd["occupants"]
            self.timeseries.append(row)
        except Exception as e:
            self.progress(f"[{self.label}] report callback error (ignored): {e}")

    # ------------------------------------------------------------------- run
    def run(self) -> dict:
        self._request_variables()
        rt = self.api.runtime
        rt.set_console_output_status(self.state, False)
        rt.callback_begin_system_timestep_before_predictor(self.state, self._on_control)
        rt.callback_end_zone_timestep_after_zone_reporting(self.state, self._on_report)
        t0 = time.perf_counter()
        self.progress(f"[{self.label}] starting energyplus...")
        rc = rt.run_energyplus(
            self.state, ["-w", self.epw, "-d", self.out_dir, "-r", self.idf]
        )
        wall = time.perf_counter() - t0
        self.progress(
            f"[{self.label}] done rc={rc} wall={wall:.1f}s steps={self._step} "
            f"elec={self.total_elec_j * J_TO_KWH:.1f}kWh "
            f"llm_calls={self._n_llm_calls} fallbacks={self._n_fallbacks}"
        )
        return self.summary(rc, wall)

    # --------------------------------------------------------------- results
    def summary(self, rc: int = 0, wall: float = 0.0) -> dict:
        occ_rows = [r for r in self.timeseries if r["occupied"]]
        pmvs = [r["max_abs_pmv"] for r in occ_rows]
        co2s = [r["max_co2_ppm"] for r in occ_rows]
        total_cost = sum(r["interval_cost_usd"] for r in self.timeseries)
        total_carbon = sum(r["interval_carbon_kg"] for r in self.timeseries)
        comfort_viol = sum(
            1 for r in occ_rows if r["max_abs_pmv"] > self.cfg["comfort"]["pmv_limit"]
        )
        co2_limit = self.cfg["comfort"].get("co2_limit_ppm", 1000)
        iaq_viol = sum(1 for r in occ_rows if r["max_co2_ppm"] > co2_limit)
        return {
            "label": self.label,
            "return_code": rc,
            "wall_seconds": round(wall, 1),
            "sim_steps": self._step,
            "total_elec_kwh": round(self.total_elec_j * J_TO_KWH, 3),
            "total_hvac_kwh": round(self.total_hvac_j * J_TO_KWH, 3),
            "total_cool_kwh": round(self.total_cool_j * J_TO_KWH, 3),
            "total_fans_kwh": round(self.total_fans_j * J_TO_KWH, 3),
            "total_gas_kwh": round(self.total_gas_j * J_TO_KWH, 3),
            "total_cost_usd": round(total_cost, 3),
            "total_carbon_kg": round(total_carbon, 3),
            "mean_occupied_abs_pmv": round(sum(pmvs) / len(pmvs), 4) if pmvs else 0.0,
            "max_occupied_abs_pmv": round(max(pmvs), 4) if pmvs else 0.0,
            "pct_occupied_steps_pmv_ok": round(
                100.0 * (len(occ_rows) - comfort_viol) / len(occ_rows), 2
            )
            if occ_rows
            else 100.0,
            "max_occupied_co2_ppm": round(max(co2s), 1) if co2s else 0.0,
            "mean_occupied_co2_ppm": round(sum(co2s) / len(co2s), 1) if co2s else 0.0,
            "pct_occupied_steps_iaq_ok": round(
                100.0 * (len(occ_rows) - iaq_viol) / len(occ_rows), 2
            )
            if occ_rows
            else 100.0,
            "n_llm_calls": self._n_llm_calls,
            "n_fallbacks": self._n_fallbacks,
        }

    def write_outputs(self, out_dir: str | Path):
        import pandas as pd

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(self.timeseries).to_csv(
            out_dir / f"{self.label}_timeseries.csv", index=False
        )
        if self.decisions:
            pd.DataFrame(self.decisions).to_csv(
                out_dir / f"{self.label}_decisions.csv", index=False
            )
        (out_dir / f"{self.label}_summary.json").write_text(
            json.dumps(self.summary(), indent=2)
        )
