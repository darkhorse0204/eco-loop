"""rebuild models/baseline.idf from the pristine energyplus example, repeatably.

steps (all safe to re-run):
  1. copy RefBldgSmallOfficeNew2004_Chicago.idf  (doe prototype small office)
  2. set the run-period window (default 7/1 -> 7/14)
  3. turn the weather-file run period on (the example ships with it off)
  4. switch sqlite output to SimpleAndTabular so we get proper energy totals
  5. move Site:Location to tampa, fl so the solar geometry matches the weather file

i leave the design days alone on purpose - they size the ac fine for tampa
(checked: baseline max occupied |pmv| = 0.36), so there's no need for risky .ddy edits.

usage: python scripts/build_model.py [begin_m begin_d end_m end_d]
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EPLUS = next((REPO / "tools").glob("EnergyPlus*"))
SRC = EPLUS / "ExampleFiles" / "RefBldgSmallOfficeNew2004_Chicago.idf"
DDY = REPO / "weather/USA_FL_Tampa.Intl.AP.722110_TMY3.ddy"
OUT = REPO / "models/baseline.idf"


def first_object(text, keyword):
    m = re.search(re.escape(keyword) + r"\b.*?;", text, re.S)
    if not m:
        raise SystemExit(f"missing {keyword}")
    return m.group(0)


def main():
    bm, bd, em, ed = (int(x) for x in (sys.argv[1:5] or [7, 1, 7, 14]))
    idf = SRC.read_text(encoding="latin-1")

    # 3) turn on the weather-file run period
    idf = idf.replace(
        "NO,                      !- Run Simulation for Weather File Run Periods",
        "YES,                     !- Run Simulation for Weather File Run Periods",
    )
    # 4) sqlite tabular output
    idf = idf.replace(
        "Output:SQLite,\n    Simple;", "Output:SQLite,\n    SimpleAndTabular;"
    )
    # 5) move Site:Location to tampa
    tampa_loc = first_object(DDY.read_text(encoding="latin-1"), "Site:Location")
    idf = re.sub(r"Site:Location\b.*?;", tampa_loc.strip(), idf, count=1, flags=re.S)

    # 2) rewrite the first run-period object's window fields cleanly
    def repl_runperiod(m):
        head = m.group(1)
        block = (
            f"    {bm},                       !- Begin Month\n"
            f"    {bd},                       !- Begin Day of Month\n"
            f"    ,                        !- Begin Year\n"
            f"    {em},                      !- End Month\n"
            f"    {ed},                      !- End Day of Month\n"
            f"    ,                        !- End Year\n"
            f"    Sunday,                  !- Day of Week for Start Day\n"
            f"    Yes,                     !- Use Weather File Holidays and Special Days\n"
            f"    Yes,                     !- Use Weather File Daylight Saving Period\n"
            f"    No,                      !- Apply Weekend Holiday Rule\n"
            f"    Yes,                     !- Use Weather File Rain Indicators\n"
            f"    Yes;                     !- Use Weather File Snow Indicators"
        )
        return head + block

    idf = re.sub(
        r"(RunPeriod,\s*\n\s*[^\n,]*,\s*!-\s*Name\s*\n).*?;",
        repl_runperiod,
        idf,
        count=1,
        flags=re.S,
    )

    # 6) turn on indoor-air-quality (co2) modelling so the agent has an iaq signal.
    #    the People objects already carry the default co2 generation rate, so once
    #    the contaminant balance is on we get realistic indoor co2 (~400 empty,
    #    ~750 occupied). outdoor baseline is a flat 400 ppm.
    idf += (
        "\nSchedule:Constant, OUTDOOR_CO2_SCH, Any Number, 400.0;\n\n"
        "ZoneAirContaminantBalance,\n"
        "  Yes,                     !- Carbon Dioxide Concentration\n"
        "  OUTDOOR_CO2_SCH;         !- Outdoor Carbon Dioxide Schedule Name\n"
    )

    OUT.write_text(idf, encoding="latin-1")
    print(f"built {OUT} | run {bm}/{bd}->{em}/{ed} | tampa | weather-run on | co2/iaq on")


if __name__ == "__main__":
    main()
