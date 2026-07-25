"""interactive savings dashboard.

    streamlit run dashboard/app.py

reads the closed-loop outputs and draws the baseline-vs-ai comparison, the live
setpoint trace, comfort, and the agent's decision log with its rationales.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs"

C_BASE, C_AI, C_PEAK = "#D55E00", "#009E73", "#E69F00"

st.set_page_config(page_title="Eco-Loop Building Agent", layout="wide", page_icon="🌡️")


@st.cache_data
def load():
    summary = json.loads((OUT / "summary.json").read_text())
    b = pd.read_csv(OUT / "baseline_timeseries.csv")
    a = pd.read_csv(OUT / "ai_timeseries.csv")
    t0 = min(b.sim_time_hours.min(), a.sim_time_hours.min())
    b["day"] = (b.sim_time_hours - t0) / 24.0
    a["day"] = (a.sim_time_hours - t0) / 24.0
    dec = pd.read_csv(OUT / "ai_decisions.csv") if (OUT / "ai_decisions.csv").exists() else pd.DataFrame()
    return summary, b, a, dec


try:
    summary, b, a, dec = load()
except Exception as e:
    st.error(f"No results yet. Run `python -m src.orchestrator` first. ({e})")
    st.stop()

sv = summary["savings"]
cfg = summary["config"]

st.title("🌡️ Eco-Loop Building Agent — Autonomous Closed-Loop Savings")
st.caption(
    f"DOE small office · Tampa FL · brain = **{cfg['llm_model']}** (OSS LLM via Ollama, "
    f"tool-calling) · controller = **{cfg['controller']}** · control every "
    f"{cfg['control_interval_min']} min · EnergyPlus Runtime API closed loop"
)

iaq = sv.get("iaq", {})
c = st.columns(6)
c[0].metric("Total electricity", f"{sv['electricity_kwh']['ai']:.0f} kWh",
            f"−{sv['electricity_kwh']['pct']:.1f}%", delta_color="inverse")
c[1].metric("HVAC electricity", f"{sv['hvac_kwh']['ai']:.0f} kWh",
            f"−{sv['hvac_kwh']['pct']:.1f}%", delta_color="inverse")
c[2].metric("Energy cost", f"${sv['cost_usd']['ai']:.0f}",
            f"−{sv['cost_usd']['pct']:.1f}%", delta_color="inverse")
c[3].metric("Carbon", f"{sv['carbon_kg']['ai']:.0f} kg",
            f"−{sv['carbon_kg']['pct']:.1f}%", delta_color="inverse")
c[4].metric("Comfort (occ. PMV ok)", f"{sv['comfort']['ai_pct_pmv_ok']:.0f}%",
            f"max |PMV| {sv['comfort']['ai_max_abs_pmv']}")
c[5].metric("Air quality (CO₂ ok)", f"{iaq.get('ai_pct_co2_ok', 100):.0f}%",
            f"max {iaq.get('ai_max_co2_ppm', 0):.0f} ppm")

st.divider()
left, right = st.columns(2)


def add_peak(fig, x, hour):
    peak = ((hour >= 16) & (hour < 21)).values
    on = False
    for i in range(len(x)):
        if peak[i] and not on:
            start, on = x.iloc[i], True
        if on and (i == len(x) - 1 or not peak[i]):
            fig.add_vrect(x0=start, x1=x.iloc[i], fillcolor=C_PEAK, opacity=0.12,
                          line_width=0)
            on = False


with left:
    st.subheader("Cumulative facility electricity")
    fig = go.Figure()
    fig.add_scatter(x=b.day, y=b.cum_elec_kwh, name="Baseline", line=dict(color=C_BASE, width=3))
    fig.add_scatter(x=a.day, y=a.cum_elec_kwh, name="AI closed-loop", line=dict(color=C_AI, width=3))
    fig.update_layout(height=340, margin=dict(l=0, r=0, t=10, b=0),
                      xaxis_title="Day", yaxis_title="kWh", legend=dict(orientation="h"))
    st.plotly_chart(fig, use_container_width=True)

with right:
    st.subheader("Cooling setpoint — AI floats up during grid peak")
    w = (a.day >= 3) & (a.day <= 6)
    bb, aa = b[w], a[w]
    fig = go.Figure()
    add_peak(fig, aa.day, aa.hour)
    fig.add_scatter(x=bb.day, y=bb.CORE_ZN_clgsp, name="Baseline", line=dict(color=C_BASE, width=2, shape="hv"))
    fig.add_scatter(x=aa.day, y=aa.CORE_ZN_clgsp, name="AI", line=dict(color=C_AI, width=2, shape="hv"))
    fig.update_layout(height=340, margin=dict(l=0, r=0, t=10, b=0),
                      xaxis_title="Day (shaded = grid peak)", yaxis_title="°C",
                      legend=dict(orientation="h"))
    st.plotly_chart(fig, use_container_width=True)

left2, right2 = st.columns(2)
with left2:
    st.subheader("Indoor temperature & comfort band")
    w = (a.day >= 3) & (a.day <= 6)
    bb, aa = b[w], a[w]
    fig = go.Figure()
    fig.add_hrect(y0=20, y1=25.5, fillcolor=C_AI, opacity=0.06, line_width=0)
    fig.add_scatter(x=bb.day, y=bb.mean_air_temp_c, name="Baseline", line=dict(color=C_BASE, width=2))
    fig.add_scatter(x=aa.day, y=aa.mean_air_temp_c, name="AI", line=dict(color=C_AI, width=2))
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0),
                      xaxis_title="Day", yaxis_title="Indoor °C", legend=dict(orientation="h"))
    st.plotly_chart(fig, use_container_width=True)

with right2:
    st.subheader("Worst occupied PMV (comfort KPI)")
    occ = a[a.occupied.astype(bool)]
    fig = go.Figure()
    fig.add_hrect(y0=-0.5, y1=0.5, fillcolor=C_AI, opacity=0.10, line_width=0)
    fig.add_scatter(x=occ.day, y=occ.max_abs_pmv, name="AI |PMV|", line=dict(color=C_AI, width=1.5))
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0),
                      xaxis_title="Day", yaxis_title="|PMV|", showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

st.subheader("Indoor air quality — CO₂ kept healthy while saving energy")
occ = a[a.occupied.astype(bool)]
fig = go.Figure()
fig.add_hrect(y0=350, y1=1000, fillcolor=C_AI, opacity=0.06, line_width=0)
fig.add_scatter(x=occ.day, y=occ.max_co2_ppm, name="Worst zone CO₂", line=dict(color="#0072B2", width=1.5))
fig.add_hline(y=1000, line=dict(color=C_BASE, dash="dash", width=1.2))
fig.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0),
                  xaxis_title="Day", yaxis_title="CO₂ (ppm)", showlegend=False)
st.plotly_chart(fig, use_container_width=True)

st.divider()
st.subheader("🤖 LLM agent decision log (tool calls)")
if not dec.empty:
    src_counts = dec["source"].value_counts().to_dict()
    st.caption(
        f"{len(dec)} decisions · sources: {src_counts} · "
        f"median latency {dec['latency_ms'].median():.0f} ms · "
        f"showing the agent's live tool-calls and rationales"
    )
    show = dec[["sim_time_hours", "hour", "occupied", "outdoor_temp_c", "carbon_gco2_kwh",
                "price_usd_kwh", "heating_sp_c", "cooling_sp_c", "source", "latency_ms",
                "rationale"]].copy()
    st.dataframe(show, use_container_width=True, height=340)
else:
    st.info("No decision log (baseline-only run).")

with st.sidebar:
    st.header("Architecture")
    st.markdown(
        "**Closed loop**\n\n"
        "1. EnergyPlus Runtime API streams sensors every timestep\n"
        "2. Every N min the LLM is called with the state + grid signals\n"
        "3. LLM **calls `set_hvac_setpoints`** (tool-calling)\n"
        "4. Hard **guardrail** clamps to the comfort envelope\n"
        "5. Setpoints **injected live** back into EnergyPlus\n\n"
        "**Latency**: model kept warm + semantic caching (call only on regime change)\n\n"
        "**Reliability**: every decision time-boxed; deterministic fallback on "
        "timeout/error → the loop never stalls.\n\n"
        "Same tools exposed over **MCP** (`mcp_server/server.py`)."
    )
    st.metric("LLM decisions", sv["autonomy"]["ai_llm_calls"])
    st.metric("Deterministic fallbacks", sv["autonomy"]["ai_fallbacks"])
